# Gastômetro FIESP

Painel de monitoramento dos gastos do Governo Federal, desenvolvido pela Assessoria Econômica da Presidência da FIESP. Consolida dados oficiais do Tesouro Nacional (RTN — Resultado do Tesouro Nacional) e os transforma em visualizações para comunicação institucional.

---

## Guia rápido por perfil

Não é preciso ler tudo. Vá direto ao que importa para o seu trabalho:

| Perfil | Seções relevantes |
|---|---|
| **Assessoria Econômica (Gustavo)** | Todas |
| **TI** | [Arquitetura](#arquitetura-do-projeto) · [Responsabilidades](#divisão-de-responsabilidades) · [Estrutura do repositório](#estrutura-do-repositório) · [Dados produzidos](#dados-produzidos-pelo-pipeline-contrato-de-dados) · [Implantação](#para-a-ti-implantação-no-servidor) · [Pendências antes da produção](#️-pendências-antes-de-ir-a-produção) |
| **Marketing** | [Arquitetura](#arquitetura-do-projeto) · [Responsabilidades](#divisão-de-responsabilidades) · [Dados disponíveis](#para-o-marketing-dados-disponíveis-no-data-lake) |

---

## Arquitetura do projeto

O projeto é dividido em quatro camadas com responsáveis distintos:

```
┌─────────────────────────────────────────────────────────────────┐
│  FONTE DE DADOS                                                 │
│  Secretaria do Tesouro Nacional — site público                  │
│  Relatório RTN (Excel, publicado mensalmente)                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ download automático
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 1 — EXTRAÇÃO E TRANSFORMAÇÃO  [responsável: Gustavo]   │
│                                                                 │
│  pipelines/rtn/load.py          (concluído)                     │
│    → baixa o Excel da RTN do Tesouro Nacional                   │
│    → transforma para formato tidy (1 linha por indicador/mês)  │
│    → calcula % do PIB | fonte: governo federal                  │
│                                                                 │
│  pipelines/rreo/load.py         (a desenvolver)                 │
│    → baixará e processará o RREO                                │
│      (Relatório Resumido da Execução Orçamentária)              │
│    → fonte: estados e municípios                                │
│                                                                 │
│  pipelines/contador_fiscal.py                                   │
│    → lê a RTN processada                                        │
│    → projeta os gastos dos próximos 2 meses                     │
│    → calcula a taxa R$/segundo (para o contador em tempo real)  │
│                                                                 │
│  Código versionado no Azure DevOps da FIESP                     │
└──────────────────────────┬──────────────────────────────────────┘
                           │ executa o código de Gustavo
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 2 — INFRAESTRUTURA E EXECUÇÃO  [responsável: TI]       │
│                                                                 │
│  → Executa o pipeline no servidor dedicado da FIESP             │
│  → Carrega os dados históricos (uma vez, manualmente)           │
│  → Agenda execuções mensais automáticas                         │
│  → Grava os dados no Data Lake da FIESP (ADLS Gen2)             │
└──────────────────────────┬──────────────────────────────────────┘
                           │ dados disponíveis no data lake
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 3 — FRONTEND PÚBLICO  [responsável: Marketing]         │
│                                                                 │
│  → Consome os dados do Data Lake                                │
│  → Implementa em HTML + C# as visualizações definidas           │
│     pela Assessoria Econômica                                   │
│  → Publica o site público (domínio FIESP)                       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  FERRAMENTA DE PROTOTIPAGEM  [uso interno: Assessoria]         │
│                                                                 │
│  dashboard/ (Streamlit)                                         │
│  → Gustavo testa visualizações aqui antes de passá-las          │
│    ao Marketing para implementação definitiva em HTML/C#        │
│  → NÃO entra no produto final de produção                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Divisão de responsabilidades

| Responsável | O que faz |
|---|---|
| **Assessoria Econômica (Gustavo)** | Escreve e mantém o código de extração e transformação dos dados (`pipelines/`). Define as visualizações no Streamlit para o Marketing implementar. Documenta o contrato de dados. |
| **TI** | Pega o código do Azure DevOps e executa no servidor dedicado. Faz o carregamento histórico dos dados (uma vez). Agenda as atualizações mensais. Garante que os dados chegam ao ADLS Gen2. |
| **Marketing** | Consome os dados do Data Lake. Implementa o frontend (HTML + C#) conforme as visualizações definidas pela Assessoria Econômica. Publica o site. |

---

## Estrutura do repositório

```
acompanhamento_fiscal/
│
├── atualizar_dados.py          ← Ponto de entrada único. Roda os dois pipelines
│                                  em sequência. É este script que a TI deve executar.
│
├── requirements.txt            ← Dependências Python. Instalar antes de rodar.
│
├── README.md                   ← Este arquivo.
│
├── config/
│   └── settings.py             ← Caminhos centrais usados por todos os scripts.
│                                  Modificar aqui se a estrutura de pastas mudar.
│
├── pipelines/                  ← Todo o código de extração e transformação
│   ├── rtn/
│   │   └── load.py             ← [CONCLUÍDO] Baixa e processa o Excel da RTN do Tesouro
│   │                              Nacional. Saída: data/rtn/rtn_mensal.parquet + metadata.json
│   ├── rreo/
│   │   └── load.py             ← [A DESENVOLVER] Baixará e processará o RREO
│   │                              (Relatório Resumido da Execução Orçamentária) dos
│   │                              estados e municípios. Saída: data/rreo/ (a definir)
│   └── contador_fiscal.py      ← [CONCLUÍDO] Calcula a taxa R$/segundo para o contador
│                                  em tempo real. Saída: data/contador_fiscal.json
│
├── data/                       ← Dados gerados pelo pipeline (não versionados no git)
│   ├── contador_fiscal.json
│   └── rtn/
│       ├── rtn_mensal.parquet
│       └── metadata.json
│
├── dashboard/                  ← Streamlit (ferramenta de prototipagem — não é produção)
│   ├── app.py
│   └── assets/
│       └── fiesp-logo.jpg
│
└── logs/
    └── pipeline.log            ← Registro de cada execução (data, hora, erros, valores)
```

> **Nota para a TI:** a pasta `data/` não está no repositório (está no `.gitignore`). Os arquivos de dados são gerados ao rodar `atualizar_dados.py` e devem ir para o Data Lake, não para o repositório.

---

## Dados produzidos pelo pipeline (contrato de dados)

Estes são os arquivos que o pipeline gera. O Marketing deve consumir estes dados do Data Lake para construir o frontend.

---

### `rtn_mensal.parquet` — Série histórica fiscal completa

O arquivo principal. Contém todas as séries do relatório RTN desde os anos 2000, uma linha por combinação de indicador e mês.

**Colunas:**

| Coluna | Tipo | Unidade | Descrição |
|---|---|---|---|
| `ano` | inteiro | — | Ano da observação (ex: 2025) |
| `mes` | inteiro | — | Mês da observação (1 a 12) |
| `data` | date | — | Primeiro dia do mês (ex: 2025-03-01) |
| `discriminacao` | texto | — | Nome do indicador fiscal (ex: "1. Receita Total", "4. Despesa Total") |
| `corrente_milhoes` | decimal | R$ milhões | Valor nominal (sem ajuste de inflação) |
| `constante_milhoes` | decimal | R$ milhões | Valor deflacionado pelo IPCA (base variável — ver metadata.json) |
| `pct_pib` | decimal | % | Proporção do PIB (ex: 2.35 = 2,35% do PIB) |

**Volume aproximado:** ~400 indicadores × ~250 meses = ~100.000 linhas

**Indicadores mais utilizados no frontend:**

| Prefixo em `discriminacao` | O que representa |
|---|---|
| `1. ` | Receita Total e subcomponentes |
| `4. ` | Despesa Total e subcomponentes |
| `7. ` | Resultado Primário |
| `8. ` | Resultado Nominal |

---

### `metadata.json` — Informações sobre a base do deflator

```json
{
  "base_constante": "Mar/2026",
  "ultima_data": "2026-03-01"
}
```

| Campo | Descrição |
|---|---|
| `base_constante` | Mês de referência do deflator IPCA usado nos valores constantes |
| `ultima_data` | Data do último mês com dado real disponível na RTN |

---

### `contador_fiscal.json` — Dados para o contador em tempo real

Alimenta o contador que mostra os gastos acumulando R$/segundo na tela.

```json
{
  "mes_referencia":      "2026-04",
  "mes_referencia_fim":  "2026-05",
  "previsao_total_2m_rs": 350000000000.0,
  "taxa_por_segundo_rs":  65432.12,
  "segundos_2m":          5356800,
  "ratio_rolling":        1.0512,
  "pago_base_2m_rs":      333000000000.0,
  "ultimo_dado_rtn":      "2026-03",
  "gerado_em":            "2026-05-27T10:00:00+00:00"
}
```

| Campo | Descrição |
|---|---|
| `mes_referencia` | Início do período de projeção (onde o contador começa) |
| `mes_referencia_fim` | Fim do período de projeção |
| `taxa_por_segundo_rs` | **Campo principal para o contador.** R$ gastos por segundo (taxa média dos 2 meses projetados) |
| `ratio_rolling` | Fator de crescimento dos gastos (ex: 1.05 = crescimento de 5%) |
| `ultimo_dado_rtn` | Último mês com dado real — referência para saber se os dados estão atualizados |
| `gerado_em` | Timestamp ISO 8601 da última geração |

---

## Como rodar localmente (Assessoria Econômica)

### 1. Instalar dependências

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

pip install -r requirements.txt
```

### 2. Atualizar os dados

```bash
python atualizar_dados.py
```

Baixa o Excel mais recente da RTN do site do Tesouro Nacional, processa e salva em `data/`. Precisa de internet. Não requer nenhuma chave de API.

### 3. Visualizar no Streamlit (prototipagem)

```bash
streamlit run dashboard/app.py
```

Abre em `http://localhost:8501`.

### 4. Enviar o código atualizado

```bash
git add .
git commit -m "descrição do que mudou"
git push
```

O `git push` envia automaticamente para **os dois repositórios**: Azure DevOps (principal, para a TI) e GitHub (para manter o Streamlit Cloud funcionando na prototipagem).

---

## Para a TI: implantação no servidor

### O que executar

O único script a rodar é:

```bash
python atualizar_dados.py
```

Ele orquestra tudo internamente: baixa a RTN, processa, calcula o contador. Registra tudo em `logs/pipeline.log`.

### Agendamento

Mensal. A RTN é publicada pela Secretaria do Tesouro Nacional normalmente na segunda semana de cada mês, com os dados do mês anterior. Sugestão de agendamento: **dia 15 de cada mês às 9h** (garante que o relatório já foi publicado).

Exemplo de cron (Linux):
```
0 9 15 * * cd /caminho/do/projeto && python atualizar_dados.py >> logs/cron.log 2>&1
```

### Carregamento histórico

O primeiro carregamento (dados históricos desde os anos 2000) deve ser feito manualmente pela TI, executando o script uma única vez no servidor. Após isso, as execuções mensais agendadas mantêm os dados atualizados incrementalmente.

### Destino dos dados

> ⚠️ **Pendência:** atualmente o pipeline grava em `data/` local. Antes de ir a produção, o pipeline será adaptado para gravar diretamente no ADLS Gen2. Aguardando: nome da storage account, nome do container, e definição do método de autenticação (preferencialmente Managed Identity).

### Dependências Python

Ver `requirements.txt`. Principais: `pandas`, `pyarrow`, `requests`, `streamlit`, `plotly`.

Versão Python recomendada: 3.11 ou superior.

---

## Para o Marketing: dados disponíveis no data lake

O Marketing consome os dados gerados pelo pipeline (seção [Dados produzidos](#dados-produzidos-pelo-pipeline-contrato-de-dados)) para construir o frontend em HTML + C#.

**Fluxo de trabalho:**

1. A Assessoria Econômica define como cada visualização deve aparecer (testando no Streamlit)
2. O Marketing implementa a visualização em HTML + C# consumindo os dados do Data Lake
3. Dúvidas sobre o significado de qualquer campo: consultar a seção de dados acima ou a Assessoria Econômica

**Principais dados para o frontend:**

| Visualização | Arquivo | Campo(s) |
|---|---|---|
| Contador em tempo real | `contador_fiscal.json` | `taxa_por_segundo_rs`, `mes_referencia` |
| Despesa total do mês | `rtn_mensal.parquet` | `discriminacao` = `"4. ..."`, `corrente_milhoes` |
| Receita × Despesa × Resultado | `rtn_mensal.parquet` | discriminações `1.`, `4.`, `7.` |
| Dados "quanto foi gasto" | `rtn_mensal.parquet` | filtrar por `discriminacao`, agrupar por `ano`/`mes` |

> Valores em `corrente_milhoes` e `constante_milhoes` estão em **R$ milhões**. Para exibir em reais, multiplicar por 1.000.000.

---

## Roadmap

### Concluído
- [x] Pipeline de extração da RTN (Tesouro Nacional) -> pipelines/federal/load.py
- [x] Cálculo do contador em tempo real (ratio rolling 12 meses)
- [x] Streamlit para prototipagem de visualizações
- [x] Versionamento no Azure DevOps da FIESP

### Próximo passo
- [ ] Desenvolver `pipelines/subnacionais/load.py` — extração e transformação dos dados de despesa de estados e municípios (RREO)
- [ ] Adaptar pipeline para gravar no ADLS Gen2 (aguardando infra TI)
- [ ] Documentar e entregar as visualizações ao Marketing para implementação em HTML/C#

### Futuro
- [ ] Relatório PDF automatizado (geração mensal via Python)
- [ ] Envio automático via WhatsApp para lista de contatos com opt-in
  - Requer conta WhatsApp Business API (aprovação pela Meta: 1–7 dias úteis)
  - Requer template de mensagem aprovado pela Meta antes do primeiro envio
- [ ] Análises adicionais: dívida/PIB, arcabouço fiscal, execução orçamentária, benchmark internacional

---

## ⚠️ Pendências antes de ir a produção

Estas duas questões **devem ser resolvidas antes** de o sistema entrar em produção. Estão funcionando em modo de contorno para desenvolvimento.

### 1. Certificado SSL — `verify=False`

**Onde:** `pipelines/rtn/load.py`, na função `baixar_excel()`

**Problema:** O site do Tesouro Nacional (`sisweb.tesouro.gov.br`) tem um problema na cadeia de certificados SSL que o Python rejeita em redes corporativas. O código usa `verify=False` para contornar isso durante o desenvolvimento.

**Risco em produção:** Desabilitar a verificação SSL expõe o sistema a ataques man-in-the-middle. Não é aceitável em produção.

**Solução (responsabilidade da TI):** Instalar o certificado raiz corporativo da FIESP no servidor onde o pipeline rodará, ou liberar a URL do Tesouro Nacional no proxy corporativo. Após isso, remover `verify=False` do código.

### 2. Gravação local em vez do Data Lake

**Onde:** `pipelines/rtn/load.py` e `pipelines/contador_fiscal.py`

**Problema:** Atualmente os dados são gravados em `data/` no sistema de arquivos local. Em produção, devem ir para o ADLS Gen2.

**O que é necessário (TI fornece):**
- Nome da storage account
- Nome do container (filesystem)
- Método de autenticação (Managed Identity recomendado)

---

## Metodologia do contador em tempo real

O contador usa a fórmula **ratio rolling 12 meses**:

```
previsão_mês_t = gasto_mesmo_mês_ano_anterior × ratio_rolling

ratio_rolling = Σ(gastos nos últimos 12 meses) / Σ(gastos nos 12 meses anteriores)
```

O ratio captura a tendência recente de crescimento. Usar o mesmo mês do ano anterior como âncora incorpora a sazonalidade natural dos gastos (dezembro é estruturalmente mais alto que fevereiro, por exemplo).

O pipeline projeta sempre **dois meses à frente** do último dado real, porque a RTN é publicada com ~1 mês de defasagem. Isso garante que o contador nunca fique sem projeção enquanto aguarda o próximo boletim.

---

## Metodologia dos alertas (Streamlit — prototipagem)

**Z-score rolling** sobre janela de 24 meses:

```
z = (valor_mês - média(24 meses anteriores)) / desvio_padrão(24 meses anteriores)
```

| Z-score | Nível |
|---|---|
| \|z\| ≥ 3,0σ | Alerta vermelho — evento muito raro (< 0,3% de probabilidade) |
| \|z\| ≥ 2,0σ | Alerta amarelo — evento incomum (< 5% de probabilidade) |
