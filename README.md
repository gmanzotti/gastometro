# Gastômetro FIESP
Painel de monitoramento dos gastos do Governo Federal.
Fonte: Secretaria do Tesouro Nacional — RTN (Resultado do Tesouro Nacional).

---

## Estrutura do projeto

```
acompanhamento_fiscal/
├── atualizar_dados.py          ← roda tudo com um comando
├── requirements.txt            ← dependências Python
│
├── config/
│   └── settings.py             ← configurações centrais (diretórios)
│
├── data/                       ← dados processados (não commitar no git)
│   ├── contador_fiscal.json    ← taxa de gasto por segundo (gerado automaticamente)
│   └── rtn/
│       ├── rtn_mensal.parquet  ← série histórica do Tesouro (gerado automaticamente)
│       └── metadata.json       ← período-base do deflator IPCA
│
├── pipelines/
│   ├── contador_fiscal.py      ← calcula a taxa de R$/segundo para o contador
│   └── rtn/
│       └── load.py             ← baixa e processa o Excel da RTN
│
├── dashboard/
│   ├── app.py                  ← o painel (Streamlit)
│   └── assets/
│       └── fiesp-logo.jpg
│
└── logs/
    └── pipeline.log            ← gerado automaticamente
```

---

## Como usar

### 1. Instalar dependências
```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
```

### 2. Baixar os dados (primeira vez e atualizações mensais)
```bash
python atualizar_dados.py
```
O script baixa o Excel da RTN do site do Tesouro Nacional, processa tudo
e salva os arquivos em `data/`. Precisa de conexão com a internet.
Não é necessária nenhuma chave de API.

### 3. Executar o painel
```bash
streamlit run dashboard/app.py
```
Abre em `http://localhost:8501`.

---

## Quando atualizar

A RTN é publicada pela Secretaria do Tesouro Nacional normalmente na
segunda semana de cada mês, com os dados do mês anterior.
Basta rodar `python atualizar_dados.py` após a publicação.

---

## Abas do painel

| Aba | Conteúdo |
|---|---|
| 💸 Gastos do Governo Federal | Contador em tempo real · Despesa Total do mês · Composição por categoria · Top 5 rubricas |
| 🔭 Observatório Fiscal | KPIs mensais e acumulado 12 meses · Receita × Despesa × Resultado · Trajetória fiscal |
| 🚨 Alertas | Detecção automática de anomalias via z-score (janela de 24 meses) |
| 📋 Explorador | Qualquer série da RTN em gráfico interativo com download CSV |

---

## Métricas disponíveis

O painel permite alternar entre três formas de ver os dados:

| Métrica | O que representa |
|---|---|
| Valores nominais (R$) | Valor em reais do período, sem ajuste de inflação |
| Valores reais (R$ constantes) | Ajustado pelo IPCA — permite comparar entre anos sem distorção inflacionária |
| % do PIB | Proporção do PIB — padrão internacional para comparação fiscal |

---

## Metodologia do contador

O contador em tempo real usa a fórmula **ratio rolling 12 meses**:

```
previsão_mês_t = gasto_mesmo_mês_ano_anterior × ratio_rolling
ratio_rolling  = Σ(gastos últimos 12m) / Σ(gastos 12m anteriores)
```

O ratio captura a tendência recente de crescimento dos gastos.
Usar o mesmo mês do ano anterior como âncora incorpora a sazonalidade
(dezembro sempre gasta mais que fevereiro, por exemplo).

---

## Metodologia de alertas

**Z-score rolling** sobre janela de 24 meses:

```
z = (valor_mês - média(24 meses anteriores)) / desvio_padrão(24 meses anteriores)
```

| Z-score | Nível |
|---|---|
| \|z\| ≥ 3,0σ | 🔴 Alerta vermelho (evento muito raro, < 0,3% de probabilidade) |
| \|z\| ≥ 2,0σ | 🟡 Alerta amarelo (evento incomum, < 5% de probabilidade) |

O `.shift(1)` garante que o mês atual não entra no cálculo da sua própria
média histórica (evita data leakage).
