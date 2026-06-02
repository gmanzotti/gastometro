"""
dashboard/pages/projecoes.py  —  Gastômetro · Projeções

Metodologia: ratio rolling 12 meses (federal) / 6 bimestres (subnacional).

Seções:
  1. Trajetória projetada: Despesa Total federal (base + projeção 2 anos)
  2. Espaço fiscal: Discricionárias vs. Obrigatórias — compressão projetada
  3. Cenário interativo: usuário ajusta taxa de crescimento e vê o impacto
  4. Resumo subnacional (se dados disponíveis)
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from components.theme import (
    C, MES_LABELS, inject_css, render_navbar, render_footer,
    fmt_bi, fmt_br, fmt_pct, plotly_dark,
    carregar_dados, rtn_valor, rtn_soma_12m,
)

st.set_page_config(
    page_title="Projeções · Gastômetro FIESP",
    page_icon="🔭",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_css()
render_navbar("projecoes")

dados    = carregar_dados()
df_rtn   = dados.get("rtn", pd.DataFrame())
contador = dados.get("contador", {})
df_est   = dados.get("estados", pd.DataFrame())
meta     = dados.get("meta_rtn", {})

if df_rtn.empty:
    st.error("Dados RTN não encontrados. Execute `python pipelines/federal/load.py`.")
    st.stop()

anos_disp  = sorted(df_rtn["ano"].unique(), reverse=True)
ano_atual  = anos_disp[0]
meses_disp = sorted(df_rtn[df_rtn["ano"] == ano_atual]["mes"].unique(), reverse=True)
mes_atual  = meses_disp[0]


def _section_title(txt: str):
    st.markdown(
        f"<div style='font-size:13px;font-weight:600;color:{C['text']};"
        f"margin-bottom:6px;'>{txt}</div>",
        unsafe_allow_html=True,
    )


# ── Cálculo do ratio rolling para RTN ─────────────────────────────────────

def _calcular_ratio_rtn(prefixo: str, col: str = "corrente_milhoes") -> float | None:
    """Ratio rolling 12 meses para uma série da RTN."""
    sub = df_rtn[df_rtn["discriminacao"].str.startswith(prefixo)].sort_values(["ano", "mes"])
    if len(sub) < 24:
        return None
    vals = sub[col].values
    soma_rec = vals[-12:].sum()
    soma_ant = vals[-24:-12].sum()
    if soma_ant <= 0 or soma_rec <= 0:
        return None
    ratio = soma_rec / soma_ant
    return ratio if 0.5 <= ratio <= 3.0 else None


def _serie_historica(prefixo: str, col: str = "corrente_milhoes") -> pd.DataFrame:
    sub = df_rtn[df_rtn["discriminacao"].str.startswith(prefixo)].sort_values(["ano", "mes"]).copy()
    sub["data"] = pd.to_datetime(
        sub["ano"].astype(str) + "-" + sub["mes"].astype(str).str.zfill(2) + "-01"
    )
    return sub[["data", col]].rename(columns={col: "valor"})


def _projetar(
    df_hist: pd.DataFrame,
    ratio: float,
    n_meses: int = 24,
) -> pd.DataFrame:
    """
    Projeta n_meses à frente usando ratio rolling.
    Âncora sazonal: mesmo mês do ano anterior × ratio.
    """
    df = df_hist.sort_values("data").copy()
    df_dict = dict(zip(df["data"], df["valor"]))

    ultima = df["data"].max()
    projs  = []
    for i in range(1, n_meses + 1):
        alvo   = ultima + pd.DateOffset(months=i)
        ancora = alvo   - pd.DateOffset(years=1)
        base   = df_dict.get(pd.Timestamp(ancora.year, ancora.month, 1))
        if base is None:
            break
        val = base * ratio
        df_dict[pd.Timestamp(alvo.year, alvo.month, 1)] = val
        projs.append({"data": pd.Timestamp(alvo.year, alvo.month, 1), "valor": val})

    return pd.DataFrame(projs)


# ── Seção 1: Trajetória da despesa total ──────────────────────────────────

_section_title("Trajetória da Despesa Total Federal")
st.caption(
    "Série histórica (RTN) + projeção para os próximos 24 meses pelo ratio rolling 12 meses. "
    "A projeção assume que a sazonalidade mensal se mantém proporcional ao crescimento recente."
)

ratio_desp = _calcular_ratio_rtn("4. ")
df_desp_hist = _serie_historica("4. ")

# Últimos 3 anos de histórico + projeção
data_corte = pd.Timestamp(f"{ano_atual - 2}-01-01")
df_desp_plot = df_desp_hist[df_desp_hist["data"] >= data_corte].copy()

if ratio_desp:
    df_proj_desp = _projetar(df_desp_hist, ratio_desp, n_meses=24)

    fig_traj = go.Figure()
    fig_traj.add_trace(go.Scatter(
        x=df_desp_plot["data"], y=df_desp_plot["valor"] / 1e3,
        mode="lines",
        line=dict(color=C["despesa"], width=2.5),
        name="Histórico",
    ))
    if not df_proj_desp.empty:
        # Ponto de conexão: último histórico → primeiro projetado
        ultimo = df_desp_plot.iloc[-1]
        x_conn = [ultimo["data"]] + list(df_proj_desp["data"])
        y_conn = [ultimo["valor"] / 1e3] + list(df_proj_desp["valor"] / 1e3)
        fig_traj.add_trace(go.Scatter(
            x=x_conn, y=y_conn,
            mode="lines",
            line=dict(color=C["warning"], width=2, dash="dash"),
            name=f"Projeção (ratio={fmt_br(ratio_desp, 4)})",
        ))
        # Banda de incerteza ±10%
        y_up  = [v * 1.10 for v in y_conn]
        y_down = [v * 0.90 for v in y_conn]
        fig_traj.add_trace(go.Scatter(
            x=x_conn + x_conn[::-1],
            y=y_up + y_down[::-1],
            fill="toself",
            fillcolor="rgba(245,158,11,0.06)",
            line=dict(width=0),
            hoverinfo="skip",
            showlegend=False,
        ))

    fig_traj.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.1)")
    fig_traj.update_layout(
        yaxis_title="R$ bilhões",
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5),
    )
    plotly_dark(fig_traj, height=400, margin=dict(l=10, r=10, t=20, b=60))
    st.plotly_chart(fig_traj, width='stretch', key="proj_trajetoria_desp")

    # Valor projetado no fim do horizonte
    if not df_proj_desp.empty:
        val_fim  = df_proj_desp["valor"].iloc[-1]
        val_base = df_desp_hist[df_desp_hist["data"].dt.year == ano_atual]["valor"].sum()
        col_m1, col_m2, col_m3 = st.columns(3)
        with col_m1:
            st.metric(
                "Ratio rolling (crescimento)",
                f"{fmt_br((ratio_desp - 1) * 100, 1)}% a.a.",
                help="Crescimento implícito na projeção pelo ratio rolling 12 meses.",
            )
        with col_m2:
            st.metric(
                "Despesa projetada (fim do horizonte / mês)",
                fmt_bi(val_fim),
                help="Valor mensal projetado no último mês da janela de projeção.",
            )
        with col_m3:
            acum_proj = df_proj_desp["valor"].sum()
            st.metric(
                "Total projetado (24 meses)",
                fmt_bi(acum_proj),
            )
else:
    st.warning("Dados insuficientes para calcular o ratio rolling. Verifique o parquet da RTN.")

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)


# ── Seção 2: Compressão do espaço fiscal ─────────────────────────────────

_section_title("Compressão do Espaço Fiscal — Obrigatórias vs. Discricionárias")
st.caption(
    "À medida que os gastos obrigatórios crescem mais rápido que a receita, "
    "o espaço para despesas discricionárias (onde vive o investimento público) se comprime. "
    "Projeção de 24 meses pelo ratio de cada categoria."
)

CATS = {
    "Benef. Previdenciários": ("4.1 ",  C["negative"]),
    "Pessoal e Encargos":     ("4.2 ",  "#F97316"),
    "Outras Obrigatórias":    ("4.3 ",  C["warning"]),
    "Discricionárias":        ("4.4.2", C["accent"]),
}

fig_comp_proj = go.Figure()
ratio_por_cat = {}

for nome, (pref, cor) in CATS.items():
    hist = _serie_historica(pref)
    hist_plot = hist[hist["data"] >= data_corte].copy()
    ratio = _calcular_ratio_rtn(pref)
    ratio_por_cat[nome] = ratio

    fig_comp_proj.add_trace(go.Scatter(
        x=hist_plot["data"], y=hist_plot["valor"] / 1e3,
        mode="lines",
        line=dict(color=cor, width=2),
        name=nome,
    ))

    if ratio:
        proj = _projetar(hist, ratio, 24)
        if not proj.empty:
            ultimo = hist_plot.iloc[-1]
            x_c = [ultimo["data"]] + list(proj["data"])
            y_c = [ultimo["valor"] / 1e3] + list(proj["valor"] / 1e3)
            fig_comp_proj.add_trace(go.Scatter(
                x=x_c, y=y_c,
                mode="lines",
                line=dict(color=cor, width=1.5, dash="dot"),
                showlegend=False,
                hovertemplate=f"{nome} projetado<br>%{{x}}: R$ %{{y:.1f}} bi<extra></extra>",
            ))

fig_comp_proj.update_layout(
    yaxis_title="R$ bilhões",
    legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5, title=""),
)
plotly_dark(fig_comp_proj, height=420, margin=dict(l=10, r=10, t=20, b=60))
st.plotly_chart(fig_comp_proj, width='stretch', key="proj_compressao")

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)


# ── Seção 3: Cenário interativo ────────────────────────────────────────────

_section_title("Cenário Interativo — e se crescer diferente?")
st.caption(
    "Ajuste a taxa de crescimento anual das despesas obrigatórias e veja o impacto "
    "no espaço fiscal disponível para discricionárias (proxy de investimento)."
)

ratio_obrig_atual = _calcular_ratio_rtn("4.3 ")
crescimento_atual_pct = round((ratio_obrig_atual - 1) * 100, 1) if ratio_obrig_atual else 8.0

col_slider, col_result = st.columns([1, 1])

with col_slider:
    taxa_user = st.slider(
        "Crescimento anual das despesas obrigatórias (%)",
        min_value=0.0, max_value=20.0,
        value=float(crescimento_atual_pct),
        step=0.5,
        help=(
            f"Valor atual pela tendência: {fmt_br(crescimento_atual_pct, 1)}%. "
            "Use este slider para simular cenários de ajuste fiscal."
        ),
    )
    taxa_fiesp = st.slider(
        "Crescimento no cenário FIESP (%)",
        min_value=0.0, max_value=20.0,
        value=min(crescimento_atual_pct * 0.5, 4.0),
        step=0.5,
        help="O que aconteceria com um ajuste fiscal que limitasse o crescimento obrigatório.",
    )

ratio_user  = 1 + taxa_user  / 100
ratio_fiesp = 1 + taxa_fiesp / 100

# Receita: projeta pelo próprio ratio
ratio_rec = _calcular_ratio_rtn("3. ")
hist_rec  = _serie_historica("3. ")
hist_obrig = _serie_historica("4.3 ")
hist_disc  = _serie_historica("4.4.2")

n_proj = 36

with col_result:
    if ratio_rec and not hist_rec.empty and not hist_obrig.empty:
        proj_rec        = _projetar(hist_rec,   ratio_rec,   n_proj)
        proj_obrig_user = _projetar(hist_obrig, ratio_user,  n_proj)
        proj_obrig_fsp  = _projetar(hist_obrig, ratio_fiesp, n_proj)

        # Espaço fiscal = receita − obrigatórias
        if not proj_rec.empty and not proj_obrig_user.empty:
            df_espaco = proj_rec.merge(
                proj_obrig_user, on="data", suffixes=("_rec", "_obrig_user")
            )
            df_espaco_fsp = proj_rec.merge(
                proj_obrig_fsp, on="data", suffixes=("_rec", "_obrig_fsp")
            )
            df_espaco["espaco_user"]  = (df_espaco["valor_rec"]  - df_espaco["valor_obrig_user"])  / 1e3
            df_espaco_fsp["espaco_fsp"] = (df_espaco_fsp["valor_rec"] - df_espaco_fsp["valor_obrig_fsp"]) / 1e3

            # Espaço atual (último mês histórico)
            rec_atual   = rtn_valor(df_rtn, "3. ",   ano_atual, mes_atual, "corrente_milhoes") or 0
            obrig_atual = rtn_valor(df_rtn, "4.3 ",  ano_atual, mes_atual, "corrente_milhoes") or 0
            espaco_atual = (rec_atual - obrig_atual) / 1e3

            st.metric(
                "Espaço fiscal atual",
                f"R$ {fmt_br(espaco_atual, 1)} bi/mês",
                help="Receita Líquida - Outras Obrigatórias no último mês disponível.",
            )
            if not df_espaco.empty:
                espaco_fim_user = df_espaco["espaco_user"].iloc[-1]
                espaco_fim_fsp  = df_espaco_fsp["espaco_fsp"].iloc[-1]
                st.metric(
                    f"Espaço projetado (cenário {fmt_br(taxa_user,1)}% a.a.)",
                    f"R$ {fmt_br(espaco_fim_user, 1)} bi/mês",
                    delta=f"{fmt_br(espaco_fim_user - espaco_atual, 1)} bi vs. hoje",
                    delta_color="normal",
                )
                st.metric(
                    f"Espaço no cenário FIESP ({fmt_br(taxa_fiesp,1)}% a.a.)",
                    f"R$ {fmt_br(espaco_fim_fsp, 1)} bi/mês",
                    delta=f"{fmt_br(espaco_fim_fsp - espaco_atual, 1)} bi vs. hoje",
                    delta_color="normal",
                )

# Gráfico do cenário
if ratio_rec and not hist_rec.empty and not hist_obrig.empty:
    proj_rec        = _projetar(hist_rec,   ratio_rec,   n_proj)
    proj_obrig_user = _projetar(hist_obrig, ratio_user,  n_proj)
    proj_obrig_fsp  = _projetar(hist_obrig, ratio_fiesp, n_proj)

    fig_cen = go.Figure()

    # Histórico
    rec_hist_plot   = hist_rec[hist_rec["data"]   >= data_corte]
    obrig_hist_plot = hist_obrig[hist_obrig["data"] >= data_corte]

    fig_cen.add_trace(go.Scatter(
        x=rec_hist_plot["data"], y=rec_hist_plot["valor"] / 1e3,
        mode="lines", line=dict(color=C["receita"], width=2),
        name="Receita Líquida (histórico)",
    ))
    fig_cen.add_trace(go.Scatter(
        x=obrig_hist_plot["data"], y=obrig_hist_plot["valor"] / 1e3,
        mode="lines", line=dict(color=C["despesa"], width=2),
        name="Obrigatórias (histórico)",
    ))

    # Projeções
    if not proj_rec.empty:
        ul = rec_hist_plot.iloc[-1]
        xc = [ul["data"]] + list(proj_rec["data"])
        yc = [ul["valor"] / 1e3] + list(proj_rec["valor"] / 1e3)
        fig_cen.add_trace(go.Scatter(
            x=xc, y=yc, mode="lines",
            line=dict(color=C["receita"], width=1.5, dash="dash"),
            name="Receita projetada", showlegend=True,
        ))

    if not proj_obrig_user.empty:
        ul = obrig_hist_plot.iloc[-1]
        xc = [ul["data"]] + list(proj_obrig_user["data"])
        yc = [ul["valor"] / 1e3] + list(proj_obrig_user["valor"] / 1e3)
        fig_cen.add_trace(go.Scatter(
            x=xc, y=yc, mode="lines",
            line=dict(color=C["negative"], width=1.5, dash="dash"),
            name=f"Obrigatórias {fmt_br(taxa_user,1)}% a.a.",
        ))

    if not proj_obrig_fsp.empty:
        ul = obrig_hist_plot.iloc[-1]
        xc = [ul["data"]] + list(proj_obrig_fsp["data"])
        yc = [ul["valor"] / 1e3] + list(proj_obrig_fsp["valor"] / 1e3)
        fig_cen.add_trace(go.Scatter(
            x=xc, y=yc, mode="lines",
            line=dict(color=C["positive"], width=1.5, dash="dot"),
            name=f"Cenário FIESP {fmt_br(taxa_fiesp,1)}% a.a.",
        ))

    # Linha de hoje
    fig_cen.add_vline(
        x=pd.Timestamp(f"{ano_atual}-{mes_atual:02d}-01").timestamp() * 1000,
        line_dash="dot", line_color=C["accent"], opacity=0.4,
        annotation_text="hoje",
        annotation_font_color=C["text_muted"],
    )

    fig_cen.update_layout(
        yaxis_title="R$ bilhões",
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5, title=""),
    )
    plotly_dark(fig_cen, height=420, margin=dict(l=10, r=10, t=20, b=60))
    st.plotly_chart(fig_cen, width='stretch', key="proj_cenario")

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)


# ── Seção 4: Resumo subnacional ───────────────────────────────────────────

if not df_est.empty:
    _section_title("Tendência de Investimento — Estados (últimos 2 anos)")
    st.caption(
        "Variação da proporção de investimento entre os dois anos disponíveis. "
        "Verde = estado aumentou o share de investimento; vermelho = reduziu."
    )

    COL_PADRAO = "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)"
    CONTAS_INV = {"Investimentos", "InversoesFinanceiras"}
    CONTA_TOT  = "DespesasExcetoIntraOrcamentarias"

    def _ratio_por_ano(ano_filtro):
        df_f = df_est[
            (df_est["ano"]    == ano_filtro) &
            (df_est["coluna"] == COL_PADRAO)
        ]
        inv = (
            df_f[df_f["cod_conta"].isin(CONTAS_INV)]
            .groupby("uf")["valor_milhoes"].sum()
        )
        tot = (
            df_f[df_f["cod_conta"] == CONTA_TOT]
            .groupby("uf")["valor_milhoes"].sum()
        )
        return (inv / tot * 100).rename(f"ratio_{ano_filtro}")

    anos_est = sorted(df_est["ano"].unique())
    if len(anos_est) >= 2:
        a1, a2 = anos_est[-2], anos_est[-1]
        r1 = _ratio_por_ano(a1)
        r2 = _ratio_por_ano(a2)
        df_trend = pd.concat([r1, r2], axis=1).dropna().reset_index()
        df_trend.columns = ["UF", str(a1), str(a2)]
        df_trend["Variação (p.p.)"] = df_trend[str(a2)] - df_trend[str(a1)]
        df_trend = df_trend.sort_values("Variação (p.p.)", ascending=False)

        fig_trend = go.Figure(go.Bar(
            y=df_trend["UF"],
            x=df_trend["Variação (p.p.)"],
            orientation="h",
            marker_color=[
                C["positive"] if v >= 0 else C["negative"]
                for v in df_trend["Variação (p.p.)"]
            ],
            marker_line_width=0,
            text=df_trend["Variação (p.p.)"].apply(
                lambda v: f"{'+' if v >= 0 else ''}{fmt_br(v, 1)} p.p."
            ),
            textposition="outside",
            textfont=dict(size=10, color=C["text_dim"]),
            cliponaxis=False,
        ))
        x_abs = df_trend["Variação (p.p.)"].abs().max()
        fig_trend.update_layout(
            xaxis_title=f"Variação no share de investimento {a1}→{a2} (pontos percentuais)",
            xaxis=dict(range=[-x_abs * 1.5, x_abs * 1.5]),
            showlegend=False,
        )
        plotly_dark(fig_trend, height=560, margin=dict(l=50, r=80, t=10, b=40))
        st.plotly_chart(fig_trend, width='stretch', key="proj_trend_estados")
    else:
        st.info("Necessário pelo menos 2 anos de dados para mostrar a tendência.")

render_footer("RTN · STN · SICONFI · Tesouro Nacional · Projeções via ratio rolling 12 meses")
