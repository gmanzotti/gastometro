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
testes/                          pytest (89 testes) — rodar após mudanças com os caminhos
                                 explícitos (testes_*.py não é descoberto pelo padrão test_*):
                                 pytest testes/testes_federal.py testes/testes_load_estados.py
                                        testes/testes_load_municipios.py
```

- Ambiente: conda env `acompanhamento_fiscal` (Anaconda, Windows). Drive `V:` é
  rede SMB → Streamlit precisa de `--server.fileWatcherType poll` p/ hot-reload.
- Proxy FIESP tem certificado self-signed → `verify=False` nos downloads
  (pendência: remover em produção, responsabilidade da TI).

## Decisões metodológicas vigentes

- **Fase da despesa subnacional: EMPENHADA** ("DESPESAS EMPENHADAS ATÉ O
  BIMESTRE (f)" / "...NO BIMESTRE") — decisão de 12/06/2026, racional de
  advocacy: fase mais abrangente do ciclo (empenhado ≥ liquidado ≥ pago).
  Os parquets guardam as 5 colunas de fase; trocar de fase é só mudar as
  constantes (`COLUNA_PADRAO` nas pages, `COLUNA_FLUXO` no contador, e os
  filtros hardcoded em theme.py) e regenerar o contador.
  - Federal (RTN) é regime de **caixa** (metodologia própria da STN) — o
    contador consolidado mistura caixa (federal) + empenhado (subnacional);
    o label da aba Geral é neutro por isso.
  - O Gasto Brasil usa "efetivamente pago" incluindo RAP (RREO Anexo 07,
    sem abertura por categoria econômica) — não comparável diretamente.
- **Investimento = Investimentos (4.1) + Inversões Financeiras (4.2)**
  (Lei 4.320/64); Amortização da Dívida excluída.
- **Rolling 12 meses** para ratios de investimento (neutraliza sazonalidade).
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

## Dinâmica com TI e produção

- TI prefere ecossistema Microsoft (Azure DevOps, ADLS). Acatar quando não
  comprometer funcionalidade nem agilidade; chefe e Skaf favorecem a
  independência dos departamentos.
- Pendências com a TI: rodar `load_producao.py` (estados ~10 min; municípios
  24-36h), infra ADLS Gen2 (storage account/container/auth) e certificado SSL.

## Pendências conhecidas (ver afazeres.txt para a lista viva)

- Enviar `load_producao.py` à TI (instruções nos docstrings).
- WhatsApp Business API: iniciar aprovação (maior lead time do projeto).
- Validar formato do dashboard com o chefe.
- Avaliar R$ constantes no Elemento 6 federal e no ratio do Termômetro.