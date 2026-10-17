#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 2 — TRANSFORMAÇÃO E CONFORMAÇÃO  (esqueleto: vocês completam)

Esta é a etapa epistemicamente densa. Cada bloco abaixo corresponde a uma
decisão que NÃO tem resposta única. Para cada uma, o RDT deve trazer:
    (a) as alternativas consideradas;
    (b) a escolhida e por quê;
    (c) o que se perde com ela;
    (d) o número de registros afetados.

Uma decisão sem (c) e (d) não conta como decisão registrada.

Saída: schema `cnf` (conformado). Cada tabela ainda usa a chave natural da
origem e carrega o rastro das decisões (flags). A etapa 3 só troca chaves
naturais por surrogate keys e carrega o esquema estrela.

Fluxo: cada tabela é lida da stg uma vez (funções le_*). Cada decisão recebe
as tabelas de que precisa e devolve as tabelas com as colunas que acrescenta.
Valores monetários circulam em centavos inteiros (exato e leve) e viram
NUMERIC(12,2) só na gravação.

    docker compose exec etl python 02_transformacao.py
"""

import re
import sys
import unicodedata

import pandas as pd
from sqlalchemy import Numeric, text

from config import eng_dw
from gravacao import copy_pg
from qualidade.assercoes import Suite

FUSO = "America/Sao_Paulo"   # RDT-09: todo horário vira hora local de Brasília
DW = eng_dw()
PREFIXO = {"LEGADO": "LEG", "ECOM": "ECOM"}
LOTE = 50_000
MONETARIAS = {"valor_total_origem", "frete_origem", "valor_mercadoria",
              "valor_desconto", "valor_frete", "valor_total", "valor_unitario",
              "valor_bruto", "valor_linha", "preco_referencia", "desconto_origem"}


def le(sql, **opcoes):
    # em blocos e com cursor no servidor: sem isso, ler 1,4 milhão de itens de
    # uma vez passa de 1 GB e estoura a memória do contêiner
    with DW.connect().execution_options(stream_results=True) as cx:
        df = pd.concat(pd.read_sql(text(sql), cx, chunksize=LOTE, **opcoes),
                       ignore_index=True)
    for c in MONETARIAS & set(df.columns):     # chegam em centavos (ver centavos)
        df[c] = df[c].astype("Int64")
    if "sistema" in df:   # uma única string 'LEGADO'/'ECOM' em vez de uma por linha
        df["sistema"] = df["sistema"].map(sys.intern)
    return df


def centavos(coluna):
    """NUMERIC(…,2) da stg -> centavos inteiros, sem passar por float."""
    return f"ROUND({coluna} * 100)::bigint"


def nulo_se_vazio(serie):
    return serie.where(serie.fillna("").str.strip() != "")


def resumo(titulo, **valores):
    print(f"  {titulo}: " + "; ".join(f"{k.replace('_', ' ')} {v}"
                                      for k, v in valores.items()))


# ---------------------------------------------------------------------------
# Utilitários que vocês vão precisar — estes estão prontos.
# ---------------------------------------------------------------------------
def so_digitos(txt):
    return re.sub(r"\D", "", txt or "")


def cpf_valido(cpf):
    """Valida os dois dígitos verificadores. Retorna None se não tiver 11 dígitos."""
    c = so_digitos(cpf)
    if len(c) != 11 or len(set(c)) == 1:
        return None if len(c) != 11 else False
    b = [int(x) for x in c]
    s = sum((10 - i) * b[i] for i in range(9))
    d1 = 0 if (11 - s % 11) >= 10 else (11 - s % 11)
    s = sum((11 - i) * (b[:9] + [d1])[i] for i in range(10))
    d2 = 0 if (11 - s % 11) >= 10 else (11 - s % 11)
    return b[9] == d1 and b[10] == d2


def normaliza_nome(nome):
    """MAIÚSCULAS sem acento, espaços colapsados. Base para pareamento aproximado."""
    n = unicodedata.normalize("NFD", (nome or "").strip().upper())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", n)


# ===========================================================================
# LEITURA DA STG — cada tabela é lida uma única vez
# ===========================================================================
def le_pedidos(qa, xref):
    # RDT-09: TIMESTAMPTZ convertido para hora local; DATETIME do legado já é
    # hora local (pedidos só entre 8h e 21h, horário de loja).
    ped = le(f'''SELECT 'LEGADO' sistema, "ID_PEDIDO" id_pedido,
                        "ID_CLIENTE" id_cliente_origem, "STATUS" status_origem,
                        "CANAL" canal_origem, "DT_PEDIDO" dt_pedido,
                        "DT_ENTREGA" dt_entrega,
                        {centavos('"VALOR_TOTAL"')} valor_total_origem,
                        {centavos('"VALOR_FRETE"')} frete_origem
                 FROM stg.legado_pedidos
                 UNION ALL
                 SELECT 'ECOM', order_id, customer_id, status, channel,
                        placed_at AT TIME ZONE '{FUSO}', delivered_at AT TIME ZONE '{FUSO}',
                        {centavos("total_amount")}, {centavos("freight_amount")}
                 FROM stg.ecom_orders''', parse_dates=["dt_pedido", "dt_entrega"])
    ped.insert(0, "id_origem", ped.sistema.map(PREFIXO) + "-" + ped.id_pedido.astype(str))
    antes = len(ped)
    ped = ped.merge(xref[["sistema", "id_origem", "cliente_id"]]
                    .rename(columns={"id_origem": "id_cliente_origem"}),
                    on=["sistema", "id_cliente_origem"], how="left")
    qa.cardinalidade_preservada("pedido x cliente", antes, len(ped))
    ped["cliente_id"] = ped.cliente_id.astype("Int64")
    return ped


def le_itens():
    return le(f'''SELECT 'LEGADO' sistema, "ID_ITEM" id_item, "ID_PEDIDO" id_pedido,
                        "ID_PRODUTO" id_produto, "QTD" quantidade,
                        {centavos('"VL_UNITARIO"')} valor_unitario,
                        {centavos('"VL_DESCONTO"')} desconto_origem
                 FROM stg.legado_itens_pedido
                 UNION ALL
                 SELECT 'ECOM', order_item_id, order_id, product_id, quantity,
                        {centavos("unit_price")}, {centavos("discount")}
                 FROM stg.ecom_order_item''')


def le_produtos():
    prod = le(f'''SELECT 'LEGADO' sistema, "ID_PRODUTO" id_origem, NULL sku,
                        "DESCRICAO" descricao, "CATEGORIA" categoria_origem,
                        NULL::int category_id,
                        {centavos('"PRECO_TABELA"')} preco_referencia,
                        CASE "ATIVO" WHEN 'S' THEN true WHEN 'N' THEN false END ativo
                 FROM stg.legado_produtos
                 UNION ALL
                 SELECT 'ECOM', p.product_id, p.sku, p.title, c.name, p.category_id,
                        {centavos("p.list_price")}, p.active
                 FROM stg.ecom_product p LEFT JOIN stg.ecom_category c USING (category_id)''')
    prod["category_id"] = prod.category_id.astype("Int64")
    return prod


def le_atendimentos(qa):
    sac = le("SELECT * FROM stg.sac_atendimentos ORDER BY _linha_origem")
    campos = [c for c in sac.columns if not c.startswith("_")]
    sac = sac.drop_duplicates(subset=campos)[campos]   # cópias exatas da exportação
    qa.unico("protocolo do SAC sem cópias", sac.protocolo)
    # decisões menores: canal em maiúsculas; motivo padronizado, e a observação
    # "cliente reiterou o contato" vira indicador
    sac["canal"] = sac.canal.str.upper()
    sac["motivo_conformado"] = (sac.motivo.str.split("\n").str[0]
                                .map(normaliza_nome).map(MOTIVO_SAC))
    sac["contato_reiterado"] = sac.motivo.str.contains("\n")
    qa.sem_nulos("motivo do SAC conformado", sac.motivo_conformado)
    return sac


def le_avaliacoes():
    return le('''SELECT review_id, order_ref, product_sku, submitted_at, rating,
                        verified_purchase, helpful_votes
                 FROM stg.avaliacoes_marketplace ORDER BY _linha_origem''')


# ===========================================================================
# DECISÃO 1 — IDENTIDADE DO CLIENTE ENTRE OS DOIS SISTEMAS
# ===========================================================================
def unifica_clientes(qa):
    """
    O CPF é a única ponte direta entre legado e e-commerce, mas:
      · parte dos CPFs está ausente nas duas origens;
      · parte tem dígito verificador inválido;
      · no legado ele aparece ora com máscara, ora sem;
      · o legado ainda contém duplicatas exatas e aproximadas do mesmo cliente.

    Perguntas que sua regra precisa responder — e o RDT, justificar:
      a) CPF com dígito verificador inválido serve para parear? Ele identifica
         de forma tão estável quanto um válido, mesmo estando errado?
      b) Sem CPF, você pareia por nome normalizado + cidade + UF? Qual a taxa
         de falsos positivos que isso introduz? Estime-a.
      c) Qual erro é mais grave aqui: fundir dois clientes distintos, ou manter
         duplicado um mesmo cliente? A resposta muda conforme o uso do dado.
      d) Como você trata as duplicatas INTERNAS do legado antes de cruzar?

    Preencha dim_cliente com o rastro: id_legado, id_ecommerce,
    origem_unificacao e regra_pareamento (que regra uniu cada par).
    """
    L = le('''SELECT "ID_CLIENTE" id, "NOME" nome, "CPF" cpf, "EMAIL" email,
                     "DT_CADASTRO" dt, "CIDADE" cidade, "UF" uf
              FROM stg.legado_clientes ORDER BY 1''')
    E = le(f'''SELECT customer_id id, full_name nome, tax_id cpf, email,
                      (created_at AT TIME ZONE '{FUSO}')::date dt,
                      city cidade, state uf
               FROM stg.ecom_customer ORDER BY 1''')
    for df in (L, E):
        df["cpf"] = nulo_se_vazio(df["cpf"].fillna("").map(so_digitos))
        df["cidade"] = nulo_se_vazio(df["cidade"].replace("N/A", ""))  # sentinela (RDT-08)
        df["nome_n"] = df["nome"].fillna("").map(normaliza_nome)
        df["dt"] = pd.to_datetime(df["dt"])

    L, Lu = deduplica_legado(L)
    for df in (Lu, E):
        df["cidade_n"] = df["cidade"].fillna("").map(normaliza_nome)
    pares = pareia(qa, Lu, E)
    clientes, xref = monta_clientes(L, Lu, E, pares)
    qa.sem_nulos("todo registro de origem tem cliente", xref.cliente_id)
    qa.unico("um registro de origem, um cliente",
             xref.sistema + "-" + xref.id_origem.astype(str))
    return clientes, xref


def deduplica_legado(L):
    """RDT-03: mesmo e-mail e mesmo nome normalizado, com CPF igual ou ausente
    em um dos registros. O menor ID sobrevive e herda o primeiro valor não nulo
    de cada campo do grupo."""
    L = L.copy()
    L["sobrevivente"] = (L.groupby(["email", "nome_n"])["id"].transform("min")
                         .fillna(L["id"]).astype(int))
    conflito = L.groupby("sobrevivente")["cpf"].transform("nunique") > 1
    L.loc[conflito, "sobrevivente"] = L.loc[conflito, "id"]
    Lu = (L.sort_values("id").groupby("sobrevivente", as_index=False)
          .agg(nome_n=("nome_n", "first"), cpf=("cpf", "first"),
               cidade=("cidade", "first"), uf=("uf", "first"), dt=("dt", "min"))
          .rename(columns={"sobrevivente": "id"}))
    resumo("legado", registros=len(L), clientes=len(Lu),
           duplicatas_fundidas=len(L) - len(Lu),
           recusadas_por_CPF_conflitante=int(conflito.sum()))
    return L, Lu


def pareia(qa, Lu, E):
    """RDT-02: pares (id_l, id_e, regra) entre os dois sistemas."""
    # regra 1: CPF de 11 dígitos, válido ou não, com o nome normalizado
    # confirmando. Só pares 1:1.
    m = (Lu.dropna(subset=["cpf"])
         .merge(E.dropna(subset=["cpf"]), on="cpf", suffixes=("_l", "_e")))
    mesmo_nome = m.nome_n_l == m.nome_n_e
    m = m[mesmo_nome]
    m = m[~m.id_l.duplicated(keep=False) & ~m.id_e.duplicated(keep=False)]
    p1 = m[["id_l", "id_e"]].assign(regra="CPF")

    # regra 2: nome + cidade + UF, único em cada base, só quando ao menos um
    # lado não tem CPF (se os dois têm e diferem, são pessoas diferentes).
    chave = ["nome_n", "cidade_n", "uf"]

    def unicos(df):
        ok = df[df.cidade_n != ""]
        return ok[~ok.duplicated(chave, keep=False)]

    cand = unicos(Lu).merge(unicos(E), on=chave, suffixes=("_l", "_e"))
    ambos_cpf = cand.cpf_l.notna() & cand.cpf_e.notna()
    # onde os dois lados têm CPF, ele arbitra: é a estimativa de falso positivo
    fp = float((cand.cpf_l != cand.cpf_e)[ambos_cpf].mean())
    livres = ~cand.id_l.isin(p1.id_l) & ~cand.id_e.isin(p1.id_e)
    p2 = cand[livres & ~ambos_cpf][["id_l", "id_e"]].assign(regra="NOME_CIDADE_UF")

    resumo("pareamento", por_CPF=len(p1), recusados_por_nome_divergente=int((~mesmo_nome).sum()),
           por_nome_cidade_UF=len(p2),
           falso_positivo_estimado=f"{fp:.2%} em {int(ambos_cpf.sum())} pares-teste")
    qa.entre("falso positivo estimado nome+cidade+UF", fp, 0, 0.05,
             "regra aproximada funde pessoas distintas demais")
    qa.entre("taxa de pareamento por CPF (clientes do legado)",
             len(p1) / len(Lu), 0.25, 0.35)
    return pd.concat([p1, p2], ignore_index=True)


def monta_clientes(L, Lu, E, pares):
    """Uma linha por pessoa, e o rastro de todo registro de origem até ela."""
    A = (pares.merge(Lu.add_suffix("_l"), on="id_l")
         .merge(E.add_suffix("_e"), on="id_e"))
    so_l = Lu[~Lu.id.isin(pares.id_l)]
    so_e = E[~E.id.isin(pares.id_e)]
    C = pd.concat([
        pd.DataFrame({"id_legado": A.id_l, "id_ecommerce": A.id_e,
                      "cpf_normalizado": A.cpf_l.fillna(A.cpf_e),
                      "nome_padronizado": A.nome_n_l,
                      "cidade": A.cidade_e.fillna(A.cidade_l),
                      "uf": A.uf_e.fillna(A.uf_l),
                      "dt_primeiro_cadastro": A[["dt_l", "dt_e"]].min(axis=1),
                      "origem_unificacao": "AMBOS", "regra_pareamento": A.regra}),
        pd.DataFrame({"id_legado": so_l.id, "cpf_normalizado": so_l.cpf,
                      "nome_padronizado": so_l.nome_n, "cidade": so_l.cidade,
                      "uf": so_l.uf, "dt_primeiro_cadastro": so_l.dt,
                      "origem_unificacao": "LEGADO"}),
        pd.DataFrame({"id_ecommerce": so_e.id, "cpf_normalizado": so_e.cpf,
                      "nome_padronizado": so_e.nome_n, "cidade": so_e.cidade,
                      "uf": so_e.uf, "dt_primeiro_cadastro": so_e.dt,
                      "origem_unificacao": "ECOM"}),
    ], ignore_index=True)
    C["id_legado"] = C["id_legado"].astype("Int64")
    C["id_ecommerce"] = C["id_ecommerce"].astype("Int64")
    C = C.sort_values(["id_legado", "id_ecommerce"], na_position="last",
                      ignore_index=True)
    C.insert(0, "cliente_id", range(1, len(C) + 1))
    C["dt_primeiro_cadastro"] = C["dt_primeiro_cadastro"].dt.date

    # CPF que sobra em mais de um cliente (pessoas diferentes com o mesmo CPF):
    # só fica com quem teve o CPF confirmado pelos dois sistemas.
    repetido = C.cpf_normalizado.notna() & C.duplicated("cpf_normalizado", keep=False)
    C["cpf_conflito"] = repetido & (C.regra_pareamento != "CPF")
    C.loc[C.cpf_conflito, "cpf_normalizado"] = None
    C["cpf_valido"] = C.cpf_normalizado.map(cpf_valido, na_action="ignore").astype("boolean")

    mapa_l = C.dropna(subset=["id_legado"]).set_index("id_legado")["cliente_id"]
    mapa_e = C.dropna(subset=["id_ecommerce"]).set_index("id_ecommerce")["cliente_id"]
    X = pd.concat([
        pd.DataFrame({"sistema": "LEGADO", "id_origem": L.id, "cpf": L.cpf,
                      "cliente_id": L.sobrevivente.map(mapa_l),
                      "duplicata_de": L.sobrevivente.where(L.sobrevivente != L.id)}),
        pd.DataFrame({"sistema": "ECOM", "id_origem": E.id, "cpf": E.cpf,
                      "cliente_id": E.id.map(mapa_e)}),
    ], ignore_index=True)
    X["duplicata_de"] = X["duplicata_de"].astype("Int64")
    return C, X


# ===========================================================================
# DECISÃO 2 — CONFORMAÇÃO DE STATUS
# ===========================================================================
STATUS = {  # (sistema, status de origem): (status_conformado, motivo_cancelamento)
    ("LEGADO", "ENTREGUE"):       ("ENTREGUE", None),
    ("LEGADO", "EM TRANSITO"):    ("EM_TRANSITO", None),
    ("LEGADO", "SEPARACAO"):      ("EM_PREPARACAO", None),
    ("LEGADO", "CANCELADO"):      ("CANCELADO", None),   # legado não distingue
    ("ECOM", "delivered"):        ("ENTREGUE", None),
    ("ECOM", "shipped"):          ("EM_TRANSITO", None),
    ("ECOM", "processing"):       ("EM_PREPARACAO", None),
    ("ECOM", "canceled"):         ("CANCELADO", "CLIENTE"),
    ("ECOM", "payment_declined"): ("CANCELADO", "PAGAMENTO_RECUSADO"),
}


def conforma_status(qa, pedidos):
    """
    Legado:      ENTREGUE · CANCELADO · EM TRANSITO · SEPARACAO
    E-commerce:  delivered · canceled · payment_declined · shipped · processing

    O legado agrega em CANCELADO duas situações que o e-commerce separa:
    cancelamento pelo cliente e recusa de pagamento.

    Alternativas, todas defensáveis:
      (i)   mapear payment_declined -> CANCELADO. Simples; perde a distinção
            justamente onde ela existe.
      (ii)  criar CANCELADO_CLIENTE e CANCELADO_PAGAMENTO, deixando o legado
            com motivo NULL. Preserva a informação; cria assimetria que precisa
            ser tratada por qualquer análise posterior.
      (iii) criar um nível hierárquico: status_conformado = CANCELADO e
            motivo_cancelamento preenchido apenas quando conhecido.

    Escolha e preencha dw.dim_status. Registre quantos pedidos caem em cada
    célula do mapeamento — esse número é parte da justificativa.
    """
    # RDT-04: alternativa (iii). O status conformado é comum aos dois sistemas;
    # o motivo só existe onde a origem distingue.
    status = (pedidos.groupby(["sistema", "status_origem"], as_index=False)
              .size().rename(columns={"size": "n_pedidos"}))
    qa.dominio("status de origem mapeados", status.sistema + "|" + status.status_origem,
               {f"{s}|{o}" for s, o in STATUS})
    status[["status_conformado", "motivo_cancelamento"]] = pd.DataFrame(
        [STATUS.get(k, (None, None)) for k in zip(status.sistema, status.status_origem)],
        index=status.index)
    antes = len(pedidos)
    pedidos = pedidos.merge(status.drop(columns="n_pedidos"),
                            on=["sistema", "status_origem"], how="left")
    qa.cardinalidade_preservada("pedido x status", antes, len(pedidos))
    return status, pedidos


# ===========================================================================
# DECISÃO 3 — SEMÂNTICA DOS VALORES MONETÁRIOS
# ===========================================================================
def conforma_valores(qa, pedidos, itens, produtos):
    """
    VALOR_TOTAL (legado) e total_amount (e-commerce) NÃO medem a mesma coisa.
    Descubra a diferença comparando cada um com a soma dos itens do pedido —
    o dicionário de dados não diz, porque na vida real ele também não diria.

    Depois, preencha dw.fato_pedido com medidas explicitamente definidas:
        valor_mercadoria  (sem frete, sem desconto)
        valor_frete
        valor_desconto
        valor_total = mercadoria + frete - desconto

    Trate também:
      · frete negativo (o que é? estorno? erro? decida e registre);
      · desconto = -1 no legado (sentinela, não valor);
      · preços com magnitude 10x acima da mediana da categoria (digitação).
        Atenção: nem todo valor alto é erro. Qual é o seu critério?
    """
    chave = ["sistema", "id_pedido"]

    # RDT-06: desconto -1 é sentinela de "sem desconto" — a reconciliação
    # abaixo só fecha com ele valendo 0. Valores em centavos: -1,00 = -100.
    itens = itens.assign(desconto_sentinela=itens.desconto_origem == -100)
    itens["valor_desconto"] = itens.desconto_origem.mask(itens.desconto_sentinela, 0)
    itens["valor_bruto"] = itens.quantidade * itens.valor_unitario
    itens["valor_linha"] = itens.valor_bruto - itens.valor_desconto
    # item órfão fica marcado, não descartado: decisão da etapa 3 (RDT-10)
    itens["orfao"] = ~pd.MultiIndex.from_frame(itens[chave]).isin(
        pd.MultiIndex.from_frame(pedidos[chave]))
    itens = itens.drop(columns="desconto_origem")

    soma = (itens[~itens.orfao].groupby(chave, as_index=False)
            .agg(qtd_itens=("id_item", "size"),
                 valor_mercadoria=("valor_bruto", "sum"),
                 valor_desconto=("valor_desconto", "sum")))
    antes = len(pedidos)
    pedidos = pedidos.merge(soma, on=chave, how="left")
    qa.cardinalidade_preservada("pedido x soma dos itens", antes, len(pedidos))
    qa.igual("pedidos sem nenhum item", int(pedidos.qtd_itens.isna().sum()), 0,
             "pedido sem item teria valor_mercadoria inventado (0)")
    pedidos["qtd_itens"] = pedidos.qtd_itens.fillna(0).astype(int)
    pedidos[["valor_mercadoria", "valor_desconto"]] = (
        pedidos[["valor_mercadoria", "valor_desconto"]].fillna(0).astype("Int64"))

    # RDT-05: a semântica de cada total, provada contra a soma dos itens.
    liquido = pedidos.valor_mercadoria - pedidos.valor_desconto
    com_frete = (pedidos.valor_total_origem - (liquido + pedidos.frete_origem)).abs() <= 1
    sem_frete = (pedidos.valor_total_origem - liquido).abs() <= 1
    leg = pedidos.sistema == "LEGADO"
    resumo("totais que batem com itens + frete",
           legado=int(com_frete[leg].sum()), ecom=int(com_frete[~leg].sum()))
    resumo("totais que batem só com itens",
           legado=int(sem_frete[leg].sum()), ecom=int(sem_frete[~leg].sum()))
    qa.igual("legado: VALOR_TOTAL = itens + frete", int((~com_frete[leg]).sum()), 0,
             "VALOR_TOTAL do legado deixou de incluir o frete")
    qa.igual("ecom: total_amount = itens (sem frete)", int((~sem_frete[~leg]).sum()), 0,
             "total_amount do e-commerce deixou de ser só mercadoria")

    # RDT-06: frete negativo é erro de sinal (mesma distribuição de status e de
    # magnitude dos fretes positivos), não estorno.
    pedidos["frete_sinal_corrigido"] = pedidos.frete_origem < 0
    pedidos["valor_frete"] = pedidos.frete_origem.abs()
    pedidos["valor_total"] = (pedidos.valor_mercadoria + pedidos.valor_frete
                              - pedidos.valor_desconto)

    # RDT-06: preço até 10x acima do normal. Critério: >= 5x a mediana da
    # categoria de origem. Só marca; o valor cobrado é o que está nos itens.
    # mediana em reais (float): é referência do critério, não valor monetário
    preco = produtos.preco_referencia.astype(float) / 100
    mediana = preco.groupby([produtos.sistema, produtos.categoria_origem]).transform("median")
    produtos = produtos.assign(mediana_categoria=mediana,
                               razao_mediana=(preco / mediana).round(2),
                               preco_suspeito=preco >= 5 * mediana)
    return pedidos, itens, produtos


# ===========================================================================
# DECISÃO 4 — TAXONOMIA DE PRODUTO
# ===========================================================================
# RDT-07: cada valor vai até o nível mais profundo que ele garante. Nível
# desconhecido fica NULL; o texto bruto segue em categoria_origem.
NIVEIS = ["categoria_nivel1", "categoria_nivel2"]
CATEGORIA_LEGADO = {
    "ELETRO":            ("Eletrodomésticos", None),
    "Eletro":            ("Eletrodomésticos", None),
    "eletrodomesticos":  ("Eletrodomésticos", None),
    "ELETRODOMÉSTICOS":  ("Eletrodomésticos", None),
    "Eletro-domesticos": ("Eletrodomésticos", None),
    "INFORMATICA":       ("Informática", None),
    "Informática":       ("Informática", None),
    "INFO":              ("Informática", None),
    "TELEFONIA":         ("Telefonia", None),
    "Celulares":         ("Telefonia", "Smartphones"),
    "CELULAR/SMARTPHONE": ("Telefonia", "Smartphones"),
    "CASA":              ("Casa e Decoração", None),
    "Casa e decoracao":  ("Casa e Decoração", None),
    "MOVEIS":            ("Casa e Decoração", "Móveis"),
    "Televisores":       ("Áudio e TV", "Televisores"),
    "TV E SOM":          ("Áudio e TV", None),          # cobre Televisores e Áudio
    "AUDIO/VIDEO":       ("Áudio e TV", None),          # idem
    "ESPORTE":           ("Esporte e Lazer", "Esporte e Lazer"),
    "Esportes e lazer":  ("Esporte e Lazer", "Esporte e Lazer"),
    "FERRAMENTAS":       ("Ferramentas", "Ferramentas"),
    "Ferramentas e construcao": ("Ferramentas", "Ferramentas"),
    "GAMES":             ("Games", None),
    "Games e consoles":  ("Games", None),
    "informatica e games": (None, None),                # dois ramos da raiz
    "DIVERSOS":          (None, None),
    "OUTROS":            (None, None),
    "":                  (None, None),
}


def conforma_categorias(qa, produtos):
    """
    O legado guarda categoria como texto livre ('ELETRO', 'Eletro-domesticos',
    'informatica e games', 'DIVERSOS', ''); o e-commerce tem taxonomia de dois
    níveis. Construa o mapeamento.

    Casos que não têm solução limpa — e é esse o ponto:
      · 'informatica e games' cobre dois ramos distintos da taxonomia;
      · 'DIVERSOS', 'OUTROS' e '' não informam nada;
      · 'TV E SOM' cobre dois filhos de 'Áudio e TV'.

    Mantenha categoria_origem preenchida com o valor bruto. Conformar não
    autoriza apagar a evidência do que havia antes.
    """
    # Taxonomia do e-commerce. Folha na raiz (Esporte e Lazer, Ferramentas)
    # repete o nome no nível 2: assim nível 2 NULL significa só "desconhecido".
    cat = le("SELECT category_id, name, parent_id FROM stg.ecom_category")
    cat["categoria_nivel1"] = cat.parent_id.map(cat.set_index("category_id").name).fillna(cat.name)
    cat["categoria_nivel2"] = cat.name
    validos = set(zip(cat.categoria_nivel1, cat.categoria_nivel2)) \
        | {(n1, None) for n1 in cat.categoria_nivel1} | {(None, None)}
    qa.igual("mapeamento do legado fora da taxonomia",
             len(set(CATEGORIA_LEGADO.values()) - validos), 0)

    leg = produtos.sistema == "LEGADO"
    qa.dominio("categorias do legado mapeadas", produtos.categoria_origem[leg],
               set(CATEGORIA_LEGADO))
    qa.sem_orfaos("produtos do e-commerce com categoria na taxonomia",
                  produtos.category_id[~leg], cat.category_id)
    mapa_legado = pd.DataFrame(list(CATEGORIA_LEGADO.values()),
                               index=list(CATEGORIA_LEGADO), columns=NIVEIS)
    niveis = pd.concat([
        mapa_legado.reindex(produtos.categoria_origem[leg]).set_axis(produtos.index[leg]),
        cat.set_index("category_id")[NIVEIS].reindex(produtos.category_id[~leg])
           .set_axis(produtos.index[~leg]),
    ])
    produtos = produtos.join(niveis)
    return produtos[["sistema", "id_origem", "sku", "descricao", *NIVEIS,
                     "categoria_origem", "preco_referencia", "mediana_categoria",
                     "razao_mediana", "preco_suspeito", "ativo"]]


# ===========================================================================
# DECISÃO 5 — DATAS, AUSÊNCIAS E SENTINELAS
# ===========================================================================
MOTIVO_SAC = {  # motivo normalizado (sem a observação) -> motivo conformado
    "ATRASO NA ENTREGA": "ATRASO_ENTREGA",
    "ATRASO ENTREGA": "ATRASO_ENTREGA",
    "PRODUTO AVARIADO": "PRODUTO_AVARIADO",
    "PRODUTO COM DEFEITO": "PRODUTO_DEFEITO",
    "CANCELAMENTO A PEDIDO DO CLIENTE": "CANCELAMENTO",
    "CANCELAMENTO CLIENTE": "CANCELAMENTO",
    "TROCA DE PRODUTO": "TROCA",
    "TROCA": "TROCA",
    "DEVOLUCAO": "DEVOLUCAO",
    "DUVIDA SOBRE PAGAMENTO": "DUVIDA_PAGAMENTO",
    "ESTORNO NAO REALIZADO": "ESTORNO_NAO_REALIZADO",
    "COBRANCA INDEVIDA": "COBRANCA_INDEVIDA",
    "ENDERECO INCORRETO": "ENDERECO_INCORRETO",
    "PRODUTO NAO RECEBIDO": "PRODUTO_NAO_RECEBIDO",
    "ELOGIO": "ELOGIO",
}


def trata_datas_e_ausencias(qa, pedidos, atendimentos, avaliacoes):
    """
    Antes de imputar qualquer coisa, classifique cada ausência:
      · não coletada          (o sistema nunca teve o dado)
      · não aplicável         (pedido cancelado não tem data de entrega)
      · sentinela             ('1900-01-01', 'N/A', -1)
    As três exigem tratamentos diferentes e NENHUMA delas é "preencher com a
    média". Documente a classificação de cada campo.

    Trate ainda:
      · DT_ENTREGA anterior a DT_PEDIDO;
      · TIMESTAMPTZ do e-commerce x DATETIME sem fuso do legado — padronize
        e diga em qual fuso;
      · data_fechamento vazia no SAC (atendimento em aberto x dado perdido).
    """
    return (_datas_pedidos(qa, pedidos), _ausencias_sac(qa, atendimentos),
            _ausencias_avaliacoes(qa, avaliacoes))


def _datas_pedidos(qa, ped):
    # RDT-08: sentinela e entrega antes do pedido viram NULL, com indicador.
    # NULL em pedido não entregue é "não aplicável" e fica como está.
    ped = ped.assign(entrega_sem_data=ped.dt_entrega == pd.Timestamp("1900-01-01"))
    ped["entrega_inconsistente"] = (ped.dt_entrega < ped.dt_pedido) & ~ped.entrega_sem_data
    ped["dt_entrega"] = ped.dt_entrega.mask(ped.entrega_sem_data | ped.entrega_inconsistente)
    ped["dias_entrega"] = (ped.dt_entrega.dt.normalize()
                           - ped.dt_pedido.dt.normalize()).dt.days.astype("Int64")
    qa.sem_nulos("data do pedido", ped.dt_pedido)
    qa.igual("pedido não entregue com data de entrega",
             int(((ped.status_conformado != "ENTREGUE") & ped.dt_entrega.notna()).sum()), 0)
    qa.igual("dias_entrega negativo", int((ped.dias_entrega < 0).sum()), 0)
    qa.igual("data de entrega sentinela restante",
             int((ped.dt_entrega.dt.year < 1901).sum()), 0)
    return ped


def _ausencias_sac(qa, sac):
    horas = sac.tempo_resolucao_horas.str.replace(",", ".").astype(float)
    nota = pd.to_numeric(nulo_se_vazio(sac.nota_satisfacao))
    a = sac.drop(columns="tempo_resolucao_horas").assign(
        cpf_cliente=nulo_se_vazio(sac.cpf_cliente.map(so_digitos)),
        data_abertura=pd.to_datetime(sac.data_abertura, format="%d/%m/%Y"),
        data_fechamento=pd.to_datetime(nulo_se_vazio(sac.data_fechamento), format="%d/%m/%Y"),
        horas_sinal_corrigido=horas < 0,
        horas_resolucao=horas.abs(),
        nota_sentinela=nota == 0,
        nota_satisfacao=nota.where(nota.between(1, 5)).astype("Int64"),
    )
    # fechamento vazio = dado não coletado (todos têm tempo de resolução),
    # não atendimento em aberto
    a["fechamento_sem_data"] = a.data_fechamento.isna()
    dias = (a.data_fechamento - a.data_abertura).dt.days
    tem = dias.notna()
    coerente = float((dias[tem] == (a.horas_resolucao[tem] // 24)).mean())
    qa.maior_igual("datas do SAC coerentes com |tempo_resolucao|", coerente, 0.999,
                   "o sinal negativo pode não ser só erro de digitação")
    return a


def _ausencias_avaliacoes(qa, av):
    fuso = av.submitted_at.str.extract(r"(Z|[+-]\d{2}:\d{2})$")[0]
    qa.igual("avaliações com fuso diferente de -03:00",
             int((fuso.notna() & (fuso != "-03:00")).sum()), 0,
             "a hipótese 'sem fuso = horário de Brasília' perde a base")
    # sem fuso: assume -03:00, o único fuso presente no arquivo
    envio = av.submitted_at.where(fuso.notna(), av.submitted_at + "-03:00")
    b = pd.DataFrame({
        "review_id": av.review_id,
        "order_ref": av.order_ref,
        "product_sku": av.product_sku,
        "data_envio": pd.to_datetime(envio, format="ISO8601", utc=True)
                        .dt.tz_convert(FUSO).dt.tz_localize(None),
        "envio_sem_fuso": fuso.isna(),
        # chave ausente vira NULL: não é nota 0, nem "não verificada", nem 0 votos
        "nota": pd.to_numeric(av.rating).astype("Int64"),
        "compra_verificada": av.verified_purchase.map({"true": True, "false": False})
                                                 .astype("boolean"),
        "votos_uteis": pd.to_numeric(av.helpful_votes).astype("Int64"),
    })
    qa.dominio("nota da avaliação em 1..5", b.nota.dropna(), {1, 2, 3, 4, 5})
    return b


# ===========================================================================
# DECISÃO 6 — CHAVES DAS FONTES DE ARQUIVO
# ===========================================================================
def liga_sac_e_avaliacoes(qa, pedidos, itens, produtos, xref, atendimentos, avaliacoes):
    """
    SAC:        chave composta (origem_sistema, id_pedido_origem) + cpf_cliente
    Avaliações: order.ref no formato 'LEG-<id>' ou 'ECOM-<id>'

    Quantos atendimentos e avaliações conseguem ser ligados a um pedido do DW?
    O que fazer com os que não conseguem — descartar, ou manter com pedido_sk
    nulo? A resposta depende da pergunta analítica; a sua é a recompra.
    """
    # Nada é descartado. Cada ligação usa a chave que a fonte declara, e as
    # contradições ficam marcadas para a etapa 3/ABT decidirem (RDT-13).
    ped = pedidos[["id_origem", "sistema", "id_pedido", "cliente_id", "dt_pedido"]]

    # --- SAC: pedido pela chave composta; cliente pelo CPF -------------------
    X = xref.dropna(subset=["cpf"]).drop_duplicates(["cpf", "cliente_id"])
    cpf_cliente = X[~X.cpf.duplicated(keep=False)].set_index("cpf").cliente_id
    a = atendimentos.assign(
        pedido_id_origem=atendimentos.origem_sistema.map(PREFIXO) + "-"
        + atendimentos.id_pedido_origem,
        cliente_id=atendimentos.cpf_cliente.map(cpf_cliente).astype("Int64"),  # CPF ambíguo não liga
    )
    antes = len(a)
    a = a.merge(ped[["id_origem", "cliente_id", "dt_pedido"]]
                .rename(columns={"id_origem": "pedido_id_origem",
                                 "cliente_id": "cliente_do_pedido"}),
                on="pedido_id_origem", how="left")
    qa.cardinalidade_preservada("atendimento x pedido", antes, len(a))
    a["pedido_existe"] = a.dt_pedido.notna()
    a["cliente_do_pedido_coincide"] = (a.cliente_do_pedido == a.cliente_id).fillna(False)
    a["abertura_antes_do_pedido"] = a.data_abertura < a.dt_pedido.dt.normalize()

    # --- avaliações: pedido por order.ref; cliente vem do pedido -------------
    antes = len(avaliacoes)
    b = avaliacoes.merge(ped.rename(columns={"id_origem": "order_ref",
                                             "sistema": "sistema_pedido"}),
                         on="order_ref", how="left")
    qa.cardinalidade_preservada("avaliação x pedido", antes, len(b))
    b["pedido_existe"] = b.dt_pedido.notna()
    b["envio_antes_do_pedido"] = b.data_envio < b.dt_pedido

    # produto: 'SKU-n' é o sku do e-commerce; 'LEG-n' é o ID_PRODUTO do legado
    leg = b.product_sku.str.startswith("LEG-")
    sku_id = produtos[produtos.sistema == "ECOM"].set_index("sku").id_origem
    b["produto_sistema"] = leg.map({True: "LEGADO", False: "ECOM"})
    b["produto_id_origem"] = (pd.to_numeric(b.product_sku.str.split("-").str[1].where(leg))
                              .fillna(b.product_sku.map(sku_id)).astype("Int64"))
    b = b.merge(produtos[["sistema", "id_origem"]].assign(_ok=True)
                .rename(columns={"sistema": "produto_sistema", "id_origem": "produto_id_origem"}),
                on=["produto_sistema", "produto_id_origem"], how="left")
    b["produto_existe"] = b.pop("_ok").notna()
    itp = (itens[["sistema", "id_pedido", "id_produto"]].drop_duplicates()
           .rename(columns={"sistema": "sistema_pedido", "id_produto": "produto_id_origem"})
           .assign(_ok=True))
    b = b.merge(itp, on=["sistema_pedido", "id_pedido", "produto_id_origem"], how="left")
    b["produto_no_pedido"] = b.pop("_ok").notna() & (b.produto_sistema == b.sistema_pedido)
    qa.cardinalidade_preservada("avaliação x produto", antes, len(b))
    return a.drop(columns="dt_pedido"), b.drop(columns=["dt_pedido", "id_pedido"])


# ===========================================================================
# VERIFICAÇÃO, RESUMO E GRAVAÇÃO
# ===========================================================================
def verifica_obrigatorias(qa, clientes, pedidos, itens, produtos):
    """Asserções obrigatórias e de consistência das tabelas finais (as de
    cardinalidade estão em cada junção)."""
    # RDT-06c: depois de remontada a tabela de produtos, a marcação continua
    # alinhada ao preço e à mediana gravados
    fora = produtos.preco_suspeito != (produtos.preco_referencia / 100
                                       >= 5 * produtos.mediana_categoria)
    qa.igual("preço suspeito coerente com 5x a mediana", int(fora.sum()), 0,
             "marcação de preço suspeito desalinhada do preço ou da mediana")
    qa.unico("cpf_normalizado único entre CPFs válidos",
             clientes.loc[clientes.cpf_valido.eq(True), "cpf_normalizado"])
    qa.dominio("origem_unificacao", clientes.origem_unificacao, {"LEGADO", "ECOM", "AMBOS"})
    soma = (itens[~itens.orfao].groupby(["sistema", "id_pedido"], as_index=False)
            .valor_bruto.sum())
    conf = pedidos.merge(soma, on=["sistema", "id_pedido"], how="left")
    qa.igual("valor_mercadoria = soma dos itens (±0,01)",
             int(((conf.valor_mercadoria - conf.valor_bruto.fillna(0)).abs() > 1).sum()), 0)


def imprime_resumo(cliente, cliente_xref, status, pedido, item, produto,
                   atendimento, avaliacao):
    """Contagens que alimentam o RDT, tiradas das tabelas finais."""
    def por_sistema(df, coluna):
        return df.groupby("sistema")[coluna].sum().astype(int).to_dict()

    resumo("clientes", **cliente.origem_unificacao.value_counts().to_dict(),
           CPF_invalido_em_par_por_CPF=int((cliente.regra_pareamento.eq("CPF")
                                            & cliente.cpf_valido.eq(False)).sum()),
           CPF_removido_por_conflito=int(cliente.cpf_conflito.sum()))
    print(status.sort_values(["status_conformado", "sistema"]).to_string(index=False))

    suspeitos = item.merge(produto.loc[produto.preco_suspeito, ["sistema", "id_origem"]]
                           .rename(columns={"id_origem": "id_produto"}))
    resumo("valores", fretes_negativos=por_sistema(pedido, "frete_sinal_corrigido"),
           descontos_menos_1=int(item.desconto_sentinela.sum()),
           itens_orfaos=por_sistema(item, "orfao"))
    resumo("preço suspeito", produtos=produto[produto.preco_suspeito].groupby("sistema").size().to_dict(),
           itens=len(suspeitos),
           fatia_do_valor_bruto=f"{suspeitos.valor_bruto.sum() / item.valor_bruto.sum():.2%}")

    leg = produto[produto.sistema == "LEGADO"].fillna({n: "—" for n in NIVEIS})
    print(leg.groupby(NIVEIS).agg(produtos=("id_origem", "size"),
                                  valores=("categoria_origem", lambda s: sorted(set(s))))
          .to_string())

    resumo("entregas", sentinela=por_sistema(pedido, "entrega_sem_data"),
           antes_do_pedido=por_sistema(pedido, "entrega_inconsistente"))
    resumo("SAC", atendimentos=len(atendimento),
           fechamento_sem_data=int(atendimento.fechamento_sem_data.sum()),
           horas_negativas=int(atendimento.horas_sinal_corrigido.sum()),
           nota_vazia=int((atendimento.nota_satisfacao.isna() & ~atendimento.nota_sentinela).sum()),
           nota_0=int(atendimento.nota_sentinela.sum()),
           pedido_encontrado=int(atendimento.pedido_existe.sum()),
           cliente_pelo_CPF=int(atendimento.cliente_id.notna().sum()),
           cliente_do_pedido_igual_ao_do_CPF=int(atendimento.cliente_do_pedido_coincide.sum()),
           aberto_antes_do_pedido=int(atendimento.abertura_antes_do_pedido.sum()))
    resumo("avaliações", sem_fuso=int(avaliacao.envio_sem_fuso.sum()),
           sem_nota=int(avaliacao.nota.isna().sum()),
           sem_compra_verificada=int(avaliacao.compra_verificada.isna().sum()),
           sem_votos=int(avaliacao.votos_uteis.isna().sum()),
           votos_0_explicitos=int((avaliacao.votos_uteis == 0).sum()),
           pedido_encontrado=int(avaliacao.pedido_existe.sum()),
           produto_encontrado=int(avaliacao.produto_existe.sum()),
           enviadas_antes_do_pedido=int(avaliacao.envio_antes_do_pedido.sum()),
           produto_no_pedido=int(avaliacao.produto_no_pedido.sum()))


def grava_cnf(tabelas):
    # recria o schema: o cnf reflete só esta execução, sem tabela antiga sobrando
    with DW.begin() as cx:
        cx.execute(text("DROP SCHEMA IF EXISTS cnf CASCADE"))
        cx.execute(text("CREATE SCHEMA cnf"))
    for nome, df in tabelas.items():
        moeda = MONETARIAS & set(df.columns)
        tipos = {c: Numeric(12, 2) for c in moeda}
        # em fatias: o to_sql converte o DataFrame inteiro em objetos Python
        # antes de gravar, o que dobraria a memória na tabela de itens
        for inicio in range(0, len(df), LOTE):
            fatia = df.iloc[inicio:inicio + LOTE]
            # centavos / 100 em float tem repr exata de 2 casas: '605.68' -> NUMERIC
            fatia = fatia.assign(**{c: (fatia[c] / 100).astype(float) for c in moeda})
            fatia.to_sql(nome, DW, schema="cnf", index=False, method=copy_pg,
                         if_exists="append", dtype=tipos)
        print(f"  cnf.{nome}: {len(df)} linhas")


if __name__ == "__main__":
    qa = Suite(etapa="02_transformacao")
    clientes, xref = unifica_clientes(qa)
    pedidos, itens, produtos = le_pedidos(qa, xref), le_itens(), le_produtos()
    atendimentos, avaliacoes = le_atendimentos(qa), le_avaliacoes()

    status, pedidos = conforma_status(qa, pedidos)
    pedidos, itens, produtos = conforma_valores(qa, pedidos, itens, produtos)
    produtos = conforma_categorias(qa, produtos)
    pedidos, atendimentos, avaliacoes = trata_datas_e_ausencias(
        qa, pedidos, atendimentos, avaliacoes)
    atendimentos, avaliacoes = liga_sac_e_avaliacoes(
        qa, pedidos, itens, produtos, xref, atendimentos, avaliacoes)
    verifica_obrigatorias(qa, clientes, pedidos, itens, produtos)

    tabelas = dict(cliente=clientes, cliente_xref=xref, status=status,
                   pedido=pedidos, item=itens, produto=produtos,
                   atendimento=atendimentos, avaliacao=avaliacoes)
    imprime_resumo(**tabelas)
    grava_cnf(tabelas)
    qa.registrar(DW)
    if qa.imprimir():
        print("Há asserções em falha. Isso não impede seguir, mas precisa "
              "aparecer no RDT com explicação.")
