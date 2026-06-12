# Gastômetro FIESP

Painel de acompanhamento dos gastos do setor público brasileiro — União,
estados e municípios — desenvolvido pela Assessoria Econômica da Presidência
da FIESP. Consolida dados oficiais em visualizações de comunicação
institucional, incluindo um contador em tempo real do gasto público (R$/segundo).

## Fontes de dados

| Esfera | Fonte | Publicação |
|---|---|---|
| Federal | RTN — Resultado do Tesouro Nacional (STN) | Mensal |
| Estados + DF | SICONFI / RREO Anexo 01 (API da STN) | Bimestral |
| Municípios | SICONFI / RREO Anexo 01 (API da STN) | Bimestral |

Malhas geográficas: API de malhas do IBGE. Deflator: IPCA.

## Metodologia (resumo)

- **Fase da despesa (estados e municípios):** despesas **empenhadas**
  (compromisso de gasto formalmente assumido), conta
  `DespesasExcetoIntraOrcamentarias` do RREO. Na esfera federal, a RTN segue o
  regime de caixa da STN.
- **Investimento público** = Investimentos + Inversões Financeiras
  (Lei 4.320/64, art. 12); exclui Amortização da Dívida.
- **Contador em tempo real:** projeta os períodos ainda não publicados
  aplicando um fator de crescimento *rolling* de 12 meses sobre o mesmo
  período do ano anterior, e converte o total projetado em uma taxa por
  segundo. Bimestres não entregues ao SICONFI são imputados por sazonalidade
  histórica.
- **Comparações de 12 meses** usam valores deflacionados pelo IPCA.

## Estrutura

```
pipelines/      extração e transformação (RTN, estados, municípios, contador)
data/           parquets e JSONs gerados pelos pipelines (não versionados)
dashboard/      protótipo de visualização em Streamlit
testes/         suíte pytest
config/         caminhos e parâmetros centrais
```

## Como rodar

```bash
pip install -r requirements.txt
python atualizar_dados.py              # federal + contador
python pipelines/estados/load_prototipo.py     # estados (~10 min)
python pipelines/municipios/load_prototipo.py  # capitais (~15 min)
streamlit run dashboard/app.py
```

O Streamlit é a ferramenta de prototipagem das visualizações; o site público
definitivo é implementado em HTML/CSS a partir deste protótipo.

---

*Assessoria Econômica da Presidência — FIESP*