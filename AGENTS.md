# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## O PBIP é gerado, não editado

Tudo em `docs/pbip/capacitacao-ia/**` é saída de `scripts/gerar_pbip.py`. Não edite
esses arquivos à mão: a próxima regeneração descarta a edição. Mude o template em
`scripts/pbip_templates/` ou o `PAGES_SPEC` do gerador e regenere:

```
pip install -r scripts/requirements.txt   # jinja2, pandas
python scripts/gerar_pbip.py --force --output docs/pbip/capacitacao-ia \
  --csv-path "C:/Projects/capacitacao/docs/dashboard_base.csv" --csv-path-mode absolute
python scripts/gerar_pbip.py --dry-run    # valida sem escrever
```

O `--csv-path` acima é o caminho da máquina Windows onde o painel é aberto; sem ele
a regeneração troca o `CsvPath` do modelo pelo caminho local e suja o diff.

A saída bate byte a byte com o que o Power BI Desktop grava ao salvar o projeto —
JSON como `json.dumps(indent=2, ensure_ascii=False)`, TMDL terminando em linha em
branco, mais a ordem de chaves que o Desktop usa. Um "salvar" no Desktop não deve
produzir diff; se produzir, o template é que está desatualizado. Os `lineageTag`
ficam em `scripts/pbip_templates/.lineage.json` para que os diffs sejam estáveis.

## Recorte e contagem do painel

O recorte do painel é `categoria IN {IA, Dados}` e mais nada. Ele vive em três
lugares que precisam andar juntos: filtro de relatório `filtro-categoria-painel`
no PBIP, `CATEGORIAS_PAINEL` em `gerar_base_dashboard.py`, e o mesmo nome em
`gerar_programa.py` / `gerar_distribuicao_publico.py`.

**Esfera não recorta o painel** — decisão de produto de 2026-09-01, revertendo o
foco federal de #17. O painel cobre as quatro esferas da base (Federal, Estadual,
Municipal, `(sem esfera)`) e o slicer *Esfera*, presente em todas as páginas, é o
único lugar onde o leitor recorta. Não reintroduza `ESFERA_PAINEL` nem
`filtro-esfera-painel` sem decisão explícita.

Já o histórico da página pública (`atualizar_historico.py` → `contagem_mensal.csv`)
continua filtrando `esfera='Federal'` na ingestão, de propósito: **as duas séries
não são diretamente comparáveis** (332.003 conclusões de IA/Dados no painel contra
96.558 federais; 212.381 contra 53.920 pessoas distintas — posição de 2026-09-01). A nota de escopo da
página 1 e o README dizem isso; se um dos lados mudar, os dois textos mudam junto.

As tabelas `dist_publico` / `dist_programa` são pré-agregadas no grão `(fatia, k)` e
não têm chave de curso: nenhum relacionamento empurra o filtro de categoria do
relatório para dentro delas sem mudar o `k` de cada pessoa. O recorte tem que ser
aplicado no gerador, onde o `k` é calculado.

Um curso pertence a vários públicos e a vários programas, então a mesma pessoa entra
em várias fatias: **as barras por público/programa não somam ao total do painel** (a
soma passa de 3x). Por isso não há pizza, percentual do total nem linha de Total nessas
páginas, e as medidas de distribuição retornam BLANK fora do contexto de uma fatia.
O total verdadeiro aparece só no cartão `Pessoas distintas no painel`.

`dashboard_base.csv` já vem filtrado para matrículas concluídas: `[Conclusoes]` conta
conclusões, não matrículas.

## Envio mensal por e-mail: desativado de propósito

O step de e-mail do cron nunca disparou (condição exigia `workflow_dispatch` —
issue #1) e foi removido em definitivo em 2026-08-13, por decisão de produto: o
canal de comunicação é a página pública. Não é regressão e não deve ser
"consertado"; reintroduzir exige decisão explícita de produto. Os secrets
`MAIL_*` do repositório deixaram de ser usados.
