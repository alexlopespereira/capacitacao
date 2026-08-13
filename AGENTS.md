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

O painel conta apenas `categoria IN {IA, Dados}`. Esse recorte está em três lugares
que precisam andar juntos: o filtro de nível de relatório do PBIP, `CATEGORIAS_PAINEL`
em `gerar_base_dashboard.py`, e o mesmo nome em `gerar_programa.py` /
`gerar_distribuicao_publico.py`.

As tabelas `dist_publico` / `dist_programa` são pré-agregadas no grão `(fatia, k)` e
não têm chave de curso: nenhum relacionamento empurra o filtro de categoria para
dentro delas sem mudar o `k` de cada pessoa. O recorte tem que ser aplicado no
gerador, onde o `k` é calculado.

Um curso pertence a vários públicos e a vários programas, então a mesma pessoa entra
em várias fatias: **as barras por público/programa não somam ao total do painel** (a
soma passa de 3x). Por isso não há pizza, percentual do total nem linha de Total nessas
páginas, e as medidas de distribuição retornam BLANK fora do contexto de uma fatia.
O total verdadeiro aparece só no cartão `Pessoas distintas no painel`.

`dashboard_base.csv` já vem filtrado para matrículas concluídas: `[Conclusoes]` conta
conclusões, não matrículas.
