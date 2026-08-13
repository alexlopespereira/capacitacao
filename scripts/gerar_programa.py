"""Gera a dimensao Programa: tabela-ponte curso->programa e a distribuicao
de cursos-por-pessoa por programa.

Um curso pode pertencer a varios programas (m:n), inclusive a mais de um
programa do mesmo publico. A fonte curada e scripts/cursos_programa.csv
(extraida da aba "Desnormalizado" do xlsx de trilhas).

Entradas:
  - scripts/cursos_programa.csv  (id_curso, tx_nome_curso, programa, publico_alvo)
  - scripts/cursos_alvo.csv      (35 cursos da meta + categoria por curso)
  - docs/dashboard_base.csv      (fato: 1 linha por matricula concluida)

Saidas:
  - docs/dashboard_programa.csv       (ponte: id_curso, nome_curso, programa, publico_alvo)
  - docs/dashboard_dist_programa.csv  (programa, qtd_cursos, qtd_pessoas, n_cursos_programa)

ESCOPO da distribuicao — a ponte cobre os 35 cursos da meta (o filtro de
categoria do relatorio a alcanca via dim_curso), mas dashboard_dist_programa
e pre-agregada no grao (programa, k), sem chave de curso: nenhum
relacionamento consegue empurrar aquele filtro para dentro dela sem mudar o k
de cada pessoa. Por isso o recorte e aplicado aqui, onde o k e calculado — so
os cursos das categorias do painel (IA e Dados) contam, o mesmo conjunto que
o resto do painel. Efeito colateral desejado: programa cujos cursos sao todos
de outra categoria deixa de aparecer numa pagina e faltar na outra.

O balde "(fora de programa)" (curso da meta que nao pertence a nenhum
programa) entra tanto na ponte quanto na distribuicao: se um curso do painel
ficar sem programa, ele vira uma fatia propria em vez de sumir.

Uma pessoa entra em cada programa que contenha um curso que ela concluiu,
entao a soma das fatias e MAIOR que o numero de pessoas do painel. Por isso o
painel nao exibe soma de fatias nem percentual do total.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PROG = Path(__file__).resolve().parent / "cursos_programa.csv"
SRC_ALVO = Path(__file__).resolve().parent / "cursos_alvo.csv"
FATO_CSV = REPO_ROOT / "docs" / "dashboard_base.csv"
OUT_BRIDGE = REPO_ROOT / "docs" / "dashboard_programa.csv"
OUT_DIST = REPO_ROOT / "docs" / "dashboard_dist_programa.csv"

# Mesmo recorte de gerar_base_dashboard.CATEGORIAS_PAINEL e do filtro de nivel
# de relatorio do PBIP (categoria IN {"IA","Dados"}).
CATEGORIAS_PAINEL = {"IA", "Dados"}
FORA_DE_PROGRAMA = "(fora de programa)"


def main() -> int:
    with open(SRC_PROG, encoding="utf-8") as f:
        curado = list(csv.DictReader(f))
    with open(SRC_ALVO, encoding="utf-8") as f:
        alvo = {r["id_curso"]: (r["tx_nome_curso"], r["categoria"])
                for r in csv.DictReader(f)}
    cursos_painel = {int(cid) for cid, (_, cat) in alvo.items()
                     if cat in CATEGORIAS_PAINEL}

    # --- Ponte (todos os 35 cursos da meta; o filtro do relatorio a alcanca
    # via dim_curso) ---
    bridge: list[dict[str, str]] = []
    mapeados: set[str] = set()
    prog_cursos: dict[str, set[int]] = defaultdict(set)
    for r in curado:
        bridge.append({
            "id_curso": r["id_curso"],
            "nome_curso": r["tx_nome_curso"],
            "programa": r["programa"],
            "publico_alvo": r["publico_alvo"],
        })
        mapeados.add(r["id_curso"])
        prog_cursos[r["programa"]].add(int(r["id_curso"]))

    for cid, (nome, _cat) in alvo.items():
        if cid not in mapeados:
            bridge.append({
                "id_curso": cid,
                "nome_curso": nome,
                "programa": FORA_DE_PROGRAMA,
                "publico_alvo": FORA_DE_PROGRAMA,
            })
            prog_cursos[FORA_DE_PROGRAMA].add(int(cid))

    bridge.sort(key=lambda r: (r["publico_alvo"], r["programa"], int(r["id_curso"])))
    with open(OUT_BRIDGE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id_curso", "nome_curso", "programa", "publico_alvo"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(bridge)

    # --- Distribuicao (pessoas por nº de cursos do programa), no escopo do painel ---
    prog_cursos = {p: (S & cursos_painel) for p, S in prog_cursos.items()}
    vazios = sorted(p for p, S in prog_cursos.items() if not S)
    prog_cursos = {p: S for p, S in prog_cursos.items() if S}

    fato = pd.read_csv(FATO_CSV, usecols=["id_curso", "codigo_pessoa", "categoria"])
    fato = fato[fato["categoria"].isin(CATEGORIAS_PAINEL)]
    pares = fato.drop_duplicates(["codigo_pessoa", "id_curso"])
    pessoa_cursos = pares.groupby("codigo_pessoa")["id_curso"].apply(set)

    dist_rows: list[dict[str, object]] = []
    for programa, S in prog_cursos.items():
        n = len(S)
        dist: Counter[int] = Counter()
        for cset in pessoa_cursos:
            k = len(cset & S)
            if k >= 1:
                dist[k] += 1
        for k in sorted(dist):
            dist_rows.append({
                "programa": programa,
                "qtd_cursos": k,
                "qtd_pessoas": dist[k],
                "n_cursos_programa": n,
            })

    with open(OUT_DIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["programa", "qtd_cursos", "qtd_pessoas", "n_cursos_programa"],
            lineterminator="\n",
        )
        w.writeheader()
        w.writerows(dist_rows)

    print(f"Escrito {OUT_BRIDGE.relative_to(REPO_ROOT)} ({len(bridge)} linhas)")
    print(f"Escrito {OUT_DIST.relative_to(REPO_ROOT)} ({len(dist_rows)} linhas)")
    print(f"  · escopo do painel: {len(cursos_painel)} cursos de {sorted(CATEGORIAS_PAINEL)}")
    print(f"  · {len(prog_cursos)} programas na distribuicao")
    for programa, S in sorted(prog_cursos.items()):
        ge1 = sum(r["qtd_pessoas"] for r in dist_rows if r["programa"] == programa)
        todos = sum(r["qtd_pessoas"] for r in dist_rows
                    if r["programa"] == programa and r["qtd_cursos"] == len(S))
        print(f"  · {programa[:48]:48} {len(S):>2}c | >=1: {ge1:>7} | todos: {todos:>6}")
    for programa in vazios:
        print(f"  · {programa[:48]:48} sem curso no escopo do painel — fora da distribuicao")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
