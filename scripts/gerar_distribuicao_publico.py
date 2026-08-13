"""Pre-computa a distribuicao de cursos-por-pessoa em cada publico-alvo.

Para cada publico P (conjunto de cursos S_P) e cada pessoa que concluiu ao
menos 1 curso de P, conta quantos cursos DISTINTOS de P ela concluiu (k).
A saida agrega isso num histograma: quantas pessoas tem cada valor de k.

Entrada:
  - docs/dashboard_base.csv          (fato: 1 linha por matricula concluida)
  - docs/dashboard_publico_alvo.csv  (ponte curso -> publico)
  - scripts/cursos_alvo.csv          (categoria por curso -> escopo do painel)

Saida:
  - docs/dashboard_dist_publico.csv
    colunas: publico_alvo, qtd_cursos, qtd_pessoas, n_cursos_publico
      qtd_cursos        = k (nº de cursos distintos do publico concluidos)
      qtd_pessoas       = nº de pessoas com exatamente k cursos do publico
      n_cursos_publico  = total de cursos do publico (constante por publico)

Derivacoes no modelo (medidas DAX sobre esta tabela):
  - "fizeram >= 1 curso" = SUM(qtd_pessoas) no contexto do publico
  - "fizeram todos"      = SUM(qtd_pessoas) onde qtd_cursos = n_cursos_publico
  - media de cursos/pessoa, % concluiu tudo, etc.

ESCOPO — a tabela e pre-agregada no grao (publico, k) e nao tem chave de
curso, entao nenhum relacionamento consegue empurrar o filtro de categoria do
relatorio para dentro dela sem mudar o k de cada pessoa. O recorte tem que ser
aplicado aqui, onde o k e calculado: contamos apenas os cursos das categorias
do painel (IA e Dados), o mesmo conjunto que o resto do painel conta. Sem
isso, a pagina de distribuicao contaria sobre os 35 cursos da meta enquanto as
demais paginas contam sobre os cursos de IA/Dados.

O balde "(fora de programa)" (curso da meta que nao pertence a nenhum
programa, e portanto a nenhum publico) e mantido: se um curso do painel ficar
sem programa, ele vira uma fatia propria em vez de sumir da distribuicao.

Uma pessoa entra em cada publico que contenha um curso que ela concluiu, entao
a soma das fatias e MAIOR que o numero de pessoas do painel. Por isso o painel
nao exibe soma de fatias nem percentual do total.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
FATO_CSV = REPO_ROOT / "docs" / "dashboard_base.csv"
PONTE_CSV = REPO_ROOT / "docs" / "dashboard_publico_alvo.csv"
SRC_ALVO = Path(__file__).resolve().parent / "cursos_alvo.csv"
OUT_CSV = REPO_ROOT / "docs" / "dashboard_dist_publico.csv"

# Mesmo recorte de gerar_base_dashboard.CATEGORIAS_PAINEL e do filtro de nivel
# de relatorio do PBIP (categoria IN {"IA","Dados"}).
CATEGORIAS_PAINEL = {"IA", "Dados"}
FORA_DE_PROGRAMA = "(fora de programa)"


def main() -> int:
    # Escopo do painel: ids de curso das categorias IA/Dados.
    with open(SRC_ALVO, encoding="utf-8") as f:
        cursos_painel = {
            int(r["id_curso"]) for r in csv.DictReader(f)
            if r["categoria"] in CATEGORIAS_PAINEL
        }

    # Ponte: publico -> conjunto de id_curso, ja restrito ao escopo do painel.
    pub_cursos: dict[str, set[int]] = defaultdict(set)
    with open(PONTE_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            cid = int(r["id_curso"])
            if cid in cursos_painel:
                pub_cursos[r["publico_alvo"]].add(cid)

    # Publico cujos cursos ficaram todos fora do painel some da distribuicao —
    # e o que faz esta pagina passar a bater com as demais.
    pub_cursos = {p: S for p, S in pub_cursos.items() if S}

    # Fato -> pessoa: conjunto de cursos distintos concluidos (dentro do painel).
    fato = pd.read_csv(FATO_CSV, usecols=["id_curso", "codigo_pessoa", "categoria"])
    fato = fato[fato["categoria"].isin(CATEGORIAS_PAINEL)]
    pares = fato.drop_duplicates(["codigo_pessoa", "id_curso"])
    pessoa_cursos = pares.groupby("codigo_pessoa")["id_curso"].apply(set)

    linhas: list[dict[str, object]] = []
    for publico, S in pub_cursos.items():
        n = len(S)
        dist: Counter[int] = Counter()
        for cset in pessoa_cursos:
            k = len(cset & S)
            if k >= 1:
                dist[k] += 1
        for k in sorted(dist):
            linhas.append({
                "publico_alvo": publico,
                "qtd_cursos": k,
                "qtd_pessoas": dist[k],
                "n_cursos_publico": n,
            })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["publico_alvo", "qtd_cursos", "qtd_pessoas", "n_cursos_publico"],
            lineterminator="\n",
        )
        w.writeheader()
        w.writerows(linhas)

    print(f"Escrito {OUT_CSV.relative_to(REPO_ROOT)}")
    print(f"  · escopo do painel: {len(cursos_painel)} cursos de {sorted(CATEGORIAS_PAINEL)}")
    print(f"  · {len(linhas)} linhas (publico x k), {len(pub_cursos)} publicos")
    for publico, S in pub_cursos.items():
        ge1 = sum(r["qtd_pessoas"] for r in linhas if r["publico_alvo"] == publico)
        todos = sum(r["qtd_pessoas"] for r in linhas
                    if r["publico_alvo"] == publico and r["qtd_cursos"] == len(S))
        print(f"  · {publico:22} {len(S):>2} cursos | >=1: {ge1:>7} | todos: {todos:>5}")
    if FORA_DE_PROGRAMA not in pub_cursos:
        print(f"  · nenhum curso do painel esta {FORA_DE_PROGRAMA} — balde vazio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
