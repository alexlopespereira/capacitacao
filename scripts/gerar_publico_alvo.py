"""Gera a tabela-ponte curso -> publico-alvo para o dashboard.

Entrada (fonte da verdade, curada e versionada):
  - scripts/cursos_publico_alvo.csv  (id_curso, tx_nome_curso, publico_alvo, programa_trilha)
  - scripts/cursos_alvo.csv          (35 cursos da meta — para detectar nao-mapeados)

Saida:
  - docs/dashboard_publico_alvo.csv  (id_curso, nome_curso, publico_alvo, programa_trilha)

A relacao curso<->publico e muitos-para-muitos: um curso pode pertencer a
varios publicos (ex.: "Etica em IA" aparece nos 6 publicos). No PBIP esta
tabela e carregada como bridge entre dim_curso e a dimensao Publico-alvo.
Por isso a soma das barras por publico NAO fecha com o total do painel: a
mesma pessoa entra em cada publico que contenha um curso que ela concluiu.

Cursos da meta sem publico mapeado recebem o rotulo "(fora de programa)" —
publico-alvo e um rotulo herdado do programa, entao curso sem programa nao
tem publico. O balde existe para que esses cursos aparecam na dimensao em
vez de sumirem dela (curso 724, p.ex.).
"""

from __future__ import annotations

import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_BRIDGE = Path(__file__).resolve().parent / "cursos_publico_alvo.csv"
SRC_ALVO = Path(__file__).resolve().parent / "cursos_alvo.csv"
OUT_CSV = REPO_ROOT / "docs" / "dashboard_publico_alvo.csv"

# Rotulo unico para curso que nao pertence a nenhum programa (e portanto a
# nenhum publico). Compartilhado com gerar_programa.py e
# gerar_distribuicao_publico.py: as tres saidas usam a mesma palavra.
FORA_DE_PROGRAMA = "(fora de programa)"


def main() -> int:
    with open(SRC_BRIDGE, encoding="utf-8") as f:
        bridge = list(csv.DictReader(f))
    with open(SRC_ALVO, encoding="utf-8") as f:
        alvo = {r["id_curso"]: r["tx_nome_curso"] for r in csv.DictReader(f)}

    rows: list[dict[str, str]] = []
    mapeados: set[str] = set()
    for r in bridge:
        rows.append({
            "id_curso": r["id_curso"],
            "nome_curso": r["tx_nome_curso"],
            "publico_alvo": r["publico_alvo"],
            "programa_trilha": r["programa_trilha"],
        })
        mapeados.add(r["id_curso"])

    # Cursos da meta sem nenhum publico -> balde "(fora de programa)".
    nao_mapeados = [cid for cid in alvo if cid not in mapeados]
    for cid in nao_mapeados:
        rows.append({
            "id_curso": cid,
            "nome_curso": alvo[cid],
            "publico_alvo": FORA_DE_PROGRAMA,
            "programa_trilha": FORA_DE_PROGRAMA,
        })

    rows.sort(key=lambda r: (r["publico_alvo"], int(r["id_curso"])))

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["id_curso", "nome_curso", "publico_alvo", "programa_trilha"],
            lineterminator="\n",
        )
        w.writeheader()
        w.writerows(rows)

    publicos = sorted({r["publico_alvo"] for r in rows})
    trilhas = sorted({r["programa_trilha"] for r in rows})
    print(f"Escrito {OUT_CSV.relative_to(REPO_ROOT)}")
    print(f"  · {len(rows)} pares (curso, publico)")
    print(f"  · {len(mapeados)} cursos mapeados + {len(nao_mapeados)} fora de programa")
    print(f"  · {len(publicos)} publicos, {len(trilhas)} trilhas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
