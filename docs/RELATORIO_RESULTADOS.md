# Relatório de resultados · experimento de vazamento

**Equipe:** Diogo Augusto de Siqueira Cândido, Líbia Canhete Alves e Cruz, Lucas Taveira De Lima
**Data:** 31/10/2026

**Pergunta:** dado o comportamento de compra do cliente até 30/06/2025, ele fará
ao menos uma nova compra entre 01/07 e 28/09/2025?

## Como medimos

As duas tabelas têm os mesmos 107.642 clientes, o mesmo alvo (22,3% de
recompra) e as mesmas três colunas: `frequencia_pedidos`, `valor_monetario` e
`recencia_dias`. A versão correta calcula tudo com pedidos até o corte
(RDT-14). A versão com vazamento tira o filtro de data da frequência e da
recência e normaliza o valor com a média e o desvio de todas as linhas
(RDT-15).

Os fluxos `orange/fluxo_correta.ows` e `orange/fluxo_vazamento.ows` seguem o
roteiro: File → Data Sampler (70/30, estratificado, sorteio fixo) → Test &
Score em "Test on test data", com Naive Bayes, Logistic Regression e Tree nos
padrões do Orange 3.40, ligado à Confusion Matrix e à ROC Analysis. Os dois
arquivos só diferem no CSV de entrada. O exportador ordena as linhas por
`cliente_sk`, e por isso o sorteio cai nos mesmos 75.350 clientes de treino e
32.292 de teste nas duas versões (7.206 positivos no teste).

O Test & Score não mostra a acurácia balanceada, que é a média entre a taxa de
acerto dos positivos e a dos negativos. O script `orange/experimento.py` refaz
o fluxo com os mesmos widgets e o mesmo sorteio e calcula essa métrica.
Carregamos os `.ows` no motor do Orange e as AUCs batem com as do script até a
quarta casa.

## a) Diferença de desempenho entre as duas versões

| Modelo | AUC correta | AUC vazamento | Diferença | Acur. bal. correta | Acur. bal. vazamento | Diferença |
|---|---:|---:|---:|---:|---:|---:|
| Naive Bayes | 0,527 | 0,892 | +0,365 | 0,500 | 0,749 | +0,249 |
| Logistic Regression | 0,532 | 0,841 | +0,309 | 0,500 | 0,598 | +0,098 |
| Tree | 0,496 | 0,930 | +0,434 | 0,496 | 0,908 | +0,412 |

Matrizes de confusão no teste (VP, FP, FN, VN):

| Modelo | Correta | Vazamento |
|---|---|---|
| Naive Bayes | 0, 0, 7.206, 25.086 | 4.603, 3.509, 2.603, 21.577 |
| Logistic Regression | 0, 1, 7.206, 25.085 | 2.093, 2.361, 5.113, 22.725 |
| Tree | 1.227, 4.474, 5.979, 20.612 | 6.111, 809, 1.095, 24.277 |

Com o vazamento, a AUC sobe entre 0,31 e 0,43 e a árvore acerta 94,1% dos
clientes. Na versão correta, Naive Bayes e Regressão Logística preveem "não
recompra" para quase todo mundo: com 22% de positivos e atributos fracos, a
classe majoritária é a melhor aposta, e a acurácia balanceada fica em 0,500. A
árvore cresce até 30.473 nós decorando ruído e fica abaixo do acaso no teste
(AUC 0,496). A acurácia simples engana nas duas versões: a correta marca 77,7%
só por responder sempre "não".

O resultado fraco da versão correta vem do dado. O gerador sorteia o cliente e
a data de cada pedido de forma independente (`gerador/gerar_dados.py`, linhas
283-284 e 394-395), e o histórico do cliente quase não informa a próxima
compra. O melhor atributo legítimo que encontramos tem AUC 0,55 sozinho, e
quatro atributos extras levaram o modelo a 0,549 no máximo (RDT-14). Um
resultado de 0,53 honesto responde à pergunta melhor do que 0,93 com dados do
futuro.

## b) O vazamento sutil fez diferença?

Não, a diferença foi zero. Isolamos o z-score na versão correta: as mesmas
três colunas, com `valor_monetario` normalizado de três formas.

| Normalização de `valor_monetario` | AUC NB | AUC LR | AUC Tree | Acur. bal. Tree |
|---|---:|---:|---:|---:|
| nenhuma | 0,5270 | 0,5318 | 0,4955 | 0,4960 |
| z-score com média e desvio do treino (correto) | 0,5270 | 0,5318 | 0,4955 | 0,4960 |
| z-score com média e desvio da base inteira (vazamento) | 0,5270 | 0,5318 | 0,4955 | 0,4960 |

As matrizes de confusão também saem idênticas. Há dois motivos:

* a informação que vaza é pequena. Com a base inteira, a média fica em
  6.924,10 e o desvio em 5.115,01; só com o treino, 6.947,10 e 5.115,48. Os
  30% de teste mexem a média em 0,3%;
* o z-score aplica a mesma conta a todas as linhas e não muda a ordem dos
  valores. A árvore corta por limiares e o Naive Bayes do Orange discretiza os
  atributos numéricos, então os dois ficam iguais com qualquer escala. A
  Regressão Logística depende da escala só pela regularização, e com 75 mil
  linhas de treino esse efeito some.

Mesmo assim, o erro existe. Ele aparece quando o pré-processamento aprende
algo que depende do alvo ou da vizinhança, como imputar pela média da classe,
selecionar atributos ou codificar categorias pela taxa do alvo usando a base
inteira, e quando a base é pequena e o teste pesa mais na média. Como esse
vazamento não mexe no número, o desempenho não denuncia o erro, e só a revisão
do código encontra.

## c) Como descobrir o vazamento recebendo só o resultado

Recebendo só a versão com vazamento, faríamos estas verificações, da mais
barata para a mais cara. Cada uma mostra o problema nesta base.

1. **Comparar com o que o problema permite.** Prever recompra a partir de
   três atributos de histórico com AUC 0,93 é bom demais. A frequência de
   compras até o corte, o atributo mais óbvio, dá 0,531 sozinha nesta base, e
   a árvore com vazamento passa dela por 0,40. Um salto desse tamanho pede
   auditoria.
2. **Medir a AUC de cada atributo sozinho.** Na versão com vazamento,
   `recencia_dias` sozinha dá 0,797 e `frequencia_pedidos` dá 0,706. Na
   correta, as duas ficam em 0,515 e 0,531. Quando um atributo quase resolve o
   problema sozinho, ele costuma carregar o alvo.
3. **Olhar o domínio dos valores.** "Dias desde a última compra até o corte"
   não pode ser negativo, e é negativo em 43.150 clientes (40,1%). Cruzando
   com o alvo: nenhum cliente com recência ≥ 0 recomprou. Uma regra
   determinística assim não existe em comportamento de compra.
4. **Ler o modelo.** A raiz da árvore corta em `recencia_dias ≤ -1`, um
   limiar que só faz sentido para quem já sabe que o cliente comprou depois.
   A árvore com vazamento tem 5.491 nós; a correta, 30.473. O modelo achou um
   atalho.
5. **Conferir a linhagem de cada atributo.** Recalcular cada coluna só com
   dados até o corte e comparar. É o que a asserção `frequência/recência
   enxergam o futuro` faz: 43.150 clientes diferem. Na versão correta, a mesma
   conta dá zero (`atributos conferidos sem dados futuros`).
6. **Simular o dia da previsão.** Rodar a mesma consulta numa cópia da base
   congelada em 30/06/2025, como ela estaria quando o modelo fosse usado. Sem
   os pedidos de julho a dezembro, a consulta sem filtro devolve a frequência
   e a recência da versão correta, e o desempenho cai junto para cerca de
   0,53.

Os passos 2 e 3 bastam para a defesa: levam minutos e dispensam o código de
quem montou a tabela. O passo 5 entra no pipeline como asserção e impede que o
erro volte.

## Reproduzir

```bash
docker compose exec etl python 04_abt.py --correta
docker compose exec etl python 04_abt.py --vazamento
docker compose exec etl python 05_exportar_orange.py
python orange/experimento.py
```

O último comando roda fora do contêiner, no ambiente com o Orange descrito no
README, e grava os dois `.ows` e imprime as tabelas deste relatório.
Para abrir os fluxos no Orange, use **File → Open** em `orange/`. O widget File
encontra o CSV em `../saida/` pelo caminho relativo.
