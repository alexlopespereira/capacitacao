"""Pre-computa a distribuicao de cursos-por-pessoa em cada publico-alvo.

Para cada publico P (conjunto de cursos S_P) e cada pessoa que concluiu ao
menos 1 curso de P, conta quantos cursos DISTINTOS de P ela concluiu (k).
A saida agrega isso num histograma: quantas pessoas tem cada valor de k.

Entrada:
  - docs/dashboard_base.csv          (fato: 1 linha por matricula concluida)
  - docs/dashboard_publico_alvo.csv  (ponte curso -> publico)

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

O bucket "(sem publico)" e ignorado (nao e um publico real).
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
FATO_CSV = REPO_ROOT / "docs" / "dashboard_base.csv"
PONTE_CSV = REPO_ROOT / "docs" / "dashboard_publico_alvo.csv"
OUT_CSV = REPO_ROOT / "docs" / "dashboard_dist_publico.csv"

SEM_PUBLICO = "(sem publico)"


def main() -> int:
    # Ponte: publico -> conjunto de id_curso.
    pub_cursos: dict[str, set[int]] = defaultdict(set)
    with open(PONTE_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["publico_alvo"] != SEM_PUBLICO:
                pub_cursos[r["publico_alvo"]].add(int(r["id_curso"]))

    # Fato -> pessoa: conjunto de cursos distintos concluidos.
    fato = pd.read_csv(FATO_CSV, usecols=["id_curso", "codigo_pessoa"])
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
            f, fieldnames=["publico_alvo", "qtd_cursos", "qtd_pessoas", "n_cursos_publico"]
        )
        w.writeheader()
        w.writerows(linhas)

    print(f"Escrito {OUT_CSV.relative_to(REPO_ROOT)}")
    print(f"  · {len(linhas)} linhas (publico x k), {len(pub_cursos)} publicos")
    for publico, S in pub_cursos.items():
        ge1 = sum(r["qtd_pessoas"] for r in linhas if r["publico_alvo"] == publico)
        todos = sum(r["qtd_pessoas"] for r in linhas
                    if r["publico_alvo"] == publico and r["qtd_cursos"] == len(S))
        print(f"  · {publico:22} {len(S):>2} cursos | >=1: {ge1:>7} | todos: {todos:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
