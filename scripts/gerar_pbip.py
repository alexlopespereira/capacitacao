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
import csv
import json
import re
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
ALVO_CSV = REPO_ROOT / "scripts" / "cursos_alvo.csv"
PROGRAMA_CSV = REPO_ROOT / "scripts" / "cursos_programa.csv"
PROJECT_NAME = "capacitacao-ia"

# Recorte do painel: o filtro de relatorio `categoria IN {IA, Dados}` e o mesmo
# que os geradores de distribuicao aplicam. Manter as tres listas em sincronia.
CATEGORIAS_PAINEL = {"IA", "Dados"}
FORA_DE_PROGRAMA = "(fora de programa)"
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
    "measure_conclusoes",
    "measure_conclusoes_ia",
    "measure_conclusoes_dados",
    "measure_pessoas_unicas",
    "measure_pessoas_unicas_ia",
    "measure_pessoas_unicas_dados",
    "measure_pct_conclusoes_ia",
    "measure_pct_conclusoes_dados",
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
             width: int = 234, height: int = 120) -> dict[str, Any]:
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
# Slicers comuns (do fato), idênticos em todas as páginas. O slicer de esfera
# recorta sob demanda: o painel não tem filtro de esfera no nível de relatório,
# então ele abre oferecendo Federal, Estadual, Municipal e "(sem esfera)".
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

def sort_desc(entity: str, measure_name: str) -> str:
    """Ordenação decrescente por uma medida — "barras ordenadas por tamanho"."""
    return json.dumps({
        "sort": [{
            "field": {"Measure": {
                "Expression": {"SourceRef": {"Entity": entity}},
                "Property": measure_name,
            }},
            "direction": "Descending",
        }],
    })


# Item 2: ordenação decrescente por [Pessoas Unicas] (ranking das páginas de
# público e de programa — cada barra é gente, não soma de conclusões).
_SORT_PESSOAS_DESC = sort_desc(ENTITY, "Pessoas Unicas")
# Ordenação decrescente por [Qtd Publicos] (cursos mais transversais).
_SORT_QTD_PUBLICOS_DESC = sort_desc(BRIDGE, "Qtd Publicos")
# Tabelas de resumo das páginas de distribuição, maior primeiro.
_SORT_DIST_PESSOAS_DESC = sort_desc(DIST, "Qtd Pessoas")
_SORT_DISTPROG_PESSOAS_DESC = sort_desc(DISTP, "Pessoas no Programa")

# Tabela sem linha de "Total": somar as fatias contaria a mesma pessoa em cada
# público/programa que contém um curso que ela concluiu. As medidas de
# dist_publico/dist_programa já retornam BLANK fora do contexto de uma fatia
# (ver os .tmdl); desligar o total aqui evita até a linha vazia.
_TABLE_NO_TOTALS = json.dumps({
    "total": [{"properties": {"totals": {"expr": {"Literal": {"Value": "false"}}}}}]
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

# Filtro de nível de relatório — o recorte padrão do painel é só a categoria:
# `categoria IN {IA, Dados}`, porque o painel de capacitação conta apenas IA e
# Dados. Cursos de Gestão/Outros seguem na tabela fato (auditáveis) mas são
# removidos de todos os visuais/slicers ligados ao fato.
# Esfera NÃO entra aqui: o painel cobre as quatro esferas da base (Federal,
# Estadual, Municipal e "(sem esfera)") e quem quiser uma delas usa o slicer
# Esfera. O histórico da página pública, esse sim, é só Federal — as duas
# séries não são diretamente comparáveis, e a nota da página 1 avisa.
# As páginas de Distribuição (tabelas dist_publico/dist_programa) não têm
# relação com o fato e não são alcançadas por este filtro: elas são
# pré-filtradas pelo mesmo recorte no gerador
# (scripts/gerar_distribuicao_publico.py e scripts/gerar_programa.py), porque o
# grão pré-agregado (fatia, k) não tem chave de curso onde o filtro pudesse
# entrar sem mudar o k de cada pessoa.

def _filtro_in(name: str, prop: str, valores: tuple[str, ...]) -> dict[str, Any]:
    """Filtro categórico de relatório, na serialização que o Desktop grava."""
    return {
        "name": name,
        "field": {"Column": {
            "Expression": {"SourceRef": {"Entity": ENTITY}}, "Property": prop,
        }},
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "d", "Entity": ENTITY, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {
                    "Expression": {"SourceRef": {"Source": "d"}},
                    "Property": prop,
                }}],
                "Values": [[{"Literal": {"Value": f"'{v}'"}}] for v in valores],
            }}}],
        },
    }


_REPORT_FILTERS_PAINEL = json.dumps({
    "filters": [
        _filtro_in("filtro-categoria-painel", "categoria", ("IA", "Dados")),
    ],
})


# --- Fatias que não somam: nota e total de referência -----------------------
#
# Um curso pertence a vários públicos e a vários programas (m:n — "Ética em IA"
# está em 8 dos 11 programas), então a mesma pessoa entra em cada fatia que
# contenha um curso que ela concluiu. Somar as barras conta gente repetida.
# O painel trata isso na apresentação, não na atribuição:
#   - cada barra conta PESSOAS DISTINTAS ("concluíram ≥1 curso desta fatia");
#   - nenhuma soma de fatias é exibida (sem total, sem % do total, sem pizza);
#   - o número único de referência fica num cartão à parte, separado das barras.

def total_referencia(x: int, y: int, width: int = 328, height: int = 64) -> dict[str, Any]:
    """Cartão com o total verdadeiro do painel — em pessoas, não em fatias."""
    return kpi_card(
        "kpi-pessoas-painel",
        "Pessoas distintas no painel (concluíram ≥1 curso de IA ou Dados)",
        "Pessoas Unicas", x=x, y=y, width=width, height=height,
    )


def nota_fatias(fatia: str, x: int, y: int, width: int, height: int = 44) -> dict[str, Any]:
    return textbox(
        "nota-pagina",
        f"Um curso pertence a vários {fatia}s, então a mesma pessoa é contada em "
        f"cada {fatia} que contenha um curso que ela concluiu: as barras NÃO somam "
        f"ao total do painel — somá-las conta gente repetida. O total verdadeiro "
        f"está no cartão acima.",
        x=x, y=y, width=width, height=height, font_size="11pt",
    )


def cursos_sem_programa() -> tuple[list[str], list[str]]:
    """Cursos da meta que não pertencem a nenhum programa, separados por escopo.

    Devolve (dentro do painel, fora do painel). Os de dentro viram a fatia
    "(fora de programa)" nas tabelas de distribuição — o gerador delas não
    descarta mais esse balde. Os de fora do painel não têm fatia (contá-los
    quebraria o recorte de categoria), então são **nomeados na nota**: some do
    painel em silêncio é exatamente o que se está corrigindo.
    """
    with open(ALVO_CSV, encoding="utf-8") as f:
        alvo = {r["id_curso"]: (r["tx_nome_curso"], r["categoria"])
                for r in csv.DictReader(f)}
    with open(PROGRAMA_CSV, encoding="utf-8") as f:
        com_programa = {r["id_curso"] for r in csv.DictReader(f)}
    dentro, fora = [], []
    for cid, (nome, categoria) in sorted(alvo.items(), key=lambda kv: kv[1][0]):
        if cid in com_programa:
            continue
        (dentro if categoria in CATEGORIAS_PAINEL else fora).append(
            nome if categoria in CATEGORIAS_PAINEL else f"{nome} ({categoria})"
        )
    return dentro, fora


def nota_distribuicao(fatia: str, x: int, y: int, width: int,
                      height: int = 78) -> dict[str, Any]:
    dentro, fora = cursos_sem_programa()
    if dentro:
        avulso = (f"Curso da meta que não pertence a nenhum programa aparece na "
                  f"fatia \"{FORA_DE_PROGRAMA}\": {', '.join(dentro)}. ")
    elif fora:
        avulso = (f"Nenhum curso de IA/Dados está fora de programa; fora do painel "
                  f"há {', '.join(fora)}, sem programa e sem fatia aqui. ")
    else:
        avulso = ""
    return textbox(
        "nota-pagina",
        f"Retrato sobre todo o histórico (todas as esferas), fixo nas "
        f"categorias IA e Dados (Gestão e Outros ficam de fora); não responde "
        f"aos filtros das outras páginas. "
        f"{avulso}"
        f"Cada pessoa entra num único balde, pelo nº exato de cursos que concluiu. "
        f"Uma pessoa pode aparecer em mais de um {fatia}: as barras NÃO somam ao "
        f"total do painel, que está no cartão acima.",
        x=x, y=y, width=width, height=height, font_size="11pt",
    )


# ---------------------------------------------------------------------------
# PAGES_SPEC — fonte única de verdade do layout.
# ---------------------------------------------------------------------------

# Layout comum: título no topo (y=10), painel de filtros na coluna direita
# (x=1016). Área de conteúdo: x=16..1000 (largura 984).
#
# Alturas das notas e dos cartões de referência, e o y do conteúdo abaixo delas,
# vieram de ajuste manual no Power BI Desktop (2026-08-17): o texto das notas
# estava sendo cortado. O Desktop grava coordenadas em float — aqui elas entram
# arredondadas para inteiro, com a altura derivada da borda inferior para não
# desalinhar (histograma da 06: y=263 + h=435 = 698, a mesma borda de antes).
# Exceção: a altura das NOTAS arredonda para cima. É ela que decide se o texto
# cabe, e arredondar 88.6 para baixo cortava de volta a 4ª linha na página 06 —
# desfazendo em silêncio o motivo do ajuste. Ao reajustar no Desktop, arredonde
# de novo em vez de colar o float cru: para cima nas notas, pela borda no resto.
PAGES_SPEC: list[dict[str, Any]] = [
    {
        "name": "01-visao-geral",
        "display_name": "Visão Geral",
        "width": 1280,
        "height": 720,
        "visuals": [
            textbox("titulo-pagina", "Visão Geral — Capacitação em IA e Dados",
                    x=16, y=10, width=984, height=50, font_size="22pt"),
            # Recorte explícito: o leitor precisa saber o que os números cobrem
            # antes de ler o primeiro KPI.
            textbox("nota-escopo",
                    "O painel cobre todas as esferas — Federal, Estadual, "
                    "Municipal e não-servidores — nas categorias IA e Dados. O "
                    "histórico da página pública cobre só a Federal: as duas "
                    "séries não são comparáveis direto. Use o slicer Esfera "
                    "para recortar.",
                    x=16, y=60, width=984, height=49, font_size="11pt"),
            kpi_card("kpi-conclusoes-ia", "Conclusões IA", "Conclusoes IA", x=16, y=112),
            kpi_card("kpi-conclusoes-dados", "Conclusões Dados", "Conclusoes Dados", x=266, y=112),
            kpi_card("kpi-pessoas-ia", "Pessoas únicas IA", "Pessoas Unicas IA", x=516, y=112),
            kpi_card("kpi-pessoas-dados", "Pessoas únicas Dados", "Pessoas Unicas Dados", x=766, y=112),
            {
                "name": "linha-temporal",
                "title": "Conclusões por mês (IA vs Dados)",
                "visual_type": "lineChart",
                "position": {"x": 16, "y": 240, "width": 984, "height": 458},
                "projections": {
                    "Category": [column("ano_mes")],
                    "Series": [column("categoria")],
                    "Y": [measure("Conclusoes")],
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
            # Esta é a página onde as esferas se comparam: a matriz traz as
            # quatro, cada número rotulado pela linha da própria esfera.
            {
                "name": "matriz-esfera-poder",
                "title": "Esfera × Poder",
                "visual_type": "pivotTable",
                "position": {"x": 16, "y": 64, "width": 984, "height": 634},
                "projections": {
                    "Columns": [column("poder")],
                    "Rows": [column("esfera")],
                    "Values": [measure("Conclusoes"), measure("Pessoas Unicas")],
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
                        measure("Conclusoes"),
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
                    x=16, y=10, width=640, height=50, font_size="22pt"),
            # Total de referência, separado das fatias.
            total_referencia(x=672, y=10, height=99),
            nota_fatias("público", x=16, y=109, width=984, height=59),
            # A — Ranking por público: pessoas distintas, ordenado por tamanho.
            {
                "name": "barra-pessoas-publico",
                "title": "Pessoas por público-alvo (concluíram ≥1 curso do público)",
                "visual_type": "barChart",
                "position": {"x": 16, "y": 174, "width": 984, "height": 234},
                "projections": {
                    "Category": [column_of(BRIDGE, "publico_alvo")],
                    "Y": [measure("Pessoas Unicas")],
                },
                "sort_json": _SORT_PESSOAS_DESC,
                "objects_json": _LABELS_ON,
            },
            # B — Mix IA × Dados dentro de cada público (% da própria fatia,
            # nunca % do total do painel).
            {
                "name": "barra-mix-ia-publico",
                "title": "Mix IA × Dados dentro de cada público (% das conclusões do público)",
                "visual_type": "hundredPercentStackedBarChart",
                "position": {"x": 16, "y": 420, "width": 484, "height": 278},
                "projections": {
                    "Category": [column_of(BRIDGE, "publico_alvo")],
                    "Series": [column("categoria")],
                    "Y": [measure("Conclusoes")],
                },
                "objects_json": _LABELS_ON,
            },
            # C — Evolução mensal de conclusões por público.
            {
                "name": "linha-publico-mes",
                "title": "Conclusões por mês e público-alvo",
                "visual_type": "lineChart",
                "position": {"x": 516, "y": 420, "width": 484, "height": 278},
                "projections": {
                    "Category": [column("ano_mes")],
                    "Series": [column_of(BRIDGE, "publico_alvo")],
                    "Y": [measure("Conclusoes")],
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
                "title": "Conclusões por público, trilha e curso",
                "visual_type": "pivotTable",
                "position": {"x": 16, "y": 64, "width": 600, "height": 634},
                "projections": {
                    "Rows": [
                        column_of(BRIDGE, "publico_alvo"),
                        column_of(BRIDGE, "programa_trilha"),
                        column_of(BRIDGE, "nome_curso"),
                    ],
                    "Values": [measure("Conclusoes"), measure("Pessoas Unicas")],
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
                "title": "Conclusões por público e poder",
                "visual_type": "pivotTable",
                "position": {"x": 628, "y": 388, "width": 372, "height": 310},
                "projections": {
                    "Columns": [column("poder")],
                    "Rows": [column_of(BRIDGE, "publico_alvo")],
                    "Values": [measure("Conclusoes")],
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
        # de filtros, pois não responde aos slicers. O recorte de categoria do
        # painel é aplicado no gerador da tabela, não aqui.
        # Clicar num público na tabela de resumo FILTRA o histograma (mostra só
        # as barras daquele público) em vez de apenas destacá-las.
        "visual_interactions": [
            {"source": "tabela-resumo-publico",
             "target": "histograma-distribuicao", "type": "DataFilter"},
        ],
        "visuals": [
            textbox("titulo-pagina", "Distribuição de cursos por pessoa, por público-alvo",
                    x=16, y=10, width=880, height=50, font_size="22pt"),
            total_referencia(x=912, y=10, width=352, height=111),
            nota_distribuicao("público", x=16, y=152, width=1248, height=89),
            # Histograma: quantas pessoas concluíram quantos cursos de cada público.
            {
                "name": "histograma-distribuicao",
                "title": "Pessoas por nº de cursos concluídos (por público)",
                "visual_type": "clusteredColumnChart",
                "position": {"x": 16, "y": 263, "width": 820, "height": 435},
                "projections": {
                    "Category": [column_of(DIST, "qtd_cursos")],
                    "Series": [column_of(DIST, "publico_alvo")],
                    "Y": [measure_of(DIST, "Qtd Pessoas")],
                },
                "objects_json": _CAT_AXIS_LABELS,
            },
            # Tabela resumo: alcance vs conclusão integral por público. Sem linha
            # de Total — a soma das fatias contaria a mesma pessoa várias vezes.
            {
                "name": "tabela-resumo-publico",
                "title": "Resumo por público (maior primeiro; as linhas não somam)",
                "visual_type": "tableEx",
                "position": {"x": 852, "y": 263, "width": 412, "height": 435},
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
                "sort_json": _SORT_DIST_PESSOAS_DESC,
                "objects_json": _TABLE_NO_TOTALS,
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
                    x=16, y=10, width=640, height=50, font_size="22pt"),
            total_referencia(x=672, y=10, height=98),
            nota_fatias("programa", x=16, y=108, width=984, height=60),
            {
                "name": "barra-pessoas-programa",
                "title": "Pessoas por programa (concluíram ≥1 curso do programa)",
                "visual_type": "barChart",
                "position": {"x": 16, "y": 180, "width": 984, "height": 228},
                "projections": {
                    "Category": [column_of(PROG, "programa")],
                    "Y": [measure("Pessoas Unicas")],
                },
                "sort_json": _SORT_PESSOAS_DESC,
                "objects_json": _LABELS_ON,
            },
            {
                "name": "barra-mix-ia-programa",
                "title": "Mix IA × Dados dentro de cada programa (% das conclusões do programa)",
                "visual_type": "hundredPercentStackedBarChart",
                "position": {"x": 16, "y": 420, "width": 484, "height": 278},
                "projections": {
                    "Category": [column_of(PROG, "programa")],
                    "Series": [column("categoria")],
                    "Y": [measure("Conclusoes")],
                },
                "objects_json": _LABELS_ON,
            },
            {
                "name": "linha-programa-mes",
                "title": "Conclusões por mês e programa",
                "visual_type": "lineChart",
                "position": {"x": 516, "y": 420, "width": 484, "height": 278},
                "projections": {
                    "Category": [column("ano_mes")],
                    "Series": [column_of(PROG, "programa")],
                    "Y": [measure("Conclusoes")],
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
        # Clicar num programa na tabela FILTRA o histograma (mostra só as barras
        # daquele programa) em vez de apenas destacar/esmaecer as demais.
        "visual_interactions": [
            {"source": "tabela-resumo-programa",
             "target": "histograma-distribuicao-programa", "type": "DataFilter"},
        ],
        "visuals": [
            textbox("titulo-pagina", "Distribuição de cursos por pessoa, por programa",
                    x=16, y=10, width=880, height=50, font_size="22pt"),
            # O cartão anterior somava "concluíram todos os cursos" entre os
            # programas (mesma pessoa contada em cada programa). No lugar dele
            # entra o total verdadeiro do painel; o "todos" fica por programa,
            # na tabela ao lado, onde tem contexto de uma fatia só.
            total_referencia(x=912, y=10, width=352, height=111),
            nota_distribuicao("programa", x=16, y=146, width=1248, height=93),
            {
                "name": "histograma-distribuicao-programa",
                "title": "Pessoas por nº de cursos concluídos (por programa)",
                "visual_type": "clusteredColumnChart",
                "position": {"x": 16, "y": 275, "width": 760, "height": 423},
                "projections": {
                    "Category": [column_of(DISTP, "qtd_cursos")],
                    "Series": [column_of(DISTP, "programa")],
                    "Y": [measure_of(DISTP, "Pessoas no Programa")],
                },
                "objects_json": _CAT_AXIS_LABELS,
            },
            {
                "name": "tabela-resumo-programa",
                "title": "Resumo por programa: fizeram ≥1 vs todos os cursos (as linhas não somam)",
                "visual_type": "tableEx",
                "position": {"x": 792, "y": 275, "width": 472, "height": 423},
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
                "sort_json": _SORT_DISTPROG_PESSOAS_DESC,
                "objects_json": _TABLE_NO_TOTALS,
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


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 sem BOM, LF (Power BI Desktop aceita). `newline=""` em open()
    # impede tradução para CRLF em Windows.
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def write_json(path: Path, content: str) -> None:
    """Grava JSON exatamente como o Power BI Desktop serializa.

    O Desktop reescreve todo JSON do projeto como `json.dumps(indent=2,
    ensure_ascii=False)` e **sem** newline final. Normalizar aqui faz o
    gerador e o Desktop convergirem: reabrir e salvar o PBIP no Desktop deixa
    de produzir diff (era o que o commit 52a8e1e teve de absorver). A ordem
    das chaves vem dos templates — `json.loads` preserva a ordem de inserção.
    """
    _write(path, json.dumps(json.loads(content), indent=2, ensure_ascii=False))


def write_tmdl(path: Path, content: str) -> None:
    """Grava TMDL com a linha em branco final que o Desktop deixa no arquivo."""
    _write(path, content.rstrip("\n") + "\n\n")


def resolve_csv_path_literal(csv_path: Path, mode: str) -> str:
    """Produz o literal para a expressão M `CsvPath`.

    relative: caminho relativo ao .pbip (string que o usuário ajusta na 1ª abertura)
    absolute: caminho absoluto resolvido em tempo de geração
    """
    if mode == "absolute":
        texto = str(csv_path).replace("\\", "/")
        # Caminho de Windows (`C:/...`) passado de uma máquina POSIX: usar como
        # veio. `resolve()` o trataria como relativo e prefixaria o cwd — é o
        # que impedia regenerar, fora do Windows, o PBIP que vai versionado.
        if re.match(r"^[A-Za-z]:/", texto):
            return texto
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

    write_json(sm_root / "definition.pbism",
               render(env, "semantic_model/definition.pbism.j2", ctx))
    write_json(sm_root / ".platform",
               render(env, "semantic_model/platform.json.j2", ctx))
    write_tmdl(definition / "database.tmdl",
               render(env, "semantic_model/database.tmdl.j2", ctx))
    write_tmdl(definition / "model.tmdl",
               render(env, "semantic_model/model.tmdl.j2", ctx))
    write_tmdl(definition / "expressions.tmdl",
               render(env, "semantic_model/expressions.tmdl.j2", ctx))
    write_tmdl(definition / "relationships.tmdl",
               render(env, "semantic_model/relationships.tmdl.j2", ctx))
    write_tmdl(definition / "tables" / "dashboard_base.tmdl",
               render(env, "semantic_model/tables/dashboard_base.tmdl.j2", ctx))
    write_tmdl(definition / "tables" / "dim_curso.tmdl",
               render(env, "semantic_model/tables/dim_curso.tmdl.j2", ctx))
    write_tmdl(definition / "tables" / "bridge_publico.tmdl",
               render(env, "semantic_model/tables/bridge_publico.tmdl.j2", ctx))
    write_tmdl(definition / "tables" / "dist_publico.tmdl",
               render(env, "semantic_model/tables/dist_publico.tmdl.j2", ctx))
    write_tmdl(definition / "tables" / "bridge_programa.tmdl",
               render(env, "semantic_model/tables/bridge_programa.tmdl.j2", ctx))
    write_tmdl(definition / "tables" / "dist_programa.tmdl",
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

    write_json(report_root / "definition.pbir",
               render(env, "report/definition.pbir.j2", ctx))
    write_json(report_root / ".platform",
               render(env, "report/platform.json.j2", ctx))
    write_json(definition / "version.json",
               render(env, "report/version.json.j2", ctx))
    write_json(definition / "report.json",
               render(env, "report/report.json.j2", ctx))
    write_json(definition / "pages" / "pages.json",
               render(env, "report/pages.json.j2", ctx))
    files_written += 5

    for page in PAGES_SPEC:
        page_dir = definition / "pages" / page["name"]
        write_json(page_dir / "page.json",
                   render(env, "report/page.json.j2", {**ctx, "page": page}))
        files_written += 1
        for visual in page["visuals"]:
            v_dir = page_dir / "visuals" / visual["name"]
            write_json(v_dir / "visual.json",
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
                        default="absolute",
                        help="como embutir o caminho do CSV na expressão M (default: absolute). "
                             "O Power Query exige caminho ABSOLUTO em File.Contents; usar "
                             "'relative' faz o Power BI Desktop falhar ao carregar com "
                             "'The supplied file path must be a valid absolute path.'")
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
        "report_filter_config": _REPORT_FILTERS_PAINEL,
    }

    # Launcher .pbip
    write_json(out_dir / f"{PROJECT_NAME}.pbip",
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
