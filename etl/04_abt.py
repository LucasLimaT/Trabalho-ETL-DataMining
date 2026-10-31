#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 4 — TABELA-BASE DE ANÁLISE (ABT) E O EXPERIMENTO DE VAZAMENTO

Pergunta analítica:
    dado o comportamento do cliente ATÉ 2025-06-30, ele comprará de novo
    entre 2025-07-01 e 2025-09-28?

Esta etapa produz DUAS versões da mesma tabela:

    --correta     todos os atributos calculados apenas com dados até a data
                  de corte. É a versão que vale.

    --vazamento   a mesma coisa, só que com dois ou três atributos calculados
                  sobre a base inteira. É a versão errada, que vocês vão
                  construir de propósito, rodar no Orange e comparar.

O objetivo do experimento não é ver um número maior. É medir a distância
entre um resultado que parece excelente e um resultado que é válido —
e descobrir que a diferença entre os dois cabe em uma linha de código.

    docker compose exec etl python 04_abt.py --correta
    docker compose exec etl python 04_abt.py --vazamento
"""

import argparse

import pandas as pd
from sqlalchemy import text

from config import DATA_CORTE, DATA_FIM_ALVO, eng_dw
from gravacao import copy_pg
from qualidade.assercoes import Suite


# ---------------------------------------------------------------------------
# ALVO — este está pronto, e é o único ponto do pipeline em que é legítimo
# olhar para o futuro, porque é exatamente isso que se quer prever.
# ---------------------------------------------------------------------------
SQL_ALVO = f"""
WITH base AS (
    SELECT DISTINCT c.cliente_sk
    FROM dw.dim_cliente c
    JOIN dw.fato_pedido p ON p.cliente_sk = c.cliente_sk
    WHERE p.data_pedido_sk <= DATE '{DATA_CORTE}'
),
compras_na_janela AS (
    SELECT DISTINCT p.cliente_sk
    FROM dw.fato_pedido p
    JOIN dw.dim_status s ON s.status_sk = p.status_sk
    WHERE p.data_pedido_sk >  DATE '{DATA_CORTE}'
      AND p.data_pedido_sk <= DATE '{DATA_FIM_ALVO}'
      AND s.status_conformado <> 'CANCELADO'   -- ajuste ao SEU mapeamento
)
SELECT b.cliente_sk,
       DATE '{DATA_CORTE}' AS data_corte,
       CASE WHEN j.cliente_sk IS NOT NULL THEN 1 ELSE 0 END AS alvo_recompra_90d
FROM base b
LEFT JOIN compras_na_janela j ON j.cliente_sk = b.cliente_sk
"""


SQL_ATRIBUTOS = f"""
SELECT p.cliente_sk,
       COUNT(*) FILTER (
           WHERE s.status_conformado <> 'CANCELADO'
       ) AS frequencia_pedidos,
       COALESCE(
           SUM(p.valor_total) FILTER (WHERE s.status_conformado <> 'CANCELADO'),
           0
       )::numeric(18,2) AS valor_monetario,
       DATE '{DATA_CORTE}' - MAX(p.data_pedido_sk) FILTER (
           WHERE s.status_conformado <> 'CANCELADO'
       ) AS recencia_dias
FROM dw.fato_pedido p
JOIN dw.dim_status s USING(status_sk)
WHERE p.data_pedido_sk <= DATE '{DATA_CORTE}'
GROUP BY p.cliente_sk
"""


# Mesmos atributos, sem o filtro de data em frequencia_pedidos e recencia_dias:
# os dois passam a enxergar a janela do alvo e o que vem depois dela.
# O HAVING mantém a população do alvo (clientes com pedido até o corte).
SQL_ATRIBUTOS_VAZAMENTO = f"""
SELECT p.cliente_sk,
       COUNT(*) FILTER (
           WHERE s.status_conformado <> 'CANCELADO'
       ) AS frequencia_pedidos,
       COALESCE(
           SUM(p.valor_total) FILTER (
               WHERE s.status_conformado <> 'CANCELADO'
                 AND p.data_pedido_sk <= DATE '{DATA_CORTE}'
           ),
           0
       )::numeric(18,2) AS valor_monetario,
       DATE '{DATA_CORTE}' - MAX(p.data_pedido_sk) FILTER (
           WHERE s.status_conformado <> 'CANCELADO'
       ) AS recencia_dias
FROM dw.fato_pedido p
JOIN dw.dim_status s USING(status_sk)
GROUP BY p.cliente_sk
HAVING bool_or(p.data_pedido_sk <= DATE '{DATA_CORTE}')
"""


def monta_atributos_corretos(dw):
    # Mantém os valores monetários sem conversão para float.
    # A recência fica vazia quando o cliente só tem pedidos cancelados.
    df = pd.read_sql(text(SQL_ATRIBUTOS), dw, coerce_float=False)
    return df.astype({"recencia_dias": "Int64"})


def monta_atributos_com_vazamento(dw):
    """
    TODO 4.2 — reproduza os MESMOS atributos, removendo o filtro de data em
    pelo menos dois deles (por exemplo recencia_dias e frequencia_pedidos),
    de modo que passem a enxergar a janela do alvo.

    Acrescente um terceiro vazamento mais sutil: normalize (z-score) um
    atributo numérico usando média e desvio calculados sobre TODAS as linhas,
    antes de qualquer partição treino/teste.

    Documente no RDT qual vazamento você introduziu em cada coluna.
    """
    df = pd.read_sql(text(SQL_ATRIBUTOS_VAZAMENTO), dw)
    # Vazamento sutil: média e desvio de todas as linhas, antes da partição.
    v = df["valor_monetario"].astype(float)
    df["valor_monetario"] = (v - v.mean()) / v.std()
    return df.astype({"recencia_dias": "Int64"})


def grava(df, dw, sufixo):
    tabela = "recompra_90d" if sufixo == "correta" else "recompra_90d_vazamento"
    if sufixo == "correta":
        # Reaproveita a tabela para não perder as chaves e os tipos das colunas.
        dw.execute(
            text(
                """
                TRUNCATE abt.recompra_90d;
                ALTER TABLE abt.recompra_90d
                ADD COLUMN IF NOT EXISTS frequencia_pedidos bigint NOT NULL
                    CHECK(frequencia_pedidos >= 0),
                ADD COLUMN IF NOT EXISTS valor_monetario numeric(18,2) NOT NULL,
                ADD COLUMN IF NOT EXISTS recencia_dias integer
                    CHECK(recencia_dias >= 0)
                """
            )
        )
        df.to_sql(
            tabela,
            dw,
            schema="abt",
            if_exists="append",
            index=False,
            method=copy_pg,
            chunksize=5_000,
        )
    else:
        df.to_sql(
            tabela,
            dw,
            schema="abt",
            if_exists="replace",
            index=False,
            method="multi",
            chunksize=5_000,
        )
    print(f"abt.{tabela}: {len(df)} linhas, {df.shape[1]} colunas")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--correta", action="store_true")
    grupo.add_argument("--vazamento", action="store_true")
    args = ap.parse_args()

    dw = eng_dw()
    alvo = pd.read_sql(text(SQL_ALVO), dw)
    print(
        f"alvo: {len(alvo)} clientes | taxa de recompra = "
        f"{alvo['alvo_recompra_90d'].mean():.3f}"
    )

    qa = Suite(etapa="04_abt_" + ("correta" if args.correta else "vazamento"))
    qa.entre(
        "taxa do alvo",
        float(alvo["alvo_recompra_90d"].mean()),
        0.02,
        0.60,
        "classe extremamente desbalanceada indica erro na janela",
    )
    qa.unico("cliente_sk no alvo", alvo["cliente_sk"])

    atributos = (
        monta_atributos_corretos(dw)
        if args.correta
        else monta_atributos_com_vazamento(dw)
    )

    antes = len(alvo)
    abt = alvo.merge(atributos, on="cliente_sk", how="left")
    qa.cardinalidade_preservada("junção alvo x atributos", antes, len(abt))

    qa.igual("contagem da ABT igual ao alvo", len(abt), antes)
    qa.sem_nulos("frequencia_pedidos", abt["frequencia_pedidos"])
    qa.sem_nulos("valor_monetario", abt["valor_monetario"])
    if args.vazamento:
        # Média zero sobre a ABT inteira prova que o z-score não veio só do treino.
        qa.entre(
            "valor_monetario normalizado com a base inteira",
            abs(float(abt["valor_monetario"].mean())),
            0,
            1e-9,
        )
    tabela = "recompra_90d" if args.correta else "recompra_90d_vazamento"
    # No vazamento, valor_monetario está em z-score e não entra na conferência.
    confere_valor = (
        "OR a.valor_monetario IS DISTINCT FROM COALESCE(h.valor, 0)"
        if args.correta
        else ""
    )
    try:
        if qa.falhou():
            raise ValueError("ABT reprovada antes da gravação")
        with dw.begin() as cx:
            grava(abt, cx, "correta" if args.correta else "vazamento")
            # Confere os atributos com um novo cálculo sobre os pedidos até o corte.
            divergencias = cx.execute(
                text(
                    f"""
                    WITH historico AS (
                        SELECT p.cliente_sk, COUNT(*) frequencia,
                               SUM(p.valor_total) valor,
                               DATE '{DATA_CORTE}' - MAX(p.data_pedido_sk) recencia
                        FROM dw.fato_pedido p
                        JOIN dw.dim_status s USING(status_sk)
                        WHERE p.data_pedido_sk < DATE '{DATA_CORTE}' + 1
                          AND s.status_conformado <> 'CANCELADO'
                        GROUP BY p.cliente_sk
                    )
                    SELECT COUNT(*) FROM abt.{tabela} a
                    LEFT JOIN historico h USING(cliente_sk)
                    WHERE a.frequencia_pedidos
                              IS DISTINCT FROM COALESCE(h.frequencia, 0)
                       OR a.recencia_dias IS DISTINCT FROM h.recencia
                       {confere_valor}
                    """
                )
            ).scalar_one()
            if args.correta:
                qa.igual("atributos conferidos sem dados futuros", divergencias, 0)
            else:
                qa.maior_igual(
                    "frequência/recência enxergam o futuro",
                    divergencias,
                    1,
                    "a versão de comparação precisa conter o vazamento",
                )
            if qa.falhou():
                raise ValueError("ABT reprovada: gravação desfeita")
    except Exception:
        qa.igual("ABT concluida sem erro", 0, 1)
        raise
    finally:
        qa.registrar(dw)
        qa.imprimir()
