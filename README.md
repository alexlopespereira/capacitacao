# Capacitação — monitoramento mensal de matrículas concluídas (ENAP)

Pipeline automatizado que coleta o dump público da Escola Virtual.Gov da
[ENAP](https://dadosaberto.evg.gov.br/) e acompanha as **matrículas
concluídas** de uma lista de **35 cursos da meta de capacitação**. O foco do
produto é o **governo federal**, mas os dois artefatos cobrem recortes
diferentes — cada número diz o que cobre:

- **Histórico público** (página estática + `contagem_mensal.csv` /
  `pessoas_por_mes.csv`): **só servidores públicos federais**
  (`esfera = 'Federal'`). Estadual, Municipal e não-servidores ficam de fora.
- **Base do dashboard** (`dashboard_base.csv` + painel Power BI): carrega
  **todas as esferas** — Federal, Estadual, Municipal e sem vínculo público
  (privado/não-servidor). O painel **abre com o filtro padrão
  `esfera = 'Federal'`**, o foco declarado; o leitor pode ampliar o recorte
  no painel Filtros, e as páginas de distribuição (retratos pré-agregados)
  ficam fixas no recorte padrão.

Para dimensionar a diferença: das 314.783 conclusões de IA/Dados na base
completa, só 89.829 (29%) são da esfera Federal — por isso nenhum número deve
ser apresentado como "de servidores federais" sem o recorte dizer isso.

Filtro comum aos dois artefatos: `sit_matricula = 'Concluida'` — toda
contagem é de **conclusões**, não de inscrições.

> Página pública: https://alexlopespereira.github.io/capacitacao/

## Como funciona

```
┌──────────────────────────┐    todo dia 1, 09:00 UTC
│  GitHub Actions (cron)   │  ───────────────────────────┐
└──────────────────────────┘                              │
                                                          ▼
            ┌──────────────────────────────────────────────────────────┐
            │ 1. Baixa tar.gz de "últimos 12 meses" do portal ENAP     │
            │ 2. Extrai cada CSV mensal (separador '|')                │
            │ 3. Histórico público (atualizar_historico.py):           │
            │    Concluida AND esfera='Federal' AND 35 cursos alvo     │
            │    → contagem_mensal.csv, pessoas_por_mes.csv, index.html│
            │ 4. Base do dashboard (gerar_base_dashboard.py):          │
            │    Concluida AND 35 cursos alvo, TODAS as esferas        │
            │    → dashboard_base.csv + agregado + relatório HTML/XLSX │
            │ 5. Derivados do painel (público, programa, distribuições;│
            │    distribuições fixas no recorte padrão do painel:      │
            │    IA/Dados + esfera Federal)                            │
            │ 6. Commita docs/ se houver novidade; Pages republica     │
            └──────────────────────────────────────────────────────────┘
```

> O workflow já teve um passo final de envio mensal por e-mail. Ele nunca
> disparou pelo cron (issue #1) e foi **desativado em definitivo em
> agosto/2026**, por decisão de produto: o canal de comunicação é a página
> pública. Os secrets `MAIL_*` não são mais usados.

## Estrutura

```
.
├── .github/workflows/atualizar-mensal.yml   # cron mensal
├── scripts/
│   ├── atualizar_historico.py               # histórico público (só Federal)
│   ├── gerar_base_dashboard.py              # base fato do painel (todas as esferas)
│   ├── gerar_publico_alvo.py                # ponte curso↔público
│   ├── gerar_programa.py                    # ponte curso↔programa + distribuição
│   ├── gerar_distribuicao_publico.py        # distribuição cursos-por-pessoa
│   ├── gerar_pbip.py                        # projeto Power BI (PBIP)
│   ├── cursos_alvo.csv                      # 35 cursos (id_curso, tx_nome_curso, categoria: IA/Dados/Gestão/Outros)
│   └── requirements.txt                     # pandas, jinja2
├── docs/                                    # GitHub Pages (raiz pública)
│   ├── index.html                           # tabela pivot + curva de pessoas (Federal)
│   ├── contagem_mensal.csv                  # matrículas concluídas por mês × curso (Federal)
│   ├── pessoas_por_mes.csv                  # (ano_mes, codigo_pessoa) — long format (Federal)
│   ├── dashboard_base.csv                   # tabela fato do painel (todas as esferas)
│   └── pbip/capacitacao-ia/                 # painel Power BI (abre focado em Federal)
└── README.md
```

## Janela de cobertura

- **Início:** 2024-08
- **Fim:** mês anterior ao corrente (mês corrente é descartado por estar incompleto)
- **Granularidade:** mensal × curso
- **Idempotência:** o merge sobrescreve recálculos do mesmo `ano_mes`,
  preservando meses fora da janela do download. Re-runs são seguros.

## Equivalência com a query SQL interna da ENAP

A query interna usa `tb_inscricao.tp_situacao_inscricao IN ('APROVADO', 'CERTIFICADO')`
filtrada por `dt_inscricao` e `id_curso` numérico. O dump público expõe apenas
`sit_matricula` (domínio `{Concluida, Desistente, Reprovado, Trancada,
Não Concluído}`), `dt_matricula` e `cod_curso` (hash, não numérico).

Mapeamento aplicado:

| Query interna | Pipeline público |
|---|---|
| `tp_situacao_inscricao IN ('APROVADO','CERTIFICADO')` | `sit_matricula = 'Concluida'` |
| `dt_inscricao` | `dt_matricula` |
| `id_curso` (numérico) | join por `nome_curso` normalizado |

A equivalência foi validada por comparação curso-a-curso para o ano de 2025:
ambas as fontes produzem **258.582** matrículas, com diferença zero em todos
os 35 cursos. Detalhes da validação ficam no repositório privado de análise.

## Configuração do repositório

- **Settings → Actions → General → Workflow permissions:** "Read and write"
- **Settings → Pages:** Source = branch `main`, folder `/docs`

Nenhum secret é necessário. (Os secrets `MAIL_*` eram do envio mensal por
e-mail, desativado em definitivo — ver issue #1.)

## Dispositivos de teste

O workflow aceita disparo manual (`workflow_dispatch`) com 1 input:

- `ate` — último ano-mes a processar (formato `YYYY-MM`); vazio = mês anterior

## Limitações conhecidas

- O dump dos "últimos 12 meses" pode incluir o mês corrente parcialmente; o
  pipeline filtra esses dados (`<= ultimo_mes_completo`) para evitar
  contagens parciais.
- A junção por `nome_curso` quebra se a ENAP renomear um curso. O script
  normaliza caixa, acentos, dashes e pontuação para mitigar variações
  triviais; renomes substanciais exigem atualização do `cursos_alvo.csv`.

## Power BI Desktop (.pbip)

Além do relatório HTML estático, o repositório gera programaticamente um
**Power BI Project (PBIP)** que abre no Power BI Desktop sem necessidade de
construir o modelo ou os visuais na UI.

### Gerar

```bash
pip install -r scripts/requirements.txt
python scripts/gerar_base_dashboard.py        # produz docs/dashboard_base.csv
python scripts/gerar_publico_alvo.py          # produz docs/dashboard_publico_alvo.csv (bridge)
python scripts/gerar_distribuicao_publico.py  # produz docs/dashboard_dist_publico.csv (histograma)
python scripts/gerar_programa.py              # produz docs/dashboard_programa.csv + dashboard_dist_programa.csv
python scripts/gerar_pbip.py --validate       # produz docs/pbip/capacitacao-ia/
```

> **Recorte padrão do painel:** `categoria IN {IA, Dados}` e `esfera =
> 'Federal'`, como filtros de nível de relatório — o painel abre no foco
> federal e o leitor pode ampliar o recorte no painel Filtros. As páginas de
> distribuição (06 e 08) são retratos pré-agregados **fixos no recorte
> padrão** (o grão delas não tem chave de curso nem de esfera para responder
> a filtro).

> A dimensão **Público-alvo** é carregada de `docs/dashboard_publico_alvo.csv`
> (tabela-ponte curso↔público, relação muitos-para-muitos). A fonte curada do
> mapeamento é `scripts/cursos_publico_alvo.csv`. Como um curso pode pertencer
> a vários públicos e programas, **a mesma pessoa entra em mais de uma fatia**:
> as barras por público/programa não somam ao total do painel. Por isso essas
> páginas contam pessoas distintas por fatia, não exibem soma nem % do total,
> e o total verdadeiro fica no cartão "Pessoas distintas no painel".

Flags úteis de `gerar_pbip.py`:

| Flag | Default | Função |
|---|---|---|
| `--output` | `docs/pbip/capacitacao-ia` | diretório de saída |
| `--csv-path` | `docs/dashboard_base.csv` | CSV fato a referenciar |
| `--csv-path-mode` | `absolute` | `absolute` (caminho resolvido em tempo de geração — recomendado, o Power Query exige caminho absoluto) ou `relative` (ajustar na 1ª abertura) |
| `--validate` | desligado | valida todos os JSON gerados |
| `--force` | desligado | sobrescreve `--output` existente |
| `--dry-run` | desligado | gera em tempdir, valida, descarta — útil em CI |

### Abrir no Power BI Desktop

1. **Instalar Power BI Desktop** (Windows, gratuito):
   <https://aka.ms/pbidesktopstore> (Microsoft Store) ou
   <https://www.microsoft.com/download/details.aspx?id=58494>.
2. **Habilitar PBIP preview:** Arquivo → Opções e configurações → Opções →
   Recursos de visualização → marcar **"Salvar como projeto do Power BI
   (.pbip)"** e **"Pasta PBIR para relatórios"** → OK → reiniciar.
3. **Abrir:** duplo-clique em `docs/pbip/capacitacao-ia/capacitacao-ia.pbip`.
4. Com o padrão `--csv-path-mode absolute`, o `CsvPath` já vem com o caminho
   absoluto resolvido — a carga acontece direto (~5–15s para 420k linhas). Se
   o repositório **não** estiver em `C:/Projects/capacitacao`, regenere com o
   caminho da sua máquina: `python scripts/gerar_pbip.py --force`
   (ou `--csv-path <caminho>/docs/dashboard_base.csv`). Só use
   `--csv-path-mode relative` se for editar o parâmetro `CsvPath` manualmente
   na 1ª abertura — caminho relativo faz o Desktop falhar com
   *"The supplied file path must be a valid absolute path."*

### O que esperar

| Página | Conteúdo |
|---|---|
| **1 — Visão Geral** | Nota de escopo (foco Federal por padrão) + 4 KPIs (Conclusões IA, Conclusões Dados, Pessoas únicas IA, Pessoas únicas Dados) + linha temporal IA vs Dados + slicers de período e categoria |
| **2 — Por Grupo** | Matriz Esfera × Poder com Conclusões e Pessoas únicas — com o filtro padrão só a linha Federal aparece; ampliar o filtro Esfera traz as demais esferas para comparação |
| **3 — Por Curso** | Tabela detalhada dos cursos (com categoria, meia altura) + gráfico de linha de pessoas concluintes ao longo do tempo — selecionar um curso na tabela cross-filtra a linha (histórico do curso) + slicer de categoria |
| **4 — Por Público-Alvo** | Ranking de pessoas distintas por público + mix IA×Dados (100%) + evolução mensal por público + cartão com o total verdadeiro do painel |
| **5 — Público × Curso** | Matriz com drill Público→Trilha→Curso + cursos mais transversais (nº de públicos) + matriz Público × Poder |
| **6 — Distribuição por Público** | Histograma de pessoas por nº de cursos concluídos + tabela resumo (fizeram ≥1, fizeram todos, % e média). Retrato estático fixo no recorte padrão (Federal, IA/Dados) — não responde aos slicers |
| **7 — Por Programa** | Ranking de pessoas distintas por programa + mix IA×Dados + evolução mensal (a dimensão Programa é o nível Trilha, curso↔programa m:n) |
| **8 — Distribuição por Programa** | Histograma de pessoas por nº de cursos do programa + tabela resumo (fizeram ≥1, fizeram todos, % e média). Retrato estático fixo no recorte padrão (Federal, IA/Dados) |

> Todas as páginas ganham um slicer de **Público-alvo** no painel de filtros,
> que cruza com qualquer visual.

### macOS / Linux

Power BI Desktop é **Windows-only**. Alternativas:

- VM Windows (Parallels/UTM/VMware).
- Power BI Service (web): zipar a pasta `capacitacao-ia/` e fazer upload
  como workspace item via [Fabric REST API](https://learn.microsoft.com/fabric/developer/rest-api/).
- Gerar localmente em macOS (`gerar_pbip.py` é puramente Python) e enviar
  os arquivos para uma máquina Windows para abrir.

### Limitações conhecidas

- **PBIR (visuais como JSON) ainda está em preview** no Desktop. Nomes
  internos de `visualType` podem mudar entre versões; se algum visual não
  carregar, ajustar o `VISUAL_TYPE_MAP` no `gerar_pbip.py`.
- **Caminho relativo do CSV em M** não é suportado nativamente — o Power Query
  exige caminho absoluto em `File.Contents`. Por isso o gerador embute o
  caminho **absoluto** no parâmetro `CsvPath` por padrão (`--csv-path-mode
  absolute`). Ao mover o repositório de lugar, basta regenerar o PBIP.
- Cada `git pull` que atualiza `dashboard_base.csv` exige clicar em
  **Atualizar** no Desktop para recarregar.

## Licença

Os dados pertencem à ENAP e são publicados sob os termos do portal
[dadosaberto.evg.gov.br](https://dadosaberto.evg.gov.br/). O código deste
repositório é livre para uso e adaptação.
