# CLAUDE.md — Memória do projeto Gastômetro FIESP

Este arquivo é a memória de trabalho do Claude neste projeto. Deve ser consultado
no início de cada sessão e **revisado/atualizado ao final de cada sessão**, junto
com os demais documentos (ver seção "Papéis dos arquivos do projeto").

## Quem é o usuário

- **Gustavo Simões** — Assessoria Econômica da Presidência da FIESP.
- Iniciante em engenharia de dados: **sempre explicar a lógica das decisões de
  engenharia e comentar o código generosamente** (didática faz parte da entrega).
- O chefe direto é o economista-chefe da FIESP, que responde a **Paulo Skaf**
  (presidente, demandante da iniciativa).
- **Atuar como camada crítica**: apontar erros de raciocínio e premissas falsas
  antes de executar, não validar ideias ruins. Exemplo real: em 12/06/2026 o
  pedido era trocar a fase de despesa para "pago" acreditando que aumentaria os
  números; a medição mostrou o oposto (pago ≤ liquidado ≤ empenhado no
  exercício) e a decisão final foi "empenhado".

## O que é o projeto

Painel fiscal público ("Gastômetro") que consolida gastos do setor público
brasileiro (federal + estados + municípios) como bandeira do **ajuste fiscal**
da FIESP. É um instrumento de **advocacy** — tecnicamente sólido, mas com
narrativa de "gasta-se muito". Alertar quando pedidos políticos puderem
comprometer a credibilidade técnica.

### Produto final e escopo (definido em 12/06/2026)

- O **produto final é um site em HTML/CSS implementado pela área de Marketing**,
  a partir do protótipo Streamlit. A configuração do produto final está **fora
  do nosso escopo**.
- O **Streamlit é apenas ferramenta de prototipagem** (deploy de referência em
  gastometrofiespv1.streamlit.app).
- Entregáveis: (1) site público; (2) **push WhatsApp + PDF** quando a base
  atualizar (ainda não iniciado; exige WhatsApp Business API, template aprovado
  pela Meta e lista opt-in).
- **Power BI interno foi ELIMINADO do projeto** (12/06/2026).
- Divisão de responsabilidades: Assessoria (Gustavo) = pipelines + definição de
  visualizações; TI = execução no servidor, agendamento, dados no ADLS Gen2;
  Marketing = frontend HTML/CSS e publicação.
- Versionamento: push duplo do remote `origin` → Azure DevOps (principal, TI)
  + GitHub (mantém o Streamlit Cloud do protótipo).

## Estrutura técnica

```
pipelines/federal/load.py        RTN/STN (Excel mensal) → data/rtn/rtn_mensal.parquet
                                 (séries 1997+/2008+, inclui rubricas "INV " das abas 1.3/1.3-A)
pipelines/estados/               SICONFI RREO Anexo 01 → data/estados/gastos_estados.parquet
  load_prototipo.py              26 estados + DF, 9 contas, 2024+ (~10 min)
  load_producao.py               versão p/ TI: checkpoint CSV, argparse, retry, logs
pipelines/municipios/            SICONFI → data/municipios/gastos_municipios.parquet
  load_prototipo.py              26 capitais, 2024+ (~14 min)
  load_producao.py               versão p/ TI: 5.570 municípios, ~24-36h, retomável
pipelines/contador_fiscal.py     consolida as 3 bases → data/contador_fiscal.json
                                 (acc + taxa R$/s por esfera e por ente; imputação sazonal)
pipelines/exportar_web.py        CAMADA WEB: gera data/web/*.json (~40 arquivos) que o
                                 site da TI consome do ADLS — schema igual ao do frontend
                                 existente; TODA a matemática importada de theme.py (zero
                                 duplicação de fórmulas). TI executa após cargas+contador
                                 e publica data/web/ no ADLS (instruções no docstring)
pipelines/simplificar_geojson.py reduz vértices das malhas (Douglas-Peucker via
                                 shapely) → data/*_geojson_simplificado.json
                                 (municípios 56MB→3,6MB; rodar quando o IBGE
                                 reatualizar as malhas; shapely é dep só de dev)
atualizar_dados.py               ponto de entrada (federal + contador)
dashboard/app.py                 aba Geral (contador hero, termômetro, 3 cards)
dashboard/pages/federal.py       6 elementos fixos
dashboard/pages/estadual.py      5 elementos fixos
dashboard/pages/municipal.py     5 elementos fixos
dashboard/components/theme.py    CSS, navbar (_NAV_PAGES), funções de cálculo compartilhadas
testes/                          pytest (104 testes) — rodar após mudanças com os caminhos
                                 explícitos (testes_*.py não é descoberto pelo padrão test_*):
                                 pytest testes/testes_federal.py testes/testes_load_estados.py
                                        testes/testes_load_municipios.py
                                        testes/testes_consistencia_estadual.py
```

- Ambiente: conda env `acompanhamento_fiscal` (Anaconda, Windows). Drive `V:` é
  rede SMB → Streamlit precisa de `--server.fileWatcherType poll` p/ hot-reload.
- Proxy FIESP tem certificado self-signed → `verify=False` nos downloads
  (pendência: remover em produção, responsabilidade da TI).

## Decisões metodológicas vigentes

- **Fase da despesa subnacional: EMPENHADA** ("DESPESAS EMPENHADAS ATÉ O
  BIMESTRE (f)" / "...NO BIMESTRE") — decisão de 12/06/2026, racional de
  advocacy: fase mais abrangente do ciclo (empenhado ≥ liquidado ≥ pago).
  Os parquets guardam as 5 colunas de fase; trocar de fase é só mudar a
  constante `COLUNA_FLUXO` (definida no contador e em theme.py) e regenerar o
  contador.
  - Federal (RTN) é regime de **caixa** (metodologia própria da STN) — o
    contador consolidado mistura caixa (federal) + empenhado (subnacional);
    o label da aba Geral é neutro por isso.
  - O Gasto Brasil usa "efetivamente pago" incluindo RAP (RREO Anexo 07,
    sem abertura por categoria econômica) — não comparável diretamente.
- **Investimento = Investimentos (4.1) + Inversões Financeiras (4.2)**
  (Lei 4.320/64); Amortização da Dívida excluída.
- **Dicotomia investimento × "despesas correntes e obrigatórias" por COMPLEMENTO**
  (decisão 15/06/2026): a fatia vermelha = `Total − Investimento`, nas três abas,
  para sempre somar 100%. NÃO usar a conta contábil `DespesasCorrentes` na barra,
  pois ela exclui a Amortização da Dívida (4.3) — que não é investimento nem
  corrente — e fazia a barra subnacional não fechar (~2,3% nos estados). O
  complemento reincorpora a amortização. Centralizado em
  `calcular_scatter_correntes_invest` (coluna `correntes_obrig_milhoes`); aba Geral
  já usava complemento (`linha_termometro`). Rótulo unificado: "Despesas correntes
  e obrigatórias". Federal mantém só "correntes" (nota própria nas notas metodológicas).
- **Aba estadual/municipal: projeção "intervalo móvel até o bimestre corrente"**
  (decisão 02/07/2026). Contador, composição, tabela, barra e mapa dessas abas
  usam UMA única base e sempre batem entre si (invariante travada em
  `testes/testes_consistencia_estadual.py`):
    `total = Σ realizado(ano, 1..último real) + ratio × Σ âncora(ano-1, b)`
  onde `b` vai do bimestre seguinte ao último real até o **bimestre em curso no
  calendário** (`_bimestre_corrente`), nunca o ano fechado. Racional: menor
  horizonte = menor erro, e não divulgar número anual reduz a contestação.
  - Base compartilhada em theme.py: `_plano_projecao` / `_projetar`, consumidas
    por `calcular_categorias_projetadas` e `calcular_scatter_correntes_invest`.
    O `ratio` por ente vem do JSON do contador → soma da composição == meta do
    contador por construção.
  - **Ressalva viva:** o invest% dessas abas é um ratio **YTD** e SOBE ao longo
    do ano (SP ~7,3% no 1º sem. → ~8,7% em dez), pois o acumulado só incorpora a
    arrancada de investimento de nov/dez quando o alvo chega a B6. Rotular sempre
    como "no ano até o bimestre X", nunca "estrutural/anual".
- **Termômetros na base YTD** (decisão 07/07/2026 — eliminou o último
  desalinhamento interno): os termômetros da Geral e da Federal usam a MESMA
  base "no ano, projetado até o período corrente" das abas Estadual/Municipal.
  `ratio_ytd_subnacional` deriva da própria `calcular_categorias_projetadas`
  (igualdade entre abas por construção); `ratio_federal` é o espelho MENSAL da
  fórmula bimestral, com plano do bloco federal do contador (replica a meta
  acc+previsão). Invariantes em testes_consistencia_estadual.py. Rolling 12m
  sobrevive apenas nas séries históricas da aba Federal (Elementos 2 e 6).
- **Camada web gerada por nós** (decisão 07/07/2026): o site da TI consome
  JSONs do ADLS; eram gerados por script da TI que reimplementava nossa
  matemática (causa raiz das divergências Streamlit × site — o script deles
  espelhava o protótipo de 30/06, pré-unificação). Agora
  `pipelines/exportar_web.py` gera tudo com as funções de theme.py; a TI só
  executa e publica. Guardas: exclui consórcios públicos (reportam sob o
  cod_ibge do município-sede!) e entes com invest% fora de [0,100].
- **Municípios <50 mil hab. podem publicar o RREO SEMESTRALMENTE** (LRF art.
  63) — por isso a extração bimestral completa retorna ~3.500 municípios, não
  5.570 (adesão varia por UF: MG 19%, BA 100%). TODOS os 686 municípios de
  50 mil+ hab. publicam bimestral; cobertura populacional 92,7%. Decisão
  07/07: manter cobertura bimestral com nota no site; avaliar consulta
  semestral complementar depois. NÃO é falha de extração.
- **Toda soma de 12m em R$ deve usar `constante_milhoes`** (IPCA) — somas
  nominais subestimam vs tabelas da STN (~2,5%).
- Imputação sazonal de bimestres não entregues no SICONFI (ratio histórico
  B_n/B_(n-1), 3 anos) no contador.
- DF tem `esfera='D'` no SICONFI — incluído manualmente no pipeline de estados.
- Conta total: `DespesasExcetoIntraOrcamentarias` (evita dupla contagem).

## Papéis dos arquivos do projeto (definidos em 12/06/2026)

| Arquivo | Papel | Público |
|---|---|---|
| `README.md` | Descrição sucinta de objetivo e métodos | Público (repositórios) |
| `afazeres.txt` | Log de sessões: feito / pendente / próximos passos | Pessoal do Gustavo |
| `arquitetura.txt` | Decisões de arquitetura do projeto | Interno: Assessoria + TI + Marketing |
| `notas_metodologicas.docx` | Fonte para o FAQ do site (item por elemento) | Marketing |
| `CLAUDE.md` | Memória do Claude (este arquivo) | Claude + Gustavo |

**Ritual obrigatório de fim de sessão: revisar e atualizar TODOS os cinco
arquivos acima antes de encerrar qualquer sessão de trabalho**, mesmo sem
pedido explícito. No mínimo: registrar a sessão no afazeres.txt e verificar se
as mudanças do dia tornaram algum dos outros arquivos desatualizado.

**Ao final, commitar e dar push para os dois repositórios** (decisão 02/07/2026):
o remote `origin` tem push duplo (Azure DevOps + GitHub), então um único
`git push origin master` envia para ambos. Nunca versionar `rascunhos/` (drafts
pessoais do Gustavo — já no .gitignore).

## Dinâmica com TI e produção

- TI prefere ecossistema Microsoft (Azure DevOps, ADLS). Acatar quando não
  comprometer funcionalidade nem agilidade; chefe e Skaf favorecem a
  independência dos departamentos.
- A TI já rodou a carga completa (estados 2016+ — pedir re-extração 2024+;
  municípios ~3.500 entes OK) e tem um protótipo HTML no ar
  (apps.fiesp.com.br/gastometro) que consome JSONs do ADLS. O frontend
  DEFINITIVO ainda será feito pelo Marketing — não gastar energia em ajustes
  cosméticos do HTML da TI.
- **SEGURANÇA (urgente, comunicado em 07/07)**: o config.js público do site da
  TI expõe SAS do ADLS com ESCRITA/DELEÇÃO válido até 2036. Pedir token
  somente-leitura curto + rotação do atual.
- Divergências de números com o site: causa raiz era a camada web da TI
  (metodologia antiga reimplementada) — resolvido com pipelines/exportar_web.py
  (ver diagnóstico completo em rascunhos/diagnostico_streamlit_vs_html_TI_20260707.md).

## Pendências conhecidas (ver afazeres.txt para a lista viva)

- Reunião com a TI: SAS, adoção do exportar_web.py, consórcios/degenerados na
  extração municipal, municípios semestrais, cronograma de re-deploy.
- Atualizar notas_metodologicas.docx (base unificada 02/07 + termômetro YTD
  07/07) — após a reunião com a TI.
- Atualizar RTN local p/ maio/2026 e regenerar contador + data/web.
- WhatsApp Business API: iniciar aprovação (maior lead time do projeto).
- Validar formato do dashboard com o chefe.
- Avaliar R$ constantes no Elemento 6 federal.