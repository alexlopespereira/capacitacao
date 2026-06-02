"""Gera pasta PBIP (Power BI Project) que abre no Power BI Desktop sem cliques.

Saída padrão: docs/pbip/capacitacao-ia/ com:
  - capacitacao-ia.pbip              (launcher)
  - capacitacao-ia.SemanticModel/    (TMDL: tabela fato + 5 medidas DAX)
  - capacitacao-ia.Report/           (PBIR JSON: 3 páginas interativas)

Pré-requisitos para abrir no Power BI Desktop:
  - Versão ≥ 2024.05 (qualquer build moderno).
  - Recurso de visualização "Salvar como projeto do Power BI (.pbip)" e
    "Pasta PBIR para relatórios" ativados em File → Options → Preview features.

Uso típico:
    python scripts/gerar_pbip.py --validate

Detalhes em README.md (seção "Power BI Desktop (.pbip)").
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

try:
    from jinja2 import ChainableUndefined, Environment, FileSystemLoader
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "ERRO: jinja2 não instalado. Rode: pip install -r scripts/requirements.txt\n"
    )
    sys.exit(1)


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "pbip_templates"
LINEAGE_FILE = TEMPLATES_DIR / ".lineage.json"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "pbip" / "capacitacao-ia"
DEFAULT_CSV = REPO_ROOT / "docs" / "dashboard_base.csv"
PROJECT_NAME = "capacitacao-ia"
ENTITY = "dashboard_base"

# Identificadores estáveis para lineageTag (TMDL) e logicalId (.platform).
# Persistir em .lineage.json para diffs git limpos entre regenerações.
LINEAGE_KEYS: tuple[str, ...] = (
    "expression_csv_path",
    "table_dashboard_base",
    "measure_matriculas",
    "measure_matriculas_ia",
    "measure_pessoas_unicas",
    "measure_pessoas_unicas_ia",
    "measure_pct_matriculas_ia",
    "column_ano_mes",
    "column_ano",
    "column_mes",
    "column_id_curso",
    "column_nome_curso",
    "column_ia",
    "column_esfera",
    "column_setor",
    "column_poder",
    "column_codigo_pessoa",
    "column_ano_mes_sort",
    "column_ia_label",
    "semantic_model_logical_id",
    "report_logical_id",
)


# ---------------------------------------------------------------------------
# Helpers para construir projections de visuais sem repetição.
# ---------------------------------------------------------------------------

def measure(name: str) -> dict[str, Any]:
    return {
        "kind": "measure",
        "entity": ENTITY,
        "property": name,
        "query_ref": name,
        "native_query_ref": name,
    }


def column(name: str) -> dict[str, Any]:
    return {
        "kind": "column",
        "entity": ENTITY,
        "property": name,
        "query_ref": f"{ENTITY}.{name}",
        "native_query_ref": name,
    }


def kpi_card(name: str, title: str, measure_name: str, x: int, y: int) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "visual_type": "card",
        "position": {"x": x, "y": y, "width": 296, "height": 140},
        "projections": {"Values": [measure(measure_name)]},
    }


def slicer(name: str, title: str, column_name: str, x: int, y: int,
           width: int = 288, height: int = 200) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "visual_type": "slicer",
        "position": {"x": x, "y": y, "width": width, "height": height},
        "projections": {"Values": [column(column_name)]},
    }


# ---------------------------------------------------------------------------
# PAGES_SPEC — fonte única de verdade do layout.
# ---------------------------------------------------------------------------

PAGES_SPEC: list[dict[str, Any]] = [
    {
        "name": "01-visao-geral",
        "display_name": "Visão Geral",
        "width": 1280,
        "height": 720,
        "visuals": [
            kpi_card("kpi-matriculas", "Total matrículas", "Matriculas", x=16, y=16),
            kpi_card("kpi-matriculas-ia", "Matrículas IA", "Matriculas IA", x=328, y=16),
            kpi_card("kpi-pessoas", "Pessoas únicas", "Pessoas Unicas", x=640, y=16),
            kpi_card("kpi-pessoas-ia", "Pessoas únicas IA", "Pessoas Unicas IA", x=952, y=16),
            {
                "name": "linha-temporal",
                "title": "Matrículas por mês (IA vs Não IA)",
                "visual_type": "lineChart",
                "position": {"x": 16, "y": 172, "width": 944, "height": 532},
                "projections": {
                    "Category": [column("ano_mes")],
                    "Series": [column("ia_label")],
                    "Y": [measure("Matriculas")],
                },
            },
            slicer("slicer-periodo", "Período", "ano_mes", x=976, y=172, height=260),
            slicer("slicer-ia", "Tipo de curso", "ia_label", x=976, y=444, height=260),
        ],
    },
    {
        "name": "02-por-grupo",
        "display_name": "Por Grupo",
        "width": 1280,
        "height": 720,
        "visuals": [
            {
                "name": "matriz-esfera-poder",
                "title": "Esfera × Poder",
                "visual_type": "pivotTable",
                "position": {"x": 16, "y": 16, "width": 928, "height": 688},
                "projections": {
                    "Rows": [column("esfera")],
                    "Columns": [column("poder")],
                    "Values": [measure("Matriculas"), measure("Pessoas Unicas")],
                },
            },
            slicer("slicer-ano", "Ano", "ano", x=960, y=16, height=200),
            slicer("slicer-setor", "Setor", "setor", x=960, y=228, height=200),
            slicer("slicer-ia", "Tipo de curso", "ia_label", x=960, y=440, height=200),
        ],
    },
    {
        "name": "03-por-curso",
        "display_name": "Por Curso",
        "width": 1280,
        "height": 720,
        "visuals": [
            {
                "name": "tabela-cursos",
                "title": "Detalhe por curso",
                "visual_type": "tableEx",
                "position": {"x": 16, "y": 80, "width": 640, "height": 624},
                "projections": {
                    "Values": [
                        column("nome_curso"),
                        column("ia_label"),
                        measure("Matriculas"),
                        measure("Pessoas Unicas"),
                        measure("% Matriculas IA"),
                    ],
                },
            },
            {
                "name": "barra-topn-cursos",
                "title": "Top 10 cursos por matrículas",
                "visual_type": "barChart",
                "position": {"x": 672, "y": 80, "width": 592, "height": 624},
                "projections": {
                    "Category": [column("nome_curso")],
                    "Y": [measure("Matriculas")],
                },
            },
            slicer("slicer-ia", "Tipo de curso", "ia_label", x=16, y=16, width=640, height=52),
        ],
    },
]


# ---------------------------------------------------------------------------
# Persistência de UUIDs (lineageTag estável entre runs).
# ---------------------------------------------------------------------------

def load_lineage() -> dict[str, str]:
    if LINEAGE_FILE.exists():
        existing = json.loads(LINEAGE_FILE.read_text(encoding="utf-8"))
    else:
        existing = {}
    changed = False
    for key in LINEAGE_KEYS:
        if key not in existing:
            existing[key] = str(uuid.uuid4())
            changed = True
    if changed:
        LINEAGE_FILE.write_text(
            json.dumps(existing, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"  · lineage UUIDs persistidos em {LINEAGE_FILE.relative_to(REPO_ROOT)}")
    return existing


# ---------------------------------------------------------------------------
# Build do semantic model.
# ---------------------------------------------------------------------------

def render(env: Environment, template_path: str, ctx: dict[str, Any]) -> str:
    tmpl = env.get_template(template_path)
    out = tmpl.render(**ctx)
    if not out.endswith("\n"):
        out += "\n"
    return out


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 sem BOM, LF (Power BI Desktop aceita). `newline=""` em open()
    # impede tradução para CRLF em Windows.
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def resolve_csv_path_literal(csv_path: Path, mode: str) -> str:
    """Produz o literal para a expressão M `CsvPath`.

    relative: caminho relativo ao .pbip (string que o usuário ajusta na 1ª abertura)
    absolute: caminho absoluto resolvido em tempo de geração
    """
    if mode == "absolute":
        # Em Windows, M precisa de backslashes escapados; aqui devolvemos
        # a forma POSIX (Power Query aceita "/" no Windows desde 2021).
        return str(csv_path.resolve()).replace("\\", "/")
    # mode == "relative" — string-pista; usuário ajusta no Power Query Editor
    # na primeira abertura (Power BI Desktop não tem caminho relativo nativo).
    try:
        rel = csv_path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        rel = csv_path
    return str(rel).replace("\\", "/")


def build_semantic_model(env: Environment, out_dir: Path, ctx: dict[str, Any]) -> int:
    sm_root = out_dir / f"{PROJECT_NAME}.SemanticModel"
    definition = sm_root / "definition"
    files_written = 0

    write_file(sm_root / "definition.pbism",
               render(env, "semantic_model/definition.pbism.j2", ctx))
    write_file(sm_root / ".platform",
               render(env, "semantic_model/platform.json.j2", ctx))
    write_file(definition / "database.tmdl",
               render(env, "semantic_model/database.tmdl.j2", ctx))
    write_file(definition / "model.tmdl",
               render(env, "semantic_model/model.tmdl.j2", ctx))
    write_file(definition / "expressions.tmdl",
               render(env, "semantic_model/expressions.tmdl.j2", ctx))
    write_file(definition / "relationships.tmdl",
               render(env, "semantic_model/relationships.tmdl.j2", ctx))
    write_file(definition / "tables" / "dashboard_base.tmdl",
               render(env, "semantic_model/tables/dashboard_base.tmdl.j2", ctx))
    files_written += 7
    return files_written


# ---------------------------------------------------------------------------
# Build do report.
# ---------------------------------------------------------------------------

def build_report(env: Environment, out_dir: Path, ctx: dict[str, Any]) -> int:
    report_root = out_dir / f"{PROJECT_NAME}.Report"
    definition = report_root / "definition"
    files_written = 0

    write_file(report_root / "definition.pbir",
               render(env, "report/definition.pbir.j2", ctx))
    write_file(report_root / ".platform",
               render(env, "report/platform.json.j2", ctx))
    write_file(definition / "version.json",
               render(env, "report/version.json.j2", ctx))
    write_file(definition / "report.json",
               render(env, "report/report.json.j2", ctx))
    write_file(definition / "pages" / "pages.json",
               render(env, "report/pages.json.j2", ctx))
    files_written += 5

    for page in PAGES_SPEC:
        page_dir = definition / "pages" / page["name"]
        write_file(page_dir / "page.json",
                   render(env, "report/page.json.j2", {**ctx, "page": page}))
        files_written += 1
        for visual in page["visuals"]:
            v_dir = page_dir / "visuals" / visual["name"]
            write_file(v_dir / "visual.json",
                       render(env, "report/visual.json.j2", {**ctx, "visual": visual}))
            files_written += 1
    return files_written


# ---------------------------------------------------------------------------
# Validação JSON.
# ---------------------------------------------------------------------------

def validate_json_files(out_dir: Path) -> tuple[int, list[tuple[Path, str]]]:
    ok = 0
    errors: list[tuple[Path, str]] = []
    # .pbip, .pbir, .pbism, .platform e .json são todos JSON.
    patterns = ("*.pbip", "*.pbir", "*.pbism", "*.json")
    targets = [p for pat in patterns for p in out_dir.rglob(pat)]
    targets += list(out_dir.rglob(".platform"))
    for p in targets:
        try:
            json.loads(p.read_text(encoding="utf-8"))
            ok += 1
        except json.JSONDecodeError as e:
            errors.append((p, str(e)))
    return ok, errors


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera pasta PBIP (Power BI Project) para o dashboard de capacitação IA."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"diretório de saída (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--csv-path", type=Path, default=DEFAULT_CSV,
                        help=f"caminho do CSV fato (default: {DEFAULT_CSV})")
    parser.add_argument("--csv-path-mode", choices=["relative", "absolute"],
                        default="relative",
                        help="como embutir o caminho do CSV na expressão M (default: relative)")
    parser.add_argument("--validate", action="store_true",
                        help="valida todos os arquivos JSON gerados")
    parser.add_argument("--force", action="store_true",
                        help="sobrescreve diretório de saída sem perguntar")
    parser.add_argument("--dry-run", action="store_true",
                        help="gera em tempdir, valida JSON e descarta")
    args = parser.parse_args()

    if args.dry_run:
        tmp = Path(tempfile.mkdtemp(prefix="pbip-dryrun-"))
        out_dir = tmp / PROJECT_NAME
        cleanup_tmp = tmp
        args.validate = True
    else:
        out_dir = args.output
        cleanup_tmp = None
        if out_dir.exists():
            if not args.force:
                print(f"ERRO: {out_dir} já existe. Use --force para sobrescrever.",
                      file=sys.stderr)
                return 1
            shutil.rmtree(out_dir)

    print(f"Gerando PBIP em {out_dir}")
    print(f"  · CSV path mode: {args.csv_path_mode}")

    lineage = load_lineage()
    csv_literal = resolve_csv_path_literal(args.csv_path, args.csv_path_mode)
    print(f"  · M expression CsvPath = \"{csv_literal}\"")

    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        keep_trailing_newline=True,
        lstrip_blocks=True,
        trim_blocks=False,
        autoescape=False,
        # ChainableUndefined: visual.optional_field renderiza vazio em vez de
        # explodir — permite que campos opcionais (title, filters) sejam
        # omitidos do PAGES_SPEC sem ramo de código defensivo no template.
        undefined=ChainableUndefined,
    )

    ctx: dict[str, Any] = {
        "project_name": PROJECT_NAME,
        "lineage": lineage,
        "csv_path_m_literal": csv_literal,
        "pages": PAGES_SPEC,
    }

    # Launcher .pbip
    write_file(out_dir / f"{PROJECT_NAME}.pbip",
               render(env, "pbip.json.j2", ctx))

    n_sm = build_semantic_model(env, out_dir, ctx)
    n_rep = build_report(env, out_dir, ctx)
    total = 1 + n_sm + n_rep

    n_pages = len(PAGES_SPEC)
    n_visuals = sum(len(p["visuals"]) for p in PAGES_SPEC)
    print(f"  · {n_pages} páginas, {n_visuals} visuais, {total} arquivos escritos")

    if args.validate:
        ok, errors = validate_json_files(out_dir)
        if errors:
            print(f"\nFALHA na validação JSON ({len(errors)} arquivo(s)):", file=sys.stderr)
            for p, err in errors:
                print(f"  - {p}: {err}", file=sys.stderr)
            if cleanup_tmp:
                shutil.rmtree(cleanup_tmp, ignore_errors=True)
            return 1
        print(f"  · {ok} JSON validados OK")

    if cleanup_tmp:
        shutil.rmtree(cleanup_tmp, ignore_errors=True)
        print("OK (dry-run: arquivos descartados).")
    else:
        rel = out_dir.relative_to(REPO_ROOT) if out_dir.is_relative_to(REPO_ROOT) else out_dir
        print(f"OK. Abra {rel}/{PROJECT_NAME}.pbip no Power BI Desktop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
