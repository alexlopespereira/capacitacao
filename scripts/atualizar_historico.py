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
PESSOAS_CSV = DOCS_DIR / "pessoas_por_mes.csv"
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


def _set_csv_field_size_limit() -> None:
    """Maior field_size_limit suportado pela plataforma.

    No Python do Windows o C long e 32-bit, entao sys.maxsize (2^63-1)
    estoura em csv.field_size_limit. Reduz ate caber.
    """
    limite = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limite)
            return
        except OverflowError:
            limite //= 10


def processar_csv(
    stream: io.TextIOBase, mapa_alvos: dict[str, tuple[int, str]]
) -> tuple[dict[tuple[str, int, str], int], set[tuple[str, str]]]:
    """Conta matriculas e coleta pessoas distintas (Concluida + Federal + 35 cursos).

    Retorna:
    - contagem: {(ano_mes, id_curso, nome): n_matriculas}
    - pessoas: set[(ano_mes, codigo_pessoa)]
    """
    _set_csv_field_size_limit()
    reader = csv.DictReader(stream, delimiter="|")
    contagem: dict[tuple[str, int, str], int] = {}
    pessoas: set[tuple[str, str]] = set()
    for row in reader:
        if (row.get("sit_matricula") or "").strip() != "Concluida":
            continue
        if (row.get("esfera") or "").strip() != "Federal":
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
        cp = (row.get("codigo_pessoa") or "").strip()
        if cp:
            pessoas.add((dt, cp))
    return contagem, pessoas


def consolidar(
    source: str | Path,
    janela_max: str,
    mapa_alvos: dict[str, tuple[int, str]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Retorna (df_contagem, df_pessoas).

    - df_contagem: ano_mes, id_curso, tx_nome_curso, count
    - df_pessoas:  ano_mes, codigo_pessoa  (long format, deduplicado)
    """
    contagem_total: dict[tuple[str, int, str], int] = {}
    pessoas_total: set[tuple[str, str]] = set()
    for nome_logico, stream in iter_csvs(source):
        ym_arquivo = extrair_ano_mes(nome_logico)
        if ym_arquivo is not None and (
            ym_arquivo < INICIO_HISTORICO or ym_arquivo > janela_max
        ):
            print(f"Pulando {nome_logico} (fora da janela)", flush=True)
            stream.close()
            continue
        print(f"Processando {nome_logico}", flush=True)
        parcial_c, parcial_p = processar_csv(stream, mapa_alvos)
        for k, v in parcial_c.items():
            contagem_total[k] = contagem_total.get(k, 0) + v
        pessoas_total.update(parcial_p)
        stream.close()

    rows = [
        {"ano_mes": ym, "id_curso": cid, "tx_nome_curso": nome, "count": n}
        for (ym, cid, nome), n in contagem_total.items()
    ]
    df_c = pd.DataFrame(rows, columns=["ano_mes", "id_curso", "tx_nome_curso", "count"])
    df_p = pd.DataFrame(
        sorted(pessoas_total), columns=["ano_mes", "codigo_pessoa"]
    )
    if not df_c.empty:
        df_c = df_c[
            (df_c["ano_mes"] >= INICIO_HISTORICO) & (df_c["ano_mes"] <= janela_max)
        ].copy()
        df_c = df_c.sort_values(["ano_mes", "id_curso"]).reset_index(drop=True)
    if not df_p.empty:
        df_p = df_p[
            (df_p["ano_mes"] >= INICIO_HISTORICO) & (df_p["ano_mes"] <= janela_max)
        ].copy()
        df_p = df_p.sort_values(["ano_mes", "codigo_pessoa"]).reset_index(drop=True)
    return df_c, df_p


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


def merge_pessoas(novo: pd.DataFrame) -> pd.DataFrame:
    """Merge idempotente do long-format de pessoas.

    Para cada ano_mes presente em `novo`, substitui as linhas correspondentes
    no historico (preserva pessoas dos meses fora da janela do download).
    """
    if PESSOAS_CSV.exists():
        antigo = pd.read_csv(PESSOAS_CSV, dtype={"codigo_pessoa": str})
    else:
        antigo = pd.DataFrame(columns=["ano_mes", "codigo_pessoa"])

    if novo.empty:
        return antigo.sort_values(["ano_mes", "codigo_pessoa"]).reset_index(drop=True)

    meses_novos = set(novo["ano_mes"].unique())
    antigo_filtrado = antigo[~antigo["ano_mes"].isin(meses_novos)]
    final = pd.concat([antigo_filtrado, novo], ignore_index=True)
    final = final.drop_duplicates(["ano_mes", "codigo_pessoa"]).reset_index(drop=True)
    return final.sort_values(["ano_mes", "codigo_pessoa"]).reset_index(drop=True)


def calcular_pessoas_acumulado(pessoas: pd.DataFrame) -> pd.DataFrame:
    """A partir do long-format pessoas, computa por mes:
    - novas_pessoas: codigo_pessoa que NAO apareceu em nenhum mes anterior
    - acumulado: total de codigo_pessoa distintos ate o mes (inclusive)
    """
    if pessoas.empty:
        return pd.DataFrame(columns=["ano_mes", "novas_pessoas", "acumulado"])
    visto: set[str] = set()
    rows = []
    for ym in sorted(pessoas["ano_mes"].unique()):
        do_mes = set(pessoas.loc[pessoas["ano_mes"] == ym, "codigo_pessoa"])
        novas = do_mes - visto
        visto.update(do_mes)
        rows.append({"ano_mes": ym, "novas_pessoas": len(novas), "acumulado": len(visto)})
    return pd.DataFrame(rows)


def gerar_html(historico: pd.DataFrame, pessoas_acum: pd.DataFrame) -> str:
    if historico.empty:
        tabela_total = "<p>Sem dados ainda.</p>"
        tabela_pivot = ""
        bloco_pessoas = ""
        destaque_pessoas = ""
    else:
        total_mes = (
            historico.groupby("ano_mes")["count"].sum().reset_index()
            .rename(columns={"count": "matriculas"})
        )
        if not pessoas_acum.empty:
            total_mes = total_mes.merge(pessoas_acum, on="ano_mes", how="left")
            total_mes[["novas_pessoas", "acumulado"]] = (
                total_mes[["novas_pessoas", "acumulado"]].fillna(0).astype(int)
            )
            total_mes = total_mes.rename(
                columns={"acumulado": "pessoas_unicas_acum"}
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

        if pessoas_acum.empty:
            destaque_pessoas = ""
        else:
            total_unico = int(pessoas_acum["acumulado"].iloc[-1])
            destaque_pessoas = (
                f"<p><strong>Pessoas únicas (servidores federais distintos) que "
                f"concluíram pelo menos um dos 35 cursos no período: "
                f"{total_unico:,}</strong></p>"
            ).replace(",", ".")

    atualizado_em = pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Matriculas concluidas — ENAP (servidores federais, 35 cursos alvo)</title>
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
<h1>Matriculas concluidas — ENAP (servidores federais, 35 cursos alvo)</h1>
<p class="meta">Fonte: <a href="https://dadosaberto.evg.gov.br/">dadosaberto.evg.gov.br</a> ·
Atualizado em {atualizado_em} ·
<a href="contagem_mensal.csv">Baixar CSV (matrículas)</a> ·
<a href="pessoas_por_mes.csv">Baixar CSV (pessoas)</a></p>

{destaque_pessoas}

<h2>Total mensal (35 cursos)</h2>
<div class="scroll">{tabela_total}</div>

<h2>Detalhe por curso × mes (matrículas)</h2>
<div class="scroll">{tabela_pivot}</div>

<p class="meta">Filtros aplicados: <code>sit_matricula = 'Concluida'</code> ·
<code>esfera = 'Federal'</code> (descarta Estadual, Municipal e nao-servidores) ·
<code>dt_matricula</code> em [2024-08, mes anterior ao corrente] ·
junção por <code>nome_curso</code> normalizado.</p>
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

    novo_c, novo_p = consolidar(args.source, janela_max, mapa_alvos)
    print(
        f"Novos meses (matriculas): {sorted(novo_c['ano_mes'].unique()) if not novo_c.empty else '[]'}"
    )
    print(
        f"Novos meses (pessoas): {sorted(novo_p['ano_mes'].unique()) if not novo_p.empty else '[]'}"
    )

    final_c = merge_historico(novo_c)
    final_p = merge_pessoas(novo_p)
    pessoas_acum = calcular_pessoas_acumulado(final_p)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    final_c.to_csv(HISTORICO_CSV, index=False)
    final_p.to_csv(PESSOAS_CSV, index=False)
    INDEX_HTML.write_text(gerar_html(final_c, pessoas_acum), encoding="utf-8")

    total_pessoas = int(pessoas_acum["acumulado"].iloc[-1]) if not pessoas_acum.empty else 0
    print(f"\nLinhas no historico (matriculas): {len(final_c)}")
    print(f"Linhas no historico (pessoas): {len(final_p)}")
    print(f"Meses cobertos: {sorted(final_c['ano_mes'].unique()) if not final_c.empty else '[]'}")
    print(f"Total acumulado de matriculas: {final_c['count'].sum() if not final_c.empty else 0}")
    print(f"Pessoas unicas no acumulado: {total_pessoas}")
    print(f"Arquivos: {HISTORICO_CSV}, {PESSOAS_CSV}, {INDEX_HTML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
