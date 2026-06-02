"""
dashboard/components/theme.py  —  Recursos compartilhados entre todas as páginas

Importar em cada página:
    from components.theme import C, inject_css, render_navbar, fmt_bi, plotly_dark
    from components.theme import carregar_dados, carregar_geojson_estados
"""

import base64
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import DATA_DIR

LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "fiesp-logo.jpg"

# ── Paleta de cores ────────────────────────────────────────────────────────
C = {
    "bg":          "#050B18",
    "bg2":         "#0D1B2E",
    "bg3":         "#0F2644",
    "border":      "#1E3A5F",
    "primary":     "#1E6FD9",
    "accent":      "#38BDF8",
    "text":        "#E2E8F0",
    "text_dim":    "#94A3B8",
    "text_muted":  "#64748B",
    "positive":    "#22C55E",
    "negative":    "#EF4444",
    "warning":     "#F59E0B",
    "receita":     "#22C55E",
    "despesa":     "#EF4444",
    "resultado":   "#38BDF8",
    "investimento":"#22C55E",
    "corrente":    "#EF4444",
    "nominal":     "#A78BFA",
}

MES_LABELS = {
    1:"Jan",2:"Fev",3:"Mar",4:"Abr",5:"Mai",6:"Jun",
    7:"Jul",8:"Ago",9:"Set",10:"Out",11:"Nov",12:"Dez",
}

# Texto de cada página para o navbar
_NAV_PAGES = [
    ("Início",      "/"),
    ("Federal",     "/federal"),
    ("Subnacional", "/subnacional"),
    ("Projeções",   "/projecoes"),
]

# Mapeamento de active_page para href
_PAGE_KEYS = {
    "home":        "/",
    "federal":     "/federal",
    "subnacional": "/subnacional",
    "projecoes":   "/projecoes",
}


# ── CSS global ─────────────────────────────────────────────────────────────

def inject_css() -> None:
    logo_b64 = _logo_b64()
    logo_img = (
        f'<img src="{logo_b64}" height="30" '
        'style="border-radius:4px;vertical-align:middle;margin-right:10px;" alt="FIESP"/>'
        if logo_b64 else ""
    )

    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

html {{ zoom: 90%; }}

html, body, .stApp {{
    font-family: 'Inter', sans-serif !important;
    background-color: {C['bg']} !important;
}}

/* Remove header/decoração padrão do Streamlit */
[data-testid="stHeader"] {{ display: none !important; }}
[data-testid="stDecoration"] {{ display: none !important; }}
[data-testid="collapsedControl"] {{ display: none !important; }}
[data-testid="stSidebarNav"] {{ display: none !important; }}
[data-testid="stSidebar"] {{ display: none !important; }}

/* Conteúdo principal sem padding extra no topo (navbar é inline) */
.main .block-container {{
    padding-top: 0 !important;
    padding-bottom: 40px !important;
    max-width: 1400px;
}}

/* Métricas */
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
}}

/* Textos */
h1, h2, h3, h4 {{ color: {C['text']} !important; }}
p {{ color: {C['text_dim']}; }}
.stMarkdown p {{ color: {C['text_dim']} !important; }}
.stCaption p {{ color: {C['text_muted']} !important; font-size: 11px !important; }}
[data-testid="stMarkdownContainer"] strong {{ color: {C['text']} !important; }}
hr {{ border-color: {C['border']} !important; opacity: 0.5; }}

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
}}
.stTabs [aria-selected="true"] {{
    background-color: {C['primary']} !important;
    color: #fff !important;
    font-weight: 600;
}}
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] {{ display: none !important; }}

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

/* Download button */
[data-testid="stDownloadButton"] button {{
    background-color: {C['bg3']} !important;
    border: 1px solid {C['border']} !important;
    color: {C['accent']} !important;
    font-weight: 500; border-radius: 6px; font-size: 12px;
}}

/* Sliders */
[data-testid="stSlider"] [data-baseweb="slider"] {{
    padding: 0 !important;
}}

/* Navbar */
.gastometro-navbar {{
    background: {C['bg2']};
    border-bottom: 1px solid {C['border']};
    padding: 0 32px;
    height: 60px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: -32px -32px 24px -32px;
    position: sticky;
    top: 0;
    z-index: 9999;
    box-shadow: 0 2px 20px rgba(0,0,0,0.4);
}}
.gastometro-navbar a {{
    text-decoration: none;
    color: {C['text_dim']};
    font-size: 14px;
    font-weight: 500;
    padding: 6px 14px;
    border-radius: 6px;
    transition: all 0.15s;
}}
.gastometro-navbar a:hover {{
    color: {C['text']};
    background: rgba(30,111,217,0.15);
}}
.gastometro-navbar a.active {{
    color: {C['accent']};
    font-weight: 600;
    background: rgba(56,189,248,0.08);
    border: 1px solid rgba(56,189,248,0.2);
}}
.nav-brand {{
    font-size: 18px;
    font-weight: 700;
    color: {C['text']} !important;
    letter-spacing: -0.3px;
    display: flex;
    align-items: center;
}}
.nav-links {{
    display: flex;
    gap: 4px;
    align-items: center;
}}

/* Cards de seção */
.section-card {{
    background: {C['bg2']};
    border: 1px solid {C['border']};
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 16px;
}}

/* Divisor de seção */
.section-divider {{
    height: 1px;
    background: linear-gradient(90deg, {C['border']} 0%, transparent 100%);
    margin: 20px 0;
}}

/* Label de subsection */
.kpi-sub {{
    font-size: 11px;
    font-weight: 600;
    color: {C['accent']};
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin-bottom: 10px;
}}

/* Alertas */
.alerta-vermelho {{
    background: linear-gradient(90deg,rgba(239,68,68,0.08) 0%,rgba(13,27,46,0.5) 100%);
    border-left: 3px solid {C['negative']};
    padding: 12px 16px; border-radius: 6px; margin-bottom: 8px;
    color: {C['text']} !important;
}}
.alerta-amarelo {{
    background: linear-gradient(90deg,rgba(245,158,11,0.08) 0%,rgba(13,27,46,0.5) 100%);
    border-left: 3px solid {C['warning']};
    padding: 12px 16px; border-radius: 6px; margin-bottom: 8px;
    color: {C['text']} !important;
}}
.alerta-vermelho *, .alerta-amarelo * {{ color: {C['text']} !important; }}
</style>
""", unsafe_allow_html=True)


def render_navbar(active_page: str) -> None:
    """Renderiza a barra de navegação no topo da página."""
    logo_b64 = _logo_b64()
    logo_img = (
        f'<img src="{logo_b64}" height="28" '
        'style="border-radius:3px;vertical-align:middle;margin-right:8px;" alt="FIESP"/>'
        if logo_b64 else ""
    )
    active_href = _PAGE_KEYS.get(active_page, "/")

    links_html = ""
    for label, href in _NAV_PAGES:
        cls = "active" if href == active_href else ""
        links_html += f'<a href="{href}" class="{cls}">{label}</a>'

    st.markdown(f"""
<div class="gastometro-navbar">
  <a href="/" class="nav-brand" style="text-decoration:none;">
    {logo_img}Gastômetro FIESP
  </a>
  <div class="nav-links">
    {links_html}
  </div>
  <div style="font-size:10px;color:{C['text_muted']};text-align:right;">
    Assessoria Econômica<br/>FIESP
  </div>
</div>
""", unsafe_allow_html=True)


def render_footer(fonte: str = "RTN · Secretaria do Tesouro Nacional") -> None:
    st.markdown(f"""
<div style="margin-top:40px;padding:16px 0;border-top:1px solid {C['border']};
            display:flex;justify-content:space-between;align-items:center;">
  <div style="font-size:11px;color:{C['text_muted']};">
    <span style="color:{C['accent']};font-weight:600;">Fonte:</span> {fonte}
  </div>
  <div style="font-size:10px;color:{C['text_muted']};letter-spacing:1px;text-transform:uppercase;">
    Assessoria Econômica · FIESP
  </div>
</div>
""", unsafe_allow_html=True)


# ── Formatadores ───────────────────────────────────────────────────────────

def fmt_br(valor: float, decimais: int = 2) -> str:
    """Formata número no padrão brasileiro: 1.234.567,89"""
    s = f"{valor:,.{decimais}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_bi(milhoes: float | None, decimais: int = 1) -> str:
    """R$ milhões → 'R$ X,X bi' / 'R$ X,X tri' / '—'"""
    if milhoes is None or (isinstance(milhoes, float) and pd.isna(milhoes)):
        return "—"
    v = abs(milhoes)
    sinal = "−" if milhoes < 0 else ""
    if v >= 1_000_000:
        return f"R$ {sinal}{fmt_br(v / 1_000_000, decimais)} tri"
    if v >= 1_000:
        return f"R$ {sinal}{fmt_br(v / 1_000, decimais)} bi"
    return f"R$ {sinal}{fmt_br(v, decimais)} mi"


def fmt_pct(v: float | None, decimais: int = 1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    sinal = "−" if v < 0 else ""
    return f"{sinal}{fmt_br(abs(v), decimais)}%"


def fmt_delta(v: float | None) -> str | None:
    """Retorna string '+X,X% a/a' para uso em st.metric delta."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return f"{'+' if v >= 0 else ''}{fmt_br(v, 1)}% a/a"


# ── Plotly ─────────────────────────────────────────────────────────────────

_RSEL = dict(
    bgcolor=C["bg3"], bordercolor=C["border"], borderwidth=1,
    font=dict(color=C["text_dim"], size=11), activecolor=C["primary"],
)
_RSLD = dict(bgcolor=C["bg2"], bordercolor=C["border"], thickness=0.06)


def plotly_dark(fig: go.Figure, height: int = 420, margin: dict | None = None) -> go.Figure:
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
            bgcolor="rgba(13,27,46,0.9)", bordercolor=C["border"], borderwidth=1,
            font=dict(color=C["text"], size=12),
        ),
    )
    fig.update_xaxes(
        gridcolor=C["border"], zerolinecolor=C["border"],
        tickfont=dict(color=C["text_dim"], size=11),
        title_font=dict(color=C["text_dim"], size=12), showline=False,
    )
    fig.update_yaxes(
        gridcolor=C["border"], zerolinecolor=C["border"],
        tickfont=dict(color=C["text_dim"], size=11),
        title_font=dict(color=C["text_dim"], size=12), showline=False,
    )
    return fig


def rangeselector_buttons() -> list:
    return [
        dict(count=1,  label="1a",  step="year", stepmode="backward"),
        dict(count=3,  label="3a",  step="year", stepmode="backward"),
        dict(count=5,  label="5a",  step="year", stepmode="backward"),
        dict(step="all", label="Máx"),
    ]


# ── Dados ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner="Carregando dados...")
def carregar_dados() -> dict:
    """Carrega todos os parquets e JSONs necessários."""
    dados: dict = {}

    rtn_path = DATA_DIR / "rtn" / "rtn_mensal.parquet"
    dados["rtn"] = pd.read_parquet(rtn_path) if rtn_path.exists() else pd.DataFrame()

    cont_path = DATA_DIR / "contador_fiscal.json"
    dados["contador"] = (
        json.loads(cont_path.read_text(encoding="utf-8"))
        if cont_path.exists() else {}
    )

    est_path = DATA_DIR / "estados" / "gastos_estados.parquet"
    dados["estados"] = (
        pd.read_parquet(est_path) if est_path.exists() else pd.DataFrame()
    )

    mun_path = DATA_DIR / "municipios" / "gastos_municipios.parquet"
    dados["municipios"] = (
        pd.read_parquet(mun_path) if mun_path.exists() else pd.DataFrame()
    )

    meta_path = DATA_DIR / "rtn" / "metadata.json"
    dados["meta_rtn"] = (
        json.loads(meta_path.read_text(encoding="utf-8"))
        if meta_path.exists() else {}
    )

    return dados


@st.cache_resource(ttl=86400)
def carregar_geojson_estados() -> dict | None:
    """Busca o GeoJSON simplificado dos estados brasileiros (IBGE Malhas API)."""
    url = (
        "https://servicodados.ibge.gov.br/api/v3/malhas/estados"
        "?resolucao=2&formato=application/vnd.geo%2Bjson"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── Cálculos subnacionais ──────────────────────────────────────────────────

def calcular_ratio_investimento_estados(df_estados: pd.DataFrame) -> pd.DataFrame:
    """
    Retorna DataFrame com cod_ibge, uf, ente, invest_ratio (%), invest_milhoes,
    total_milhoes — usando despesas liquidadas até o bimestre mais recente disponível.
    """
    if df_estados.empty:
        return pd.DataFrame()

    df = df_estados[
        df_estados["coluna"] == "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)"
    ].copy()

    # Ano e bimestre mais recentes com dados
    max_ano = df["ano"].max()
    max_bim = df[df["ano"] == max_ano]["periodo"].max()

    df_rec = df[(df["ano"] == max_ano) & (df["periodo"] == max_bim)]

    invest = (
        df_rec[df_rec["cod_conta"].isin({"Investimentos", "InversoesFinanceiras"})]
        .groupby(["cod_ibge", "uf", "ente"])["valor_milhoes"].sum()
        .reset_index()
        .rename(columns={"valor_milhoes": "invest_milhoes"})
    )
    total = (
        df_rec[df_rec["cod_conta"] == "DespesasExcetoIntraOrcamentarias"]
        .groupby(["cod_ibge", "uf", "ente"])["valor_milhoes"].sum()
        .reset_index()
        .rename(columns={"valor_milhoes": "total_milhoes"})
    )

    merged = invest.merge(total, on=["cod_ibge", "uf", "ente"], how="inner")
    merged["invest_ratio"] = (
        merged["invest_milhoes"] / merged["total_milhoes"] * 100
    ).round(2)
    merged["ano"]    = max_ano
    merged["periodo"] = max_bim
    return merged.sort_values("invest_ratio", ascending=False)


def calcular_serie_estado(
    df_estados: pd.DataFrame,
    cod_ibge: int,
    cod_conta: str,
    coluna: str = "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)",
) -> pd.DataFrame:
    """Retorna série temporal para um estado e conta específicos."""
    df = df_estados[
        (df_estados["cod_ibge"]  == cod_ibge) &
        (df_estados["cod_conta"] == cod_conta) &
        (df_estados["coluna"]    == coluna)
    ].sort_values(["ano", "periodo"]).copy()
    df["label"] = df["ano"].astype(str) + "-B" + df["periodo"].astype(str)
    return df


# ── Helpers de RTN ────────────────────────────────────────────────────────

def rtn_valor(df: pd.DataFrame, prefixo: str, a: int, m: int, col: str) -> float | None:
    sub = df[df["discriminacao"].str.startswith(prefixo)]
    row = sub[(sub["ano"] == a) & (sub["mes"] == m)][col]
    return float(row.iloc[0]) if len(row) == 1 else None


def rtn_soma_12m(df: pd.DataFrame, prefixo: str, a: int, m: int, col: str) -> float | None:
    sub  = df[df["discriminacao"].str.startswith(prefixo)]
    m_ini, a_ini = (m + 1, a - 1) if m < 12 else (1, a)
    mask = (
        ((sub["ano"] > a_ini) | ((sub["ano"] == a_ini) & (sub["mes"] >= m_ini))) &
        ((sub["ano"] < a)     | ((sub["ano"] == a)     & (sub["mes"] <= m)))
    )
    vals = sub[mask][col].dropna()
    if len(vals) < 6:
        return None
    return float(vals.mean()) if col == "pct_pib" else float(vals.sum())


def rtn_delta_yoy(
    df: pd.DataFrame, prefixo: str, a: int, m: int, col: str
) -> float | None:
    atual = rtn_valor(df, prefixo, a, m, col)
    ant   = rtn_valor(df, prefixo, a - 1, m, col)
    if atual is None or ant is None or ant == 0:
        return None
    return round((atual - ant) / abs(ant) * 100, 1)


# ── Interno ────────────────────────────────────────────────────────────────

def _logo_b64() -> str:
    if not LOGO_PATH.exists():
        return ""
    data = LOGO_PATH.read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()
