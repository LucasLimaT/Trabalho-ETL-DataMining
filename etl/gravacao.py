# -*- coding: utf-8 -*-
"""Escrita no PostgreSQL compartilhada pelas etapas do pipeline."""

import csv
import io


def copy_pg(tabela, cx, colunas, linhas):
    """Método de escrita do to_sql via COPY: ~10x mais rápido que INSERT multi."""
    buf = io.StringIO()
    # QUOTE_NOTNULL: None sai sem aspas (NULL no COPY) e '' sai como "" (texto
    # vazio). Sem isso, o COPY transformaria todo texto vazio em NULL.
    csv.writer(buf, quoting=csv.QUOTE_NOTNULL).writerows(linhas)
    buf.seek(0)
    cols = ", ".join(f'"{c}"' for c in colunas)
    nome = f"{tabela.schema}.{tabela.name}" if tabela.schema else tabela.name
    with cx.connection.cursor() as cur:
        cur.copy_expert(f"COPY {nome} ({cols}) FROM STDIN WITH CSV", buf)
