#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 1 — EXTRAÇÃO  (esqueleto: vocês completam)

Objetivo: trazer as quatro fontes para o esquema `stg` do DW, SEM transformar
significado. Renomear, converter tipo e decidir o que é nulo vem depois.

Regra da camada stg (seção 2.3 do referencial):
  · nomes de coluna espelham a origem;
  · nenhum valor é descartado, corrigido ou reinterpretado;
  · acrescente apenas metadados de procedência:
        _fonte           de onde veio
        _extraido_em     quando
        _linha_origem    posição na origem, quando fizer sentido

Decisão obrigatória a registrar no RDT antes de codificar:
  carga completa ou incremental? Justifique com base no que as origens oferecem.

    docker compose exec etl python 01_extracao.py
"""

import json
import re
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import Integer, inspect, text

from config import (ARQ_AVALIACOES, ARQ_SAC, eng_dw, eng_mysql, eng_origem)
from gravacao import copy_pg
from qualidade.assercoes import Suite

AGORA = datetime.now(timezone.utc)
DW = eng_dw()
LOTE = 50_000          # leia em blocos: a base não cabe confortavelmente na RAM

# RDT-01: carga completa. Nenhuma origem tem coluna de alteração (updated_at)
# nem log de mudanças; status e datas de entrega mudam sem deixar rastro.
TABELAS_LEGADO = ["CLIENTES", "PRODUTOS", "PEDIDOS", "ITENS_PEDIDO"]
TABELAS_ECOM = ["customer", "category", "product", "orders", "order_item"]


def grava_stg(df, tabela, primeiro_bloco, tipos=None):
    df["_fonte"] = tabela.split("_")[0]
    df["_extraido_em"] = AGORA
    df.to_sql(tabela, DW, schema="stg",
              if_exists="replace" if primeiro_bloco else "append",
              index=False, method=copy_pg, chunksize=LOTE, dtype=tipos)


def conta(engine, sql):
    with engine.connect() as cx:
        return cx.execute(text(sql)).scalar()


def copia_tabela(qa, origem, schema, tabela, destino):
    """Copia uma tabela em blocos, com os tipos da origem e sem tocar nos valores."""
    # tipos genéricos refletidos da origem: DECIMAL continua NUMERIC, DATETIME
    # continua sem fuso, TIMESTAMPTZ continua com fuso.
    tipos = {c["name"]: c["type"].as_generic()
             for c in inspect(origem).get_columns(tabela, schema=schema)}
    nome = f"{schema}.{tabela}" if schema else tabela
    lidas = 0
    with origem.connect().execution_options(stream_results=True) as cx:
        for i, df in enumerate(pd.read_sql(text(f"SELECT * FROM {nome}"), cx,
                                           chunksize=LOTE, coerce_float=False)):
            # inteiro com NULL chega como float (1.0); volta a inteiro
            for c in df.columns:
                if isinstance(tipos[c], Integer) and df[c].dtype.kind == "f":
                    df[c] = df[c].astype("Int64")
            grava_stg(df, destino, i == 0, tipos)
            lidas += len(df)
    esperadas = conta(origem, f"SELECT COUNT(*) FROM {nome}")
    gravadas = conta(DW, f"SELECT COUNT(*) FROM stg.{destino}")
    qa.igual(f"linhas {nome} lidas", lidas, esperadas,
             "extração perdeu ou duplicou linhas da origem")
    qa.igual(f"linhas stg.{destino} gravadas", gravadas, esperadas,
             "gravação na stg divergiu da origem")
    print(f"  stg.{destino}: {gravadas} linhas")


def extrai_mysql(qa):
    eng = eng_mysql()
    for tabela in TABELAS_LEGADO:
        copia_tabela(qa, eng, None, tabela, f"legado_{tabela.lower()}")

    # A conexão vem com charset=latin1: os acentos têm de chegar intactos.
    com_acento = conta(DW, """SELECT COUNT(*) FROM stg.legado_clientes
                              WHERE "NOME" ~ '[ÁÉÍÓÚÂÊÔÃÕÇ]'""")
    mojibake = conta(DW, """SELECT COUNT(*) FROM stg.legado_clientes
                            WHERE "NOME" ~ 'Ã[\\x80-\\xBF]|\\uFFFD'
                               OR "CIDADE" ~ 'Ã[\\x80-\\xBF]|\\uFFFD'""")
    qa.maior_igual("legado: nomes com acento preservado", com_acento, 1,
                   "nenhum acento chegou: leitura com charset errado")
    qa.igual("legado: nomes/cidades com mojibake", mojibake, 0,
             "acentos corrompidos (latin1 lido como utf8)")


def extrai_postgres(qa):
    # placed_at/delivered_at/created_at ficam TIMESTAMPTZ na stg: o instante é
    # preservado. A padronização de fuso (RDT-09) é transformação, etapa 2.
    eng = eng_origem()
    for tabela in TABELAS_ECOM:
        copia_tabela(qa, eng, "shop", tabela, f"ecom_{tabela}")


def extrai_sac(qa):
    # keep_default_na=False: '' continua '', e não vira NaN. Decidir o que é
    # ausência é transformação (RDT-08), não leitura.
    df = pd.read_csv(ARQ_SAC, sep=";", encoding="latin-1", dtype=str,
                     keep_default_na=False)
    df["_linha_origem"] = range(1, len(df) + 1)
    grava_stg(df, "sac_atendimentos", True)

    # contagem independente do parser: registros começam com o protocolo
    with open(ARQ_SAC, encoding="latin-1", newline="") as f:
        esperadas = len(re.findall(r"(?m)^P\d+;", f.read()))
    qa.igual("linhas SAC lidas", len(df), esperadas,
             "parser perdeu ou partiu registros com quebra de linha interna")
    qa.sem_nulos("protocolo do SAC", df["protocolo"].mask(df["protocolo"] == ""))
    print(f"  stg.sac_atendimentos: {len(df)} linhas")


def _txt(v):
    """Valor JSON como texto, sem perder o original: 4 -> '4', "4" -> '4'."""
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v)


def extrai_avaliacoes(qa):
    linhas, n = [], 0
    with open(ARQ_AVALIACOES, encoding="utf-8") as f:
        for n, bruto in enumerate(f, start=1):
            o = json.loads(bruto)
            linhas.append({
                "review_id": o.get("review_id"),
                "order_ref": _txt(o.get("order", {}).get("ref")),
                "order_channel": _txt(o.get("order", {}).get("channel")),
                "product_sku": _txt(o.get("product", {}).get("sku")),
                "submitted_at": _txt(o.get("submitted_at")),
                "rating": _txt(o.get("rating")),
                "title": _txt(o.get("title")),
                "comment": _txt(o.get("comment")),
                "verified_purchase": _txt(o.get("verified_purchase")),
                "helpful_votes": _txt(o.get("helpful_votes")),
                # chave ausente != chave nula: o objeto bruto guarda a diferença
                # (e o tipo original de rating).
                "_json_bruto": bruto.rstrip("\n"),
                "_linha_origem": n,
            })
    df = pd.DataFrame(linhas)
    grava_stg(df, "avaliacoes_marketplace", True)

    qa.igual("linhas avaliações lidas", len(df), n,
             "linhas do NDJSON não viraram registros")
    qa.unico("review_id", df["review_id"])
    qa.sem_nulos("order_ref das avaliações", df["order_ref"])
    print(f"  stg.avaliacoes_marketplace: {len(df)} linhas")


if __name__ == "__main__":
    qa = Suite(etapa="01_extracao")
    extrai_mysql(qa)
    extrai_postgres(qa)
    extrai_sac(qa)
    extrai_avaliacoes(qa)

    qa.registrar(DW)
    qa.imprimir()
