# Capacitação — monitoramento mensal de matrículas concluídas (ENAP)

Pipeline automatizado que coleta o dump público da Escola Virtual.Gov da
[ENAP](https://dadosaberto.evg.gov.br/), agrega o número mensal de
**matrículas concluídas por servidores públicos federais** para uma lista
de **35 cursos da meta de capacitação** e publica o histórico como página
estática.

Filtros aplicados ao dataset bruto:
- `sit_matricula = 'Concluida'`
- `esfera = 'Federal'` (descarta Estadual, Municipal e registros sem
  vínculo público — terceiros, sociedade civil, etc.)

> Página pública: https://alexlopespereira.github.io/capacitacao/

## Como funciona

```
┌──────────────────────────┐    todo dia 1, 09:00 UTC
│  GitHub Actions (cron)   │  ───────────────────────────┐
└──────────────────────────┘                              │
                                                          ▼
            ┌────────────────────────────────────────────────────────┐
            │ 1. Baixa tar.gz de "últimos 12 meses" do portal ENAP   │
            │ 2. Extrai cada CSV mensal (separator '|')              │
            │ 3. Filtra sit_matricula='Concluida' AND esfera='Federal' │
            │ 4. Filtra os 35 cursos alvo (match por nome_curso)     │
            │ 5. Agrega por (ano_mes, id_curso) → count              │
            │ 6. Merge idempotente em docs/contagem_mensal.csv       │
            │ 7. Regenera docs/index.html (tabela pivot)             │
            │ 8. Commita docs/ se houver novidade                    │
            │ 9. Envia e-mail aos destinatários cadastrados          │
            └────────────────────────────────────────────────────────┘
```

## Estrutura

```
.
├── .github/workflows/atualizar-mensal.yml   # cron + envio de e-mail
├── scripts/
│   ├── atualizar_historico.py               # job principal
│   ├── cursos_alvo.csv                      # 35 cursos (id_curso, tx_nome_curso)
│   └── requirements.txt                     # pandas
├── docs/                                    # GitHub Pages (raiz pública)
│   ├── index.html                           # tabela pivot + curva de pessoas
│   ├── contagem_mensal.csv                  # matrículas por mês × curso
│   └── pessoas_por_mes.csv                  # (ano_mes, codigo_pessoa) — long format
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

## Configuração (GitHub Secrets necessários)

| Secret | Descrição |
|---|---|
| `MAIL_USERNAME` | E-mail do remetente (Gmail) |
| `MAIL_PASSWORD` | App Password de 16 caracteres do Gmail |
| `MAIL_TO` | Lista de destinatários separados por vírgula |

> `MAIL_TO` é guardado como **secret** (não variable) para evitar exposição
> dos endereços nos logs públicos do repositório.

Configuração adicional necessária no repositório:

- **Settings → Actions → General → Workflow permissions:** "Read and write"
- **Settings → Pages:** Source = branch `main`, folder `/docs`

## Dispositivos de teste

O workflow aceita disparo manual (`workflow_dispatch`) com 3 inputs:

- `ate` — último ano-mes a processar (formato `YYYY-MM`); vazio = mês anterior
- `test_email` — se preenchido, envia apenas para esse endereço (não usa `MAIL_TO`)
- `forcar_envio` — `true` para enviar e-mail mesmo sem mês novo

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
python scripts/gerar_pbip.py --validate       # produz docs/pbip/capacitacao-ia/
```

> A dimensão **Público-alvo** é carregada de `docs/dashboard_publico_alvo.csv`
> (tabela-ponte curso↔público, relação muitos-para-muitos). A fonte curada do
> mapeamento é `scripts/cursos_publico_alvo.csv`. Como um curso pode pertencer a
> vários públicos, **a soma de matrículas por público excede o total geral**
> (dupla contagem esperada); os KPIs sem o filtro de público permanecem corretos.

Flags úteis de `gerar_pbip.py`:

| Flag | Default | Função |
|---|---|---|
| `--output` | `docs/pbip/capacitacao-ia` | diretório de saída |
| `--csv-path` | `docs/dashboard_base.csv` | CSV fato a referenciar |
| `--csv-path-mode` | `relative` | `relative` (ajustar na 1ª abertura) ou `absolute` (caminho resolvido em tempo de geração) |
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
4. Na **primeira abertura** com `--csv-path-mode relative`, o Power Query
   pedirá o caminho do parâmetro `CsvPath`. Aponte para o
   `docs/dashboard_base.csv` (absoluto na sua máquina) → **Aplicar**. Carga
   leva ~5–15s para 385k linhas.

### O que esperar

| Página | Conteúdo |
|---|---|
| **1 — Visão Geral** | 4 KPIs (Matrículas, Matrículas IA, Pessoas únicas, Pessoas únicas IA) + linha temporal IA vs Não IA + slicers de período e tipo |
| **2 — Por Grupo** | Matriz Esfera × Poder com Matrículas e Pessoas únicas + slicers de ano, setor, tipo |
| **3 — Por Curso** | Tabela detalhada dos 35 cursos + barra Top 10 por matrículas + slicer de tipo |
| **4 — Por Público-Alvo** | Ranking de matrículas e de pessoas únicas por público + mix IA×Não-IA (100%) + evolução mensal por público |
| **5 — Público × Curso** | Matriz com drill Público→Trilha→Curso + cursos mais transversais (nº de públicos) + matriz Público × Poder |

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
- **Caminho relativo do CSV em M** não é suportado nativamente — daí o
  parâmetro `CsvPath` (workaround padrão). Para evitar a fricção na
  primeira abertura, regenerar com `--csv-path-mode absolute`.
- Cada `git pull` que atualiza `dashboard_base.csv` exige clicar em
  **Atualizar** no Desktop para recarregar.

## Licença

Os dados pertencem à ENAP e são publicados sob os termos do portal
[dadosaberto.evg.gov.br](https://dadosaberto.evg.gov.br/). O código deste
repositório é livre para uso e adaptação.
