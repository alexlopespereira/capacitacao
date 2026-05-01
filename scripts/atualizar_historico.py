"""Atualiza o historico mensal de matriculas concluidas para os 35 cursos alvo.

Fluxo:
1. Baixa o tar.gz publico da ENAP (ultimos 12 meses) ou usa --source local.
2. Itera CSVs mensais (formato YYYY_MM_*.csv), agrega por (ano_mes, nome_curso)
   contando sit_matricula='Concluida' filtrado pelos 35 cursos alvo.
3. Restringe ao intervalo [2024-10, ultimo_mes_completo].
4. Faz merge idempotente em docs/contagem_mensal.csv (substitui meses
   recalculados, preserva meses fora da janela do download).
5. Regenera docs/index.html com tabela pivot e total por mes.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import tarfile
import unicodedata
from datetime import date
from html import escape
from pathlib import Path
from typing import Iterator
from urllib.request import urlopen

import pandas as pd

URL_DEFAULT = (
    "https://dadosaberto.evg.gov.br/ultimos_dozemeses/"
    "escolavirtual_dadosabertos_matriculas_ultimos_dozemeses_utf8.tar.gz"
)
INICIO_HISTORICO = "2024-08"

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
HISTORICO_CSV = DOCS_DIR / "contagem_mensal.csv"
INDEX_HTML = DOCS_DIR / "index.html"
CURSOS_ALVO = Path(__file__).parent / "cursos_alvo.csv"

DASH_CHARS = "‐‑‒–—―−"
ANO_MES_RE = re.compile(r"(\d{4})_(\d{2})_escolavirtual")


def normalize(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = text.strip().strip('"').strip("'")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    for ch in DASH_CHARS:
        text = text.replace(ch, "-")
    text = text.lower()
    for sep in ("-", ":", ",", ";", "?"):
        text = text.replace(sep, " ")
    return " ".join(text.split())


def carregar_alvos() -> tuple[pd.DataFrame, dict[str, tuple[int, str]]]:
    df = pd.read_csv(CURSOS_ALVO)
    df["nome_norm"] = df["tx_nome_curso"].map(normalize)
    mapa = {
        row.nome_norm: (int(row.id_curso), row.tx_nome_curso)
        for row in df.itertuples(index=False)
    }
    return df, mapa


def ultimo_mes_completo() -> str:
    hoje = date.today()
    if hoje.month == 1:
        return f"{hoje.year - 1}-12"
    return f"{hoje.year}-{hoje.month - 1:02d}"


def iter_csvs(source: Path | str) -> Iterator[tuple[str, io.TextIOBase]]:
    """Produz (nome_logico, stream_texto) para cada CSV em source.

    source pode ser:
    - URL http(s) apontando para um tar.gz
    - caminho local de tar.gz
    - diretorio local com tar.gz/CSVs
    """
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        print(f"Baixando {source}...", flush=True)
        with urlopen(source) as resp:
            data = resp.read()
        print(f"Download: {len(data) / 1024 / 1024:.1f} MB", flush=True)
        yield from _iter_targz(io.BytesIO(data), label="ENAP")
        return

    p = Path(source)
    if p.is_dir():
        # Dedupe: se existe X.tar.gz e X.csv (extraido) na mesma pasta,
        # processa apenas o tar.gz para evitar contagem dupla.
        children = sorted(p.iterdir())
        stems_targz = {
            c.name.removesuffix(".tar.gz")
            for c in children
            if c.name.endswith(".tar.gz")
        }
        for child in children:
            if child.suffix == ".csv" and child.stem in stems_targz:
                print(f"Pulando CSV extraido (ja coberto pelo tar.gz): {child.name}")
                continue
            yield from iter_csvs(child)
        return

    if p.suffix == ".csv":
        yield p.name, p.open(encoding="utf-8", newline="")
        return

    if p.name.endswith(".tar.gz") or p.suffix in (".tgz", ".gz"):
        yield from _iter_targz(p.open("rb"), label=p.name)
        return


def _iter_targz(
    fileobj, label: str
) -> Iterator[tuple[str, io.TextIOBase]]:
    with tarfile.open(fileobj=fileobj, mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            if member.name.endswith(".csv"):
                yield member.name, io.TextIOWrapper(
                    extracted, encoding="utf-8", newline=""
                )
            elif member.name.endswith(".tar.gz") or member.name.endswith(".tgz"):
                inner_bytes = io.BytesIO(extracted.read())
                yield from _iter_targz(inner_bytes, label=f"{label}::{member.name}")


def extrair_ano_mes(nome_logico: str) -> str | None:
    m = ANO_MES_RE.search(nome_logico)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def processar_csv(
    stream: io.TextIOBase, mapa_alvos: dict[str, tuple[int, str]]
) -> dict[tuple[str, int, str], int]:
    """Conta matriculas Concluidas por (ano_mes, id_curso, nome_canonico)."""
    csv.field_size_limit(sys.maxsize)
    reader = csv.DictReader(stream, delimiter="|")
    contagem: dict[tuple[str, int, str], int] = {}
    for row in reader:
        if (row.get("sit_matricula") or "").strip() != "Concluida":
            continue
        nome = (row.get("nome_curso") or "").strip()
        nn = normalize(nome)
        alvo = mapa_alvos.get(nn)
        if alvo is None:
            continue
        dt = (row.get("dt_matricula") or "")[:7]
        if not dt or len(dt) != 7 or dt[4] != "-":
            continue
        chave = (dt, alvo[0], alvo[1])
        contagem[chave] = contagem.get(chave, 0) + 1
    return contagem


def consolidar(
    source: str | Path,
    janela_max: str,
    mapa_alvos: dict[str, tuple[int, str]],
) -> pd.DataFrame:
    """Retorna DF com colunas: ano_mes, id_curso, tx_nome_curso, count."""
    contagem_total: dict[tuple[str, int, str], int] = {}
    for nome_logico, stream in iter_csvs(source):
        ym_arquivo = extrair_ano_mes(nome_logico)
        if ym_arquivo is not None and (
            ym_arquivo < INICIO_HISTORICO or ym_arquivo > janela_max
        ):
            print(f"Pulando {nome_logico} (fora da janela)", flush=True)
            stream.close()
            continue
        print(f"Processando {nome_logico}", flush=True)
        parcial = processar_csv(stream, mapa_alvos)
        for k, v in parcial.items():
            contagem_total[k] = contagem_total.get(k, 0) + v
        stream.close()

    rows = [
        {
            "ano_mes": ym,
            "id_curso": cid,
            "tx_nome_curso": nome,
            "count": n,
        }
        for (ym, cid, nome), n in contagem_total.items()
    ]
    df = pd.DataFrame(rows, columns=["ano_mes", "id_curso", "tx_nome_curso", "count"])
    if df.empty:
        return df
    df = df[
        (df["ano_mes"] >= INICIO_HISTORICO) & (df["ano_mes"] <= janela_max)
    ].copy()
    return df.sort_values(["ano_mes", "id_curso"]).reset_index(drop=True)


def merge_historico(novo: pd.DataFrame) -> pd.DataFrame:
    if HISTORICO_CSV.exists():
        antigo = pd.read_csv(HISTORICO_CSV)
    else:
        antigo = pd.DataFrame(
            columns=["ano_mes", "id_curso", "tx_nome_curso", "count"]
        )

    if novo.empty:
        return antigo.sort_values(["ano_mes", "id_curso"]).reset_index(drop=True)

    meses_novos = set(novo["ano_mes"].unique())
    antigo_filtrado = antigo[~antigo["ano_mes"].isin(meses_novos)]
    final = pd.concat([antigo_filtrado, novo], ignore_index=True)
    final = final.sort_values(["ano_mes", "id_curso"]).reset_index(drop=True)
    return final


def gerar_html(historico: pd.DataFrame) -> str:
    if historico.empty:
        tabela_total = "<p>Sem dados ainda.</p>"
        tabela_pivot = ""
    else:
        total_mes = (
            historico.groupby("ano_mes")["count"].sum().reset_index()
            .rename(columns={"count": "total"})
        )
        tabela_total = _df_to_html(total_mes)

        pivot = (
            historico.pivot_table(
                index=["id_curso", "tx_nome_curso"],
                columns="ano_mes",
                values="count",
                aggfunc="sum",
                fill_value=0,
            )
            .astype(int)
            .reset_index()
            .sort_values("id_curso")
        )
        pivot.columns.name = None
        tabela_pivot = _df_to_html(pivot)

    atualizado_em = pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Matriculas concluidas — ENAP (cursos alvo)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 1400px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #666; margin-bottom: 1.5rem; font-size: 0.9rem; }}
  table {{ border-collapse: collapse; margin: 1rem 0; font-size: 0.85rem; }}
  th, td {{ border: 1px solid #ddd; padding: 4px 8px; text-align: right; }}
  th {{ background: #f4f4f4; position: sticky; top: 0; }}
  td:nth-child(-n+2), th:nth-child(-n+2) {{ text-align: left; }}
  .scroll {{ overflow-x: auto; }}
  a {{ color: #0366d6; }}
</style>
</head>
<body>
<h1>Matriculas concluidas — ENAP (35 cursos alvo)</h1>
<p class="meta">Fonte: <a href="https://dadosaberto.evg.gov.br/">dadosaberto.evg.gov.br</a> ·
Atualizado em {atualizado_em} ·
<a href="contagem_mensal.csv">Baixar CSV</a></p>

<h2>Total mensal (somatorio dos 35 cursos)</h2>
<div class="scroll">{tabela_total}</div>

<h2>Detalhe por curso × mes</h2>
<div class="scroll">{tabela_pivot}</div>

<p class="meta">Filtros: <code>sit_matricula = 'Concluida'</code>,
<code>dt_matricula</code> em [2024-10, mes anterior ao corrente].
Junção por <code>nome_curso</code> normalizado.</p>
</body>
</html>
"""


def _df_to_html(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    head = "".join(f"<th>{escape(str(c))}</th>" for c in cols)
    body_rows = []
    for row in df.itertuples(index=False, name=None):
        cells = "".join(f"<td>{escape(str(v))}</td>" for v in row)
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        f"<table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=URL_DEFAULT,
        help="URL ou caminho local (tar.gz, CSV ou diretorio).",
    )
    parser.add_argument(
        "--ate",
        default=None,
        help="Ultimo ano-mes a processar (YYYY-MM). Default: mes anterior ao atual.",
    )
    args = parser.parse_args()

    janela_max = args.ate or ultimo_mes_completo()
    print(f"Janela: {INICIO_HISTORICO} ate {janela_max}")

    _, mapa_alvos = carregar_alvos()
    print(f"Cursos alvo: {len(mapa_alvos)}")

    novo = consolidar(args.source, janela_max, mapa_alvos)
    print(
        f"Novos meses processados: {sorted(novo['ano_mes'].unique()) if not novo.empty else '[]'}"
    )

    final = merge_historico(novo)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    final.to_csv(HISTORICO_CSV, index=False)
    INDEX_HTML.write_text(gerar_html(final), encoding="utf-8")

    print(f"\nLinhas no historico: {len(final)}")
    print(f"Meses cobertos: {sorted(final['ano_mes'].unique()) if not final.empty else '[]'}")
    print(f"Arquivos: {HISTORICO_CSV}, {INDEX_HTML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
