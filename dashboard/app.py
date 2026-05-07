"""
dashboard/app.py  --  Frontend Streamlit
-----------------------------------------
Painel de acompanhamento fiscal com dados do Tesouro Nacional (RTN).
Le exclusivamente da camada gold (Parquet).

Executar:
  streamlit run dashboard/app.py
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import DATA_DIR, ORGAOS_ALTA_VIGILANCIA, RUBRICAS_ALTA_VIGILANCIA

GOLD_DIR = DATA_DIR / "despesas" / "gold"

MES_LABELS = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}


def _fmt(valor: float, decimais: int = 2) -> str:
    s = f"{valor:,.{decimais}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


# -- Configuracao da pagina ------------------------------------------------
st.set_page_config(
    page_title="Radar Fiscal FIESP",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  .alerta-vermelho {
    background-color: #FDECEA; border-left: 4px solid #C0392B;
    padding: 12px 16px; border-radius: 4px; margin-bottom: 8px;
  }
  .alerta-amarelo {
    background-color: #FEF9E7; border-left: 4px solid #F39C12;
    padding: 12px 16px; border-radius: 4px; margin-bottom: 8px;
  }
  .fonte-tag {
    font-size: 11px; color: #888;
    background: #EEF; border-radius: 4px; padding: 2px 6px;
  }
  .kpi-sub {
    font-size: 15px; font-weight: 700; color: #222; margin-bottom: 4px;
  }
</style>
""", unsafe_allow_html=True)


# -- Carregamento de dados (com cache) -------------------------------------

@st.cache_data(ttl=3600, show_spinner="Carregando dados...")
def carregar_dados():
    dados = {}
    arquivos = {
        "orgao":      GOLD_DIR / "despesas_mensal_orgao.parquet",
        "natureza":   GOLD_DIR / "despesas_mensal_natureza.parquet",
        "vigilancia": GOLD_DIR / "despesas_vigilancia.parquet",
        "anomalias":  GOLD_DIR / "anomalias.parquet",
        "rtn":        DATA_DIR / "rtn" / "rtn_mensal.parquet",
    }
    for nome, caminho in arquivos.items():
        dados[nome] = pd.read_parquet(caminho) if caminho.exists() else pd.DataFrame()
    return dados


# -- Sidebar ---------------------------------------------------------------

def sidebar_filtros(df_rtn: pd.DataFrame) -> dict:
    st.sidebar.title("🔍 Filtros")

    if df_rtn.empty:
        return {}

    anos = sorted(df_rtn["ano"].unique(), reverse=True)
    ano_sel = st.sidebar.selectbox("Ano de referencia", anos)

    meses = sorted(df_rtn[df_rtn["ano"] == ano_sel]["mes"].unique(), reverse=True)
    mes_sel = st.sidebar.selectbox(
        "Mes de referencia", meses,
        format_func=lambda m: MES_LABELS.get(m, m),
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Alertas**")
    mostrar_amarelo  = st.sidebar.checkbox("Mostrar alertas amarelos",  value=True)
    mostrar_vermelho = st.sidebar.checkbox("Mostrar alertas vermelhos", value=True)

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        "<span class='fonte-tag'>Dados: Tesouro Nacional (RTN)</span>",
        unsafe_allow_html=True,
    )
    return {
        "ano": ano_sel,
        "mes": mes_sel,
        "mostrar_amarelo":  mostrar_amarelo,
        "mostrar_vermelho": mostrar_vermelho,
    }


# -- Helpers RTN -----------------------------------------------------------

def _rtn_serie(df_rtn: pd.DataFrame, prefixo: str) -> pd.DataFrame:
    return df_rtn[df_rtn["discriminacao"].str.startswith(prefixo)]


def _rtn_valor(df_rtn, prefixo, a, m, col):
    sub = _rtn_serie(df_rtn, prefixo)
    row = sub[(sub["ano"] == a) & (sub["mes"] == m)][col]
    return float(row.iloc[0]) if len(row) == 1 else None


def _rtn_soma_12m(df_rtn, prefixo, a, m, col):
    """
    Soma (ou media, para pct_pib) dos 12 meses terminando em (a, m) inclusive.
    Para pct_pib retorna a media porque cada valor ja e uma taxa anualizada;
    a media dos 12 meses equivale ao total anual / PIB * 100.
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
    """Variacao percentual m/m usando fn para calcular o valor (pontual ou acumulado)."""
    if fn is None:
        fn = lambda p, aa, mm: _rtn_valor(df_rtn, p, aa, mm, col)
    atual = fn(prefixo, a, m)
    m_ant, a_ant = (m - 1, a) if m > 1 else (12, a - 1)
    ant = fn(prefixo, a_ant, m_ant)
    if atual is None or ant is None or ant == 0:
        return None
    return round((atual - ant) / abs(ant) * 100, 1)


def _fmt_rtn(v, is_pib: bool):
    if v is None:
        return "—"
    sinal = "−" if v < 0 else ""
    if is_pib:
        return f"{sinal}{_fmt(abs(v), 1)}%"
    return f"R$ {sinal}{_fmt(abs(v) / 1e3, 1)} bi"


# -- Constantes de negocio -------------------------------------------------

KPIS_RTN = [
    ("3. ",  "Receita Liquida",    "Receita Total menos Transferencias por Reparticao."),
    ("4. ",  "Despesa Total",      "Previdencia + Pessoal + Obrigatorias + Discricionarias."),
    ("5. ",  "Result. Primario",   "Receita Liquida - Despesa Total. Negativo = deficit."),
    ("10.", "Result. Nominal",    "Resultado Primario + Juros Nominais. Negativo = deficit."),
]

COMP_DESPESA = [
    ("4.1 ",  "Benef. Previdenciarios"),
    ("4.2 ",  "Pessoal e Encargos"),
    ("4.3 ",  "Outras Obrigatorias"),
    ("4.4.2", "Discricionarias"),
]

SERIES_ALERTA = [
    ("3. ",   "Receita Liquida"),
    ("4. ",   "Despesa Total"),
    ("4.1 ",  "Benef. Previdenciarios"),
    ("4.2 ",  "Pessoal e Encargos"),
    ("4.3 ",  "Outras Obrigatorias"),
    ("4.4.2", "Discricionarias"),
    ("5. ",   "Result. Primario"),
    ("10.",   "Result. Nominal"),
]


# -- Aba 1: Resultado Fiscal (RTN) -----------------------------------------

def aba_resultado_fiscal(dados, filtros, col_val, is_pib, opcao_sel):
    df = dados.get("rtn", pd.DataFrame())
    if df.empty:
        st.info("Execute `python pipelines/rtn/load.py` para baixar os dados.")
        return

    ano, mes = filtros["ano"], filtros["mes"]

    # Closures sobre df, col_val, is_pib, ano, mes
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

    # -- Linha 1: KPIs do mes --------------------------------------------
    st.markdown(
        f"<div class='kpi-sub'>Mes: "
        f"<strong>{MES_LABELS.get(mes, mes)}/{ano}</strong></div>",
        unsafe_allow_html=True,
    )
    for col, (prefixo, label, help_) in zip(st.columns(4), KPIS_RTN):
        with col:
            st.metric(label, fv(val(prefixo)), delta=ds(dlt(prefixo)), help=help_)

    st.markdown(
        "<hr style='border:none; border-top:1px dashed #ccc; margin:12px 0 10px 0;'>",
        unsafe_allow_html=True,
    )

    # -- Linha 2: KPIs acumulado 12 meses --------------------------------
    st.markdown(
        "<div class='kpi-sub'>Acumulado 12 meses</div>",
        unsafe_allow_html=True,
    )
    sufixo_12m = "(media 12m)" if is_pib else "(soma 12m)"
    for col, (prefixo, label, help_) in zip(st.columns(4), KPIS_RTN):
        with col:
            st.metric(
                f"{label} {sufixo_12m}",
                fv(s12(prefixo)),
                delta=ds(dlt12(prefixo)),
                help=f"{help_} {'Media' if is_pib else 'Soma'} dos ultimos 12 meses.",
            )

    st.markdown("---")

    col_esq, col_dir = st.columns([3, 2])

    # -- Grafico de linha: Receita x Despesa x Resultado -----------------
    with col_esq:
        y_label = "% do PIB" if is_pib else "R$ Milhoes"
        st.subheader("Receita x Despesa x Resultado Primario")

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
                "Receita Liquida":  "#27AE60",
                "Despesa Total":    "#E74C3C",
                "Result. Primario": "#2E86C1",
            },
            labels={"valor": y_label, "data": "", "serie": ""},
        )
        fig.add_hline(y=0, line_dash="dot", line_color="#888", opacity=0.6)
        data_fim = pd.Timestamp(f"{ano}-{mes:02d}-01")
        data_ini = data_fim - pd.DateOffset(years=3)
        fig.update_layout(
            height=420,
            font=dict(size=13),
            legend=dict(orientation="h", yanchor="top", y=-0.22,
                        xanchor="center", x=0.5, title="", font=dict(size=13)),
            margin=dict(l=10, r=10, t=20, b=10),
            xaxis=dict(
                tickformat="%m/%Y",
                tickfont=dict(size=12),
                title_font=dict(size=13),
                range=[str(data_ini.date()), str((data_fim + pd.DateOffset(months=1)).date())],
                rangeslider=dict(visible=True, thickness=0.06),
                rangeselector=dict(buttons=[
                    dict(count=1,  label="1a",  step="year", stepmode="backward"),
                    dict(count=3,  label="3a",  step="year", stepmode="backward"),
                    dict(count=5,  label="5a",  step="year", stepmode="backward"),
                    dict(step="all", label="Max"),
                ]),
            ),
            yaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Resultado primario acumulado no ano (barras)
        st.markdown(f"**Resultado primario acumulado {ano}**")
        df_ytd = _rtn_serie(df, "5. ")
        df_ytd = df_ytd[(df_ytd["ano"] == ano) & (df_ytd["mes"] <= mes)].sort_values("mes").copy()
        if not df_ytd.empty:
            df_ytd["acumulado"] = df_ytd[col_val].cumsum()
            df_ytd["label"]     = df_ytd["mes"].map(MES_LABELS)
            y_acum  = df_ytd["acumulado"] / (1 if is_pib else 1e3)
            y_title = "% do PIB (acum.)" if is_pib else "R$ bilhoes (acum.)"
            texts_y = [
                f"{_fmt(v, 1)}%" if is_pib else f"R$ {_fmt(v, 1)} bi"
                for v in y_acum
            ]
            fig3 = go.Figure(go.Bar(
                x=df_ytd["label"], y=y_acum,
                marker_color=["#27AE60" if v >= 0 else "#E74C3C" for v in y_acum],
                text=texts_y, textposition="outside",
            ))
            fig3.add_hline(y=0, line_dash="dot", line_color="#888", opacity=0.6)
            fig3.update_layout(
                yaxis_title=y_title, height=240,
                font=dict(size=13),
                yaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
                xaxis=dict(tickfont=dict(size=12)),
                margin=dict(l=10, r=10, t=10, b=10),
            )
            st.plotly_chart(fig3, use_container_width=True)

    # -- Composicao da despesa (mensal + 12m) ----------------------------
    with col_dir:
        def _comp_chart(titulo, fn_valor, height=270):
            items = [{"Categoria": n, "Valor": fn_valor(p)} for p, n in COMP_DESPESA]
            items = [i for i in items if i["Valor"] is not None]
            if not items:
                return
            df_c = pd.DataFrame(items).sort_values("Valor", ascending=True)
            if is_pib:
                x_vals  = df_c["Valor"]
                x_title = "% do PIB"
                texts   = df_c["Valor"].apply(lambda v: f"{_fmt(v, 1)}%")
            else:
                x_vals  = df_c["Valor"] / 1e3
                x_title = "R$ bilhoes"
                texts   = df_c["Valor"].apply(lambda v: f"R$ {_fmt(v / 1e3, 1)} bi")
            fig2 = go.Figure(go.Bar(
                x=x_vals, y=df_c["Categoria"], orientation="h",
                marker_color="#E74C3C", text=texts, textposition="outside",
                cliponaxis=False,
            ))
            # Estende o eixo X 35% além do maior valor para garantir que
            # os rótulos externos caibam sem serem truncados
            x_max = float(x_vals.max())
            fig2.update_layout(
                xaxis_title=x_title, height=height,
                font=dict(size=13),
                xaxis=dict(
                    range=[0, x_max * 1.38],
                    tickfont=dict(size=12),
                    title_font=dict(size=13),
                ),
                yaxis=dict(tickfont=dict(size=13)),
                margin=dict(l=10, r=20, t=10, b=20),
            )
            fig2.update_traces(textfont_size=13)
            st.markdown(f"**{titulo}**")
            st.plotly_chart(fig2, use_container_width=True)

        _comp_chart(
            f"Composicao da despesa — {MES_LABELS.get(mes, mes)}/{ano}",
            lambda p: val(p),
        )
        _comp_chart(
            "Composicao da despesa — acumulado 12 meses",
            lambda p: s12(p),
        )

    unidade = "% do PIB (media 12m)" if is_pib else "R$ milhoes"
    st.caption(
        f"Fonte: RTN · Secretaria do Tesouro Nacional · {unidade} · "
        "Atualizacao: `python pipelines/rtn/load.py`"
    )


# -- Aba 2: Alertas (RTN) --------------------------------------------------

def aba_alertas_rtn(dados, filtros, col_val, is_pib):
    df = dados.get("rtn", pd.DataFrame())
    if df.empty:
        st.info("Dados RTN nao encontrados.")
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
        f"**{len(alertas)} alerta(s) em "
        f"{MES_LABELS.get(mes, mes)}/{ano}** — z-score calculado sobre janela de 24 meses"
    )
    for a in sorted(alertas, key=lambda x: abs(x["zscore"]), reverse=True):
        css   = f"alerta-{a['nivel']}"
        icone = "🔴" if a["nivel"] == "vermelho" else "🟡"
        v_fmt = _fmt_rtn(a["valor"], is_pib)
        st.markdown(
            f'<div class="{css}">'
            f'{icone} <strong>{a["serie"]}</strong>'
            f' &nbsp;|&nbsp; Z-score: <strong>{a["zscore"]:.1f}σ</strong>'
            f' &nbsp;|&nbsp; Valor: <strong>{v_fmt}</strong>'
            f'</div>',
            unsafe_allow_html=True,
        )


# -- Aba 3: Vigilancia Fiscal (RTN) ----------------------------------------

def aba_vigilancia_rtn(dados, filtros, col_val, is_pib):
    df = dados.get("rtn", pd.DataFrame())
    if df.empty:
        st.info("Dados RTN nao encontrados.")
        return

    ano, mes = filtros["ano"], filtros["mes"]

    st.subheader(f"Painel de indicadores — {MES_LABELS.get(mes, mes)}/{ano}")

    SERIES_VIG = [
        ("3. ",   "Receita Liquida"),
        ("4. ",   "Despesa Total"),
        ("4.1 ",  "   Benef. Previdenciarios"),
        ("4.2 ",  "   Pessoal e Encargos"),
        ("4.3 ",  "   Outras Obrigatorias"),
        ("4.4.2", "   Discricionarias"),
        ("5. ",   "Resultado Primario"),
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
            "Indicador":     nome,
            "Mes atual":     _fmt_rtn(v_atual, is_pib),
            "Acum. 12m":     _fmt_rtn(v_12m, is_pib),
            f"{ano - 1}":    _fmt_rtn(v_ant, is_pib),
            "Var. a/a":      (f"{'+' if var_yoy > 0 else ''}{_fmt(var_yoy, 1)}%"
                              if var_yoy is not None else "—"),
            "Media 3 anos":  _fmt_rtn(v_media, is_pib) if v_media is not None else "—",
        })

    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.markdown("---")

    # Trajetoria do Resultado Primario acumulado 12m (historico completo)
    st.subheader("Trajetoria fiscal — Resultado Primario acumulado 12 meses")
    st.caption("Soma rolling de 12 meses. Linha abaixo de zero = deficit acumulado.")

    sub_res = _rtn_serie(df, "5. ").sort_values(["ano", "mes"]).copy()
    p_sel   = ano * 100 + mes
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
        # "MM/YYYY" → datetime para o rangeslider funcionar
        df_traj["data"] = pd.to_datetime(
            df_traj["label"].str[3:] + "-" + df_traj["label"].str[:2] + "-01"
        )
        y_title = "% do PIB (12m)" if is_pib else "R$ bilhoes (12m)"
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_traj["data"], y=df_traj["valor"],
            mode="lines+markers", fill="tozeroy",
            line_color="#2E86C1", fillcolor="rgba(46,134,193,0.12)",
        ))
        fig.add_hline(y=0, line_dash="dot", line_color="#E74C3C", opacity=0.7)
        data_fim = pd.Timestamp(f"{ano}-{mes:02d}-01")
        data_ini = data_fim - pd.DateOffset(years=5)
        fig.update_layout(
            yaxis_title=y_title, height=400, showlegend=False,
            font=dict(size=13),
            margin=dict(l=10, r=10, t=30, b=10),
            xaxis=dict(
                tickformat="%m/%Y",
                tickfont=dict(size=12),
                title_font=dict(size=13),
                range=[str(data_ini.date()), str((data_fim + pd.DateOffset(months=1)).date())],
                rangeslider=dict(visible=True, thickness=0.06),
                rangeselector=dict(buttons=[
                    dict(count=2,  label="2a",  step="year", stepmode="backward"),
                    dict(count=5,  label="5a",  step="year", stepmode="backward"),
                    dict(count=10, label="10a", step="year", stepmode="backward"),
                    dict(step="all", label="Max"),
                ]),
            ),
            yaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
        )
        st.plotly_chart(fig, use_container_width=True)


# -- Aba 4: Explorador de series RTN ---------------------------------------

def aba_explorador_rtn(dados, filtros, col_val, is_pib, opcao_sel):
    df = dados.get("rtn", pd.DataFrame())
    if df.empty:
        st.info("Dados RTN nao encontrados.")
        return

    ano, mes = filtros["ano"], filtros["mes"]

    series_disp = sorted(df["discriminacao"].unique().tolist())
    serie_sel   = st.selectbox("Serie fiscal", series_disp)

    sub = df[df["discriminacao"] == serie_sel].sort_values(["ano", "mes"]).copy()
    if sub.empty:
        st.info("Serie sem dados.")
        return

    sub["data"] = pd.to_datetime(
        sub["ano"].astype(str) + "-" + sub["mes"].astype(str).str.zfill(2) + "-01"
    )
    y_label = "% do PIB" if is_pib else "R$ Milhoes"

    fig = px.line(
        sub, x="data", y=col_val,
        labels={col_val: y_label, "data": ""},
        title=serie_sel,
    )
    fig.add_hline(y=0, line_dash="dot", line_color="#888", opacity=0.6)
    data_fim = pd.Timestamp(f"{ano}-{mes:02d}-01")
    data_ini = data_fim - pd.DateOffset(years=3)
    fig.update_layout(
        height=400, font=dict(size=13),
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(
            tickformat="%m/%Y",
            tickfont=dict(size=12),
            title_font=dict(size=13),
            range=[str(data_ini.date()), str((data_fim + pd.DateOffset(months=1)).date())],
            rangeslider=dict(visible=True, thickness=0.06),
            rangeselector=dict(buttons=[
                dict(count=1,  label="1a",  step="year", stepmode="backward"),
                dict(count=3,  label="3a",  step="year", stepmode="backward"),
                dict(count=5,  label="5a",  step="year", stepmode="backward"),
                dict(step="all", label="Max"),
            ]),
        ),
        yaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Ultimos 24 meses em tabela
    st.markdown("**Ultimos 24 meses**")
    p_sel   = ano * 100 + mes
    sub_p   = sub["ano"] * 100 + sub["mes"]
    ultimos = sub[sub_p <= p_sel].tail(24).sort_values(["ano", "mes"], ascending=False).copy()
    ultimos["Periodo"] = ultimos["mes"].map(MES_LABELS) + "/" + ultimos["ano"].astype(str)
    ultimos["Valor"]   = ultimos[col_val].apply(
        lambda v: _fmt_rtn(v, is_pib) if pd.notna(v) else "—"
    )
    st.dataframe(ultimos[["Periodo", "Valor"]], hide_index=True, use_container_width=True)

    csv = sub.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Baixar CSV",
        data=csv,
        file_name=f"rtn_{serie_sel[:40].replace(' ', '_').replace('.', '')}.csv",
        mime="text/csv",
    )


# -- App principal ---------------------------------------------------------

def main():
    st.title("📊 Radar Fiscal FIESP")
    st.caption(
        "Monitoramento de resultados fiscais do Governo Federal  ·  "
        "Secretaria do Tesouro Nacional"
    )

    dados  = carregar_dados()
    df_rtn = dados.get("rtn", pd.DataFrame())

    if df_rtn.empty:
        st.error(
            "Dados RTN nao encontrados. "
            "Execute `python pipelines/rtn/load.py` para baixar."
        )
        st.stop()

    filtros = sidebar_filtros(df_rtn)
    if not filtros:
        st.stop()

    # Seletor de metrica compartilhado entre todas as abas
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
        "Metrica", list(OPCOES.keys()),
        horizontal=True, label_visibility="collapsed",
    )
    col_val = OPCOES[opcao_sel]
    is_pib  = col_val == "pct_pib"

    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Resultado Fiscal",
        "🚨 Alertas",
        "🔎 Vigilancia Fiscal",
        "📋 Explorador",
    ])

    with tab1:
        aba_resultado_fiscal(dados, filtros, col_val, is_pib, opcao_sel)
    with tab2:
        aba_alertas_rtn(dados, filtros, col_val, is_pib)
    with tab3:
        aba_vigilancia_rtn(dados, filtros, col_val, is_pib)
    with tab4:
        aba_explorador_rtn(dados, filtros, col_val, is_pib, opcao_sel)

    st.markdown("---")
    st.caption(
        "Fonte: RTN · Secretaria do Tesouro Nacional · "
        "Atualizacao: `python pipelines/rtn/load.py`"
    )


# -- Funcoes legadas (Portal da Transparencia) -- temporariamente desabilitadas
# Reativar quando os endpoints forem liberados. Ver diagnostico_api_endpoints.xlsx.
# As funcoes aba_visao_geral, aba_alertas, aba_vigilancia, aba_explorador foram
# removidas desta versao. Recuperar do historico de sessao se necessario.


if __name__ == "__main__":
    main()
