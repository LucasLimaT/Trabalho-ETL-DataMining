# Registro de Decisões de Transformação (RDT) · integral, fichas 01 a 15

**Equipe:** Diogo Augusto de Siqueira Cândido, Líbia Canhete Alves e Cruz, Lucas Taveira De Lima
**Data:** 31/10/2026  **Versão do pipeline:** Semana 4 (etapas 01 a 05, ABT correta e com vazamento)

Os números deste documento vêm da execução de `01_extracao.py` e
`02_transformacao.py` sobre a base gerada com semente 20262, escala 1.0. Cada
asserção citada fica gravada na tabela `qa.execucao_assercao`, nas etapas
`01_extracao` e `02_transformacao`.

**Onde ficam os dados.** A etapa 1 copia as origens, sem mudar nada, para o
schema `stg`. A etapa 2 grava os dados já tratados no schema `cnf`. Ali cada
registro ainda usa o identificador que tinha na origem, e um conjunto de
colunas de marcação (verdadeiro ou falso) indica quais registros alguma decisão
alterou. Na Semana 3, a etapa 3 usa esses identificadores para carregar
as chaves do DW e montar o esquema estrela. Criamos o `cnf` por dois motivos: a
etapa 3 não precisa refazer nenhuma decisão, e qualquer número deste documento
pode ser conferido com uma consulta.

---

## Ficha RDT-01 · Carga completa ou incremental

**Etapa do pipeline:** 01_extracao · todas as funções `extrai_*`

**O que o dado mostrava:** nenhuma das quatro fontes registra quando um dado
mudou. As tabelas do legado não têm data de alteração. No e-commerce,
`customer.created_at` e `orders.placed_at` guardam só a data de criação, e não
existe coluna de atualização. O SAC é uma exportação manual feita todo mês e
emendada na anterior, e o JSON é o retorno completo da API. Enquanto isso,
49.427 pedidos (12,1% dos 410.000) estão em trânsito ou em preparação. O status
e a data de entrega deles vão mudar sem que nada na linha avise. Carregar tudo
de novo, as 2.205.864 linhas, leva cerca de 14 segundos no ambiente do
laboratório.

**Alternativas consideradas:**

1. Carga incremental pelo número do pedido: buscar só pedidos com ID maior que
   o último já lido. Isso traz os pedidos novos, mas não percebe quando um
   pedido antigo muda de status, e é essa mudança que define se o cliente
   comprou de novo.
2. Carga incremental pela data do pedido. Tem o mesmo problema e ainda depende
   de datas que, no legado, não informam o fuso.
3. Carga completa a cada execução, apagando e recriando a `stg`.

**Decisão:** alternativa 3.

**Justificativa:** sem uma coluna que mostre o que mudou, qualquer carga
incremental perde atualizações sem avisar. A base é pequena o bastante para ser
recarregada em 14 segundos. A carga completa também garante a
reprodutibilidade exigida no projeto: apagar tudo, subir de novo e rodar os
mesmos comandos devolve exatamente a mesma `stg`.

**O que se perde:** o histórico. Cada execução apaga a anterior, então não dá
para saber em que dia um pedido passou de `shipped` para `delivered`, nem
reconstruir como a base estava numa data passada. Fica só a data e hora da
última carga, na coluna `_extraido_em`. O tempo de carga também cresce junto
com a base; num sistema real, com dezenas de milhões de linhas, teríamos de
rever essa escolha.

**Registros afetados:** 2.205.864 linhas recarregadas a cada execução (100%
das fontes), em 11 tabelas da `stg`.

**Asserção associada:** para cada tabela dos bancos, `linhas <tabela> lidas` e
`linhas stg.<tabela> gravadas` comparam o que foi copiado com a contagem feita
na própria origem (18 asserções). No CSV, `linhas SAC lidas` compara com uma
contagem de protocolos feita direto no arquivo, sem passar pelo leitor de CSV
do pandas. No JSON, `linhas avaliações lidas` compara com o número de linhas do
arquivo.

---

## Ficha RDT-02 · Identidade do cliente entre os dois sistemas

**Etapa do pipeline:** 02_transformacao · `unifica_clientes`

**O que o dado mostrava:** depois de juntar os cadastros repetidos do legado
(RDT-03), sobram 45.000 clientes no legado e 85.000 no e-commerce. O CPF é o
único dado que liga os dois sistemas, mas falta em 8,0% dos clientes do legado
e em 8,1% dos do e-commerce. Comparando só os números do CPF, 12.606 pares
coincidem. Em 12.604 deles o nome também é o mesmo. Nos outros 2, o nome é de
outra pessoa: o mesmo CPF aparece como `MÁRCIA SOARES MACHADO` no legado e como
`Wagner Correia Martins` no e-commerce, por exemplo. Dos 12.604 pares, 1.135
(9,0%) têm CPF com dígito verificador errado. Mesmo assim, o CPF errado aparece
igual nos dois sistemas, com o mesmo nome e a mesma UF. O e-commerce ainda tem 3
CPFs usados por pessoas de nomes diferentes.

Para quem não tem CPF, testamos ligar os cadastros por nome, cidade e UF,
aceitando só combinações que aparecem uma única vez em cada base. Testada nos
12.409 pares em que os dois lados têm CPF, essa regra erra em 421 (3,39%):
pessoas diferentes com o mesmo nome, na mesma cidade e UF. Usando só nome e UF,
sem a cidade, o erro sobe para 6,4%.

**Alternativas consideradas:**

1. Usar só CPFs com dígito verificador correto. Perde 1.135 pares verdadeiros,
   porque confunde CPF válido com CPF útil.
2. Usar qualquer CPF de 11 dígitos, válido ou não, desde que o nome confirme.
3. A alternativa 2 e, para quem não tem CPF em um dos lados, nome + cidade + UF.
4. A alternativa 2 e, para quem não tem CPF, nome + UF. Encontra mais pares, com
   quase o dobro de erros.

**Decisão:** alternativa 3. A coluna `regra_pareamento` guarda qual regra uniu
cada cliente: `CPF` ou `NOME_CIDADE_UF`.

**Justificativa:** um CPF errado que foi digitado igual nos dois sistemas
identifica a pessoa tão bem quanto um CPF correto. Exigir o mesmo nome barra os
2 casos de pessoas diferentes com o mesmo CPF.

Para decidir sobre a regra por nome, comparamos os dois erros possíveis pensando
na pergunta da recompra. Se a mesma pessoa fica dividida em dois clientes, o
modelo pode concluir que ela não voltou a comprar quando ela voltou pelo outro
sistema. No diagnóstico da Semana 1, isso reduziu em 28% a recompra observada
no legado. Se duas pessoas diferentes viram um cliente só, as compras das duas
se somam e o cliente parece comprar mais do que compra. Com a alternativa 3,
esperamos cerca de 75 fusões erradas contra cerca de 2.140 pessoas unidas
corretamente. Cada fusão por nome fica marcada, então na etapa 4 dá para
retirar esses clientes e medir o efeito.

**Regra complementar:** quando um mesmo CPF ainda aparece em mais de um cliente
(pessoas diferentes com o mesmo número), ele fica só com o cliente cujo CPF os
dois sistemas confirmaram. Os outros ficam sem CPF e com `cpf_conflito = true`
na tabela `cnf.cliente`.

**O que se perde:**
* cerca de 75 clientes (3,39% de 2.214) são, na verdade, duas pessoas, e o
  modelo vê um único cliente com o histórico das duas;
* os 2 pares com o mesmo CPF e nomes diferentes ficam separados, mesmo que
  algum seja a mesma pessoa que mudou de nome, depois de um casamento, por
  exemplo;
* algumas pessoas sem CPF continuam duplicadas: as que têm nome, cidade e UF
  repetidos na base e as que estão com cidade `N/A`. Estimamos que 14.902
  pessoas compram nos dois sistemas (os 12.604 pares por CPF divididos pela
  chance de as duas pontas terem CPF, 0,920 × 0,919). Unimos 14.818, das
  quais cerca de 75 estão erradas, então cerca de 160 pessoas (1,1%) continuam
  separadas;
* os 7 clientes que perderam o CPF por conflito não podem ser ligados a
  atendimentos do SAC pelo CPF.

**Registros afetados:** 14.818 clientes presentes nos dois sistemas (12,9% dos
115.182 clientes finais), formados por 29.636 cadastros de origem (22,4% dos
132.250). Desses, 12.604 foram unidos pelo CPF e 2.214 por nome, cidade e UF.
Os clientes unidos por nome têm 13.674 pedidos (3,3% dos 410.000). Sete
clientes ficaram com `cpf_conflito`.

**Asserção associada:** `falso positivo estimado nome+cidade+UF` entre 0 e 5%
(obtido 3,39%); `taxa de pareamento por CPF (clientes do legado)` entre 25% e
35% (obtido 28,0%); `cpf_normalizado único entre CPFs válidos`;
`origem_unificacao` só com LEGADO, ECOM ou AMBOS; `todo registro de origem tem
cliente`; `um registro de origem, um cliente`.

---

## Ficha RDT-03 · Cadastros repetidos no legado

**Etapa do pipeline:** 02_transformacao · `unifica_clientes`, antes de cruzar
os dois sistemas

**O que o dado mostrava:** 2.250 e-mails aparecem em dois cadastros da tabela
`CLIENTES` (4,8% dos 47.250). Em todos os casos, o segundo cadastro tem número
de cliente acima de 45.000. Em 900 casos, os dois cadastros são idênticos,
tirando o número. Nos outros 1.350, o nome muda só por um espaço ou um acento, e
o CPF muda em 1.206: em 652 falta em um dos cadastros, e em 554 um tem pontos e
traço e o outro não. Nenhum par tem dois CPFs diferentes, e data de cadastro,
cidade, UF e telefone são sempre iguais. Os cadastros repetidos também têm
pedidos: 2.097 deles somam 6.241 pedidos (4,8% do legado).

**Alternativas consideradas:**

1. Não juntar os cadastros. As compras da pessoa ficam divididas entre dois
   clientes.
2. Juntar só as cópias idênticas (900). Os 1.350 quase iguais continuam
   separados.
3. Juntar cadastros com o mesmo e-mail e o mesmo nome (comparado em maiúsculas,
   sem acento e sem espaços extras), desde que o CPF seja igual ou falte em um
   dos dois. Fica o cadastro de menor número, completado com os campos que
   faltavam nele e existiam no outro.
4. Juntar por nome e CPF. Deixa de fora os 652 pares em que um dos cadastros
   não tem CPF.

**Decisão:** alternativa 3.

**Justificativa:** os e-mails seguem o formato `nome<n>@exemplo.com.br` e não
se repetem entre pessoas diferentes, e o nome confirma. A exigência de CPF igual
ou ausente evita juntar duas pessoas que usem o mesmo e-mail da família. Ao
completar o cadastro que fica com o CPF do outro, alguns clientes ganham um CPF
que não tinham, o que ajuda a ligação entre os sistemas (RDT-02).

**O que se perde:** a grafia original do nome nos 1.350 cadastros quase iguais
e o número do cadastro descartado, que não vai para a dimensão de clientes. Os
dois continuam guardados em `stg.legado_clientes`, e a coluna
`cnf.cliente_xref.duplicata_de` liga cada cadastro descartado ao que ficou. Se
algum par for de duas pessoas com o mesmo e-mail e o mesmo nome, as compras
delas se juntam. Não encontramos sinal disso, mas também não temos como provar
que não acontece.

**Registros afetados:** 2.250 cadastros juntados (4,76% de `CLIENTES`), 900
idênticos e 1.350 quase iguais. 6.241 pedidos (4,8% do legado) passam a
pertencer ao cadastro que ficou. Nenhum par foi recusado por CPF diferente.

**Asserção associada:** `todo registro de origem tem cliente` (nenhum dos
47.250 cadastros do legado fica sem cliente) e `um registro de origem, um
cliente`.

---

## Ficha RDT-04 · Status do pedido nos dois sistemas, incluindo `payment_declined`

**Etapa do pipeline:** 02_transformacao · `conforma_status`

**O que o dado mostrava:**

| Sistema | Status de origem | Pedidos | % do sistema |
|---|---|---:|---:|
| Legado | ENTREGUE | 101.192 | 77,8% |
| Legado | CANCELADO | 15.774 | 12,1% |
| Legado | EM TRANSITO | 7.798 | 6,0% |
| Legado | SEPARACAO | 5.236 | 4,0% |
| E-commerce | delivered | 207.204 | 74,0% |
| E-commerce | canceled | 22.413 | 8,0% |
| E-commerce | payment_declined | 13.990 | 5,0% |
| E-commerce | shipped | 22.375 | 8,0% |
| E-commerce | processing | 14.018 | 5,0% |

O legado cancela 12,1% dos pedidos. O e-commerce, somando cancelamento e
pagamento recusado, cancela 13,0%. Como as taxas são parecidas, o `CANCELADO`
do legado provavelmente também inclui pagamentos recusados, mas o legado não
deixa separar um caso do outro.

**Alternativas consideradas:**

1. Tratar `payment_declined` como `CANCELADO`, sem motivo. É simples e igual
   para os dois sistemas, mas apaga a diferença nos 13.990 pedidos em que ela
   existe.
2. Criar `CANCELADO_CLIENTE` e `CANCELADO_PAGAMENTO`. Os 15.774 cancelados do
   legado não se encaixam em nenhum dos dois e exigiriam um terceiro valor. Toda
   consulta teria de somar três status para contar cancelamentos.
3. Usar dois níveis: `CANCELADO` para os três casos no status, e uma coluna
   `motivo_cancelamento` preenchida só quando a origem informa o motivo.

**Decisão:** alternativa 3. O resultado ficou assim:

| Status conformado | Motivo | Status de origem | Pedidos |
|---|---|---|---:|
| ENTREGUE | | ENTREGUE e delivered | 308.396 |
| EM_TRANSITO | | EM TRANSITO e shipped | 30.173 |
| EM_PREPARACAO | | SEPARACAO e processing | 19.254 |
| CANCELADO | vazio | CANCELADO (legado) | 15.774 |
| CANCELADO | CLIENTE | canceled | 22.413 |
| CANCELADO | PAGAMENTO_RECUSADO | payment_declined | 13.990 |

**Justificativa:** para a recompra, qualquer cancelamento quer dizer que a
compra não aconteceu, seja qual for o motivo. Com um único status `CANCELADO`,
a etapa 4 usa o mesmo filtro para os dois sistemas. Quem quiser estudar
pagamentos recusados no e-commerce ainda tem o motivo, e o motivo vazio no
legado diz com clareza que esse sistema não sabe.

**O que se perde:**
* a comparação entre os sistemas: o motivo só existe no e-commerce, então
  qualquer análise por motivo usa 36.403 pedidos e deixa de fora 15.774;
* assumimos que `canceled` é cancelamento feito pelo cliente, como diz o
  enunciado; o dicionário de dados não confirma isso;
* `SEPARACAO` (separação no estoque) e `processing` (processamento, que pode
  incluir a aprovação do pagamento) não são a mesma etapa. Juntamos os dois em
  `EM_PREPARACAO` e essa diferença se perde.

**Registros afetados:** 52.177 pedidos cancelados (12,7% dos 410.000), dos
quais 15.774 (3,8%) sem motivo; 19.254 pedidos (4,7%) juntados em
`EM_PREPARACAO`.

**Asserção associada:** `status de origem mapeados`. Todo status encontrado nas
origens precisa constar na tabela de conversão. Se aparecer um status novo, a
asserção falha, em vez de o pedido ficar sem status sem que ninguém perceba.

---

## Ficha RDT-05 · O que `VALOR_TOTAL` e `total_amount` medem, e como descobrimos

**Etapa do pipeline:** 02_transformacao · `conforma_valores`

**O que o dado mostrava:** para cada pedido, somamos os itens (quantidade ×
preço − desconto) e comparamos com o total gravado, aceitando diferença de até
R$ 0,01.

| | Legado (130.000 pedidos) | E-commerce (280.000 pedidos) |
|---|---:|---:|
| Total = itens + frete | 130.000 | 48 |
| Total = só itens | 24 | 280.000 |
| Soma dos totais gravados | R$ 281.084.957,13 | R$ 734.859.099,95 |
| Soma dos itens (já com desconto) | R$ 275.324.024,56 | R$ 734.859.099,95 |
| Soma dos fretes | R$ 5.760.932,57 | R$ 10.991.760,38 |

Os 24 pedidos do legado e os 48 do e-commerce que batem na conta "errada" têm
frete de no máximo R$ 0,01, por isso as duas contas dão o mesmo valor. A conclusão
é que `VALOR_TOTAL` inclui o frete e `total_amount` não inclui. Fora do próprio
dado, a única pista era um bilhete de 2021 no dicionário: "cuidado com o
VALOR_TOTAL, não é o que parece".

**Alternativas consideradas:**

1. Usar o total de cada sistema como veio. O legado contaria R$ 5,76 milhões de
   frete como venda de mercadoria, e nada no processo acusaria o erro.
2. Tirar o frete do `VALOR_TOTAL` do legado para que ele fique igual ao
   `total_amount`. Corrige um total usando outro total, e qualquer erro no
   frete passa para o resultado.
3. Deixar os totais de origem de lado e recalcular quatro valores a partir dos
   itens, com a mesma fórmula nos dois sistemas: `valor_mercadoria` (quantidade
   × preço, sem desconto e sem frete), `valor_desconto` (soma dos descontos),
   `valor_frete` e `valor_total = mercadoria + frete − desconto`.

**Decisão:** alternativa 3. Os totais de origem continuam guardados em
`cnf.pedido.valor_total_origem`, só para conferência.

**Justificativa:** cada valor passa a ter uma definição escrita, a mesma nos
dois sistemas. Os itens são o dado mais detalhado e batem 100% com os totais
das duas origens. Recalcular a partir deles não altera nenhum valor que já
existia, só deixa claro como cada total é formado.

**O que se perde:** o `valor_total` do DW deixa de ser comparável diretamente
com o `total_amount` do e-commerce, porque inclui R$ 10,99 milhões de frete que
o e-commerce não incluía. Um relatório antigo da Commerce Br vai mostrar
números diferentes dos nossos. Também deixamos de tratar o total de origem como
o valor certo: se um dia os itens vierem incompletos e o total estiver certo, o
DW vai repetir o erro dos itens.

**Registros afetados:** os 410.000 pedidos foram recalculados (100%). Em
280.000 (68,3%), o `valor_total` do DW difere do `total_amount` pelo valor do
frete. No legado, só os 362 pedidos com frete negativo ficam diferentes do
`VALOR_TOTAL` (RDT-06). Totais no DW: R$ 281.117.154,85 no legado e
R$ 745.915.357,63 no e-commerce.

**Asserção associada:** `legado: VALOR_TOTAL = itens + frete` e
`ecom: total_amount = itens (sem frete)`, as duas esperando zero diferenças. Se
uma origem mudar o que o total significa, a asserção falha. Também
`valor_mercadoria = soma dos itens (±0,01)` e `pedidos sem nenhum item` = 0.

---

## Ficha RDT-06 · Frete negativo, desconto `-1` e preços 10× acima do normal

**Etapa do pipeline:** 02_transformacao · `conforma_valores`

São três problemas de valor, cada um com um tratamento.

### 6a · Frete negativo

**O que o dado mostrava:** 1.174 pedidos têm frete negativo: 362 no legado
(0,28%) e 812 no e-commerce (0,29%). O status desses pedidos se distribui como
no resto da base. No legado, 78,7% estão entregues (77,8% na base toda) e 10,2%
cancelados (12,1% na base). No e-commerce, 74,5% estão entregues (74,0% na
base). Sem o sinal de menos, a média desses fretes é R$ 41,18, perto da média
dos fretes positivos (R$ 44,56 no legado e R$ 39,49 no e-commerce), e nenhum
passa do maior frete positivo.

**Alternativas:** (1) manter o valor negativo, como se fosse estorno; (2) trocar
por zero; (3) deixar vazio; (4) tirar o sinal de menos e marcar o pedido.

**Decisão:** alternativa 4. O pedido fica com `frete_sinal_corrigido = true`.

**Justificativa:** se fossem estornos, apareceriam mais em pedidos cancelados ou
devolvidos. Esses fretes aparecem nos mesmos status e com os mesmos valores dos
fretes comuns, o que indica um sinal digitado errado. Trocar por zero criaria um
frete grátis que não existiu, e deixar vazio esconderia um valor que está ali,
só com o sinal trocado.

**O que se perde:** se alguns forem estornos de verdade, o DW conta como pago um
frete que voltou para o cliente. O `valor_total` do legado fica R$ 32.197,72
acima do `VALOR_TOTAL` de origem (0,011%), e o do e-commerce fica R$ 64.497,30
acima de itens + frete da origem (0,009%). As duas diferenças estão dentro do
limite de 0,1% que será conferido na Semana 3.

**Registros afetados:** 1.174 pedidos (0,29% dos 410.000).

### 6b · Desconto `-1` no legado

**O que o dado mostrava:** 4.056 itens (1,02% dos itens do legado), em 4.003
pedidos, têm desconto igual a `-1`. Nenhum outro desconto é negativo. Lendo
`-1` como zero, os 130.000 pedidos batem com o `VALOR_TOTAL`. Lendo como valor
real, que seria um acréscimo de R$ 1, os 4.003 pedidos deixam de bater.

**Alternativas:** (1) usar o `-1` como valor; (2) deixar vazio; (3) trocar por
zero e marcar o item com `desconto_sentinela`.

**Decisão:** alternativa 3.

**Justificativa:** a conferência dos totais mostra que o sistema que calculou o
pedido leu `-1` como "sem desconto". Deixar vazio impediria somar os descontos
do pedido, e a tabela de itens do DW não aceita desconto vazio.

**O que se perde:** a diferença entre um desconto zero digitado (5.908 itens) e
um campo que ninguém preencheu (os 4.056). Ela só continua visível pela
marcação.

**Registros afetados:** 4.056 itens (0,29% dos 1.392.453 itens), em 4.003
pedidos (3,1% do legado).

### 6c · Preço 10× acima da mediana da categoria

**O que o dado mostrava:** comparamos o preço de tabela de cada produto com a
mediana da sua categoria. Não existe um corte claro entre preços normais e
anormais. No legado, 70 produtos passam de 3× a mediana, 15 passam de 4×, 6
passam de 5× e 1 passa de 8×. No e-commerce, os números são 274, 58, 25 e 13. O
caso mais extremo é um produto de R$ 5.477,80 numa categoria com mediana de
R$ 356,88, cerca de 15×. Os preços cobrados nos pedidos acompanham o preço de
tabela, e os totais batem com esses itens. Se houve erro de digitação, o cliente
pagou o valor errado.

**Alternativas:** (1) não fazer nada; (2) dividir por 10 os preços acima de um
limite, no produto e nos itens; (3) marcar o produto como suspeito e não mudar
nenhum valor.

**Decisão:** alternativa 3. O critério é preço maior ou igual a 5 vezes a
mediana da categoria. A marcação, a mediana da categoria e a razão entre as
duas ficam nas colunas `preco_suspeito`, `mediana_categoria` e `razao_mediana`
da tabela `cnf.produto`.

**Justificativa:** um produto barato digitado 10 vezes mais caro pode ficar com
preço normal, e um produto caro de verdade pode passar de 5× sem erro nenhum.
Nenhum critério separa os dois casos com certeza. Por isso escolhemos um
tratamento que não muda valores: com a marcação, dá para tirar esses produtos
de médias de preço ou de ticket, e os pedidos continuam batendo com a origem.
Dividir por 10 mudaria pedidos que o cliente pagou, quebraria a conferência de
0,1% da Semana 3 e erraria com todo produto caro de verdade acima do limite.

**O que se perde:** os pedidos desses produtos continuam com o valor
possivelmente inflado. Os 31 produtos somam 1,46% do valor vendido em itens, o
que basta para distorcer o ticket médio de quem os comprou. Erros de 10× em
produtos baratos, que ficam abaixo de 5× a mediana, passam sem ser notados.

**Registros afetados:** 31 produtos (6 no legado, 0,20%; 25 no e-commerce,
0,21%), presentes em 2.907 itens (0,21% dos itens).

**Asserções associadas (6a, 6b e 6c):** `legado: VALOR_TOTAL = itens + frete`,
que só fecha com `-1` valendo zero e com o frete de origem, sinal incluído;
`valor_mercadoria = soma dos itens (±0,01)`; e `preço suspeito coerente com 5x
a mediana`, que confere na tabela final se todo produto marcado tem preço de
pelo menos 5 vezes a mediana gravada, e todo produto não marcado fica abaixo
disso. A conferência de 0,1% da etapa 3 vai
medir o efeito de tirar o sinal do frete.

---

## Ficha RDT-07 · Categorias de produto

**Etapa do pipeline:** 02_transformacao · `conforma_categorias`

**O que o dado mostrava:** o legado tem 27 formas diferentes de escrever a
categoria para seus 3.000 produtos. Uma contagem feita no próprio MySQL mostra
só 24, porque a configuração de comparação de texto do banco
(`latin1_swedish_ci`) ignora maiúsculas e acentos e trata `ELETRO` e `Eletro`,
ou `INFORMATICA` e `Informática`, como iguais. O e-commerce usa uma lista fixa
de categorias em dois níveis: 6 categorias principais com subcategorias e 2
sem subcategorias (`Esporte e Lazer` e `Ferramentas`). Verificamos se a
descrição do produto ajudaria a resolver os casos duvidosos e ela não ajuda: os
54 produtos cuja descrição começa com `TELEVISOR` estão espalhados por 9
categorias, e só 8 estão numa categoria de TV.

**Alternativas consideradas para os casos difíceis:**

* `informatica e games` (112 produtos), que mistura duas categorias principais:
  (a) colocar em Informática, a mais provável; (b) criar uma categoria nova,
  "Informática e Games", que não existe no e-commerce; (c) deixar sem
  categoria e guardar o texto original.
* `TV E SOM` e `AUDIO/VIDEO` (226 produtos), que misturam `Televisores` e
  `Áudio`: (a) escolher uma das duas subcategorias; (b) usar a categoria
  principal `Áudio e TV` e deixar a subcategoria vazia.
* `DIVERSOS`, `OUTROS` e categoria em branco (340 produtos): (a) criar uma
  categoria "Não classificado"; (b) deixar os dois níveis vazios.
* Descobrir a categoria pela descrição: descartada pelo teste descrito acima.

**Decisão:** cada texto do legado vai até o nível que ele permite afirmar com
certeza. Se o texto indica a categoria principal mas não a subcategoria, a
subcategoria fica vazia. Se nem a principal é certa, as duas ficam vazias. Nas
duas categorias sem subcategoria, repetimos o nome no segundo nível, nos dois
sistemas. Assim, subcategoria vazia sempre quer dizer "não sabemos".

| Categoria | Subcategoria | Textos do legado | Produtos |
|---|---|---|---:|
| Eletrodomésticos | vazia | ELETRO, Eletro, eletrodomesticos, ELETRODOMÉSTICOS, Eletro-domesticos | 533 |
| Informática | vazia | INFORMATICA, Informática, INFO | 300 |
| Telefonia | Smartphones | Celulares, CELULAR/SMARTPHONE | 216 |
| Telefonia | vazia | TELEFONIA | 113 |
| Casa e Decoração | Móveis | MOVEIS | 131 |
| Casa e Decoração | vazia | CASA, Casa e decoracao | 264 |
| Áudio e TV | Televisores | Televisores | 118 |
| Áudio e TV | vazia | TV E SOM, AUDIO/VIDEO | 226 |
| Esporte e Lazer | Esporte e Lazer | ESPORTE, Esportes e lazer | 221 |
| Ferramentas | Ferramentas | FERRAMENTAS, Ferramentas e construcao | 230 |
| Games | vazia | GAMES, Games e consoles | 196 |
| vazia | vazia | informatica e games, DIVERSOS, OUTROS, em branco | 452 |

**Justificativa:** escolher Informática para `informatica e games`, ou
Televisores para `TV E SOM`, seria inventar uma classificação que o dado não
dá. Criar uma categoria nova bagunçaria a lista que o e-commerce controla. E
"Não classificado" seria um valor de mentira no lugar de um dado que falta, o
mesmo problema que tratamos na RDT-08. O texto original fica na coluna
`categoria_origem`, e qualquer análise pode voltar a ele.

**O que se perde:**
* 452 produtos do legado (15,1%) ficam sem categoria. Eles aparecem em 59.733
  itens vendidos, 15,0% dos itens do legado, que ficam fora de qualquer análise
  por categoria;
* 1.632 produtos do legado (54,4%) têm categoria mas não têm subcategoria. Uma
  análise por subcategoria cobre todo o e-commerce e só 916 produtos do legado
  (30,5%); 69,5% dos itens do legado ficam sem subcategoria;
* `Celulares` foi para `Smartphones`. Se houver capas ou carregadores com esse
  rótulo, ficam na subcategoria errada. O mesmo vale para materiais de
  construção que estejam em `Ferramentas e construcao`, que foi para
  `Ferramentas`.

**Registros afetados:** os 3.000 produtos do legado foram classificados (100%):
452 sem categoria e 1.632 sem subcategoria. Os 12.000 produtos do e-commerce
mantêm a classificação que já tinham.

**Asserção associada:** `categorias do legado mapeadas`, que falha se aparecer
um texto novo na origem; `mapeamento do legado fora da taxonomia` = 0, que
garante que toda categoria usada existe na lista do e-commerce; e
`produtos do e-commerce com categoria na taxonomia`, com zero produtos de
categoria desconhecida.

---

## Ficha RDT-08 · Classificação e tratamento de cada tipo de ausência

**Etapa do pipeline:** 01_extracao (preservação) e 02_transformacao ·
`trata_datas_e_ausencias`, `unifica_clientes`, `conforma_valores`

**O que o dado mostrava:** um campo vazio pode ter causas diferentes,
dependendo do campo. Já na extração tomamos dois cuidados. Lemos o CSV sem
deixar o pandas converter sozinho textos como `''` e `N/A` em vazio. E
guardamos a linha original do JSON, porque o pandas trataria do mesmo jeito uma
chave que veio vazia e uma chave que nem veio. Separamos as ausências em três
tipos: dado que não foi coletado, dado que não se aplica ao registro e
"sentinela", um valor falso posto no lugar do que falta (como a data
01/01/1900). Somamos a eles as inconsistências, valores que existem mas não
fazem sentido.

| Campo | O que aparece | Registros | Tipo | Tratamento |
|---|---|---:|---|---|
| CPF no legado | texto em branco | 4.423 | não coletado | vazio |
| CPF no e-commerce | vazio | 6.855 | não coletado (opcional no cadastro) | vazio |
| Cidade no legado | `N/A` | 933 | sentinela | vazio |
| Data de entrega no legado | vazia em pedido não entregue | 28.808 | não se aplica | mantida vazia |
| Data de entrega no e-commerce | vazia em pedido não entregue | 72.796 | não se aplica | mantida vazia |
| Data de entrega no legado | `1900-01-01` | 1.025 | sentinela | vazia + marcação `entrega_sem_data` |
| Entrega antes do pedido | legado 775, e-commerce 1.683 | 2.458 | inconsistência | vazia + marcação `entrega_inconsistente` |
| Desconto no legado | `-1` | 4.056 | sentinela | zero (RDT-06) |
| SAC: data de fechamento | em branco | 5.791 | não coletado | vazia + marcação `fechamento_sem_data` |
| SAC: nota de satisfação | em branco | 8.578 | não coletado (sem resposta) | vazia |
| SAC: nota de satisfação | `0` | 2.800 | sentinela (a escala vai de 1 a 5) | vazia + marcação `nota_sentinela` |
| SAC: tempo de resolução | negativo | 350 | inconsistência de sinal | sem o sinal + marcação `horas_sinal_corrigido` |
| Avaliação: nota | chave ausente | 4.831 | não coletado | vazia |
| Avaliação: compra verificada | chave ausente | 15.731 | não coletado | vazia (e não "falso") |
| Avaliação: votos úteis | chave ausente | 72.129 | não coletado | vazia (e não zero) |

Três casos pediram mais evidência. Os 5.791 atendimentos sem data de fechamento
têm todos o tempo de resolução preenchido: foram resolvidos e a data se perdeu
na exportação, então não estão em aberto. Nos atendimentos que têm as duas
datas, a diferença em dias entre abertura e fechamento é igual ao tempo de
resolução dividido por 24, arredondado para baixo, em 99,99% dos casos. Nos 329
atendimentos com tempo negativo e com as duas datas, a conta fecha em 100% dos
casos quando tiramos o sinal, o que mostra que o sinal está errado. Nas
avaliações, 2.188 trazem `helpful_votes: 0` escrito. Se a chave ausente
quisesse dizer zero votos, esse zero escrito não apareceria.

**Alternativas consideradas:**

1. Preencher o que falta com a média ou o valor mais comum. Descartamos para
   todos os campos: não existe nota média de quem não respondeu à pesquisa, nem
   data de entrega média para um pedido cancelado.
2. Deixar tudo vazio, sem diferenciar a causa.
3. Classificar cada caso. "Não coletado" e "não se aplica" ficam só vazios.
   Sentinelas e inconsistências ficam vazias e marcadas, porque saber que o
   registro falhou pode ajudar o modelo. Corrigir o valor só quando o próprio
   dado prova qual é o certo (desconto `-1` e sinal das horas).
4. No SAC, calcular a data de fechamento como abertura + tempo de resolução, já
   que a conta vale em 99,99% dos casos.

**Decisão:** alternativa 3. A alternativa 4 fica anotada como opção para a
tabela de análise (etapa 4), mas não entra no DW.

**Justificativa:** o tipo da ausência decide o tratamento. "Não se aplica" não
é dado faltando, e preencher criaria uma entrega que nunca aconteceu.
Sentinelas e inconsistências carregam uma informação (a logística não registrou
a entrega, o cliente não respondeu à pesquisa) que o modelo pode usar. Não
calculamos a data de fechamento porque o tempo de resolução, que é o dado útil,
já está guardado, e o DW deve ser cauteloso com valores calculados.

**O que se perde:**
* a data real de entrega de 3.483 pedidos (1.025 sentinelas e 2.458
  inconsistentes). Esses pedidos ficam fora de qualquer cálculo de prazo. Se
  as falhas de registro acontecem mais em entregas demoradas, o prazo médio fica
  mais curto do que o real, e não temos como verificar;
* em 2.458 pedidos o erro pode estar na data do pedido, e não na da entrega.
  Mantivemos a data do pedido porque é ela que define se a compra cai antes ou
  depois do corte da recompra;
* a data de fechamento de 5.791 atendimentos (6,1%);
* a diferença entre "nota 0 digitada" e "sem resposta", que só continua visível
  pela marcação;
* nas avaliações, nota, compra verificada e votos vazios querem dizer "a API não
  mandou". Uma média de votos que ignore os vazios fala só de 54,9% das
  avaliações.

**Registros afetados:** pedidos: 3.483 com a data de entrega apagada (0,85% dos
410.000) e 101.604 com a data vazia mantida por não se aplicar. Clientes:
11.278 sem CPF e 933 com cidade `N/A`. SAC: 5.791 sem data de fechamento,
11.378 sem nota (12,0% dos 95.000) e 350 com o sinal corrigido. Avaliações:
4.831 sem nota (3,0%), 15.731 sem compra verificada (9,8%) e 72.129 sem votos
(45,1%).

**Asserção associada:** `pedido não entregue com data de entrega` = 0;
`dias_entrega negativo` = 0; `data de entrega sentinela restante` = 0;
`datas do SAC coerentes com |tempo_resolucao|` de pelo menos 99,9% (obtido
99,99%); `nota da avaliação em 1..5`; `data do pedido` sem vazios.

---

## Ficha RDT-09 · Fuso horário

**Etapa do pipeline:** 01_extracao (preservação) e 02_transformacao ·
`trata_datas_e_ausencias`, `unifica_clientes`

**O que o dado mostrava:** o e-commerce grava data e hora com fuso. Quando lemos
as datas de cadastro no horário de Brasília, todas caem à meia-noite, o que
indica que o sistema grava datas locais com fuso −3. O legado grava data e hora
sem fuso, e todos os 130.000 pedidos foram feitos entre 8h e 21h, horário de
loja, o que indica horário local. No JSON, 128.085 avaliações (80,1%) vêm com
`-03:00`, 31.915 (19,9%) vêm sem fuso e nenhuma traz outro fuso. Se
passássemos tudo para UTC (o horário de referência mundial), 34.894 pedidos do
e-commerce (12,5%) mudariam de dia. Desses, 37 pedidos feitos na noite de
30/06/2025 passariam para 01/07/2025 e entrariam no período do alvo, e 23
pedidos de 28/09/2025 sairiam dele.

**Alternativas consideradas:**

1. Passar tudo para UTC. É o padrão técnico mais comum, mas muda o dia de 12,5%
   dos pedidos e ainda exige escolher um fuso para o legado.
2. Passar tudo para o horário de Brasília (`America/Sao_Paulo`) e guardar sem
   fuso.
3. Deixar cada fonte como veio. Toda comparação entre fontes dependeria de
   lembrar de converter na hora da consulta.

**Decisão:** alternativa 2. Na `stg`, as datas do e-commerce continuam com fuso,
como vieram. Na etapa 2, convertemos essas datas para o horário de Brasília,
tratamos as datas do legado como horário local e colocamos `-03:00` nas
avaliações que vieram sem fuso.

**Justificativa:** a data de corte (30/06/2025) e o período do alvo são datas do
calendário brasileiro, onde a empresa opera. No horário local, um pedido feito
às 22h do dia 30 é do dia 30, para o cliente e para a empresa. O Brasil não tem
horário de verão desde 2019, então, entre 2023 e 2025, o horário de Brasília é
sempre UTC−3 e a conversão não perde nada.

**O que se perde:** a informação explícita do fuso. Se a empresa tiver lojas em
outro fuso, como Manaus ou Cuiabá (UTC−4), os pedidos dessas lojas ficam com a
hora deslocada em uma hora, e não temos a UF da loja para corrigir. O mesmo vale
para as 31.915 avaliações sem fuso: assumimos `-03:00` porque é o único fuso
que aparece no arquivo, mas a API não confirma.

**Registros afetados:** 280.000 datas de pedido e 207.204 datas de entrega do
e-commerce convertidas; 85.000 datas de cadastro; 130.000 pedidos do legado
tratados como horário local; 160.000 avaliações convertidas, 31.915 delas
(19,9%) com o fuso atribuído por nós.

**Asserção associada:** `avaliações com fuso diferente de -03:00` = 0. Se
aparecer outro fuso, a suposição usada para as avaliações sem fuso deixa de
valer. Também `data do pedido` sem vazios depois da conversão.

---

## Decisões menores registradas no código

Estas decisões não ganharam ficha porque não descartam informação, mas ficam
registradas aqui:

* **Linhas repetidas do SAC:** 1.140 linhas (1,2%) são cópias exatas,
  protocolo incluído. Removemos as cópias, e a asserção `protocolo do SAC sem
  cópias` confirma que nenhum protocolo ficou com dois conteúdos diferentes.
* **Canal e motivo do SAC:** o canal foi para maiúsculas, já que `chat` e `CHAT`
  são o mesmo canal. Os 18 textos de motivo viraram 12 motivos padronizados, e
  a observação "cliente reiterou o contato" (1.360 casos, depois de remover as
  cópias) virou a marcação `contato_reiterado`.
* **Ligação do SAC e das avaliações aos pedidos (decisão 6 do código):** está
  em `liga_sac_e_avaliacoes` e não descarta nenhum registro. Todo atendimento
  encontra o pedido citado pelo sistema e número (95.000 de 95.000), e 89.075
  (93,8%) encontram o cliente pelo CPF. Toda avaliação encontra o pedido pelo
  código `order.ref` (160.000) e o produto pelo SKU (160.000). As contradições
  ficaram marcadas. O cliente do pedido é o mesmo do CPF em só 1 atendimento.
  47.667 atendimentos foram abertos antes do pedido citado, e 79.867 avaliações
  foram enviadas antes do pedido. O produto avaliado está no pedido em só 80
  avaliações. Como essas ligações serão usadas na carga e na tabela de análise
  é assunto da ficha RDT-13, na Semana 3.

---

## Atualização da semana 3- fichas 10 a 13

A carga continua as regras das fichas anteriores. Suas verificações ficam registradas em
*qa.execucao_assercao*, na etapa *03_carga_dw*.

### *RDT-10* - Itens sem pedido

*Etapa 03_carga_dw - carrega_fato_item.*

Na *cnf*, *20.577* itens não têm pedido correspondente. Para definir como esses
registros seriam tratados na carga, foram consideradas três opções:

1. *Excluir esses itens, perdendo a possibilidade de investigá-los.*
2. *Inventar pedidos para manter os itens no DW, mesmo sem conhecer a compra.*
3. *Carregar só os itens com pedido e preservar os demais na cnf.*

Foi adotado o carregamento apenas dos itens com pedido, enquanto os órfãos permaneceram
em *cnf.item*, com *orfao=true*. Assim, os casos sem pedido continuam disponíveis para
investigação sem criar compras fictícias.

Essa escolha deixa *20.577* itens (*1,48%*) fora das análises do *DW* e permite carregar
os outros *1.371.876* (*98,52%*). Para verificar se essa separação não perdeu nem
duplicou linhas, a carga foi conferida de três formas:

1. *A quantidade carregada deve ser igual à de itens com pedido válido. Nesta execução,
   foram 1.371.876 linhas.*
2. *1.371.876 carregados + 20.577 órfãos = 1.392.453 itens da cnf.*
3. *Por pedido, a quantidade de linhas e os valores dos itens coincidem com as medidas
   do pedido.*

### *RDT-11* - O que cada linha representa

*Etapa 03_carga_dw - carrega_fato_pedido e carrega_fato_item.*

A base reúne *410.000* pedidos e *1.371.876* itens válidos. Como a junção direta de
pedidos, itens e atendimentos pode repetir o valor da compra, foram consideradas duas
opções para organizar esses dados:

1. *Reunir pedido, item e atendimento na mesma tabela, repetindo medidas.*
2. *Separar os fatos e ligar cada registro pelas chaves correspondentes.*

Foi adotada a separação dos fatos por tipo de acontecimento, de modo que cada linha
representa um pedido ou um item. Nesse desenho, o sistema de origem e o identificador
distinguem pedidos com o mesmo número.

Esse desenho abrange os *410.000* pedidos e os *1.371.876* itens válidos, ou *100%* de
cada conjunto, sem excluir linhas. Como os fatos têm níveis de detalhe diferentes,
precisam ser agregados antes das junções, e o frete permanece no pedido, sem rateio
entre itens.

Para conferir esse desenho, foram procurados pedidos repetidos por sistema e
identificador e itens sem pedido. Por pedido, foram comparadas as quantidades de linhas
de item e os valores brutos, descontos e valores líquidos.

### *RDT-12* - Pedido sem cliente

*Etapa 03_carga_dw - carrega_fato_pedido.*

Os pedidos da *cnf* usam os clientes unificados na entrega anterior, e nenhum dos
*410.000* pedidos ficou sem cliente (*0%*). Para tratar uma possível ausência em cargas
futuras, foram considerados três caminhos:

1. *Excluir os pedidos sem cliente, reduzindo o total financeiro.*
2. *Preservar o pedido com um cliente “desconhecido”, reunindo pessoas diferentes no
   mesmo cadastro.*
3. *Interromper a carga e investigar a referência ausente.*

Foi decidido interromper a carga quando faltar a referência do cliente, registrando a
falha e desfazendo a transação para preservar o *DW* anterior. Com isso, a atualização
precisa aguardar a correção, mas nenhuma compra é excluída nem atribuída a um cadastro
fictício.

Para confirmar essa proteção, foi testada uma referência de cliente ausente, e a carga
foi recusada sem alterar o *DW* anterior.

### *RDT-13* - Ligações de atendimentos e avaliações

*Etapa 03_carga_dw - carrega_fatos_auxiliares.*

Só um dos *95.000* atendimentos tem cliente compatível com o pedido, mas foi aberto
antes da compra. Entre *160.000* avaliações, *80* citam um produto do pedido e apenas
*38* também respeitam a ordem entre compra e envio. Diante dessas incompatibilidades,
foram consideradas três opções:

1. *Confiar só no número do pedido, aceitando ligações contraditórias.*
2. *Excluir os registros contraditórios, alterando as contagens.*
3. *Manter todos os registros e vincular ao pedido só os casos coerentes.*

Para preservar os registros sem aceitar ligações incompatíveis, o vínculo com pedidos
existentes foi condicionado aos critérios abaixo:

1. *No SAC, o cliente deve ser o mesmo e a compra já deve ter ocorrido na abertura do
   atendimento.*
2. *Na avaliação, o produto deve fazer parte do pedido e a compra já deve ter ocorrido
   no envio.*

Com esses critérios, *95.000* atendimentos (*100%*) e *159.962* avaliações (*99,97625%*)
ficam sem pedido, enquanto *38* avaliações (*0,02375%*) mantêm o vínculo. Os casos sem
ligação ficam fora das análises por pedido, e suas referências permanecem na *cnf*.

Para conferir essa decisão, foram comparadas as contagens de atendimentos e avaliações e
verificados cliente, produto e datas das ligações aceitas.

### *ABT* inicial e limites

A partir dos pedidos carregados, foi construída uma *ABT* com *107.642* clientes com
pedido até *30/06/2025*, respeitando os seguintes cortes:

1. *Os atributos usam apenas pedidos não cancelados até 30/06/2025.*
2. *O alvo considera compras não canceladas entre 01/07 e 28/09/2025.*

Os *2.976* clientes com apenas pedidos cancelados até o corte receberam frequência e
valor zero, com recência vazia. Na janela futura, o alvo *1* foi observado em *24.021*
clientes (*22,3156%*).

Esses resultados devem ser lidos com as limitações da entrega anterior. Sem o histórico
das mudanças de status, a *ABT* reconstrói o passado com os dados atuais. Além disso,
permanecem os riscos da unificação de clientes da ficha *02*.

Pelos critérios de vínculo da ficha *13*, o *SAC* e as avaliações não foram usados nos
atributos iniciais. Com essa base definida, a versão com vazamento e a comparação no
*Orange* ficam para a semana *4*.

---

## Atualização da semana 4 - fichas 14 e 15

As duas fichas vêm da etapa `04_abt.py` e do experimento no Orange
(`orange/experimento.py`). As asserções ficam em `qa.execucao_assercao`, nas
etapas `04_abt_correta` e `04_abt_vazamento`. Os resultados dos modelos estão
no [relatório de resultados](RELATORIO_RESULTADOS.md).

---

## Ficha RDT-14 · Atributos da ABT e verificação de ausência de vazamento

**Etapa do pipeline:** 04_abt · `SQL_ATRIBUTOS`, `monta_atributos_corretos`

**O que o dado mostrava:** a ABT tem 107.642 clientes com algum pedido até
30/06/2025, e 24.021 deles (22,3%) compraram de novo na janela do alvo. Antes
de escolher os atributos, medimos a AUC de cada candidato sozinho, calculado só
com pedidos até o corte. Nenhum passou de 0,55:

| Atributo candidato (até o corte) | AUC sozinho |
|---|---:|
| `frequencia_pedidos` (pedidos não cancelados) | 0,536 |
| `valor_monetario` | 0,532 |
| `recencia_dias` | 0,515 |
| sistemas em que o cliente comprou (1 ou 2) | 0,546 |
| pedidos nos últimos 365 dias | 0,520 |
| atendimentos do SAC | 0,521 |
| pedidos cancelados | 0,512 |
| `origem_unificacao = AMBOS` | 0,554 |

O gerador explica o teto baixo. Ele sorteia o cliente e a data de cada pedido
de forma independente (`gerador/gerar_dados.py`, linhas 283-284 no legado e
394-395 no e-commerce). Assim, o histórico de um cliente diz pouco sobre a
compra seguinte. O sinal que sobra vem de quantos pedidos cada sistema reparte
entre seus clientes: quem está nos dois sistemas recompra 35,9%, contra 20,7%
no e-commerce e 18,8% no legado.

Dois campos do cadastro trazem informação do futuro. Dos 14.737 clientes
`AMBOS` da ABT, 2.417 (16,4%) só se cadastraram no e-commerce depois de
30/06/2025, então `origem_unificacao` conta, para esses clientes, um fato que
ainda não existia na data do corte. E 15.880 clientes da ABT (14,8%) têm
`dt_primeiro_cadastro` posterior ao corte, embora tenham pedido antes dele.

**Alternativas consideradas:**

1. Manter os três atributos da Semana 3 (frequência, valor e recência), todos
   com pedidos até o corte e sem cancelados.
2. Somar atributos do cadastro: `origem_unificacao` e tempo desde o cadastro.
   São os que mais separam as classes, mas trazem dados posteriores ao corte.
3. Somar atributos legítimos calculados até o corte: sistemas usados, pedidos
   em 365 dias, cancelados e atendimentos do SAC.

**Decisão:** alternativa 1.

**Justificativa:** a alternativa 2 introduz vazamento em 2.417 clientes por
`origem_unificacao` e em 15.880 por `dt_primeiro_cadastro`, e o objetivo desta
ABT é responder à pergunta sem olhar para depois de 30/06/2025. Testamos a
alternativa 3 no mesmo protocolo do Orange: a AUC subiu de 0,527 para 0,545
(Naive Bayes), de 0,532 para 0,549 (Regressão Logística) e de 0,496 para 0,505
(Árvore). Com ganho de no máximo 0,018, preferimos manter nas duas versões do
experimento os mesmos três atributos. Assim, a comparação da RDT-15 isola o
efeito do vazamento, sem misturar atributos novos.

**O que se perde:**
* até 0,018 de AUC que a alternativa 3 acrescentaria;
* a diferença entre cliente de um sistema e cliente dos dois, que é o maior
  sinal do dado. Uma versão futura pode usar "sistemas em que comprou até o
  corte", que mede quase o mesmo sem vazar;
* 2.976 clientes (2,8%) só têm pedidos cancelados até o corte. Eles ficam com
  frequência e valor zero e recência vazia, e o modelo não os distingue de quem
  nunca comprou de verdade.

**Registros afetados:** 107.642 clientes (100% da ABT), 2.976 (2,8%) com
recência vazia.

**Asserção associada:** `atributos conferidos sem dados futuros` = 0. Depois
de gravar, a etapa recalcula frequência, valor e recência só com pedidos até o
corte e conta os clientes em que algum valor difere da ABT (obtido 0). Também
`taxa do alvo` entre 0,02 e 0,60 (obtido 0,223), `junção alvo x atributos` e
`contagem da ABT igual ao alvo` (107.642 nas duas).

---

## Ficha RDT-15 · Vazamentos introduzidos de propósito na versão de comparação

**Etapa do pipeline:** 04_abt · `SQL_ATRIBUTOS_VAZAMENTO`,
`monta_atributos_com_vazamento`; tabela `abt.recompra_90d_vazamento`

**O que o dado mostrava:** o DW tem pedidos até 30/12/2025, ou seja, 68.406
pedidos (16,7%) depois do corte, 34.903 deles depois da janela do alvo. Na
ABT, 43.150 clientes (40,1%) têm algum pedido não cancelado depois do corte.
Um atributo que enxergue esses pedidos conhece o alvo.

**Alternativas consideradas:**

1. Tirar o filtro de data de `frequencia_pedidos` e `recencia_dias`, mantendo
   a fórmula. A frequência passa a contar pedidos até 30/12/2025, e a recência
   fica negativa para quem comprou depois do corte.
2. Calcular a recência a partir da última data da base (30/12/2025) em vez da
   data do corte. Também vaza, mas os valores continuam positivos e o erro fica
   mais difícil de achar.
3. Para o vazamento sutil: z-score de `valor_monetario` com média e desvio de
   todas as 107.642 linhas, antes da partição treino/teste, contra o z-score
   correto, com média e desvio só do treino.

**Decisão:** alternativas 1 e 3. `valor_monetario` continua calculado até o
corte, então o z-score é o único vazamento nessa coluna.

| Coluna | Vazamento | Como ficou |
|---|---|---|
| `frequencia_pedidos` | sem filtro de data | média de 4,40 pedidos entre quem recomprou e 2,97 entre quem não recomprou (na versão correta: 2,97 e 2,71) |
| `recencia_dias` | sem filtro de data | negativa em 43.150 clientes; nenhum cliente com recência ≥ 0 recomprou |
| `valor_monetario` | z-score antes da partição | média 6.924,10 e desvio 5.115,01 da base inteira; no treino seriam 6.947,10 e 5.115,48 |

**Justificativa:** a alternativa 1 reproduz o erro mais comum em ABTs, uma
consulta escrita sem a cláusula de data, e muda uma linha de SQL. Escolhemos
ela, e não a 2, porque o experimento compara o efeito dos vazamentos com as
mesmas fórmulas da versão correta. O z-score global reproduz um erro de
pré-processamento que nenhuma consulta denuncia.

**O que se perde:** a validade da versão inteira. Na data do corte, os pedidos
de julho a dezembro ainda não existem, então o modelo treinado nela não pode
ser usado. A AUC medida (até 0,930) não acontece em produção. A tabela também é
gravada por `to_sql` com `replace`, sem as restrições da ABT correta (chave
primária, `recencia_dias >= 0`), justamente para aceitar valores negativos.
Ela existe só para o experimento.

**Registros afetados:** 43.150 clientes (40,1%) com frequência e recência
diferentes da versão correta; 107.642 (100%) com valor normalizado; a recência
vazia cai de 2.976 para 1.863 clientes, porque parte dos que só tinham
cancelados até o corte comprou depois.

**Asserção associada:** `frequência/recência enxergam o futuro` ≥ 1 (obtido
43.150), que recalcula os dois atributos com pedidos até o corte e conta as
diferenças; `valor_monetario normalizado com a base inteira`, que exige média
zero sobre todas as linhas (obtido 1,4 × 10⁻¹⁶). No experimento, o
`orange/experimento.py` confirma que as duas versões usam os mesmos 32.292
clientes de teste.

---

## Tabela-resumo

| Ficha | Decisão | Registros afetados | % da base | Perde informação? |
|---|---|---:|---:|---|
| RDT-01 | Carga completa | 2.205.864 linhas | 100% | Sim: histórico entre cargas |
| RDT-02 | CPF (válido ou não) + nome; nome + cidade + UF sem CPF | 14.818 clientes unidos | 12,9% dos clientes | Sim: ~75 fusões erradas, ~160 pessoas não unidas |
| RDT-03 | Cadastros repetidos juntados por e-mail + nome + CPF compatível | 2.250 cadastros | 4,8% de CLIENTES | Sim: grafia do cadastro descartado (guardada na stg) |
| RDT-04 | CANCELADO + motivo quando conhecido | 52.177 pedidos | 12,7% dos pedidos | Sim: motivo no legado (15.774); SEPARACAO e processing juntos |
| RDT-05 | Valores recalculados a partir dos itens, frete separado | 410.000 pedidos | 100% | Sim: comparação direta com total_amount |
| RDT-06 | Frete sem sinal; −1 = 0; preço suspeito só marcado | 1.174 pedidos; 4.056 itens; 31 produtos | 0,29%; 0,29%; 0,21% | Sim: estornos reais, se houver; preços inflados mantidos |
| RDT-07 | Classificar até o nível certo, o resto vazio | 452 sem categoria; 1.632 sem subcategoria | 15,1% e 54,4% do legado | Sim: categoria de 15% dos itens do legado |
| RDT-08 | Ausências classificadas; sentinelas marcadas | 3.483 entregas; 5.791 fechamentos; 11.378 notas | 0,85%; 6,1%; 12,0% | Sim: datas reais que não voltam |
| RDT-09 | Horário de Brasília | 280.000 pedidos + 160.000 avaliações | 100% do e-commerce | Sim: fuso explícito; lojas fora de UTC−3 |
| RDT-10 | Itens sem pedido ficam só na cnf | 20.577 itens | 1,48% dos itens | Sim: itens fora das análises do DW |
| RDT-11 | Um fato por grão: pedido e item | 410.000 pedidos; 1.371.876 itens | 100% | Sim: frete fica no pedido, sem rateio por item |
| RDT-12 | Pedido sem cliente interrompe a carga | 0 pedidos | 0% | Sim: a atualização espera a correção |
| RDT-13 | SAC e avaliações ligados ao pedido só quando coerentes | 95.000 atendimentos e 159.962 avaliações sem pedido | 100% e 99,98% | Sim: análises por pedido sem SAC e quase sem avaliações |
| RDT-14 | Três atributos até o corte; cadastro fora | 107.642 clientes; 2.976 sem recência | 100%; 2,8% da ABT | Sim: até 0,018 de AUC; sinal de cliente nos dois sistemas |
| RDT-15 | Frequência e recência sem filtro de data; z-score global | 43.150 clientes vazados; 107.642 normalizados | 40,1%; 100% da ABT | Sim: a versão inteira deixa de ser utilizável |

---

## Reflexão final

**1. Qual decisão mais mudaria o resultado do modelo?** Medimos uma: o filtro
de data dos atributos (RDT-15). Sem ele, a AUC da árvore vai de 0,496 para
0,930. Entre as decisões de limpeza, apostamos na identidade do cliente
(RDT-02), e essa aposta ainda é uma hipótese para o modelo. Medimos o efeito
no alvo: quem a regra uniu nos dois sistemas recompra 35,9%, contra cerca de
20% nos demais. Se não tivéssemos unido as 14.818 pessoas, cada metade
apareceria como um cliente que comprou menos, e parte dos que voltaram pelo
outro sistema contaria como alvo 0. Não treinamos o modelo sem a unificação
para medir a diferença na AUC.

**2. Alguma decisão por conveniência técnica afetaria pessoas reais?** Sim, a
regra nome + cidade + UF da RDT-02. Escolhemos ela para unir clientes sem CPF,
e ela funde cerca de 75 pares de pessoas diferentes. Numa campanha de retenção,
cada fusão soma as compras de duas pessoas: o cliente parece fiel, sai da lista
de risco, e uma das pessoas deixa de receber a oferta. O mesmo vale para os
2.976 clientes que só tinham pedidos cancelados (RDT-14): para o modelo, quem
teve o pagamento recusado parece alguém que nunca quis comprar.

**3. Que informação gostaríamos de ter tido?** O histórico de mudanças dos
pedidos, com data de cada status, para reconstruir a base como ela estava em
30/06/2025 (RDT-01). Também uma data de cadastro confiável: 15.880 clientes
têm pedido antes do próprio cadastro, e por isso não conseguimos usar o tempo
de relacionamento. Por fim, algum dado de comportamento além dos pedidos. No
gerador, cada pedido recebe um cliente sorteado, e o melhor atributo legítimo
que achamos chega a uma AUC de 0,55. Sem um sinal de comportamento, nenhuma
limpeza leva o modelo correto muito além disso, e preferimos mostrar esse
teto a esconder o resultado.
