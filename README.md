# Radar Fiscal FIESP
Monitoramento automatizado de gastos do Governo Federal.
Fonte primária: Portal da Transparência (CGU).

---

## Estrutura do projeto

```
acompanhamento_fiscal/
├── atualizar_dados.py          ← O Gerente (orquestra o pipeline)
├── requirements.txt
├── .env                        ← Crie este arquivo com sua chave de API
│
├── config/
│   └── settings.py             ← Configurações centrais, thresholds, rubricas
│
├── data/                       ← O Almoxarifado (não commitar no git)
│   └── despesas/
│       ├── raw/                ← JSON bruto da API (nunca modificar)
│       ├── silver/             ← Parquet limpo, granularidade de empenho
│       └── gold/               ← Parquet agregado, pronto para o dashboard
│
├── pipelines/                  ← A Fábrica
│   └── despesas/
│       ├── extract.py          ← Operário 1: Coleta
│       ├── silver.py           ← Operário 2: Padronização
│       └── gold.py             ← Operário 3: Regras de negócio + alertas
│
├── dashboard/
│   └── app.py                  ← Frontend Streamlit
│
└── logs/                       ← Gerado automaticamente
    └── pipeline.log
```

---

## Configuração inicial

### 1. Instalar dependências
```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

### 2. Obter chave de API
Cadastre-se em https://portaldatransparencia.gov.br/api-de-dados/cadastrar
A chave é gratuita e aumenta o rate limit de ~90 para ~600 req/min.

Crie um arquivo `.env` na raiz do projeto:
```
TRANSPARENCIA_API_KEY=sua_chave_aqui
```

Carregue antes de executar:
```bash
export $(cat .env | xargs)   # Linux/Mac
```

### 3. Carga histórica (primeira vez — pode demorar horas)
```bash
python atualizar_dados.py --historico
```

### 4. Atualização diária (adicionar ao cron)
```bash
python atualizar_dados.py
```

### 5. Executar o dashboard
```bash
streamlit run dashboard/app.py
```

---

## Tabelas gold disponíveis

| Arquivo | Conteúdo |
|---|---|
| `despesas_mensal_orgao.parquet` | Gasto mensal por órgão + z-score + variação m/m e a/a |
| `despesas_mensal_natureza.parquet` | Gasto mensal por natureza de despesa + z-score |
| `despesas_vigilancia.parquet` | Empenhos individuais de rubricas/órgãos de alta vigilância |
| `anomalias.parquet` | Alertas ativos (z-score ≥ 2σ) por órgão e natureza de despesa |

---

## Adicionar nova fonte (próximas iterações)

1. Criar `pipelines/<fonte>/extract.py`, `silver.py`, `gold.py`
2. Criar diretórios `data/<fonte>/raw/`, `silver/`, `gold/`
3. Registrar em `atualizar_dados.py` → `FONTES_DISPONIVEIS`
4. Adicionar aba correspondente no dashboard

Fontes planejadas (ordem sugerida):
- `cartao_corporativo` — CPGF, mensal, endpoint `/cartoes`
- `emendas` — emendas parlamentares, D+1
- `siop_creditos` — créditos suplementares (alertas de expansão orçamentária)
- `resultado_primario` — nota mensal do Tesouro Nacional

---

## Metodologia de alertas

**Z-score rolling**: para cada órgão/rubrica e mês t, calcula:
```
z = (valor_t - média(t-12, t-1)) / std(t-12, t-1)
```
- `z ≥ 2.0σ` → alerta amarelo (atenção)
- `z ≥ 3.0σ` → alerta vermelho (crítico)

Thresholds configuráveis em `config/settings.py`.
Mínimo de 3 observações para calcular z-score (evita falsos positivos no início da série).
