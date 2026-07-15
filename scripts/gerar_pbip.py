"""Gera pasta PBIP (Power BI Project) que abre no Power BI Desktop sem cliques.

Saída padrão: docs/pbip/capacitacao-ia/ com:
  - capacitacao-ia.pbip              (launcher)
  - capacitacao-ia.SemanticModel/    (TMDL: tabela fato + 8 medidas DAX)
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
BRIDGE = "bridge_publico"
DIMC = "dim_curso"
DIST = "dist_publico"
PROG = "bridge_programa"
DISTP = "dist_programa"

# Identificadores estáveis para lineageTag (TMDL) e logicalId (.platform).
# Persistir em .lineage.json para diffs git limpos entre regenerações.
LINEAGE_KEYS: tuple[str, ...] = (
    "expression_csv_path",
    "table_dashboard_base",
    "measure_matriculas",
    "measure_matriculas_ia",
    "measure_matriculas_dados",
    "measure_pessoas_unicas",
    "measure_pessoas_unicas_ia",
    "measure_pessoas_unicas_dados",
    "measure_pct_matriculas_ia",
    "measure_pct_matriculas_dados",
    "column_ano_mes",
    "column_ano",
    "column_mes",
    "column_id_curso",
    "column_nome_curso",
    "column_categoria",
    "column_esfera",
    "column_setor",
    "column_poder",
    "column_codigo_pessoa",
    "column_ano_mes_sort",
    # Dimensão Público-alvo (bridge m:n curso<->público).
    "table_dim_curso",
    "column_dimc_id_curso",
    "column_dimc_nome_curso",
    "column_dimc_categoria",
    "table_bridge_publico",
    "measure_qtd_publicos",
    "column_bridge_id_curso",
    "column_bridge_nome_curso",
    "column_bridge_publico_alvo",
    "column_bridge_programa_trilha",
    "rel_fact_curso",
    "rel_bridge_curso",
    # Distribuição cursos-por-pessoa por público (tabela pré-computada).
    "table_dist_publico",
    "column_dist_publico_alvo",
    "column_dist_qtd_cursos",
    "column_dist_qtd_pessoas",
    "column_dist_n_cursos",
    "measure_dist_qtd_pessoas",
    "measure_dist_pessoas_todos",
    "measure_dist_pct_todos",
    "measure_dist_media",
    "measure_dist_cursos_no_publico",
    # Dimensão Programa (bridge m:n curso<->programa + distribuição).
    "table_bridge_programa",
    "column_progbridge_id_curso",
    "column_progbridge_nome_curso",
    "column_progbridge_programa",
    "column_progbridge_publico_alvo",
    "rel_progbridge_curso",
    "table_dist_programa",
    "column_distprog_programa",
    "column_distprog_qtd_cursos",
    "column_distprog_qtd_pessoas",
    "column_distprog_n_cursos",
    "measure_distprog_pessoas",
    "measure_distprog_todos",
    "measure_distprog_pct",
    "measure_distprog_media",
    "measure_distprog_cursos",
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
    return column_of(ENTITY, name)


def measure_of(entity: str, name: str) -> dict[str, Any]:
    return {
        "kind": "measure",
        "entity": entity,
        "property": name,
        "query_ref": name,
        "native_query_ref": name,
    }


def column_of(entity: str, name: str) -> dict[str, Any]:
    return {
        "kind": "column",
        "entity": entity,
        "property": name,
        "query_ref": f"{entity}.{name}",
        "native_query_ref": name,
    }


def kpi_card(name: str, title: str, measure_name: str, x: int, y: int,
             width: int = 234, height: int = 116) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "visual_type": "card",
        "position": {"x": x, "y": y, "width": width, "height": height},
        "projections": {"Values": [measure(measure_name)]},
    }


# Estilo do slicer: "Dropdown" (em vez de lista vertical "Basic").
_SLICER_DROPDOWN = json.dumps({
    "data": [
        {"properties": {"mode": {"expr": {"Literal": {"Value": "'Dropdown'"}}}}}
    ]
})


def slicer(name: str, title: str, column_name: str, x: int, y: int,
           width: int = 288, height: int = 200,
           entity: str = ENTITY) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "visual_type": "slicer",
        "position": {"x": x, "y": y, "width": width, "height": height},
        "projections": {"Values": [column_of(entity, column_name)]},
        "objects_json": _SLICER_DROPDOWN,
    }


def textbox(name: str, text: str, x: int, y: int,
            width: int, height: int, font_size: str = "20pt") -> dict[str, Any]:
    """Caixa de texto estática (usada como título de cada aba — item 5)."""
    return {
        "name": name,
        "visual_type": "textbox",
        "text": text,
        "font_size": font_size,
        "position": {"x": x, "y": y, "width": width, "height": height},
    }


# Painel lateral de filtros (item 3): 5 slicers fixos, idênticos em todas as
# abas (esfera, setor, poder, categoria, ano), empilhados na coluna direita.
FILTER_PANEL_X = 1016
FILTER_PANEL_W = 248
# Slicers comuns (do fato), idênticos em todas as páginas.
_COMMON_FILTERS = (
    ("slicer-esfera", "Esfera", "esfera", ENTITY),
    ("slicer-setor", "Setor", "setor", ENTITY),
    ("slicer-poder", "Poder", "poder", ENTITY),
    ("slicer-categoria", "Categoria (IA / Dados)", "categoria", ENTITY),
    ("slicer-ano", "Ano", "ano", ENTITY),
)
# Slicer da dimensão de análise no topo do painel. Vem de uma bridge e, por ser
# bidirecional até o fato, cruza com todos os visuais.
_DIM_PUBLICO = ("slicer-publico", "Público-alvo", "publico_alvo", BRIDGE)
_DIM_PROGRAMA = ("slicer-programa", "Programa", "programa", PROG)


def filter_panel(dim: tuple[str, str, str, str] = _DIM_PUBLICO) -> list[dict[str, Any]]:
    specs = (dim,) + _COMMON_FILTERS
    h, gap, y0 = 104, 6, 12
    return [
        slicer(name, title, col, x=FILTER_PANEL_X, y=y0 + i * (h + gap),
               width=FILTER_PANEL_W, height=h, entity=entity)
        for i, (name, title, col, entity) in enumerate(specs)
    ]


# --- Configurações de query reaproveitáveis (itens 1 e 2) -------------------

# Item 1: rótulos de valores ligados no gráfico de linha.
_LABELS_ON = json.dumps(
    {"labels": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}}}]}
)

# Item 2: ordenação decrescente por [Matriculas].
_SORT_MATRICULAS_DESC = json.dumps({
    "sort": [{
        "field": {"Measure": {
            "Expression": {"SourceRef": {"Entity": ENTITY}},
            "Property": "Matriculas",
        }},
        "direction": "Descending",
    }],
    "isDefaultSort": False,
})

# Ordenação decrescente por [Pessoas Unicas] (página Público-alvo).
_SORT_PESSOAS_DESC = json.dumps({
    "sort": [{
        "field": {"Measure": {
            "Expression": {"SourceRef": {"Entity": ENTITY}},
            "Property": "Pessoas Unicas",
        }},
        "direction": "Descending",
    }],
    "isDefaultSort": False,
})

# Ordenação decrescente por [Qtd Publicos] (cursos mais transversais).
_SORT_QTD_PUBLICOS_DESC = json.dumps({
    "sort": [{
        "field": {"Measure": {
            "Expression": {"SourceRef": {"Entity": BRIDGE}},
            "Property": "Qtd Publicos",
        }},
        "direction": "Descending",
    }],
    "isDefaultSort": False,
})

# Eixo X categórico (histograma): força barras discretas por nº de cursos em vez
# de eixo contínuo numérico, com rótulos de valores ligados.
_CAT_AXIS_LABELS = json.dumps({
    "categoryAxis": [
        {"properties": {"axisType": {"expr": {"Literal": {"Value": "'Categorical'"}}}}}
    ],
    "labels": [
        {"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}}}
    ],
})

# Filtro de nível de relatório: o painel de capacitação conta apenas IA e Dados.
# Cursos de Gestão/Outros seguem na tabela fato (auditáveis) mas são removidos de
# todos os visuais/slicers ligados ao fato — é o que torna os indicadores "mais
# precisos" sem apagar dados. Não afeta as páginas de Distribuição (tabelas
# dist_publico/dist_programa, sem relação com o fato).
_REPORT_FILTER_IA_DADOS = json.dumps({
    "filters": [{
        "name": "filtro-categoria-painel",
        "field": {"Column": {
            "Expression": {"SourceRef": {"Entity": ENTITY}}, "Property": "categoria",
        }},
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "d", "Entity": ENTITY, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {
                    "Expression": {"SourceRef": {"Source": "d"}},
                    "Property": "categoria",
                }}],
                "Values": [
                    [{"Literal": {"Value": "'IA'"}}],
                    [{"Literal": {"Value": "'Dados'"}}],
                ],
            }}}],
        },
    }],
})


# ---------------------------------------------------------------------------
# PAGES_SPEC — fonte única de verdade do layout.
# ---------------------------------------------------------------------------

# Layout comum: título no topo (y=16), conteúdo a partir de y=64, painel de
# filtros na coluna direita (x=1016). Área de conteúdo: x=16..1000 (largura 984).
PAGES_SPEC: list[dict[str, Any]] = [
    {
        "name": "01-visao-geral",
        "display_name": "Visão Geral",
        "width": 1280,
        "height": 720,
        "visuals": [
            textbox("titulo-pagina", "Visão Geral — Capacitação em IA e Dados",
                    x=16, y=10, width=984, height=50, font_size="22pt"),
            kpi_card("kpi-matriculas-ia", "Matrículas IA", "Matriculas IA", x=16, y=64),
            kpi_card("kpi-matriculas-dados", "Matrículas Dados", "Matriculas Dados", x=266, y=64),
            kpi_card("kpi-pessoas-ia", "Pessoas únicas IA", "Pessoas Unicas IA", x=516, y=64),
            kpi_card("kpi-pessoas-dados", "Pessoas únicas Dados", "Pessoas Unicas Dados", x=766, y=64),
            {
                "name": "linha-temporal",
                "title": "Matrículas por mês (IA vs Dados)",
                "visual_type": "lineChart",
                "position": {"x": 16, "y": 196, "width": 984, "height": 502},
                "projections": {
                    "Category": [column("ano_mes")],
                    "Series": [column("categoria")],
                    "Y": [measure("Matriculas")],
                },
                # Item 1: rótulos de valores ligados.
                "objects_json": _LABELS_ON,
            },
            *filter_panel(),
        ],
    },
    {
        "name": "02-por-grupo",
        "display_name": "Por Grupo",
        "width": 1280,
        "height": 720,
        "visuals": [
            textbox("titulo-pagina", "Por Grupo — Esfera × Poder",
                    x=16, y=10, width=984, height=50, font_size="22pt"),
            {
                "name": "matriz-esfera-poder",
                "title": "Esfera × Poder",
                "visual_type": "pivotTable",
                "position": {"x": 16, "y": 64, "width": 984, "height": 634},
                "projections": {
                    "Rows": [column("esfera")],
                    "Columns": [column("poder")],
                    "Values": [measure("Matriculas"), measure("Pessoas Unicas")],
                },
            },
            *filter_panel(),
        ],
    },
    {
        "name": "03-por-curso",
        "display_name": "Por Curso",
        "width": 1280,
        "height": 720,
        "visuals": [
            textbox("titulo-pagina", "Por Curso",
                    x=16, y=10, width=984, height=50, font_size="22pt"),
            # Tabela (metade da altura anterior). Selecionar uma linha (curso)
            # cross-filtra o gráfico de linha abaixo -> histórico temporal do curso.
            {
                "name": "tabela-cursos",
                "title": "Detalhe por curso (selecione um curso para ver o histórico abaixo)",
                "visual_type": "tableEx",
                "position": {"x": 16, "y": 64, "width": 984, "height": 317},
                "projections": {
                    "Values": [
                        column("nome_curso"),
                        column("categoria"),
                        measure("Matriculas"),
                        measure("Pessoas Unicas"),
                    ],
                },
            },
            # Linha: pessoas concluintes ao longo do tempo. Sem seleção mostra o
            # total (todos os cursos IA+Dados); com um curso selecionado na
            # tabela, mostra a série temporal daquele curso.
            {
                "name": "linha-concluintes-curso",
                "title": "Pessoas concluintes ao longo do tempo (do curso selecionado)",
                "visual_type": "lineChart",
                "position": {"x": 16, "y": 397, "width": 984, "height": 301},
                "projections": {
                    "Category": [column("ano_mes")],
                    "Y": [measure("Pessoas Unicas")],
                },
                "objects_json": _LABELS_ON,
            },
            *filter_panel(),
        ],
    },
    {
        "name": "04-por-publico",
        "display_name": "Por Público-Alvo",
        "width": 1280,
        "height": 720,
        "visuals": [
            textbox("titulo-pagina", "Por Público-Alvo",
                    x=16, y=10, width=984, height=50, font_size="22pt"),
            # A — Ranking de matrículas por público.
            {
                "name": "barra-matriculas-publico",
                "title": "Matrículas por público-alvo",
                "visual_type": "barChart",
                "position": {"x": 16, "y": 64, "width": 484, "height": 300},
                "projections": {
                    "Category": [column_of(BRIDGE, "publico_alvo")],
                    "Y": [measure("Matriculas")],
                },
                "sort_json": _SORT_MATRICULAS_DESC,
                "objects_json": _LABELS_ON,
            },
            # B — Pessoas únicas por público (alcance real).
            {
                "name": "barra-pessoas-publico",
                "title": "Pessoas únicas por público-alvo",
                "visual_type": "barChart",
                "position": {"x": 516, "y": 64, "width": 484, "height": 300},
                "projections": {
                    "Category": [column_of(BRIDGE, "publico_alvo")],
                    "Y": [measure("Pessoas Unicas")],
                },
                "sort_json": _SORT_PESSOAS_DESC,
                "objects_json": _LABELS_ON,
            },
            # C — Mix IA × Dados por público (100% empilhado).
            {
                "name": "barra-mix-ia-publico",
                "title": "Mix IA × Dados por público (%)",
                "visual_type": "hundredPercentStackedBarChart",
                "position": {"x": 16, "y": 380, "width": 484, "height": 318},
                "projections": {
                    "Category": [column_of(BRIDGE, "publico_alvo")],
                    "Series": [column("categoria")],
                    "Y": [measure("Matriculas")],
                },
                "objects_json": _LABELS_ON,
            },
            # D — Evolução mensal de matrículas por público.
            {
                "name": "linha-publico-mes",
                "title": "Matrículas por mês e público-alvo",
                "visual_type": "lineChart",
                "position": {"x": 516, "y": 380, "width": 484, "height": 318},
                "projections": {
                    "Category": [column("ano_mes")],
                    "Series": [column_of(BRIDGE, "publico_alvo")],
                    "Y": [measure("Matriculas")],
                },
                "objects_json": _LABELS_ON,
            },
            *filter_panel(),
        ],
    },
    {
        "name": "05-publico-curso",
        "display_name": "Público × Curso",
        "width": 1280,
        "height": 720,
        "visuals": [
            textbox("titulo-pagina", "Público × Curso",
                    x=16, y=10, width=984, height=50, font_size="22pt"),
            # E — Matriz Público → Trilha → Curso (drill-down).
            {
                "name": "matriz-publico-curso",
                "title": "Matrículas por público, trilha e curso",
                "visual_type": "pivotTable",
                "position": {"x": 16, "y": 64, "width": 600, "height": 634},
                "projections": {
                    "Rows": [
                        column_of(BRIDGE, "publico_alvo"),
                        column_of(BRIDGE, "programa_trilha"),
                        column_of(BRIDGE, "nome_curso"),
                    ],
                    "Values": [measure("Matriculas"), measure("Pessoas Unicas")],
                },
            },
            # F — Cursos mais transversais (nº de públicos por curso).
            {
                "name": "barra-cursos-transversais",
                "title": "Cursos mais transversais (nº de públicos)",
                "visual_type": "barChart",
                "position": {"x": 628, "y": 64, "width": 372, "height": 310},
                "projections": {
                    "Category": [column_of(BRIDGE, "nome_curso")],
                    "Y": [measure_of(BRIDGE, "Qtd Publicos")],
                },
                "sort_json": _SORT_QTD_PUBLICOS_DESC,
                "objects_json": _LABELS_ON,
            },
            # G — Composição institucional: Público × Poder.
            {
                "name": "matriz-publico-poder",
                "title": "Matrículas por público e poder",
                "visual_type": "pivotTable",
                "position": {"x": 628, "y": 388, "width": 372, "height": 310},
                "projections": {
                    "Rows": [column_of(BRIDGE, "publico_alvo")],
                    "Columns": [column("poder")],
                    "Values": [measure("Matriculas")],
                },
            },
            *filter_panel(),
        ],
    },
    {
        "name": "06-distribuicao-publico",
        "display_name": "Distribuição por Público",
        "width": 1280,
        "height": 720,
        # Página estática (tabela dist_publico sem relacionamentos): sem painel
        # de filtros, pois não responde aos slicers.
        "visuals": [
            textbox("titulo-pagina", "Distribuição de cursos por pessoa, por público-alvo",
                    x=16, y=10, width=1248, height=50, font_size="22pt"),
            textbox("nota-pagina",
                    "Retrato sobre todo o histórico (todas as esferas). Não responde aos filtros das outras páginas.",
                    x=16, y=62, width=1248, height=28, font_size="11pt"),
            # Histograma: quantas pessoas concluíram quantos cursos de cada público.
            {
                "name": "histograma-distribuicao",
                "title": "Pessoas por nº de cursos concluídos (por público)",
                "visual_type": "clusteredColumnChart",
                "position": {"x": 16, "y": 92, "width": 820, "height": 606},
                "projections": {
                    "Category": [column_of(DIST, "qtd_cursos")],
                    "Series": [column_of(DIST, "publico_alvo")],
                    "Y": [measure_of(DIST, "Qtd Pessoas")],
                },
                "objects_json": _CAT_AXIS_LABELS,
            },
            # Tabela resumo: alcance vs conclusão integral por público.
            {
                "name": "tabela-resumo-publico",
                "title": "Resumo por público",
                "visual_type": "tableEx",
                "position": {"x": 852, "y": 92, "width": 412, "height": 606},
                "projections": {
                    "Values": [
                        column_of(DIST, "publico_alvo"),
                        measure_of(DIST, "Qtd Cursos no Publico"),
                        measure_of(DIST, "Qtd Pessoas"),
                        measure_of(DIST, "Pessoas com Todos os Cursos"),
                        measure_of(DIST, "% Concluiu Tudo"),
                        measure_of(DIST, "Media Cursos por Pessoa"),
                    ],
                },
            },
        ],
    },
    {
        "name": "07-por-programa",
        "display_name": "Por Programa",
        "width": 1280,
        "height": 720,
        "visuals": [
            textbox("titulo-pagina", "Por Programa",
                    x=16, y=10, width=984, height=50, font_size="22pt"),
            {
                "name": "barra-matriculas-programa",
                "title": "Matrículas por programa",
                "visual_type": "barChart",
                "position": {"x": 16, "y": 64, "width": 484, "height": 300},
                "projections": {
                    "Category": [column_of(PROG, "programa")],
                    "Y": [measure("Matriculas")],
                },
                "sort_json": _SORT_MATRICULAS_DESC,
                "objects_json": _LABELS_ON,
            },
            {
                "name": "barra-pessoas-programa",
                "title": "Pessoas únicas por programa",
                "visual_type": "barChart",
                "position": {"x": 516, "y": 64, "width": 484, "height": 300},
                "projections": {
                    "Category": [column_of(PROG, "programa")],
                    "Y": [measure("Pessoas Unicas")],
                },
                "sort_json": _SORT_PESSOAS_DESC,
                "objects_json": _LABELS_ON,
            },
            {
                "name": "barra-mix-ia-programa",
                "title": "Mix IA × Dados por programa (%)",
                "visual_type": "hundredPercentStackedBarChart",
                "position": {"x": 16, "y": 380, "width": 484, "height": 318},
                "projections": {
                    "Category": [column_of(PROG, "programa")],
                    "Series": [column("categoria")],
                    "Y": [measure("Matriculas")],
                },
                "objects_json": _LABELS_ON,
            },
            {
                "name": "linha-programa-mes",
                "title": "Matrículas por mês e programa",
                "visual_type": "lineChart",
                "position": {"x": 516, "y": 380, "width": 484, "height": 318},
                "projections": {
                    "Category": [column("ano_mes")],
                    "Series": [column_of(PROG, "programa")],
                    "Y": [measure("Matriculas")],
                },
                "objects_json": _LABELS_ON,
            },
            *filter_panel(_DIM_PROGRAMA),
        ],
    },
    {
        "name": "08-distribuicao-programa",
        "display_name": "Distribuição por Programa",
        "width": 1280,
        "height": 720,
        "visuals": [
            textbox("titulo-pagina", "Distribuição de cursos por pessoa, por programa",
                    x=16, y=10, width=900, height=50, font_size="22pt"),
            textbox("nota-pagina",
                    "Retrato sobre todo o histórico (todas as esferas). Não responde aos filtros das outras páginas.",
                    x=16, y=62, width=900, height=28, font_size="11pt"),
            # KPI: pessoas que concluíram TODOS os cursos de um programa. Sem
            # seleção = total (soma entre programas); selecionar um programa na
            # tabela de resumo ao lado cross-filtra este card para aquele programa.
            {
                "name": "kpi-todos-programa",
                "title": "Concluíram todos os cursos do programa",
                "visual_type": "card",
                "position": {"x": 928, "y": 10, "width": 336, "height": 74},
                "projections": {
                    "Values": [measure_of(DISTP, "Pessoas Todos do Programa")],
                },
            },
            {
                "name": "histograma-distribuicao-programa",
                "title": "Pessoas por nº de cursos concluídos (por programa)",
                "visual_type": "clusteredColumnChart",
                "position": {"x": 16, "y": 92, "width": 760, "height": 606},
                "projections": {
                    "Category": [column_of(DISTP, "qtd_cursos")],
                    "Series": [column_of(DISTP, "programa")],
                    "Y": [measure_of(DISTP, "Pessoas no Programa")],
                },
                "objects_json": _CAT_AXIS_LABELS,
            },
            {
                "name": "tabela-resumo-programa",
                "title": "Resumo por programa: fizeram ≥1 vs todos os cursos",
                "visual_type": "tableEx",
                "position": {"x": 792, "y": 92, "width": 472, "height": 606},
                "projections": {
                    "Values": [
                        column_of(DISTP, "programa"),
                        measure_of(DISTP, "Qtd Cursos no Programa"),
                        measure_of(DISTP, "Pessoas no Programa"),
                        measure_of(DISTP, "Pessoas Todos do Programa"),
                        measure_of(DISTP, "% Concluiu Programa"),
                        measure_of(DISTP, "Media Cursos Programa"),
                    ],
                },
            },
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
    write_file(definition / "tables" / "dim_curso.tmdl",
               render(env, "semantic_model/tables/dim_curso.tmdl.j2", ctx))
    write_file(definition / "tables" / "bridge_publico.tmdl",
               render(env, "semantic_model/tables/bridge_publico.tmdl.j2", ctx))
    write_file(definition / "tables" / "dist_publico.tmdl",
               render(env, "semantic_model/tables/dist_publico.tmdl.j2", ctx))
    write_file(definition / "tables" / "bridge_programa.tmdl",
               render(env, "semantic_model/tables/bridge_programa.tmdl.j2", ctx))
    write_file(definition / "tables" / "dist_programa.tmdl",
               render(env, "semantic_model/tables/dist_programa.tmdl.j2", ctx))
    files_written += 12
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
        "report_filter_config": _REPORT_FILTER_IA_DADOS,
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
