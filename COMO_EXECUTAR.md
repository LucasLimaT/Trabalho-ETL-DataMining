# Como executar o projeto

Passo a passo para rodar o pipeline do zero e reproduzir o experimento no
Orange. Os comandos valem para Windows (PowerShell), Linux e macOS, exceto onde
há indicação.

## 1. Pré-requisitos

* Docker com Docker Compose v2 (`docker compose version` precisa responder)
* 8 GB de RAM e cerca de 2 GB livres em disco
* Git
* Orange Data Mining 3.40, para o passo 6

## 2. Baixar o repositório

```bash
git clone https://github.com/LucasLimaT/Trabalho-ETL-DataMining.git
cd Trabalho-ETL-DataMining
```

## 3. Subir o ambiente

```bash
docker compose up -d --build
docker compose logs -f seed
```

O serviço `seed` gera os dados e carrega os bancos de origem. Espere a mensagem
`ambiente pronto.` (de 3 a 5 minutos) e saia dos logs com `Ctrl+C`. Os
contêineres continuam rodando.

Com menos de 8 GB de RAM, crie um arquivo `.env` na raiz com a linha
`ESCALA=0.2` antes de subir. A base fica menor, e os números deixam de bater
com os do RDT e do relatório.

## 4. Rodar o pipeline

Execute os comandos na ordem. Cada etapa grava no banco `mercatto_dw` e
imprime as próprias asserções no final.

```bash
docker compose exec etl python 01_extracao.py
docker compose exec etl python 02_transformacao.py
docker compose exec etl python 03_carga_dw.py
docker compose exec etl python 04_abt.py --correta
docker compose exec etl python 04_abt.py --vazamento
docker compose exec etl python 05_exportar_orange.py
```

| Comando | Resultado |
|---|---|
| `01_extracao.py` | cópia das quatro fontes no schema `stg` |
| `02_transformacao.py` | dados tratados no schema `cnf` |
| `03_carga_dw.py` | esquema estrela no schema `dw` e conferência financeira |
| `04_abt.py --correta` | tabela `abt.recompra_90d` |
| `04_abt.py --vazamento` | tabela `abt.recompra_90d_vazamento` |
| `05_exportar_orange.py` | `saida/abt_recompra_correta.csv` e `saida/abt_recompra_vazamento.csv` |

Para ver o perfil das fontes antes de transformar qualquer coisa, rode
`docker compose exec etl python 00_perfilamento.py`. O relatório sai em
`saida/`.

## 5. Conferir as asserções

```bash
docker compose exec pg_dw psql -U postgres -d mercatto_dw -c "SELECT etapa, count(*) AS verificacoes, sum((NOT aprovado)::int) AS falhas FROM qa.execucao_assercao GROUP BY etapa ORDER BY etapa"
```

Toda etapa precisa mostrar `falhas = 0`. Rodar uma etapa de novo acrescenta
um lote ao histórico, então as contagens crescem a cada execução. Para
inspecionar as tabelas pelo navegador, abra o Adminer em
<http://localhost:8080> (servidor `pg_dw`, usuário e senha `postgres`).

## 6. Rodar o experimento no Orange

O Orange não roda no contêiner. O script `orange/experimento.py` precisa de um
Python com o Orange 3.40 instalado, e há duas formas de conseguir um.

**Opção A, com o Orange já instalado.** O Orange traz um Python próprio. Para
descobrir onde ele está, abra o Orange, coloque um widget **Python Script** no
canvas e execute:

```python
import sys; print(sys.executable)
```

Use o caminho impresso no lugar de `python` no comando abaixo. No macOS, ele
costuma ser `/Applications/Orange.app/Contents/MacOS/python`.

**Opção B, com um ambiente virtual.** Precisa de Python 3.12:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux e macOS: source .venv/bin/activate
pip install -r requirements.txt orange3==3.40.0 PyQt6==6.8.1 PyQt6-WebEngine==6.8.0
```

O `requirements.txt` fixa as versões do scikit-learn e do scipy. O Orange e o
PyQt6 ficam só neste comando porque não rodam no contêiner. Com outras versões
desses pacotes, as métricas podem sair diferentes das do relatório.

Depois, na raiz do repositório:

```bash
python orange/experimento.py
```

O script grava `orange/fluxo_correta.ows` e `orange/fluxo_vazamento.ows` e
imprime AUC, acurácia balanceada e matrizes de confusão das duas versões.

Para abrir os fluxos, use **File → Open** no Orange e escolha um dos `.ows`. Na
opção B, `orange-canvas orange/fluxo_correta.ows` abre direto. O widget File
encontra o CSV em `saida/` pelo caminho relativo, e o Test & Score mostra as
mesmas AUCs impressas pelo script.

## 7. Recomeçar do zero

```bash
docker compose down -v
```

Depois apague as pastas geradas:

```bash
# Linux e macOS
rm -rf dados saida
# Windows (PowerShell)
Remove-Item -Recurse -Force dados, saida
```

Volte ao passo 3. A semente do gerador é fixa, então os dados e os resultados
saem idênticos.

## Problemas comuns

* **`seed` terminou com erro:** veja `docker compose logs seed`. Para repetir só
  a carga, rode `docker compose run --rm seed python carregar.py --forcar`.
* **Contêiner sem memória durante a geração:** use `ESCALA=0.2` no `.env`,
  rode `docker compose down -v` e suba de novo.
* **Orange não encontra o CSV:** rode o passo 4 até o `05_exportar_orange.py`.
  Os fluxos leem os arquivos de `saida/`, e não os de `dados/`.
* **`experimento.py` acusa módulo ausente:** o comando rodou com um Python sem o
  Orange. Volte ao passo 6 e use o Python da opção A ou o `.venv` da opção B.
