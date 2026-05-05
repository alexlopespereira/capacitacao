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
│   ├── index.html                           # tabela pivot
│   └── contagem_mensal.csv                  # histórico (fonte de verdade)
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

## Licença

Os dados pertencem à ENAP e são publicados sob os termos do portal
[dadosaberto.evg.gov.br](https://dadosaberto.evg.gov.br/). O código deste
repositório é livre para uso e adaptação.
