#!/usr/bin/env python3
"""Carrega as dimensões e os fatos a partir dos dados da cnf.

    docker compose exec etl python 03_carga_dw.py

A carga é desfeita se alguma verificação falhar. As origens e a cnf são mantidas.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

from config import eng_dw, eng_mysql, eng_origem
from qualidade.assercoes import Suite

VINCULO_SAC = (
    "a.pedido_existe AND a.cliente_do_pedido_coincide "
    "AND NOT a.abertura_antes_do_pedido"
)
VINCULO_AVALIACAO = (
    "a.pedido_existe AND a.produto_no_pedido AND NOT a.envio_antes_do_pedido"
)


def confere(cx, qa, nome, sql, esperado=0):
    qa.igual(nome, cx.execute(text(sql)).scalar_one(), esperado)


def exige_aprovacao(qa):
    if qa.falhou():
        raise ValueError(
            "Asserção reprovada: carga desfeita; consulte qa.execucao_assercao."
        )


def totais_origens():
    """Consulta os totais das origens com os ajustes das fichas 05 e 06."""
    totais = {}
    consultas = [
        (
            "LEGADO",
            eng_mysql(),
            """
            SELECT SUM(VALOR_TOTAL) bruto,
                   SUM(ABS(VALOR_FRETE) - VALOR_FRETE) ajuste
            FROM PEDIDOS
            """,
        ),
        (
            "ECOM",
            eng_origem(),
            """
            SELECT SUM(total_amount) bruto, SUM(ABS(freight_amount)) ajuste
            FROM shop.orders
            """,
        ),
    ]
    for sistema, engine, sql in consultas:
        try:
            with engine.connect() as cx:
                resultado = cx.execute(text(sql)).mappings().one()
                totais[sistema] = (
                    Decimal(resultado["bruto"]),
                    Decimal(resultado["ajuste"]),
                )
        finally:
            engine.dispose()
    return totais


def carrega_dim_tempo(cx):
    meses = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]
    linhas = []
    dia = date(2022, 12, 1)
    while dia <= date(2026, 3, 31):
        linhas.append(
            {
                "data": dia,
                "ano": dia.year,
                "trimestre": (dia.month - 1) // 3 + 1,
                "mes": dia.month,
                "dia": dia.day,
                "semana": dia.isoweekday(),
                "nome": meses[dia.month - 1],
                "fim": dia.isoweekday() >= 6,
            }
        )
        dia += timedelta(days=1)
    cx.execute(
        text(
            """
            INSERT INTO dw.dim_tempo
            VALUES (:data, :ano, :trimestre, :mes, :dia, :semana, :nome, :fim)
            """
        ),
        linhas,
    )


def carrega_dimensoes(cx, qa):
    # Usa o identificador da cnf para manter a chave do cliente entre as cargas.
    cx.execute(
        text(
            """
            INSERT INTO dw.dim_cliente (
                cliente_sk, cpf_normalizado, cpf_valido, nome_padronizado,
                cidade, uf, dt_primeiro_cadastro, id_legado, id_ecommerce,
                origem_unificacao, regra_pareamento
            )
            SELECT cliente_id, cpf_normalizado, cpf_valido, nome_padronizado,
                   cidade, uf, dt_primeiro_cadastro, id_legado, id_ecommerce,
                   origem_unificacao, regra_pareamento
            FROM cnf.cliente
            ORDER BY cliente_id;

            INSERT INTO dw.dim_produto (
                sku, descricao, categoria_nivel1, categoria_nivel2,
                categoria_origem, preco_referencia, ativo, id_legado,
                id_ecommerce, origem
            )
            SELECT sku, descricao, categoria_nivel1, categoria_nivel2,
                   categoria_origem, preco_referencia, ativo,
                   CASE WHEN sistema = 'LEGADO' THEN id_origem END,
                   CASE WHEN sistema = 'ECOM' THEN id_origem END,
                   sistema
            FROM cnf.produto
            ORDER BY sistema, id_origem;

            INSERT INTO dw.dim_status (
                status_origem, sistema, status_conformado, motivo_cancelamento
            )
            SELECT status_origem, sistema, status_conformado, motivo_cancelamento
            FROM cnf.status
            ORDER BY sistema, status_origem;

            INSERT INTO dw.dim_canal (canal_origem, canal_conformado, sistema)
            SELECT DISTINCT canal_origem,
                CASE canal_origem
                    WHEN 'LOJA' THEN 'FISICO'
                    WHEN 'SITE' THEN 'ONLINE_PROPRIO'
                    WHEN 'web' THEN 'ONLINE_PROPRIO'
                    WHEN 'app' THEN 'ONLINE_PROPRIO'
                    WHEN 'marketplace' THEN 'MARKETPLACE'
                    WHEN 'TELEVENDAS' THEN 'TELEVENDAS'
                END,
                sistema
            FROM cnf.pedido
            ORDER BY sistema, canal_origem;

            SELECT setval(
                pg_get_serial_sequence('dw.dim_cliente', 'cliente_sk'),
                COALESCE(MAX(cliente_sk), 1), MAX(cliente_sk) IS NOT NULL
            )
            FROM dw.dim_cliente;
            """
        )
    )
    for destino, origem in [
        ("dim_cliente", "cliente"),
        ("dim_produto", "produto"),
        ("dim_status", "status"),
    ]:
        confere(
            cx,
            qa,
            f"contagem {destino}",
            f"""
            SELECT (SELECT COUNT(*) FROM dw.{destino})
                 - (SELECT COUNT(*) FROM cnf.{origem})
            """,
        )


def carrega_fato_pedido(cx, qa):
    confere(
        cx,
        qa,
        "pedidos sem cliente correspondente",
        """
        SELECT COUNT(*)
        FROM cnf.pedido p
        LEFT JOIN dw.dim_cliente c ON c.cliente_sk = p.cliente_id
        WHERE c.cliente_sk IS NULL
        """,
    )
    exige_aprovacao(qa)
    cx.execute(
        text(
            """
            INSERT INTO dw.fato_pedido (
                id_origem, sistema_origem, cliente_sk, data_pedido_sk,
                data_entrega_sk, canal_sk, status_sk, qtd_itens,
                valor_mercadoria, valor_frete, valor_desconto, valor_total,
                dias_entrega
            )
            SELECT p.id_origem, p.sistema, p.cliente_id, p.dt_pedido::date,
                   p.dt_entrega::date, c.canal_sk, s.status_sk, p.qtd_itens,
                   p.valor_mercadoria, p.valor_frete, p.valor_desconto,
                   p.valor_total, p.dias_entrega
            FROM cnf.pedido p
            JOIN dw.dim_canal c
              ON (c.sistema, c.canal_origem) = (p.sistema, p.canal_origem)
            JOIN dw.dim_status s
              ON (s.sistema, s.status_origem) = (p.sistema, p.status_origem)
            ORDER BY p.sistema, p.id_pedido
            """
        )
    )
    confere(
        cx,
        qa,
        "contagem de pedidos preservada",
        """
        SELECT (SELECT COUNT(*) FROM dw.fato_pedido)
             - (SELECT COUNT(*) FROM cnf.pedido)
        """,
    )


def carrega_fato_item(cx, qa):
    # Os itens sem pedido ficam na cnf para consulta, conforme a ficha 10.
    cx.execute(
        text(
            """
            INSERT INTO dw.fato_item_pedido (
                pedido_sk, produto_sk, quantidade, valor_unitario,
                valor_desconto, valor_linha
            )
            SELECT p.pedido_sk, d.produto_sk, i.quantidade, i.valor_unitario,
                   i.valor_desconto, i.valor_linha
            FROM cnf.item i
            JOIN dw.fato_pedido p
              ON p.sistema_origem = i.sistema
             AND p.id_origem = (
                 CASE i.sistema WHEN 'LEGADO' THEN 'LEG-' ELSE 'ECOM-' END
             ) || i.id_pedido
            JOIN dw.dim_produto d
              ON d.origem = i.sistema
             AND i.id_produto = CASE d.origem
                 WHEN 'LEGADO' THEN d.id_legado ELSE d.id_ecommerce
             END
            WHERE NOT i.orfao
            ORDER BY i.sistema, i.id_item
            """
        )
    )
    confere(
        cx,
        qa,
        "itens validos carregados sem perda",
        """
        SELECT (SELECT COUNT(*) FROM dw.fato_item_pedido)
             - (SELECT COUNT(*) FROM cnf.item WHERE NOT orfao)
        """,
    )
    confere(
        cx,
        qa,
        "itens carregados mais orfaos igual a cnf",
        """
        SELECT (SELECT COUNT(*) FROM dw.fato_item_pedido)
             + (SELECT COUNT(*) FROM cnf.item WHERE orfao)
             - (SELECT COUNT(*) FROM cnf.item)
        """,
    )


def carrega_fatos_auxiliares(cx, qa):
    cx.execute(
        text(
            f"""
            INSERT INTO dw.fato_atendimento (
                protocolo, cliente_sk, pedido_sk, data_abertura_sk,
                data_fechamento_sk, canal, motivo_conformado,
                nota_satisfacao, horas_resolucao
            )
            SELECT a.protocolo, c.cliente_sk,
                   CASE WHEN {VINCULO_SAC} THEN p.pedido_sk END,
                   a.data_abertura::date, a.data_fechamento::date, a.canal,
                   a.motivo_conformado, a.nota_satisfacao, a.horas_resolucao
            FROM cnf.atendimento a
            LEFT JOIN dw.dim_cliente c ON c.cliente_sk = a.cliente_id
            LEFT JOIN dw.fato_pedido p ON p.id_origem = a.pedido_id_origem
            ORDER BY a.protocolo;

            INSERT INTO dw.fato_avaliacao (
                review_id, pedido_sk, produto_sk, data_sk, nota,
                compra_verificada, votos_uteis
            )
            SELECT a.review_id,
                   CASE WHEN {VINCULO_AVALIACAO} THEN p.pedido_sk END,
                   d.produto_sk, a.data_envio::date, a.nota,
                   a.compra_verificada, a.votos_uteis
            FROM cnf.avaliacao a
            LEFT JOIN dw.fato_pedido p ON p.id_origem = a.order_ref
            LEFT JOIN dw.dim_produto d
              ON d.origem = a.produto_sistema
             AND a.produto_id_origem = CASE d.origem
                 WHEN 'LEGADO' THEN d.id_legado ELSE d.id_ecommerce
             END
            ORDER BY a.review_id
            """
        )
    )
    for fato, origem in [
        ("fato_atendimento", "atendimento"),
        ("fato_avaliacao", "avaliacao"),
    ]:
        confere(
            cx,
            qa,
            f"contagem {fato} preservada",
            f"""
            SELECT (SELECT COUNT(*) FROM dw.{fato})
                 - (SELECT COUNT(*) FROM cnf.{origem})
            """,
        )
    confere(
        cx,
        qa,
        "SAC so com vinculo confiavel",
        f"""
        SELECT COUNT(*)
        FROM dw.fato_atendimento f
        JOIN cnf.atendimento a USING(protocolo)
        WHERE (f.pedido_sk IS NOT NULL)
              IS DISTINCT FROM COALESCE({VINCULO_SAC}, false)
        """,
    )
    confere(
        cx,
        qa,
        "avaliacoes so com vinculo confiavel",
        f"""
        SELECT COUNT(*)
        FROM dw.fato_avaliacao f
        JOIN cnf.avaliacao a USING(review_id)
        WHERE (f.pedido_sk IS NOT NULL)
              IS DISTINCT FROM COALESCE({VINCULO_AVALIACAO}, false)
        """,
    )


def verifica_carga(cx, qa, totais):
    confere(
        cx,
        qa,
        "dias de entrega negativos",
        "SELECT COUNT(*) FROM dw.fato_pedido WHERE dias_entrega < 0",
    )
    confere(
        cx,
        qa,
        "medidas do pedido coerentes",
        """
        SELECT COUNT(*) FROM dw.fato_pedido
        WHERE valor_total <> valor_mercadoria + valor_frete - valor_desconto
        """,
    )
    confere(
        cx,
        qa,
        "itens reconciliados com pedidos",
        """
        SELECT COUNT(*) FROM (
            SELECT p.pedido_sk
            FROM dw.fato_pedido p
            LEFT JOIN dw.fato_item_pedido i USING(pedido_sk)
            GROUP BY p.pedido_sk
            HAVING COUNT(i.item_sk) <> p.qtd_itens
                OR SUM(i.quantidade * i.valor_unitario) <> p.valor_mercadoria
                OR SUM(i.valor_desconto) <> p.valor_desconto
                OR SUM(i.valor_linha) <> p.valor_mercadoria - p.valor_desconto
        ) divergencias
        """,
    )
    for campo, dimensao, chave in [
        ("cliente_sk", "dim_cliente", "cliente_sk"),
        ("canal_sk", "dim_canal", "canal_sk"),
        ("status_sk", "dim_status", "status_sk"),
        ("data_pedido_sk", "dim_tempo", "data_sk"),
    ]:
        confere(
            cx,
            qa,
            f"pedidos sem {campo}",
            f"""
            SELECT COUNT(*) FROM dw.fato_pedido p
            LEFT JOIN dw.{dimensao} d ON p.{campo} = d.{chave}
            WHERE d.{chave} IS NULL
            """,
        )
    cx.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS qa.reconciliacao_financeira (
                executado_em timestamptz NOT NULL DEFAULT now(),
                sistema text NOT NULL,
                total_bruto_origem numeric(18,2) NOT NULL,
                ajuste_semantico numeric(18,2) NOT NULL,
                total_equivalente_origem numeric(18,2) NOT NULL,
                total_dw numeric(18,2) NOT NULL,
                diferenca_pct numeric NOT NULL,
                aprovado boolean NOT NULL,
                PRIMARY KEY(executado_em, sistema)
            )
            """
        )
    )
    for sistema, (bruto, ajuste) in totais.items():
        total_dw = cx.execute(
            text(
                """
                SELECT SUM(valor_total) FROM dw.fato_pedido
                WHERE sistema_origem = :s
                """
            ),
            {"s": sistema},
        ).scalar_one()
        total_origem = bruto + ajuste
        if total_origem == 0:
            raise ValueError(
                "Origem sem total financeiro: "
                "não é possível calcular diferença relativa."
            )
        diferenca_pct = abs(total_dw - total_origem) / abs(total_origem) * 100
        qa.entre(
            f"reconciliacao {sistema} (%)", diferenca_pct, Decimal(0), Decimal("0.1")
        )
        cx.execute(
            text(
                """
                INSERT INTO qa.reconciliacao_financeira (
                    sistema, total_bruto_origem, ajuste_semantico,
                    total_equivalente_origem, total_dw, diferenca_pct, aprovado
                )
                VALUES (:s, :b, :a, :e, :o, :d, :ok)
                """
            ),
            {
                "s": sistema,
                "b": bruto,
                "a": ajuste,
                "e": total_origem,
                "o": total_dw,
                "d": diferenca_pct,
                "ok": diferenca_pct <= Decimal("0.1"),
            },
        )


def executa_carga(engine, totais):
    qa = Suite(etapa="03_carga_dw")
    try:
        with engine.begin() as cx:
            # O CASCADE também limpa a ABT, que será refeita na etapa 4.
            cx.execute(
                text(
                    """
                    TRUNCATE dw.fato_atendimento, dw.fato_avaliacao,
                        dw.fato_item_pedido, dw.fato_pedido, dw.dim_cliente,
                        dw.dim_produto, dw.dim_canal, dw.dim_status, dw.dim_tempo
                    RESTART IDENTITY CASCADE
                    """
                )
            )
            carrega_dim_tempo(cx)
            carrega_dimensoes(cx, qa)
            cx.execute(
                text(
                    """
                    ANALYZE dw.dim_cliente;
                    ANALYZE dw.dim_produto;
                    ANALYZE dw.dim_canal;
                    ANALYZE dw.dim_status;
                    ANALYZE dw.dim_tempo
                    """
                )
            )
            print("Dimensões carregadas.", flush=True)
            carrega_fato_pedido(cx, qa)
            cx.execute(text("ANALYZE dw.fato_pedido"))
            print("Pedidos carregados.", flush=True)
            carrega_fato_item(cx, qa)
            print("Itens carregados.", flush=True)
            carrega_fatos_auxiliares(cx, qa)
            print("Atendimentos e avaliações carregados.", flush=True)
            verifica_carga(cx, qa, totais)
            exige_aprovacao(qa)
    except Exception:
        qa.igual(
            "carga concluida sem erro",
            0,
            1,
            "Transação desfeita; corrigir a causa antes de repetir.",
        )
        raise
    finally:
        # Registra a falha mesmo quando os dados da carga foram desfeitos.
        qa.registrar(engine)
        qa.imprimir()


if __name__ == "__main__":
    executa_carga(eng_dw(), totais_origens())
