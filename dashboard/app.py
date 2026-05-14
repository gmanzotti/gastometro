"""
dashboard/app.py  —  O Painel: Gastômetro FIESP
─────────────────────────────────────────────────────────────────────────
Este é o arquivo principal do painel. Ele cria toda a interface visual
que aparece no navegador quando você roda o Gastômetro.

O que este arquivo faz:
  - Lê os dados processados (arquivos .parquet e .json) da pasta data/
  - Monta 4 abas interativas com gráficos e tabelas
  - Aplica o tema visual dark da FIESP (cores, fontes, layout)
  - Exibe um contador em tempo real de gastos do Governo Federal

As 4 abas do painel:
  💸 Gastos do Governo Federal — despesas do mês e acumulado 12 meses
  🔭 Observatório Fiscal       — receita × despesa × resultado primário
  🚨 Alertas                   — detecção automática de anomalias (z-score)
  📋 Explorador                — qualquer série histórica da RTN com download

Fonte de dados:
  Todos os dados vêm do Tesouro Nacional (RTN — Resultado do Tesouro
  Nacional), processados pelo pipeline em pipelines/rtn/load.py.

Como executar:
  streamlit run dashboard/app.py
"""

import base64
import calendar
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import DATA_DIR

# Caminho para o logo da FIESP (exibido no cabeçalho e na sidebar)
LOGO_PATH = Path(__file__).parent / "assets" / "fiesp-logo.jpg"


def _logo_existe() -> bool:
    return LOGO_PATH.exists()


def _logo_b64() -> str:
    """Retorna data URI base64 do logo FIESP, ou string vazia se não encontrado."""
    if not LOGO_PATH.exists():
        return ""
    data = LOGO_PATH.read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()

# Dicionário que mapeia número do mês para abreviação em português.
# Usado em labels de gráficos e na sidebar.
MES_LABELS = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

# Paleta de cores do tema dark da FIESP.
# Todas as cores do painel estão centralizadas aqui — se quiser mudar
# alguma cor, basta alterar o valor correspondente neste dicionário.
# As cores são códigos hexadecimais CSS (ex: "#050B18" = azul quase preto).
C = {
    "bg":         "#050B18",
    "bg2":        "#0D1B2E",
    "bg3":        "#0F2644",
    "border":     "#1E3A5F",
    "primary":    "#1E6FD9",
    "accent":     "#38BDF8",
    "text":       "#E2E8F0",
    "text_dim":   "#94A3B8",
    "text_muted": "#64748B",
    "positive":   "#22C55E",
    "negative":   "#EF4444",
    "warning":    "#F59E0B",
    "receita":    "#22C55E",
    "despesa":    "#EF4444",
    "resultado":  "#38BDF8",
    "nominal":    "#A78BFA",
}

# Constantes de estilo para os seletores de período dos gráficos Plotly.
# _RSEL = estilo do rangeselector (botões "1a", "3a", "5a", "Máx")
# _RSLD = estilo do rangeslider (a barra de navegação abaixo do gráfico)
# Definidas aqui uma vez para evitar repetição nos vários gráficos.
_RSEL = dict(
    bgcolor=C["bg3"],
    bordercolor=C["border"],
    borderwidth=1,
    font=dict(color=C["text_dim"], size=11),
    activecolor=C["primary"],
)
_RSLD = dict(bgcolor=C["bg2"], bordercolor=C["border"], thickness=0.06)


def _fmt(valor: float, decimais: int = 2) -> str:
    """
    Formata um número no padrão brasileiro: ponto como separador de milhar
    e vírgula como separador decimal.
    Exemplo: _fmt(1234567.89) → "1.234.567,89"

    A lógica de troca de caracteres:
      1. Python usa vírgula para milhar e ponto para decimal: "1,234,567.89"
      2. Troca vírgulas por "X" (marcador temporário): "1X234X567.89"
      3. Troca ponto por vírgula (decimal BR): "1X234X567,89"
      4. Troca "X" por ponto (milhar BR): "1.234.567,89"
    """
    s = f"{valor:,.{decimais}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _plotly_dark(fig, height=420, margin=None):
    """
    Aplica o tema visual dark da FIESP a qualquer figura Plotly.
    Chamada em todos os gráficos para garantir consistência visual.
    Ajusta fundo, cores de grade, fontes e tamanho da figura.
    """
    if margin is None:
        margin = dict(l=10, r=10, t=30, b=10)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(13,27,46,0.4)",
        font=dict(family="Inter, sans-serif", color=C["text_dim"], size=12),
        height=height,
        margin=margin,
        title_font=dict(color=C["text"], size=14),
        legend=dict(
            bgcolor="rgba(13,27,46,0.9)",
            bordercolor=C["border"],
            borderwidth=1,
            font=dict(color=C["text"], size=12),
        ),
    )
    fig.update_xaxes(
        gridcolor=C["border"],
        zerolinecolor=C["border"],
        tickfont=dict(color=C["text_dim"], size=11),
        title_font=dict(color=C["text_dim"], size=12),
        showline=False,
    )
    fig.update_yaxes(
        gridcolor=C["border"],
        zerolinecolor=C["border"],
        tickfont=dict(color=C["text_dim"], size=11),
        title_font=dict(color=C["text_dim"], size=12),
        showline=False,
    )
    return fig


# ── Configuração da página ────────────────────────────────────────────────
# st.set_page_config() DEVE ser a primeira chamada Streamlit do script.
# Define título da aba do navegador, ícone e layout da página.
st.set_page_config(
    page_title="Gastômetro FIESP",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Bloco de CSS customizado injetado diretamente na página.
# O Streamlit permite isso via st.markdown com unsafe_allow_html=True.
# Aqui definimos fontes, cores de fundo, estilos das abas, cards, alertas, etc.
# O "f" antes das aspas permite usar as variáveis do dicionário C{} dentro do CSS.
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html {{
    zoom: 90%;
}}

html, body, .stApp {{
    font-family: 'Inter', sans-serif !important;
    background-color: {C['bg']} !important;
}}

/* Barra superior do Streamlit — mantida visível para que o botão de
   recolher/expandir a sidebar funcione. Estilizada para o tema escuro. */
[data-testid="stHeader"] {{
    background-color: {C['bg2']} !important;
    border-bottom: 1px solid {C['border']} !important;
}}
[data-testid="stDecoration"] {{ display: none !important; }}

/* Sidebar */
[data-testid="stSidebar"] {{
    background-color: {C['bg2']} !important;
    border-right: 1px solid {C['border']};
}}
[data-testid="stSidebarUserContent"] {{ padding-top: 0 !important; }}

/* Conteudo principal */
.main .block-container {{
    padding-top: 0 !important;
    max-width: 1600px;
}}

/* Abas */
.stTabs [data-baseweb="tab-list"] {{
    background-color: {C['bg2']};
    border-radius: 10px;
    padding: 4px;
    gap: 2px;
    border: 1px solid {C['border']};
    margin-bottom: 16px;
}}
.stTabs [data-baseweb="tab"] {{
    border-radius: 7px;
    color: {C['text_dim']} !important;
    font-weight: 500;
    font-size: 13px;
    padding: 8px 18px;
    background: transparent;
    border: none !important;
    transition: background 0.15s;
}}
.stTabs [aria-selected="true"] {{
    background-color: {C['primary']} !important;
    color: #fff !important;
    font-weight: 600;
}}
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] {{ display: none !important; }}

/* Metric cards */
[data-testid="metric-container"] {{
    background-color: {C['bg2']} !important;
    border: 1px solid {C['border']} !important;
    border-radius: 10px !important;
    padding: 16px 20px !important;
    transition: border-color 0.2s;
}}
[data-testid="metric-container"]:hover {{
    border-color: {C['primary']} !important;
}}
[data-testid="stMetricLabel"] > div,
[data-testid="stMetricLabel"] label {{
    color: {C['text_dim']} !important;
    font-size: 11px !important;
    font-weight: 500 !important;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}}
[data-testid="stMetricValue"] > div {{
    color: {C['text']} !important;
    font-size: 22px !important;
    font-weight: 700 !important;
    letter-spacing: -0.3px;
}}

/* Textos */
h1, h2, h3, h4 {{ color: {C['text']} !important; }}
p {{ color: {C['text_dim']}; }}
.stMarkdown p {{ color: {C['text_dim']} !important; }}
.stCaption p, caption {{ color: {C['text_muted']} !important; font-size: 11px !important; }}
[data-testid="stMarkdownContainer"] strong {{ color: {C['text']} !important; }}

/* Dividers */
hr {{ border-color: {C['border']} !important; opacity: 0.5; }}

/* Botão de reabrir a sidebar */
[data-testid="collapsedControl"] {{
    display: flex !important;
    color: {C['accent']} !important;
}}

/* Alertas */
.alerta-vermelho {{
    background: linear-gradient(90deg, rgba(239,68,68,0.08) 0%, rgba(13,27,46,0.5) 100%);
    border-left: 3px solid {C['negative']};
    padding: 12px 16px; border-radius: 6px; margin-bottom: 8px;
    color: {C['text']} !important;
}}
.alerta-amarelo {{
    background: linear-gradient(90deg, rgba(245,158,11,0.08) 0%, rgba(13,27,46,0.5) 100%);
    border-left: 3px solid {C['warning']};
    padding: 12px 16px; border-radius: 6px; margin-bottom: 8px;
    color: {C['text']} !important;
}}
.alerta-vermelho *, .alerta-amarelo * {{ color: {C['text']} !important; }}

/* Sub-header de secao KPI */
.kpi-sub {{
    font-size: 11px; font-weight: 600; color: {C['accent']};
    text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 8px;
}}

/* Tag de fonte */
.fonte-tag {{
    font-size: 10px; color: {C['text_muted']};
    background: rgba(30,58,95,0.4); border: 1px solid {C['border']};
    border-radius: 4px; padding: 2px 8px;
}}

/* DataFrames */
[data-testid="stDataFrame"] {{
    border: 1px solid {C['border']} !important;
    border-radius: 8px;
    overflow: hidden;
}}

/* Scrollbar */
::-webkit-scrollbar {{ width: 5px; height: 5px; }}
::-webkit-scrollbar-track {{ background: {C['bg']}; }}
::-webkit-scrollbar-thumb {{ background: {C['border']}; border-radius: 3px; }}
::-webkit-scrollbar-thumb:hover {{ background: {C['primary']}; }}

/* Download button */
[data-testid="stDownloadButton"] button {{
    background-color: {C['bg3']} !important;
    border: 1px solid {C['border']} !important;
    color: {C['accent']} !important;
    font-weight: 500; border-radius: 6px; font-size: 12px;
}}
[data-testid="stDownloadButton"] button:hover {{
    border-color: {C['accent']} !important;
}}

/* Separador de secao */
.section-divider {{
    height: 1px; background: linear-gradient(90deg, {C['border']} 0%, transparent 100%);
    margin: 16px 0;
}}
</style>
""", unsafe_allow_html=True)


# -- Carregamento de dados (com cache) -------------------------------------

@st.cache_data(ttl=3600, show_spinner="Carregando dados...")
def carregar_dados():
    """
    Lê os arquivos de dados e devolve um dicionário para o painel.

    O decorator @st.cache_data faz com que o Streamlit guarde o resultado
    em memória por 1 hora (ttl=3600 segundos). Assim, o arquivo não é
    relido a cada interação do usuário — só quando os dados ficam "velhos".

    Retorna um dicionário com duas chaves:
      "rtn"      → DataFrame com toda a série histórica do Tesouro Nacional
      "contador" → dicionário com a taxa de gastos por segundo (para o contador)
    """
    dados = {}

    # Lê o parquet da RTN (Resultado do Tesouro Nacional).
    # Parquet é um formato de arquivo colunar muito mais eficiente que CSV
    # para leitura — ocupa menos espaço e carrega mais rápido.
    rtn_path = DATA_DIR / "rtn" / "rtn_mensal.parquet"
    dados["rtn"] = pd.read_parquet(rtn_path) if rtn_path.exists() else pd.DataFrame()

    # Lê o JSON do contador fiscal (taxa de gasto por segundo)
    cont_path = DATA_DIR / "contador_fiscal.json"
    dados["contador"] = (
        json.loads(cont_path.read_text(encoding="utf-8")) if cont_path.exists() else {}
    )
    return dados


# ── Sidebar (painel lateral) ──────────────────────────────────────────────

def sidebar_filtros(df_rtn: pd.DataFrame) -> dict:
    """
    Monta a barra lateral do painel com os filtros de ano/mês e os toggles
    de alerta. Retorna um dicionário com as seleções do usuário.

    O Streamlit re-executa todo o script a cada interação — quando o usuário
    muda o ano ou o mês, o script roda do início com os novos filtros.
    """
    if _logo_existe():
        st.sidebar.image(str(LOGO_PATH), width=140)
    st.sidebar.markdown(f"""
    <div style="padding:4px 4px 20px 4px; border-bottom:1px solid {C['border']}; margin-bottom:20px;">
      <div style="font-size:9px; letter-spacing:2.5px; color:{C['accent']}; font-weight:700;
                  text-transform:uppercase; margin-bottom:6px;">
        ASSESSORIA ECONÔMICA · FIESP
      </div>
      <div style="font-size:18px; font-weight:700; color:{C['text']}; letter-spacing:-0.3px;">
        Gastômetro
      </div>
    </div>
    <div style="font-size:9px; letter-spacing:2px; color:{C['text_muted']}; font-weight:700;
                text-transform:uppercase; margin-bottom:14px;">
      Parâmetros
    </div>
    """, unsafe_allow_html=True)

    if df_rtn.empty:
        return {}

    anos = sorted(df_rtn["ano"].unique(), reverse=True)
    ano_sel = st.sidebar.selectbox("Ano de referência", anos)

    meses = sorted(df_rtn[df_rtn["ano"] == ano_sel]["mes"].unique(), reverse=True)
    mes_sel = st.sidebar.selectbox(
        "Mês de referência", meses,
        format_func=lambda m: MES_LABELS.get(m, m),
    )

    st.sidebar.markdown(f"""
    <div style="height:1px; background:{C['border']}; margin:20px 0 16px 0;"></div>
    <div style="font-size:9px; letter-spacing:2px; color:{C['text_muted']}; font-weight:700;
                text-transform:uppercase; margin-bottom:12px;">
      Alertas
    </div>
    """, unsafe_allow_html=True)

    mostrar_amarelo  = st.sidebar.checkbox("Mostrar alertas amarelos",  value=True)
    mostrar_vermelho = st.sidebar.checkbox("Mostrar alertas vermelhos", value=True)

    st.sidebar.markdown(f"""
    <div style="height:1px; background:{C['border']}; margin:20px 0 16px 0;"></div>
    <div style="font-size:10px; color:{C['text_muted']}; line-height:1.6;">
      <span style="color:{C['accent']}; font-weight:600;">Fonte:</span><br>
      Tesouro Nacional · RTN<br>
      <span style="font-size:9px; opacity:0.7;">Secretaria do Tesouro Nacional</span>
    </div>
    """, unsafe_allow_html=True)

    return {
        "ano": ano_sel,
        "mes": mes_sel,
        "mostrar_amarelo":  mostrar_amarelo,
        "mostrar_vermelho": mostrar_vermelho,
    }


# ── Funções auxiliares para consultar a RTN ───────────────────────────────
# Estas funções evitam repetição de código ao buscar valores na série RTN.
# Todas recebem o DataFrame completo e filtram internamente.

def _rtn_serie(df_rtn: pd.DataFrame, prefixo: str) -> pd.DataFrame:
    """Filtra o DataFrame da RTN pelas linhas cujo indicador começa com 'prefixo'."""
    return df_rtn[df_rtn["discriminacao"].str.startswith(prefixo)]


def _rtn_valor(df_rtn, prefixo, a, m, col):
    """Retorna o valor de um indicador em um mês/ano específico. None se não encontrado."""
    sub = _rtn_serie(df_rtn, prefixo)
    row = sub[(sub["ano"] == a) & (sub["mes"] == m)][col]
    return float(row.iloc[0]) if len(row) == 1 else None


def _rtn_soma_12m(df_rtn, prefixo, a, m, col):
    """
    Retorna a soma (ou média, para % do PIB) dos 12 meses terminando em (a, m) inclusive.

    Para que serve: calcular o "acumulado 12 meses", que é o padrão para
    comparar desempenho fiscal sem distorção de sazonalidade.

    Para % do PIB retorna a MÉDIA porque cada valor já é uma taxa anualizada;
    a média dos 12 meses equivale ao total anual / PIB × 100.
    Para valores em R$, retorna a SOMA dos 12 meses.

    Retorna None se houver menos de 6 meses com dados (insuficiente para ser representativo).
    """
    sub = _rtn_serie(df_rtn, prefixo)
    m_ini = m + 1
    a_ini = a - 1
    if m_ini > 12:
        m_ini -= 12
        a_ini += 1
    mask = (
        ((sub["ano"] > a_ini) | ((sub["ano"] == a_ini) & (sub["mes"] >= m_ini))) &
        ((sub["ano"] < a)     | ((sub["ano"] == a)     & (sub["mes"] <= m)))
    )
    vals = sub[mask][col].dropna()
    if len(vals) < 6:
        return None
    return float(vals.mean()) if col == "pct_pib" else float(vals.sum())


def _rtn_delta(df_rtn, prefixo, a, m, col, fn=None):
    """
    Calcula a variação percentual mês a mês (m/m).
    Se 'fn' for fornecida, usa essa função para calcular o valor (ex: soma 12m).
    Sem 'fn', usa o valor pontual do mês.
    Retorna None se não houver dados suficientes.
    """
    if fn is None:
        fn = lambda p, aa, mm: _rtn_valor(df_rtn, p, aa, mm, col)
    atual = fn(prefixo, a, m)
    m_ant, a_ant = (m - 1, a) if m > 1 else (12, a - 1)
    ant = fn(prefixo, a_ant, m_ant)
    if atual is None or ant is None or ant == 0:
        return None
    return round((atual - ant) / abs(ant) * 100, 1)


def _fmt_rtn(v, is_pib: bool):
    """
    Formata um valor da RTN para exibição no painel.
    - Se is_pib=True: formata como porcentagem (ex: "2,3%")
    - Se is_pib=False: formata como R$ bilhões (ex: "R$ 1.234,5 bi")
      (a RTN armazena em R$ milhões, então divide por 1.000 para obter bilhões)
    - Retorna "—" para valores nulos.
    """
    if v is None:
        return "—"
    sinal = "−" if v < 0 else ""
    if is_pib:
        return f"{sinal}{_fmt(abs(v), 1)}%"
    return f"R$ {sinal}{_fmt(abs(v) / 1e3, 1)} bi"


# ── Constantes de negócio ─────────────────────────────────────────────────
# Estas listas definem QUAIS séries da RTN aparecem em cada parte do painel.
# Os prefixos (ex: "3. ", "4. ") correspondem à numeração oficial da RTN:
#   1.  = Receita Total
#   3.  = Receita Líquida (após transferências por repartição)
#   4.  = Despesa Total
#   4.1 = Benefícios Previdenciários
#   4.2 = Pessoal e Encargos Sociais
#   4.3 = Outras Despesas Obrigatórias
#   4.4.2 = Despesas Discricionárias
#   5.  = Resultado Primário (Receita Líquida - Despesa Total)
#   10. = Resultado Nominal (Primário + Juros)

# KPIs principais do Observatório Fiscal (4 cards no topo)
KPIS_RTN = [
    ("3. ",  "Receita Líquida",    "Receita Total menos Transferências por Repartição."),
    ("4. ",  "Despesa Total",      "Previdência + Pessoal + Obrigatórias + Discricionárias."),
    ("5. ",  "Result. Primário",   "Receita Líquida - Despesa Total. Negativo = déficit."),
    ("10.", "Result. Nominal",    "Resultado Primário + Juros Nominais. Negativo = déficit."),
]

# Categorias de despesa para o gráfico de composição (barras horizontais)
COMP_DESPESA = [
    ("4.1 ",   "Benef. Previdenciários"),
    ("4.2 ",   "Pessoal e Encargos Sociais"),
    ("4.3 ",   "Outras Despesas Obrigatórias"),
    ("4.4.1 ", "Obrigatórias com Controle de Fluxo (Saúde, Educação e Benefícios Sociais)"),
    ("4.4.2",  "Despesas Discricionárias"),
]

# Rubricas detalhadas (subitens de 4.3 e 4.4.1) para o gráfico "Rubricas (top 5)"
# São os itens mais granulares da RTN dentro de Outras Obrigatórias e
# Obrigatórias com Controle de Fluxo — permite identificar o que mais pesou
RUBRICAS_DESP = [
    "4.3.01", "4.3.02", "4.3.03", "4.3.04", "4.3.05",
    "4.3.06", "4.3.07", "4.3.08", "4.3.09", "4.3.10",
    "4.3.11", "4.3.12", "4.3.13", "4.3.14", "4.3.15",
    "4.3.16", "4.3.17", "4.3.18", "4.3.19", "4.3.20",
    "4.4.1.1", "4.4.1.2", "4.4.1.3", "4.4.1.4",
]

# Séries monitoradas pela aba de Alertas (detecção de anomalias via z-score)
SERIES_ALERTA = [
    ("3. ",   "Receita Líquida"),
    ("4. ",   "Despesa Total"),
    ("4.1 ",  "Benef. Previdenciários"),
    ("4.2 ",  "Pessoal e Encargos"),
    ("4.3 ",  "Outras Obrigatórias"),
    ("4.4.2", "Discricionárias"),
    ("5. ",   "Result. Primário"),
    ("10.",   "Result. Nominal"),
]


# -- Helpers reutilizáveis para composição de despesas ---------------------

def _card_metrica_grande(label: str, valor: str, delta: str | None = None):
    """Cartão HTML de métrica grande, centralizado e com destaque visual."""
    if delta:
        cor = C["negative"] if delta.startswith("+") else C["positive"]
        delta_html = (
            f'<div style="font-size:15px; color:{cor}; margin-top:8px; font-weight:500;">'
            f'{delta}</div>'
        )
    else:
        delta_html = ""
    st.markdown(f"""
<div style="
    background: linear-gradient(135deg, {C['bg2']} 0%, {C['bg3']} 60%);
    border: 1px solid {C['border']};
    border-radius: 12px;
    padding: 24px 32px;
    text-align: center;
    margin-bottom: 12px;
">
    <div style="font-size:10px; text-transform:uppercase; letter-spacing:2px;
                color:{C['text_muted']}; margin-bottom:8px;">
        {label}
    </div>
    <div style="font-size:48px; font-weight:700; color:{C['text']};
                letter-spacing:-1px; line-height:1.15;">
        {valor}
    </div>
    {delta_html}
</div>
""", unsafe_allow_html=True)


def _render_comp_rtn(fn_valor, is_pib, titulo):
    """Gráfico de barras horizontais com composição das categorias RTN."""
    import textwrap

    items = [{"Categoria": n, "Valor": fn_valor(p)} for p, n in COMP_DESPESA]
    items = [i for i in items if i["Valor"] is not None and pd.notna(i["Valor"])]
    if not items:
        st.info("Sem dados de composição.")
        return
    df_c = pd.DataFrame(items).sort_values("Valor", ascending=True)

    def _wrap(s: str) -> str:
        return "<br>".join(textwrap.wrap(str(s), 24))

    df_c["label"] = df_c["Categoria"].apply(_wrap)
    n_bars = len(df_c)
    altura = max(220, n_bars * 72 + 70)

    if is_pib:
        x_vals  = df_c["Valor"]
        x_title = "% do PIB"
        texts   = df_c["Valor"].apply(lambda v: f"{_fmt(v, 1)}%")
    else:
        x_vals  = df_c["Valor"] / 1e3
        x_title = "R$ bilhões"
        texts   = df_c["Valor"].apply(lambda v: f"R$ {_fmt(v / 1e3, 1)} bi")
    fig = go.Figure(go.Bar(
        x=x_vals, y=df_c["label"], orientation="h",
        marker_color=C["despesa"],
        marker_line_width=0,
        text=texts, textposition="outside",
        textfont=dict(size=11, color=C["text_dim"]),
        cliponaxis=False,
    ))
    x_max = float(x_vals.max())
    fig.update_layout(
        xaxis_title=x_title,
        xaxis=dict(range=[0, x_max * 1.55]),
    )
    _plotly_dark(fig, height=altura, margin=dict(l=175, r=20, t=10, b=30))
    fig.update_layout(title_text="", showlegend=False)
    st.markdown(
        f"<div style='font-size:12px; font-weight:600; color:{C['text']}; margin-bottom:4px;'>"
        f"{titulo}</div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(fig, use_container_width=True, key=titulo)


def _folhas_rtn(df_rtn: pd.DataFrame) -> list:
    """
    Identifica as séries "folha" da RTN — os itens mais granulares
    disponíveis dentro das despesas (séries que começam com "4.").

    Em uma hierarquia como:
       4. Despesa Total
         4.1 Benefícios Previdenciários
         4.3 Outras Obrigatórias
           4.3.01 LOAS/RMV
           4.3.02 Abono Salarial

    As "folhas" são os itens que não têm subitens (ex: 4.3.01, 4.3.02).
    O total (4.) e os grupos (4.1, 4.3) são excluídos porque somá-los
    ao lado de suas subdivisões geraria dupla contagem.
    """
    series_desp = [s for s in df_rtn["discriminacao"].unique() if s.startswith("4.")]

    def _cod(s: str) -> str:
        return s.split(" ")[0].rstrip(".")

    codigos = {_cod(s) for s in series_desp}
    folhas = []
    for serie in series_desp:
        cod = _cod(serie)
        if cod == "4":
            continue  # total geral
        is_parent = any(c != cod and c.startswith(cod + ".") for c in codigos)
        if not is_parent:
            folhas.append(serie)
    return folhas


def _rtn_top_n_df(
    df_rtn: pd.DataFrame, ano: int, mes: int, col_val: str,
    n: int = 5, modo: str = "mes",
) -> pd.DataFrame:
    """Top N itens de RUBRICAS_DESP da RTN por valor no período (mês ou acumulado 12m)."""
    rows = []
    for prefixo in RUBRICAS_DESP:
        v = (
            _rtn_valor(df_rtn, prefixo, ano, mes, col_val)
            if modo == "mes"
            else _rtn_soma_12m(df_rtn, prefixo, ano, mes, col_val)
        )
        if v is not None and v > 0:
            match = df_rtn[df_rtn["discriminacao"].str.startswith(prefixo)]["discriminacao"]
            serie = match.iloc[0] if not match.empty else prefixo
            partes = serie.split(" ", 1)
            nome = partes[1].strip() if len(partes) > 1 else serie
            rows.append({"discriminacao": serie, "nome": nome, "valor": v})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).nlargest(n, "valor")


def _render_top_n_rtn(
    df_rtn: pd.DataFrame, ano: int, mes: int, col_val: str, is_pib: bool,
    titulo: str, n: int = 5, modo: str = "mes",
):
    """Gráfico horizontal top N rubricas a partir das séries folha da RTN.
    Fonte única e consistente com o gráfico de composição."""
    import textwrap

    df = _rtn_top_n_df(df_rtn, ano, mes, col_val, n, modo)
    if df.empty:
        st.info("Sem dados RTN para o período.")
        return
    df = df.sort_values("valor", ascending=True).copy()

    def _wrap(s: str) -> str:
        return "<br>".join(textwrap.wrap(str(s), 24))

    df["label"] = df["nome"].apply(_wrap)

    n_bars = len(df)
    altura = max(220, n_bars * 72 + 70)

    if is_pib:
        x_vals  = df["valor"]
        x_title = "% do PIB"
        texts   = df["valor"].apply(lambda v: f"{_fmt(v, 1)}%")
    else:
        x_vals  = df["valor"] / 1e3   # corrente_milhoes → bilhões
        x_title = "R$ bilhões"
        texts   = df["valor"].apply(lambda v: f"R$ {_fmt(v / 1e3, 1)} bi")

    x_max = float(x_vals.max()) if len(x_vals) > 0 else 1.0

    fig = go.Figure(go.Bar(
        x=x_vals, y=df["label"], orientation="h",
        marker_color=C["warning"],
        marker_line_width=0,
        text=texts, textposition="outside",
        textfont=dict(size=11, color=C["text_dim"]),
        cliponaxis=False,
    ))
    fig.update_layout(
        xaxis_title=x_title,
        xaxis=dict(range=[0, x_max * 1.55]),
    )
    _plotly_dark(fig, height=altura, margin=dict(l=175, r=20, t=10, b=30))
    fig.update_layout(title_text="", showlegend=False)
    st.markdown(
        f"<div style='font-size:12px; font-weight:600; color:{C['text']}; margin-bottom:4px;'>"
        f"{titulo}</div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(fig, use_container_width=True, key=titulo)


def _calcular_base_contador(df_rtn: pd.DataFrame, contador: dict) -> float:
    """
    Calcula o acumulado real de despesas do ano até o mês anterior ao projetado.

    O contador exibe: gastos já realizados (dados reais da RTN) + gasto estimado
    do mês atual (em andamento). Esta função calcula a parcela "já realizada".

    Exemplo: se o contador está prevendo junho/2025, esta função soma
    janeiro a maio/2025 com dados reais da RTN.
    """
    mes_ref = contador.get("mes_referencia", "")
    if not mes_ref:
        return 0.0
    ano_prev = int(mes_ref[:4])
    mes_prev = int(mes_ref[5:7])
    serie = df_rtn[df_rtn["discriminacao"].str.startswith("4. ")]
    mask  = (serie["ano"] == ano_prev) & (serie["mes"] < mes_prev)
    return float(serie[mask]["corrente_milhoes"].sum()) * 1_000_000


def _contador_html(
    acc_base_rs: float,
    taxa_rs: float,
    start_ms: int,
    mes_ref: str,
    mes_ref_fim: str,
    ultimo_dado: str,
) -> str:
    """
    Gera o HTML+JavaScript do contador em tempo real.

    Como funciona:
      - acc_base_rs: total acumulado nos meses com dado real (R$)
      - taxa_rs: R$ por segundo — taxa média sobre 2 meses projetados
      - start_ms: timestamp Unix em milissegundos do início do 1º mês projetado

    O JavaScript calcula:
      total = acc_base + (segundos_desde_início_de_T+1 × taxa)

    E atualiza o display a cada 100ms (10 vezes por segundo),
    criando a ilusão de um contador contínuo.
    A taxa cobre T+1 e T+2, então o contador corre sem interrupção até o fim de T+2.
    """
    ano_ref      = mes_ref[:4]
    mes_ini_fmt  = mes_ref[5:7] + "/" + mes_ref[:4]
    mes_fim_fmt  = mes_ref_fim[5:7] + "/" + mes_ref_fim[:4]
    ult_fmt      = ultimo_dado[5:7] + "/" + ultimo_dado[:4]
    # Exibe "Abr–Mai/2026" quando os dois meses são do mesmo ano,
    # ou "Dez/2025–Jan/2026" quando cruzam a virada de ano.
    if mes_ref[:4] == mes_ref_fim[:4]:
        intervalo_fmt = f"{mes_ref[5:7]}–{mes_fim_fmt}"
    else:
        intervalo_fmt = f"{mes_ini_fmt}–{mes_fim_fmt}"
    return f"""
<div style="
    background: linear-gradient(135deg, {C['bg']} 0%, {C['bg3']} 100%);
    border: 2px solid {C['border']};
    border-radius: 16px;
    padding: 24px 40px 20px;
    text-align: center;
">
    <div style="font-size:10px; letter-spacing:3px; color:{C['accent']};
                font-weight:700; text-transform:uppercase; margin-bottom:10px;">
        Gastos Acumulados do Governo Federal — {ano_ref}
    </div>
    <div id="cnt-valor" style="
        font-size:54px; font-weight:700; color:{C['text']};
        font-family: 'Courier New', monospace;
        letter-spacing:-1px; line-height:1.15;
    ">R$&nbsp;—</div>
    <div id="cnt-info" style="font-size:11px; color:{C['text_muted']}; margin-top:8px;">
        calculando...
    </div>
    <div style="font-size:10px; color:{C['text_muted']}; margin-top:6px; opacity:0.6;">
        Dados reais até {ult_fmt} · Projeção {intervalo_fmt} pela fórmula ratio rolling 12m
        · R$&nbsp;{taxa_rs:,.0f}/s
    </div>
</div>
<script>
(function() {{
    const accBase = {acc_base_rs:.2f};
    const taxa    = {taxa_rs:.4f};
    const startMs = {start_ms};

    function fmt(n) {{
        return 'R$ ' + n.toLocaleString('pt-BR', {{
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }});
    }}

    function update() {{
        const elapsedSec = Math.max(0, (Date.now() - startMs) / 1000);
        const total = accBase + elapsedSec * taxa;
        const bi    = (total / 1e9).toLocaleString('pt-BR', {{
            minimumFractionDigits: 1, maximumFractionDigits: 1
        }});
        document.getElementById('cnt-valor').innerHTML = fmt(total);
        document.getElementById('cnt-info').innerText  = 'R$ ' + bi + ' bilhões';
    }}

    setInterval(update, 100);
    update();
}})();
</script>
"""


# ── Aba 0: Gastos do Governo Federal ─────────────────────────────────────

def aba_gastos_governo(dados, filtros, col_val, is_pib):
    """
    Renderiza a aba "💸 Gastos do Governo Federal".

    Conteúdo:
      - Contador em tempo real (HTML+JS)
      - Card com Despesa Total do mês selecionado
      - Gráfico de composição por categoria (Previdência, Pessoal, etc.)
      - Gráfico Top 5 rubricas mais pesadas
      - Repetição para acumulado 12 meses
      - Explorador de séries de despesa com download CSV
    """
    df_rtn   = dados.get("rtn", pd.DataFrame())
    contador = dados.get("contador", {})

    if df_rtn.empty:
        st.info("Execute `python pipelines/rtn/load.py` para baixar os dados.")
        return

    ano, mes = filtros["ano"], filtros["mes"]

    # ── Contador em tempo real ─────────────────────────────────────────────
    if contador:
        mes_ref     = contador.get("mes_referencia", "")
        mes_ref_fim = contador.get("mes_referencia_fim", mes_ref)
        ano_prev    = int(mes_ref[:4])
        mes_prev    = int(mes_ref[5:7])
        start_dt    = datetime(ano_prev, mes_prev, 1, 0, 0, 0, tzinfo=timezone.utc)
        start_ms    = int(start_dt.timestamp() * 1000)
        acc_base_rs = _calcular_base_contador(df_rtn, contador)
        taxa_rs     = contador.get("taxa_por_segundo_rs", 0.0)
        ultimo_dado = contador.get("ultimo_dado_rtn", "")

        st.components.v1.html(
            _contador_html(acc_base_rs, taxa_rs, start_ms, mes_ref, mes_ref_fim, ultimo_dado),
            height=168,
        )
    else:
        st.info(
            "Execute `python pipelines/contador_fiscal.py` para habilitar "
            "o contador em tempo real."
        )

    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

    # ── Helpers de valor ──────────────────────────────────────────────────
    def val(p):    return _rtn_valor(df_rtn, p, ano, mes, col_val)
    def s12(p):    return _rtn_soma_12m(df_rtn, p, ano, mes, col_val)
    def dlt(p):    return _rtn_delta(df_rtn, p, ano, mes, col_val)
    def dlt12(p):  return _rtn_delta(
        df_rtn, p, ano, mes, col_val,
        fn=lambda pp, aa, mm: _rtn_soma_12m(df_rtn, pp, aa, mm, col_val),
    )
    def fv(v):     return _fmt_rtn(v, is_pib)
    def ds(d):
        if d is None:
            return None
        return f"{'+' if d > 0 else ''}{_fmt(d, 1)}% m/m"

    # ── Bloco: Mês ────────────────────────────────────────────────────────
    st.markdown(
        f"<div class='kpi-sub'>Mês: <strong style='color:{C['text']}'>"
        f"{MES_LABELS.get(mes, mes)}/{ano}</strong></div>",
        unsafe_allow_html=True,
    )
    _card_metrica_grande(
        f"Despesa Total — {MES_LABELS.get(mes, mes)}/{ano}",
        fv(val("4. ")),
        ds(dlt("4. ")),
    )
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        _render_comp_rtn(
            fn_valor=lambda p: val(p),
            is_pib=is_pib,
            titulo=f"Composição — {MES_LABELS.get(mes, mes)}/{ano}",
        )
    with col_g2:
        _render_top_n_rtn(
            df_rtn, ano, mes, col_val, is_pib,
            titulo=f"Rubricas (top 5) — {MES_LABELS.get(mes, mes)}/{ano}",
            n=5, modo="mes",
        )

    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

    # ── Bloco: Acumulado 12 meses ─────────────────────────────────────────
    st.markdown("<div class='kpi-sub'>Acumulado 12 meses</div>", unsafe_allow_html=True)
    sufixo_12m = "média 12m" if is_pib else "soma 12m"
    _card_metrica_grande(
        f"Despesa Total — Acumulado 12 meses ({sufixo_12m})",
        fv(s12("4. ")),
        ds(dlt12("4. ")),
    )
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        _render_comp_rtn(
            fn_valor=lambda p: s12(p),
            is_pib=is_pib,
            titulo="Composição — acumulado 12 meses",
        )
    with col_g2:
        _render_top_n_rtn(
            df_rtn, ano, mes, col_val, is_pib,
            titulo="Rubricas (top 5) — acumulado 12 meses",
            n=5, modo="12m",
        )

    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

    # ── Explorador de séries de despesa (RTN, filtrado a "4.") ────────────
    st.markdown(
        f"<div style='font-size:14px; font-weight:600; color:{C['text']}; margin-bottom:10px;'>"
        "Explorador de Despesas</div>",
        unsafe_allow_html=True,
    )

    series_desp = sorted([s for s in df_rtn["discriminacao"].unique() if s.startswith("4.")])
    serie_sel   = st.selectbox("Série de despesa", series_desp, key="gastos_serie_desp")

    sub = df_rtn[df_rtn["discriminacao"] == serie_sel].sort_values(["ano", "mes"]).copy()
    if not sub.empty:
        sub["data"] = pd.to_datetime(
            sub["ano"].astype(str) + "-" + sub["mes"].astype(str).str.zfill(2) + "-01"
        )
        y_label  = "% do PIB" if is_pib else "R$ Milhões"
        data_fim = pd.Timestamp(f"{ano}-{mes:02d}-01")
        data_ini = data_fim - pd.DateOffset(years=3)

        fig = px.line(
            sub, x="data", y=col_val,
            labels={col_val: y_label, "data": ""},
            title=serie_sel,
            color_discrete_sequence=[C["despesa"]],
        )
        fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.15)", opacity=0.8)
        fig.update_layout(
            xaxis=dict(
                tickformat="%m/%Y",
                range=[str(data_ini.date()), str((data_fim + pd.DateOffset(months=1)).date())],
                rangeslider=dict(**_RSLD),
                rangeselector=dict(**_RSEL, buttons=[
                    dict(count=1,  label="1a",  step="year", stepmode="backward"),
                    dict(count=3,  label="3a",  step="year", stepmode="backward"),
                    dict(count=5,  label="5a",  step="year", stepmode="backward"),
                    dict(step="all", label="Máx"),
                ]),
            ),
        )
        _plotly_dark(fig, height=400, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True, key="gastos_explorador")

        p_sel   = ano * 100 + mes
        sub_p   = sub["ano"] * 100 + sub["mes"]
        ultimos = (
            sub[sub_p <= p_sel]
            .tail(24)
            .sort_values(["ano", "mes"], ascending=False)
            .copy()
        )
        ultimos["Período"] = ultimos["mes"].map(MES_LABELS) + "/" + ultimos["ano"].astype(str)
        ultimos["Valor"]   = ultimos[col_val].apply(
            lambda v: _fmt_rtn(v, is_pib) if pd.notna(v) else "—"
        )
        st.dataframe(ultimos[["Período", "Valor"]], hide_index=True, use_container_width=True)

        csv = sub.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Baixar CSV",
            data=csv,
            file_name=f"despesa_{serie_sel[:40].replace(' ', '_').replace('.', '')}.csv",
            mime="text/csv",
        )

    st.caption(
        "Fonte: RTN · Secretaria do Tesouro Nacional · "
        "Séries de Despesa Total e subcategorias."
    )


# ── Aba 1: Observatório Fiscal ────────────────────────────────────────────

def aba_observatorio_fiscal(dados, filtros, col_val, is_pib, opcao_sel):
    """
    Renderiza a aba "🔭 Observatório Fiscal".

    Conteúdo:
      - 4 KPIs do mês: Receita Líquida, Despesa Total, Resultado Primário, Resultado Nominal
      - Os mesmos 4 KPIs no acumulado 12 meses
      - Gráfico de linha: Receita × Despesa × Resultado Primário
      - Gráfico de área: Trajetória do Resultado Primário acumulado 12 meses
      - Gráfico de barras: Resultado Primário acumulado no ano
      - Tabela comparativa com variações anuais
    """
    df = dados.get("rtn", pd.DataFrame())
    if df.empty:
        st.info("Execute `python pipelines/rtn/load.py` para baixar os dados.")
        return

    ano, mes = filtros["ano"], filtros["mes"]

    def val(p):   return _rtn_valor(df, p, ano, mes, col_val)
    def s12(p):   return _rtn_soma_12m(df, p, ano, mes, col_val)
    def dlt(p):   return _rtn_delta(df, p, ano, mes, col_val)
    def dlt12(p): return _rtn_delta(
        df, p, ano, mes, col_val,
        fn=lambda pp, aa, mm: _rtn_soma_12m(df, pp, aa, mm, col_val),
    )
    def fv(v):  return _fmt_rtn(v, is_pib)
    def ds(d):
        if d is None:
            return None
        return f"{'+' if d > 0 else ''}{_fmt(d, 1)}% m/m"

    # ── 1. KPIs do mês ────────────────────────────────────────────────────
    st.markdown(
        f"<div class='kpi-sub'>Mês: <strong style='color:{C['text']}'>"
        f"{MES_LABELS.get(mes, mes)}/{ano}</strong></div>",
        unsafe_allow_html=True,
    )
    for col, (prefixo, label, help_) in zip(st.columns(4), KPIS_RTN):
        with col:
            st.metric(label, fv(val(prefixo)), delta=ds(dlt(prefixo)), help=help_)

    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

    st.markdown("<div class='kpi-sub'>Acumulado 12 meses</div>", unsafe_allow_html=True)
    sufixo_12m = "(média 12m)" if is_pib else "(soma 12m)"
    for col, (prefixo, label, help_) in zip(st.columns(4), KPIS_RTN):
        with col:
            st.metric(
                f"{label} {sufixo_12m}",
                fv(s12(prefixo)),
                delta=ds(dlt12(prefixo)),
                help=f"{help_} {'Média' if is_pib else 'Soma'} dos últimos 12 meses.",
            )

    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

    # ── 2. Receita × Despesa × Resultado Primário ─────────────────────────
    y_label = "% do PIB" if is_pib else "R$ Milhões"
    st.markdown(
        f"<div style='font-size:13px; font-weight:600; color:{C['text']}; margin-bottom:8px;'>"
        "Receita × Despesa × Resultado Primário</div>",
        unsafe_allow_html=True,
    )
    p_sel = ano * 100 + mes
    linhas = []
    for prefixo, nome, _ in KPIS_RTN[:3]:
        sub = _rtn_serie(df, prefixo).copy()
        sub = sub[sub["ano"] * 100 + sub["mes"] <= p_sel].sort_values(["ano", "mes"])
        sub["serie"] = nome
        sub["data"]  = pd.to_datetime(
            sub["ano"].astype(str) + "-" + sub["mes"].astype(str).str.zfill(2) + "-01"
        )
        linhas.append(sub[["data", col_val, "serie"]].rename(columns={col_val: "valor"}))
    df_chart = pd.concat(linhas, ignore_index=True)
    fig = px.line(
        df_chart, x="data", y="valor", color="serie",
        color_discrete_map={
            "Receita Líquida":  C["receita"],
            "Despesa Total":    C["despesa"],
            "Result. Primário": C["resultado"],
        },
        labels={"valor": y_label, "data": "", "serie": ""},
    )
    fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.15)", opacity=0.8)
    data_fim = pd.Timestamp(f"{ano}-{mes:02d}-01")
    data_ini = data_fim - pd.DateOffset(years=3)
    fig.update_layout(
        legend=dict(
            orientation="h", yanchor="top", y=-0.12,
            xanchor="center", x=0.5, title="",
            font=dict(size=12, color=C["text"]),
            bgcolor="rgba(13,27,46,0.9)",
            bordercolor=C["border"], borderwidth=1,
        ),
        xaxis=dict(
            tickformat="%m/%Y",
            tickfont=dict(size=11, color=C["text_dim"]),
            title_font=dict(size=12),
            range=[str(data_ini.date()), str((data_fim + pd.DateOffset(months=1)).date())],
            rangeslider=dict(**_RSLD),
            rangeselector=dict(**_RSEL, buttons=[
                dict(count=1,  label="1a",  step="year", stepmode="backward"),
                dict(count=3,  label="3a",  step="year", stepmode="backward"),
                dict(count=5,  label="5a",  step="year", stepmode="backward"),
                dict(step="all", label="Máx"),
            ]),
        ),
    )
    _plotly_dark(fig, height=420, margin=dict(l=10, r=10, t=20, b=60))
    st.plotly_chart(fig, use_container_width=True, key="obs_receita_despesa")

    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

    # ── 3. Trajetória fiscal — Resultado Primário acumulado 12 meses ──────
    st.markdown(
        f"<div style='font-size:13px; font-weight:600; color:{C['text']}; margin-bottom:4px;'>"
        "Trajetória fiscal — Resultado Primário acumulado 12 meses</div>",
        unsafe_allow_html=True,
    )
    st.caption("Soma rolling de 12 meses. Linha abaixo de zero = déficit acumulado.")
    sub_res = _rtn_serie(df, "5. ").sort_values(["ano", "mes"]).copy()
    traj    = []
    for _, row in sub_res.iterrows():
        a, m = int(row["ano"]), int(row["mes"])
        if a * 100 + m > p_sel:
            break
        v = _rtn_soma_12m(df, "5. ", a, m, col_val)
        if v is not None:
            traj.append({"label": f"{m:02d}/{a}", "valor": v / (1 if is_pib else 1e3),
                         "periodo": a * 100 + m})
    if traj:
        df_traj = pd.DataFrame(traj).sort_values("periodo")
        df_traj["data"] = pd.to_datetime(
            df_traj["label"].str[3:] + "-" + df_traj["label"].str[:2] + "-01"
        )
        y_title = "% do PIB (12m)" if is_pib else "R$ bilhões (12m)"
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=df_traj["data"], y=df_traj["valor"],
            mode="lines+markers", fill="tozeroy",
            line=dict(color=C["resultado"], width=2),
            fillcolor="rgba(56,189,248,0.08)",
            marker=dict(size=3, color=C["resultado"]),
        ))
        fig2.add_hline(y=0, line_dash="dot", line_color=C["negative"], opacity=0.5)
        data_fim2 = pd.Timestamp(f"{ano}-{mes:02d}-01")
        data_ini2 = data_fim2 - pd.DateOffset(years=5)
        fig2.update_layout(
            yaxis_title=y_title,
            showlegend=False,
            xaxis=dict(
                tickformat="%m/%Y",
                range=[str(data_ini2.date()), str((data_fim2 + pd.DateOffset(months=1)).date())],
                rangeslider=dict(**_RSLD),
                rangeselector=dict(**_RSEL, buttons=[
                    dict(count=2,  label="2a",  step="year", stepmode="backward"),
                    dict(count=5,  label="5a",  step="year", stepmode="backward"),
                    dict(count=10, label="10a", step="year", stepmode="backward"),
                    dict(step="all", label="Máx"),
                ]),
            ),
        )
        _plotly_dark(fig2, height=400, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig2, use_container_width=True, key="obs_trajetoria")

    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

    # ── 4. Resultado primário acumulado no ano ────────────────────────────
    st.markdown(
        f"<div style='font-size:12px; font-weight:600; color:{C['text']}; margin-bottom:4px;'>"
        f"Resultado primário acumulado {ano}</div>",
        unsafe_allow_html=True,
    )
    df_ytd = _rtn_serie(df, "5. ")
    df_ytd = df_ytd[(df_ytd["ano"] == ano) & (df_ytd["mes"] <= mes)].sort_values("mes").copy()
    if not df_ytd.empty:
        df_ytd["acumulado"] = df_ytd[col_val].cumsum()
        df_ytd["label"]     = df_ytd["mes"].map(MES_LABELS)
        y_acum  = df_ytd["acumulado"] / (1 if is_pib else 1e3)
        y_title = "% do PIB (acum.)" if is_pib else "R$ bilhões (acum.)"
        texts_y = [
            f"{_fmt(v, 1)}%" if is_pib else f"R$ {_fmt(v, 1)} bi"
            for v in y_acum
        ]
        fig3 = go.Figure(go.Bar(
            x=df_ytd["label"], y=y_acum,
            marker_color=[C["positive"] if v >= 0 else C["negative"] for v in y_acum],
            marker_line_width=0,
            text=texts_y, textposition="outside",
            textfont=dict(size=11, color=C["text_dim"]),
        ))
        fig3.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.15)", opacity=0.8)
        fig3.update_layout(yaxis_title=y_title)
        _plotly_dark(fig3, height=260, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig3, use_container_width=True, key="obs_ytd_barras")

    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

    # ── 5. Painel de indicadores ──────────────────────────────────────────
    st.markdown(
        f"<div style='font-size:13px; font-weight:600; color:{C['text']}; margin-bottom:12px;'>"
        f"Painel de indicadores — {MES_LABELS.get(mes, mes)}/{ano}</div>",
        unsafe_allow_html=True,
    )
    SERIES_VIG = [
        ("3. ",   "Receita Líquida"),
        ("4. ",   "Despesa Total"),
        ("4.1 ",  "   Benef. Previdenciários"),
        ("4.2 ",  "   Pessoal e Encargos"),
        ("4.3 ",  "   Outras Obrigatórias"),
        ("4.4.2", "   Discricionárias"),
        ("5. ",   "Resultado Primário"),
        ("10.",   "Resultado Nominal"),
    ]
    rows = []
    for prefixo, nome in SERIES_VIG:
        sub = df[df["discriminacao"].str.startswith(prefixo)].sort_values(["ano", "mes"])
        if sub.empty:
            continue
        row_sel = sub[(sub["ano"] == ano) & (sub["mes"] == mes)]
        if row_sel.empty:
            continue
        v_atual = row_sel[col_val].iloc[0]
        v_12m   = _rtn_soma_12m(df, prefixo, ano, mes, col_val)
        hist    = sub[(sub["mes"] == mes) & (sub["ano"] < ano)].tail(3)
        v_media = hist[col_val].mean() if not hist.empty else None
        row_ant = sub[(sub["ano"] == ano - 1) & (sub["mes"] == mes)]
        v_ant   = row_ant[col_val].iloc[0] if not row_ant.empty else None
        var_yoy = None
        if v_ant is not None and v_ant != 0:
            var_yoy = round((v_atual - v_ant) / abs(v_ant) * 100, 1)
        rows.append({
            "Indicador":    nome,
            "Mês atual":    _fmt_rtn(v_atual, is_pib),
            "Acum. 12m":    _fmt_rtn(v_12m, is_pib),
            f"{ano - 1}":   _fmt_rtn(v_ant, is_pib),
            "Var. a/a":     (f"{'+' if var_yoy > 0 else ''}{_fmt(var_yoy, 1)}%"
                             if var_yoy is not None else "—"),
            "Média 3 anos": _fmt_rtn(v_media, is_pib) if v_media is not None else "—",
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    unidade = "% do PIB (média 12m)" if is_pib else "R$ milhões"
    st.caption(
        f"Fonte: RTN · Secretaria do Tesouro Nacional · {unidade} · "
        "Atualização: `python pipelines/rtn/load.py`"
    )


# ── Aba 2: Alertas ────────────────────────────────────────────────────────

def aba_alertas_rtn(dados, filtros, col_val, is_pib):
    """
    Renderiza a aba "🚨 Alertas".

    Detecta valores estatisticamente anômalos usando z-score:
      z = (valor_do_mês - média_24m_anteriores) / desvio_padrão_24m_anteriores

    Interpretação do z-score:
      |z| ≥ 3,0σ → ALERTA VERMELHO (evento muito raro, < 0,3% de probabilidade)
      |z| ≥ 2,0σ → ALERTA AMARELO  (evento incomum, < 5% de probabilidade)

    O .shift(1) garante que o mês atual não entra no cálculo da sua própria
    média histórica (o que seria "trapacear" — data leakage).

    O usuário pode filtrar alertas por nível via sidebar.
    """
    df = dados.get("rtn", pd.DataFrame())
    if df.empty:
        st.info("Dados RTN não encontrados.")
        return

    ano, mes = filtros["ano"], filtros["mes"]

    alertas = []
    for prefixo, nome in SERIES_ALERTA:
        sub = df[df["discriminacao"].str.startswith(prefixo)].sort_values(["ano", "mes"]).copy()
        if len(sub) < 8:
            continue
        vals  = sub[col_val].fillna(0)
        media = vals.shift(1).rolling(24, min_periods=6).mean()
        std   = vals.shift(1).rolling(24, min_periods=6).std()
        sub["zscore"] = (vals - media) / std.replace(0, np.nan)

        row_sel = sub[(sub["ano"] == ano) & (sub["mes"] == mes)]
        if row_sel.empty:
            continue
        z = row_sel["zscore"].iloc[0]
        v = row_sel[col_val].iloc[0]
        if pd.isna(z):
            continue

        if abs(z) >= 3.0:
            nivel = "vermelho"
        elif abs(z) >= 2.0:
            nivel = "amarelo"
        else:
            continue

        alertas.append({"serie": nome, "zscore": z, "valor": v, "nivel": nivel})

    niveis = []
    if filtros["mostrar_vermelho"]:
        niveis.append("vermelho")
    if filtros["mostrar_amarelo"]:
        niveis.append("amarelo")
    alertas = [a for a in alertas if a["nivel"] in niveis]

    if not alertas:
        st.success(
            f"Nenhuma anomalia detectada em "
            f"{MES_LABELS.get(mes, mes)}/{ano} com os filtros selecionados."
        )
        return

    st.markdown(
        f"<div style='font-size:13px; font-weight:600; color:{C['text']}; margin-bottom:12px;'>"
        f"{len(alertas)} alerta(s) em {MES_LABELS.get(mes, mes)}/{ano}"
        f"<span style='font-size:11px; color:{C['text_muted']}; font-weight:400;'>"
        f" — z-score calculado sobre janela de 24 meses</span></div>",
        unsafe_allow_html=True,
    )
    for a in sorted(alertas, key=lambda x: abs(x["zscore"]), reverse=True):
        css   = f"alerta-{a['nivel']}"
        icone = "●" if a["nivel"] == "vermelho" else "◆"
        cor   = C["negative"] if a["nivel"] == "vermelho" else C["warning"]
        v_fmt = _fmt_rtn(a["valor"], is_pib)
        st.markdown(
            f'<div class="{css}">'
            f'<span style="color:{cor}; font-size:14px;">{icone}</span> '
            f'<strong>{a["serie"]}</strong>'
            f' &nbsp;|&nbsp; Z-score: <strong style="color:{cor}">{a["zscore"]:.1f}σ</strong>'
            f' &nbsp;|&nbsp; Valor: <strong>{v_fmt}</strong>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── Aba 3: Explorador de séries RTN ──────────────────────────────────────

def aba_explorador_rtn(dados, filtros, col_val, is_pib, opcao_sel):
    """
    Renderiza a aba "📋 Explorador".

    Permite que o usuário selecione qualquer série disponível na RTN
    (receitas, despesas, resultado, etc.) e veja o histórico completo
    em gráfico interativo com botões de período (1a, 3a, 5a, Máx).

    Também exibe tabela com os últimos 24 meses e botão de download CSV.
    Útil para análises ad-hoc e exportação de dados para Excel.
    """
    df = dados.get("rtn", pd.DataFrame())
    if df.empty:
        st.info("Dados RTN não encontrados.")
        return

    ano, mes = filtros["ano"], filtros["mes"]

    series_disp = sorted(df["discriminacao"].unique().tolist())
    serie_sel   = st.selectbox("Série fiscal", series_disp)

    sub = df[df["discriminacao"] == serie_sel].sort_values(["ano", "mes"]).copy()
    if sub.empty:
        st.info("Série sem dados.")
        return

    sub["data"] = pd.to_datetime(
        sub["ano"].astype(str) + "-" + sub["mes"].astype(str).str.zfill(2) + "-01"
    )
    y_label = "% do PIB" if is_pib else "R$ Milhões"

    fig = px.line(
        sub, x="data", y=col_val,
        labels={col_val: y_label, "data": ""},
        title=serie_sel,
        color_discrete_sequence=[C["resultado"]],
    )
    fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.15)", opacity=0.8)
    data_fim = pd.Timestamp(f"{ano}-{mes:02d}-01")
    data_ini = data_fim - pd.DateOffset(years=3)
    fig.update_layout(
        xaxis=dict(
            tickformat="%m/%Y",
            range=[str(data_ini.date()), str((data_fim + pd.DateOffset(months=1)).date())],
            rangeslider=dict(**_RSLD),
            rangeselector=dict(**_RSEL, buttons=[
                dict(count=1,  label="1a",  step="year", stepmode="backward"),
                dict(count=3,  label="3a",  step="year", stepmode="backward"),
                dict(count=5,  label="5a",  step="year", stepmode="backward"),
                dict(step="all", label="Máx"),
            ]),
        ),
    )
    _plotly_dark(fig, height=400, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, use_container_width=True, key="explorador_serie")

    # Ultimos 24 meses em tabela
    st.markdown(
        f"<div style='font-size:12px; font-weight:600; color:{C['text']}; margin-bottom:6px;'>"
        "Últimos 24 meses</div>",
        unsafe_allow_html=True,
    )
    p_sel   = ano * 100 + mes
    sub_p   = sub["ano"] * 100 + sub["mes"]
    ultimos = sub[sub_p <= p_sel].tail(24).sort_values(["ano", "mes"], ascending=False).copy()
    ultimos["Período"] = ultimos["mes"].map(MES_LABELS) + "/" + ultimos["ano"].astype(str)
    ultimos["Valor"]   = ultimos[col_val].apply(
        lambda v: _fmt_rtn(v, is_pib) if pd.notna(v) else "—"
    )
    st.dataframe(ultimos[["Período", "Valor"]], hide_index=True, use_container_width=True)

    csv = sub.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Baixar CSV",
        data=csv,
        file_name=f"rtn_{serie_sel[:40].replace(' ', '_').replace('.', '')}.csv",
        mime="text/csv",
    )




# ── Ponto de entrada principal ────────────────────────────────────────────

def main():
    """
    Orquestra a montagem completa do painel:
      1. Carrega os dados (com cache de 1 hora)
      2. Verifica se os dados existem — exibe erro amigável se não
      3. Monta a sidebar com filtros
      4. Exibe o cabeçalho principal com logo e título
      5. Renderiza o seletor de métrica (nominal / real / % PIB)
      6. Chama cada função de aba
      7. Exibe o rodapé com a fonte dos dados

    O Streamlit executa esta função do início ao fim a cada interação
    do usuário — é diferente de uma aplicação web tradicional.
    """
    dados  = carregar_dados()
    df_rtn = dados.get("rtn", pd.DataFrame())

    if df_rtn.empty:
        st.error(
            "Dados RTN não encontrados. "
            "Execute `python pipelines/rtn/load.py` para baixar."
        )
        st.stop()

    filtros = sidebar_filtros(df_rtn)
    if not filtros:
        st.stop()

    logo_b64 = _logo_b64()

    # Logo no header principal (base64 inline)
    logo_img = (
        f'<img src="{logo_b64}" '
        f'height="54" style="border-radius:5px; margin-right:20px; flex-shrink:0;" alt="FIESP"/>'
        if logo_b64 else ""
    )

    # Header principal
    periodo_ref = f"{MES_LABELS.get(filtros['mes'], '')}/{filtros['ano']}"
    st.markdown(f"""
    <div style="
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 18px 28px;
        background: linear-gradient(135deg, {C['bg2']} 0%, {C['bg3']} 100%);
        border: 1px solid {C['border']};
        border-radius: 12px;
        margin-bottom: 20px;
        margin-top: 0;
    ">
      <div style="flex-shrink:0;">
        {logo_img}
      </div>
      <div style="flex:1; text-align:center; padding:0 24px;">
        <div style="font-size:12px; letter-spacing:3px; color:{C['accent']};
                    font-weight:700; text-transform:uppercase; margin-bottom:8px;">
          GESTÃO DE PROJETOS ESPECIAIS
        </div>
        <div style="font-size:42px; font-weight:700; color:{C['text']};
                    letter-spacing:-0.5px; line-height:1.1;">
          Gastômetro FIESP
        </div>
        <div style="font-size:16px; color:{C['text_dim']}; margin-top:6px;">
          Monitoramento dos gastos do Governo Federal
        </div>
      </div>
      <div style="text-align:right; flex-shrink:0;">
        <div style="font-size:9px; color:{C['text_muted']}; text-transform:uppercase;
                    letter-spacing:1.5px; margin-bottom:6px;">Referência</div>
        <div style="font-size:22px; font-weight:700; color:{C['accent']};">
          {periodo_ref}
        </div>
        <div style="font-size:10px; color:{C['text_muted']}; margin-top:4px;">
          Tesouro Nacional · RTN
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Seletor de métrica: permite alternar entre três formas de ver os dados.
    # - Nominal (R$ correntes): o valor em reais do período, sem ajuste
    # - Real (R$ constantes): ajustado pelo IPCA, elimina efeito da inflação
    # - % do PIB: proporção do PIB, permite comparação entre anos e países
    meta_path = DATA_DIR / "rtn" / "metadata.json"
    base_label = "base IPCA"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        base_label = meta.get("base_constante", base_label)

    OPCOES = {
        "Valores nominais (R$)":               "corrente_milhoes",
        f"Valores reais (R$ de {base_label})": "constante_milhoes",
        "% do PIB":                            "pct_pib",
    }
    opcao_sel = st.radio(
        "Métrica", list(OPCOES.keys()),
        horizontal=True, label_visibility="collapsed",
    )
    col_val = OPCOES[opcao_sel]           # nome da coluna a usar nos gráficos
    is_pib  = col_val == "pct_pib"        # flag usada para formatar os valores

    tab0, tab1, tab2, tab3 = st.tabs([
        "💸 Gastos do Governo Federal",
        "🔭 Observatório Fiscal",
        "🚨 Alertas",
        "📋 Explorador",
    ])

    with tab0:
        aba_gastos_governo(dados, filtros, col_val, is_pib)
    with tab1:
        aba_observatorio_fiscal(dados, filtros, col_val, is_pib, opcao_sel)
    with tab2:
        aba_alertas_rtn(dados, filtros, col_val, is_pib)
    with tab3:
        aba_explorador_rtn(dados, filtros, col_val, is_pib, opcao_sel)

    # Footer
    st.markdown(f"""
    <div style="
        margin-top: 32px;
        padding: 16px 20px;
        border-top: 1px solid {C['border']};
        display: flex;
        justify-content: space-between;
        align-items: center;
    ">
      <div style="font-size:11px; color:{C['text_muted']};">
        <span style="color:{C['accent']}; font-weight:600;">Fonte:</span>
        RTN · Secretaria do Tesouro Nacional
      </div>
      <div style="font-size:10px; color:{C['text_muted']}; letter-spacing:1px; text-transform:uppercase;">
        Assessoria Econômica · FIESP
      </div>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
