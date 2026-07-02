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
    ("Geral",      "/"),
    ("Federal",    "/federal"),
    ("Estadual",   "/estadual"),
    ("Municipal",  "/municipal"),
]

# Mapeamento de active_page para href
_PAGE_KEYS = {
    "home":       "/",
    "federal":    "/federal",
    "estadual":   "/estadual",
    "municipal":  "/municipal",
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
        links_html += f'<a href="{href}" class="{cls}" target="_self">{label}</a>'

    st.markdown(f"""
<div class="gastometro-navbar">
  <a href="/" class="nav-brand" style="text-decoration:none;" target="_self">
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


def plotly_dark(fig: go.Figure, height: int = 420, margin: dict | None = None) -> go.Figure:
    if margin is None:
        margin = dict(l=10, r=10, t=30, b=10)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(13,27,46,0.4)",
        font=dict(family="Inter, sans-serif", color=C["text_dim"], size=12),
        height=height,
        margin=margin,
        legend=dict(
            bgcolor="rgba(13,27,46,0.9)", bordercolor=C["border"], borderwidth=1,
            font=dict(color=C["text"], size=12),
        ),
    )
    # Estiliza a fonte do título APENAS se o gráfico tiver título: definir
    # title_font num gráfico sem título cria layout.title = {font: ...} sem
    # texto, e o renderer do Streamlit exibe "undefined" no canto do gráfico.
    if fig.layout.title.text:
        fig.update_layout(title_font=dict(color=C["text"], size=14))
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
        dict(count=10, label="10a", step="year", stepmode="backward"),
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


@st.cache_data(ttl=86400)
def carregar_geojson_estados() -> dict | None:
    """Carrega GeoJSON dos estados: simplificado > cru > API do IBGE.

    Preferimos a malha simplificada (gerada por pipelines/simplificar_geojson.py),
    bem mais leve, que é o que torna o mapa interativo rápido de carregar. Se ela não
    existir, caímos no arquivo cru; por último, baixamos do IBGE.
    """
    for nome in ("estados_geojson_simplificado.json", "estados_geojson.json"):
        local = DATA_DIR / nome
        if local.exists():
            return json.loads(local.read_text(encoding="utf-8"))
    url = (
        "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
        "?resolucao=2&intrarregiao=UF&formato=application/vnd.geo%2Bjson"
    )
    try:
        r = requests.get(url, timeout=15, verify=False)
        r.raise_for_status()
        data = r.json()
        local.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner="Carregando malha municipal…")
def carregar_geojson_municipios() -> dict | None:
    """Carrega GeoJSON dos municípios: simplificado > cru > API do IBGE.

    A malha municipal crua tem ~56 MB e é a principal causa de lentidão do
    painel. A versão simplificada (pipelines/simplificar_geojson.py) tem ~3,6 MB.
    Se nenhuma existir localmente, baixamos a malha completa do IBGE.
    """
    for nome in ("municipios_geojson_simplificado.json", "municipios_geojson.json"):
        local = DATA_DIR / nome
        if local.exists():
            return json.loads(local.read_text(encoding="utf-8"))
    url = (
        "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
        "?resolucao=1&intrarregiao=municipio&formato=application/vnd.geo%2Bjson"
    )
    try:
        r = requests.get(url, timeout=60, verify=False)
        r.raise_for_status()
        data = r.json()
        local.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception:
        return None


# ── Cálculos subnacionais ──────────────────────────────────────────────────

def _rolling12_siconfi(df_base: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula invest_ratio em rolling 12 meses a partir de dados SICONFI bimestrais.

    O SICONFI reporta valores ACUMULADOS desde janeiro ("ATÉ O BIMESTRE"), então
    para obter um janela de 12 meses que não fique presa ao ciclo jan-bim usamos:

        rolling_12m = B_max(ano_atual) + [B6(ano-1) − B_max(ano-1)]
                    = gasto jan→bim_atual/2026 + gasto bim+1→dez/2025

    Isso neutraliza a sazonalidade do investimento, que se concentra no fim do ano
    (estados correm para executar orçamento antes de dezembro fechar).

    Se dados do ano anterior não estiverem disponíveis para um ente, usa apenas o
    período atual como fallback (sem rolling).
    """
    max_ano = df_base["ano"].max()
    cobertura = (
        df_base[df_base["ano"] == max_ano]
        .groupby("periodo")["cod_ibge"].nunique()
    )
    max_bim = int(cobertura.idxmax())
    ano_ant = max_ano - 1

    def _agregar(ano: int, bim: int) -> pd.DataFrame:
        sub = df_base[(df_base["ano"] == ano) & (df_base["periodo"] == bim)]
        inv = (
            sub[sub["cod_conta"].isin({"Investimentos", "InversoesFinanceiras"})]
            .groupby(["cod_ibge", "uf", "ente"])["valor_milhoes"].sum()
            .reset_index().rename(columns={"valor_milhoes": "inv"})
        )
        tot = (
            sub[sub["cod_conta"] == "DespesasExcetoIntraOrcamentarias"]
            .groupby(["cod_ibge", "uf", "ente"])["valor_milhoes"].sum()
            .reset_index().rename(columns={"valor_milhoes": "tot"})
        )
        return inv.merge(tot, on=["cod_ibge", "uf", "ente"], how="inner")

    curr   = _agregar(max_ano, max_bim)   # jan→bim_max do ano atual
    b6_ant = _agregar(ano_ant, 6)          # jan→dez do ano anterior (B6 = ano completo)
    bx_ant = _agregar(ano_ant, max_bim)    # jan→bim_max do ano anterior (para subtrair)

    m = (
        curr
        .merge(b6_ant.rename(columns={"inv": "inv_b6", "tot": "tot_b6"}),
               on=["cod_ibge", "uf", "ente"], how="left")
        .merge(bx_ant.rename(columns={"inv": "inv_bx", "tot": "tot_bx"}),
               on=["cod_ibge", "uf", "ente"], how="left")
    )

    # Onde o complemento do ano anterior está disponível, aplica rolling 12m
    tem_roll = m["inv_b6"].notna() & m["inv_bx"].notna()
    m["invest_milhoes"] = m["inv"].where(
        ~tem_roll, m["inv"] + m["inv_b6"] - m["inv_bx"]
    )
    m["total_milhoes"] = m["tot"].where(
        ~tem_roll, m["tot"] + m["tot_b6"] - m["tot_bx"]
    )

    m = m[m["total_milhoes"] > 0].copy()
    m["invest_ratio"] = (m["invest_milhoes"] / m["total_milhoes"] * 100).round(2)
    m["ano"]     = max_ano
    m["periodo"] = max_bim
    return (
        m[["cod_ibge", "uf", "ente", "invest_ratio",
           "invest_milhoes", "total_milhoes", "ano", "periodo"]]
        .sort_values("invest_ratio", ascending=False)
    )


def calcular_ratio_investimento_estados(df_estados: pd.DataFrame) -> pd.DataFrame:
    """Ratio invest/total em rolling 12 meses para estados + DF."""
    if df_estados.empty:
        return pd.DataFrame()
    df = df_estados[
        df_estados["coluna"] == "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)"
    ].copy()
    return _rolling12_siconfi(df)


def calcular_ratio_investimento_municipios(df_municipios: pd.DataFrame) -> pd.DataFrame:
    """Ratio invest/total em rolling 12 meses para municípios."""
    if df_municipios.empty:
        return pd.DataFrame()
    df = df_municipios[
        df_municipios["coluna"] == "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)"
    ].copy()
    return _rolling12_siconfi(df)


def calcular_serie_estado(
    df_estados: pd.DataFrame,
    cod_ibge: int,
    cod_conta: str,
    coluna: str = "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)",
) -> pd.DataFrame:
    """Retorna série temporal para um estado e conta específicos."""
    df = (
        df_estados[
            (df_estados["cod_ibge"]  == cod_ibge) &
            (df_estados["cod_conta"] == cod_conta) &
            (df_estados["coluna"]    == coluna)
        ]
        .groupby(["ano", "periodo"], as_index=False)["valor_milhoes"]
        .sum()
        .sort_values(["ano", "periodo"])
    )
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


# ── Termômetro de investimento (compartilhado entre Geral e Federal) ──────

def ratio_federal(df: pd.DataFrame) -> tuple | None:
    """(invest_pct, invest_mi, total_mi) usando RTN — rolling 12 meses.

    Usa a série memo 'Investimento' da aba 1.2 (idêntica ao total da aba 1.3:
    investimentos GND 4 + inversões GND 5 + ajuste de OB), não a proxy de
    Discricionárias que incluía custeio discricionário.
    """
    if df.empty:
        return None
    ano  = int(df["ano"].max())
    mes  = int(df[df["ano"] == ano]["mes"].max())
    col  = "corrente_milhoes"
    tot  = rtn_soma_12m(df, "4. ",          ano, mes, col)
    inv  = rtn_soma_12m(df, "Investimento", ano, mes, col)
    if tot is None or inv is None or tot == 0:
        return None
    return round(inv / tot * 100, 1), inv, tot


def linha_termometro(
    label: str,
    nota: str,
    ratio: tuple | None,
    destaque: bool = False,
) -> str:
    """Retorna HTML de uma linha do termômetro (label | invest% | barra | correntes%)."""
    bar_h  = "48px" if destaque else "36px"
    n_size = "26px" if destaque else "20px"
    l_size = "15px" if destaque else "13px"
    mb     = "24px" if destaque else "14px"
    fw     = "700"  if destaque else "600"

    # Grid com coluna de label explícita: main=200px, sub-entes=232px
    # Isso garante que invest% dos sub-entes fique exatamente 32px à direita do consolidado
    col1   = "200px" if destaque else "232px"
    grid   = f"display:grid;grid-template-columns:{col1} 80px 1fr 80px;align-items:center;gap:20px;"
    border = f"border-left:3px solid {C['accent']};padding-left:8px;" if destaque else "padding-left:32px;"

    if ratio is None:
        return (
            f'<div style="margin-bottom:{mb};{grid}opacity:0.4;">'
            f'<div style="{border}font-size:{l_size};font-weight:{fw};color:{C["text"]}">{label}</div>'
            f'<div style="grid-column:2/5;font-size:12px;color:{C["text_muted"]};text-align:center;">dados não disponíveis</div>'
            f'</div>'
        )

    invest_pct   = ratio[0]
    corrente_pct = round(100 - invest_pct, 1)

    return (
        f'<div style="margin-bottom:{mb};{grid}">'
        f'<div style="{border}">'
        f'<div style="font-size:{l_size};font-weight:{fw};color:{C["text"]};line-height:1.2;">{label}</div>'
        f'<div style="font-size:10px;color:{C["text_muted"]};margin-top:3px;line-height:1.4;">{nota}</div>'
        f'</div>'
        f'<div style="text-align:right;">'
        f'<div style="font-size:{n_size};font-weight:800;color:{C["investimento"]};'
        f'font-family:\'Courier New\',monospace;line-height:1;">{fmt_br(invest_pct, 1)}%</div>'
        f'<div style="font-size:9px;color:{C["text_muted"]};margin-top:2px;">investimento</div>'
        f'</div>'
        f'<div style="height:{bar_h};border-radius:8px;overflow:hidden;display:flex;'
        f'border:1px solid {C["border"]};">'
        f'<div style="width:{invest_pct:.2f}%;background:linear-gradient(90deg,#14532d,{C["investimento"]});min-width:4px;"></div>'
        f'<div style="flex:1;background:linear-gradient(90deg,{C["corrente"]},#7f1d1d);"></div>'
        f'</div>'
        f'<div>'
        f'<div style="font-size:{n_size};font-weight:800;color:{C["corrente"]};'
        f'font-family:\'Courier New\',monospace;line-height:1;">{fmt_br(corrente_pct, 1)}%</div>'
        f'<div style="font-size:9px;color:{C["text_muted"]};margin-top:2px;">correntes</div>'
        f'</div>'
        f'</div>'
    )


def termometro_header(cols_grid: str = "200px 80px 1fr 80px") -> str:
    """Cabeçalho de colunas do termômetro (Esfera | Investimento | Proporção | Correntes)."""
    return (
        f'<div style="display:grid;grid-template-columns:{cols_grid};gap:20px;'
        f'margin-bottom:20px;padding-bottom:14px;border-bottom:1px solid {C["border"]};">'
        f'<div style="font-size:10px;color:{C["text_muted"]};'
        f'text-transform:uppercase;letter-spacing:1px;">Esfera</div>'
        f'<div style="text-align:right;font-size:10px;color:{C["investimento"]};'
        f'text-transform:uppercase;letter-spacing:1px;">Investimento</div>'
        f'<div style="text-align:center;font-size:10px;color:{C["text_muted"]};'
        f'text-transform:uppercase;letter-spacing:1px;">Proporção</div>'
        f'<div style="font-size:10px;color:{C["corrente"]};'
        f'text-transform:uppercase;letter-spacing:1px;">Correntes</div>'
        f'</div>'
    )


# ── Cálculos scatter e categorias ─────────────────────────────────────────

# ── Projeção "intervalo móvel" (mesma base do contador) ────────────────────
#
# Toda a aba estadual/municipal (contador, composição e tabela) fala a MESMA
# língua: gasto realizado no ano corrente + projeção dos bimestres que faltam
# para fechar o BIMESTRE EM CURSO no calendário — nunca o ano fechado. A fórmula
# por ente é idêntica à do contador (pipelines/contador_fiscal.py):
#
#     total = Σ realizado(ano, 1..último_real)  +  ratio × Σ âncora(ano-1, b)
#             \_______________________________/     \_________________________/
#                    já aconteceu                      b = bimestres a projetar
#
# O 'ratio' vem do contador (JSON) por ente — assim a soma da composição fecha
# EXATAMENTE com a meta do contador. A coluna usada é o FLUXO do bimestre
# ("NO BIMESTRE"): somar os fluxos jan..B_n reproduz o "até o bimestre B_n" e
# permite projetar cada bimestre futuro na sua própria âncora sazonal.

COLUNA_FLUXO = "DESPESAS EMPENHADAS NO BIMESTRE"

_CATS_ESTADUAL = [
    ("PessoalEEncargosSociais", "Pessoal e Encargos"),
    ("JurosEEncargosDaDivida",  "Juros da Dívida"),
    ("OutrasDespesasCorrentes", "Outras Correntes"),
    ("Investimentos",           "Investimentos"),
    ("InversoesFinanceiras",    "Inversões Financeiras"),
    ("AmortizacaoDaDivida",     "Amort. Dívida"),
]
_CONTAS_INVEST = {"Investimentos", "InversoesFinanceiras"}
_CONTA_TOTAL   = "DespesasExcetoIntraOrcamentarias"


def _parse_bim(s: str) -> tuple[int, int]:
    """'2026-B4' → (2026, 4)."""
    ano, bim = s.split("-B")
    return int(ano), int(bim)


def _prox_bim(ano: int, bim: int) -> tuple[int, int]:
    return (ano + 1, 1) if bim == 6 else (ano, bim + 1)


def _bimestre_corrente() -> tuple[int, int]:
    """Bimestre do calendário hoje. B1=jan/fev … B6=nov/dez."""
    from datetime import datetime
    d = datetime.now()
    return d.year, (d.month + 1) // 2


def _plano_projecao(bloco: dict | None, df_ente: pd.DataFrame):
    """Define (ratio, último_real, [bimestres_a_projetar]) para um ente.

    Prioriza o bloco do contador (JSON) — assim o dashboard replica EXATAMENTE a
    conta do contador. Se o ente não estiver no contador, deriva do próprio dado
    + calendário, com ratio neutro (1.0).
    """
    if bloco and bloco.get("ultimo_dado") and bloco.get("bim_referencia_fim"):
        ratio = float(bloco.get("ratio_rolling", 1.0))
        ult   = _parse_bim(bloco["ultimo_dado"])
        fim   = _parse_bim(bloco["bim_referencia_fim"])
        ini   = _parse_bim(bloco.get("bim_referencia", bloco["bim_referencia_fim"]))
    else:
        ratio = float(bloco.get("ratio_rolling", 1.0)) if bloco else 1.0
        ult_ano = int(df_ente["ano"].max())
        ult_bim = int(df_ente[df_ente["ano"] == ult_ano]["periodo"].max())
        ult = (ult_ano, ult_bim)
        fim = _bimestre_corrente()
        ini = _prox_bim(*ult)

    proj: list[tuple[int, int]] = []
    a, b = ini
    while (a, b) <= fim and len(proj) < 6:
        proj.append((a, b))
        a, b = _prox_bim(a, b)
    return ratio, ult, proj


def _projetar(df_fluxo: pd.DataFrame, contas: set, ratio: float,
              ult: tuple[int, int], proj: list) -> float:
    """Realizado (ano corrente até o último real) + projeção sazonal × ratio,
    para o conjunto de contas informado. Valores em R$ milhões."""
    sub = df_fluxo[df_fluxo["cod_conta"].isin(contas)]
    ult_ano, ult_bim = ult
    realizado = sub[
        (sub["ano"] == ult_ano) & (sub["periodo"] <= ult_bim)
    ]["valor_milhoes"].sum()
    projetado = 0.0
    for (ap, bp) in proj:
        projetado += sub[
            (sub["ano"] == ap - 1) & (sub["periodo"] == bp)
        ]["valor_milhoes"].sum()
    return float(realizado + projetado * ratio)


def calcular_categorias_projetadas(
    df: pd.DataFrame,
    cod_ibge_list: list | None,
    bloco: dict | None,
) -> pd.DataFrame:
    """Composição por categoria, projetada até o bimestre corrente (mesma base do
    contador). cod_ibge_list=None consolida todos os entes. As 6 categorias-folha
    somam exatamente a meta do contador daquele ente/consolidado."""
    df_f = df[df["coluna"] == COLUNA_FLUXO].copy()
    if cod_ibge_list is not None:
        df_f = df_f[df_f["cod_ibge"].isin(cod_ibge_list)]
    if df_f.empty:
        return pd.DataFrame()

    ratio, ult, proj = _plano_projecao(bloco, df_f)

    linhas = [
        {
            "cod_conta": cod_conta,
            "nome": nome,
            "valor_projetado": _projetar(df_f, {cod_conta}, ratio, ult, proj),
        }
        for cod_conta, nome in _CATS_ESTADUAL
    ]
    ano_ref, bim_ref = proj[-1] if proj else ult
    out = pd.DataFrame(linhas)
    out["ano"]     = ano_ref
    out["periodo"] = bim_ref
    return out.sort_values("valor_projetado", ascending=True).reset_index(drop=True)


def calcular_scatter_correntes_invest(
    df: pd.DataFrame,
    blocos_por_cod: dict | None = None,
) -> pd.DataFrame:
    """Projeção por ente (investimento, total e o complemento correntes/
    obrigatórias), até o bimestre corrente. `blocos_por_cod` mapeia
    {cod_ibge: bloco_do_contador} para pegar o ratio de cada ente. Usado na
    tabela comparativa, na barra invest×correntes e no mapa.

    "Despesas correntes e obrigatórias" = total − investimento (COMPLEMENTO), para
    que investimento + correntes/obrigatórias sempre somem 100% (mesma lógica do
    termômetro da aba Geral). O complemento reincorpora a Amortização da Dívida
    (4.3), que não é investimento nem despesa corrente contábil.
    """
    blocos_por_cod = blocos_por_cod or {}
    df_f = df[df["coluna"] == COLUNA_FLUXO].copy()
    if df_f.empty:
        return pd.DataFrame()

    entes = df_f[["cod_ibge", "uf", "ente"]].drop_duplicates()
    linhas = []
    for _, e in entes.iterrows():
        cod = int(e["cod_ibge"])
        df_ente = df_f[df_f["cod_ibge"] == cod]
        ratio, ult, proj = _plano_projecao(blocos_por_cod.get(cod), df_ente)
        inv = _projetar(df_ente, _CONTAS_INVEST, ratio, ult, proj)
        tot = _projetar(df_ente, {_CONTA_TOTAL}, ratio, ult, proj)
        if tot <= 0:
            continue
        ano_ref, bim_ref = proj[-1] if proj else ult
        linhas.append({
            "cod_ibge": cod, "uf": e["uf"], "ente": e["ente"],
            "invest_milhoes": inv,
            "total_milhoes": tot,
            "correntes_obrig_milhoes": tot - inv,
            "invest_ratio": round(inv / tot * 100, 2),
            "ano": ano_ref, "periodo": bim_ref,
        })
    if not linhas:
        return pd.DataFrame()
    return (
        pd.DataFrame(linhas)
        .sort_values("invest_ratio", ascending=False)
        .reset_index(drop=True)
    )


# ── Interno ────────────────────────────────────────────────────────────────

def _logo_b64() -> str:
    if not LOGO_PATH.exists():
        return ""
    data = LOGO_PATH.read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()
