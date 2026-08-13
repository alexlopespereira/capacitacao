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

O recorte padrão do painel é `categoria IN {IA, Dados}` **e `esfera = 'Federal'`**
(o foco declarado do produto). Cada metade do recorte vive em lugares que precisam
andar juntos:

- categoria: filtro de relatório `filtro-categoria-painel` no PBIP,
  `CATEGORIAS_PAINEL` em `gerar_base_dashboard.py`, e o mesmo nome em
  `gerar_programa.py` / `gerar_distribuicao_publico.py`;
- esfera: filtro de relatório `filtro-esfera-painel` (padrão **editável** no painel
  Filtros — o leitor pode ampliar para as demais esferas) e `ESFERA_PAINEL` em
  `gerar_pbip.py`, `gerar_programa.py` e `gerar_distribuicao_publico.py`.

A base (`dashboard_base.csv`) carrega **todas** as esferas — o recorte é de
apresentação, nunca de carga. Já o histórico da página pública
(`atualizar_historico.py` → `contagem_mensal.csv`) filtra `esfera='Federal'` na
ingestão: as duas séries só são comparáveis no recorte padrão do painel.

As tabelas `dist_publico` / `dist_programa` são pré-agregadas no grão `(fatia, k)` e
não têm chave de curso nem de esfera: nenhum relacionamento empurra os filtros do
relatório para dentro delas sem mudar o `k` de cada pessoa. O recorte tem que ser
aplicado no gerador, onde o `k` é calculado — e nelas ele é **fixo** no padrão
(retrato), enquanto no resto do painel é padrão editável.

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
