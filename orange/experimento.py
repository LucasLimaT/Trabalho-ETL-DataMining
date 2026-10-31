#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SEMANA 4 — FLUXOS DO ORANGE E COMPARAÇÃO DAS DUAS ABTs

Roda fora do contêiner, num Python com o Orange 3.40 (o ambiente está no
README), depois do 05_exportar_orange.py:

    python orange/experimento.py

1. Gera fluxo_correta.ows e fluxo_vazamento.ows com a mesma função. A única
   diferença entre os dois é o arquivo de entrada.
2. Refaz em código o que os fluxos fazem: mesmo sorteio do Data Sampler e
   learners criados pelos próprios widgets, com os padrões deles. Imprime
   AUC, CA e acurácia balanceada (o Test & Score não mostra a última).
3. Isola o vazamento sutil: ABT correta com valor_monetario em z-score
   calculado na base inteira, contra o z-score calculado só no treino.
"""

import logging
import os
import warnings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Avisos internos do Orange, do scikit-learn e do Qt não mudam o resultado.
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")
warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

import numpy as np
from AnyQt.QtWidgets import QApplication
from sklearn.metrics import balanced_accuracy_score, confusion_matrix, roc_auc_score

app = QApplication([])

from Orange.canvas import config
from Orange.data import Table
from Orange.evaluation import AUC, CA, TestOnTestData
from Orange.widgets.data.owdatasampler import OWDataSampler, SampleRandomP
from Orange.widgets.data.owfile import OWFile
from Orange.widgets.evaluate.owconfusionmatrix import OWConfusionMatrix
from Orange.widgets.evaluate.owrocanalysis import OWROCAnalysis
from Orange.widgets.evaluate.owtestandscore import OWTestAndScore
from Orange.widgets.model.owlogisticregression import OWLogisticRegression
from Orange.widgets.model.ownaivebayes import OWNaiveBayes
from Orange.widgets.model.owtree import OWTreeLearner
from orangecanvas.registry import WidgetRegistry
from orangecanvas.scheme import Scheme, SchemeLink, SchemeNode
from orangecanvas.scheme.readwrite import scheme_to_ows_stream
from orangewidget.utils.filedialogs import RecentPath

DIR_ORANGE = os.path.dirname(os.path.abspath(__file__))
DIR_SAIDA = os.path.join(DIR_ORANGE, "..", "saida")
VERSOES = {"correta": "abt_recompra_correta.csv",
           "vazamento": "abt_recompra_vazamento.csv"}
LEARNERS = [OWNaiveBayes, OWLogisticRegression, OWTreeLearner]


# --------------------------------------------------------------- 1. fluxos
def configura(cls, **ajustes):
    w = cls()
    for k, v in ajustes.items():
        setattr(w, k, v)
    return w.settingsHandler.pack_data(w)


def gera_fluxo(arquivo_csv, destino, registro):
    relpath = os.path.join("..", "saida", arquivo_csv)
    caminho = RecentPath(os.path.normpath(os.path.join(DIR_ORANGE, relpath)),
                         "basedir", relpath)
    nos = {
        "file": (OWFile, (80, 320), dict(recent_paths=[caminho], source=OWFile.LOCAL_FILE)),
        "sampler": (OWDataSampler, (240, 320), dict(
            sampling_type=OWDataSampler.FixedProportion,
            sampleSizePercentage=70, stratify=True, use_seed=True)),
        "nb": (OWNaiveBayes, (420, 40), {}),
        "lr": (OWLogisticRegression, (420, 140), {}),
        "tree": (OWTreeLearner, (420, 240), {}),
        "score": (OWTestAndScore, (600, 320), dict(resampling=OWTestAndScore.TestOnTest)),
        "matriz": (OWConfusionMatrix, (780, 240), {}),
        "roc": (OWROCAnalysis, (780, 400), {}),
    }
    esquema = Scheme(title=os.path.splitext(os.path.basename(destino))[0])
    for nome, (cls, pos, ajustes) in nos.items():
        desc = registro.widget(f"{cls.__module__}.{cls.__name__}")
        nos[nome] = SchemeNode(desc, position=pos, properties=configura(cls, **ajustes))
        esquema.add_node(nos[nome])

    ligacoes = [
        ("file", "Data", "sampler", "Data"),
        ("sampler", "Data Sample", "score", "Data"),
        ("sampler", "Remaining Data", "score", "Test Data"),
        ("nb", "Learner", "score", "Learner"),
        ("lr", "Learner", "score", "Learner"),
        ("tree", "Learner", "score", "Learner"),
        ("score", "Evaluation Results", "matriz", "Evaluation Results"),
        ("score", "Evaluation Results", "roc", "Evaluation Results"),
    ]
    for origem, saida, alvo, entrada in ligacoes:
        o, a = nos[origem], nos[alvo]
        esquema.add_link(SchemeLink(o, o.output_channel(saida), a, a.input_channel(entrada)))

    with open(destino, "wb") as f:
        scheme_to_ows_stream(esquema, f, pretty=True, pickle_fallback=True)
    print(f"fluxo gravado: {os.path.relpath(destino)}")


# ------------------------------------------------------------ 2. avaliação
def particao(data):
    # Mesmo sorteio do Data Sampler: 70% estratificado, semente fixa do widget.
    resto, amostra = SampleRandomP(0.7, stratified=True,
                                   random_state=OWDataSampler.RandomSeed)(data)
    return data[amostra], data[resto]


def avalia(treino, teste, rotulo):
    learners = [cls().create_learner() for cls in LEARNERS]
    res = TestOnTestData(store_data=True)(treino, teste, learners)
    aucs, cas = AUC(res), CA(res)
    linhas = []
    for i, cls in enumerate(LEARNERS):
        tn, fp, fn, tp = confusion_matrix(res.actual, res.predicted[i]).ravel()
        bal = balanced_accuracy_score(res.actual, res.predicted[i])
        linhas.append(f"| {rotulo} | {cls.name} | {aucs[i]:.4f} | {cas[i]:.4f} | "
                      f"{bal:.4f} | {tp} | {fp} | {fn} | {tn} |")
    return linhas


def zscore(data, coluna, media, desvio):
    data = data.copy()
    j = data.domain.index(coluna)
    with data.unlocked(data.X):
        data.X[:, j] = (data.X[:, j] - media) / desvio
    return data


def auc_univariada(teste):
    # AUC de cada atributo sozinho, usado direto como escore (sentido indiferente).
    y = teste.Y
    saida = []
    for var in teste.domain.attributes:
        x = teste.get_column(var)
        ok = ~np.isnan(x)
        auc = roc_auc_score(y[ok], x[ok])
        saida.append(f"{var.name} = {max(auc, 1 - auc):.4f} ({int((~ok).sum())} nulos)")
    return ", ".join(saida)


if __name__ == "__main__":
    cfg = config.Config()
    cfg.init()
    registro = WidgetRegistry()
    cfg.widget_discovery(registro).run(cfg.widgets_entry_points())
    for versao, arquivo in VERSOES.items():
        gera_fluxo(arquivo, os.path.join(DIR_ORANGE, f"fluxo_{versao}.ows"), registro)

    print("\n| Versão | Modelo | AUC | CA | Acurácia balanceada | VP | FP | FN | VN |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    tabelas = {}
    for versao, arquivo in VERSOES.items():
        data = Table(os.path.join(DIR_SAIDA, arquivo))
        treino, teste = particao(data)
        tabelas[versao] = (data, treino, teste)
        print("\n".join(avalia(treino, teste, versao)))

    # Vazamento sutil isolado: só muda de onde vêm a média e o desvio.
    data, treino, teste = tabelas["correta"]
    v = data.get_column("valor_monetario")
    vt = treino.get_column("valor_monetario")
    glob = (v.mean(), v.std(ddof=1))
    trein = (vt.mean(), vt.std(ddof=1))
    print("\n".join(avalia(zscore(treino, "valor_monetario", *trein),
                           zscore(teste, "valor_monetario", *trein),
                           "correta + z-score do treino")))
    print("\n".join(avalia(zscore(treino, "valor_monetario", *glob),
                           zscore(teste, "valor_monetario", *glob),
                           "correta + z-score da base inteira")))
    print(f"\nmédia/desvio de valor_monetario: base inteira = {glob[0]:.2f}/{glob[1]:.2f}; "
          f"só treino = {trein[0]:.2f}/{trein[1]:.2f}")

    print(f"\npartição: {len(treino)} treino, {len(teste)} teste; "
          f"positivos no teste = {teste.Y.mean():.4f}")
    mesmo_teste = np.array_equal(tabelas["correta"][2].metas[:, 0],
                                 tabelas["vazamento"][2].metas[:, 0])
    print(f"mesmo conjunto de teste nas duas versões: {mesmo_teste}")
    assert mesmo_teste, "as duas versões precisam ser avaliadas nos mesmos clientes"

    print("\nAUC de cada atributo sozinho, no teste:")
    for versao, (_, _, teste) in tabelas.items():
        print(f"  {versao}: {auc_univariada(teste)}")
