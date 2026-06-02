"""
dashboard/pages/federal.py  —  Gastômetro · Governo Federal

Seções:
  1. Comparação ano atual vs. ano anterior (KPIs lado a lado)
  2. Receita × Despesa × Resultado Primário (série histórica)
  3. Composição da despesa (barras horizontais)
  4. Trajetória do resultado primário acumulado 12m
  5. Alertas de anomalia (z-score)
  6. Explorador de séries RTN
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from components.theme import (
    C, MES_LABELS, _RSEL, _RSLD,
    inject_css, render_navbar, render_footer,
    fmt_bi, fmt_br, fmt_pct, plotly_dark, rangeselector_buttons,
    carregar_dados, rtn_valor, rtn_soma_12m, rtn_delta_yoy,
)

st.set_page_config(
    page_title="Federal · Gastômetro FIESP",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_css()
render_navbar("federal")

dados    = carregar_dados()
df_rtn   = dados.get("rtn", pd.DataFrame())
meta     = dados.get("meta_rtn", {})
contador = dados.get("contador", {})

if df_rtn.empty:
    st.error("Dados RTN não encontrados. Execute `python pipelines/federal/load.py`.")
    st.stop()

anos_disp  = sorted(df_rtn["ano"].unique(), reverse=True)
ano_atual  = anos_disp[0]
ano_ant    = ano_atual - 1
meses_disp = sorted(df_rtn[df_rtn["ano"] == ano_atual]["mes"].unique(), reverse=True)
mes_atual  = meses_disp[0]

base_label = meta.get("base_constante", "IPCA")
OPCOES = {
    "Valores nominais (R$)":               "corrente_milhoes",
    f"Valores reais (R$ de {base_label})": "constante_milhoes",
    "% do PIB":                            "pct_pib",
}
opcao_sel = st.radio(
    "Métrica", list(OPCOES.keys()), horizontal=True, label_visibility="collapsed",
)
col_val = OPCOES[opcao_sel]
is_pib  = col_val == "pct_pib"

def fv(v):
    if v is None:
        return "—"
    sinal = "−" if v < 0 else ""
    if is_pib:
        return f"{sinal}{fmt_br(abs(v), 1)}%"
    return fmt_bi(v)

KPIS = [
    ("3. ",   "Receita Líquida",   "normal",   "Receita Total menos transferências por repartição."),
    ("4. ",   "Despesa Total",     "inverse",  "Previdência + Pessoal + Obrigatórias + Discricionárias."),
    ("5. ",   "Result. Primário",  "normal",   "Receita Líquida – Despesa Total. Negativo = déficit."),
    ("10.",   "Result. Nominal",   "normal",   "Primário + Juros Nominais."),
]

COMP_DESPESA = [
    ("4.1 ",   "Benef. Previdenciários"),
    ("4.2 ",   "Pessoal e Encargos Sociais"),
    ("4.3 ",   "Outras Obrigatórias"),
    ("4.4.1 ", "Obrigatórias c/ Controle de Fluxo"),
    ("4.4.2",  "Discricionárias"),
]

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


# ── Seção 1: Comparação ano atual × ano anterior ──────────────────────────

st.markdown(
    f"<div class='kpi-sub'>{ano_ant} → {ano_atual} &nbsp;·&nbsp; "
    f"{MES_LABELS.get(mes_atual, mes_atual)}</div>",
    unsafe_allow_html=True,
)

cols = st.columns(4)
for col_ui, (prefixo, label, dcor, help_) in zip(cols, KPIS):
    with col_ui:
        val_atual = rtn_valor(df_rtn, prefixo, ano_atual, mes_atual, col_val)
        val_ant   = rtn_valor(df_rtn, prefixo, ano_ant,   mes_atual, col_val)
        delta = None
        if val_atual is not None and val_ant is not None and val_ant != 0:
            diff_pct = round((val_atual - val_ant) / abs(val_ant) * 100, 1)
            delta = f"{'+' if diff_pct >= 0 else ''}{fmt_br(diff_pct, 1)}% a/a"
        st.metric(label, fv(val_atual), delta=delta, delta_color=dcor, help=help_)

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

# Comparação mensal: tabela lado a lado dos dois anos
with st.expander(f"Comparativo detalhado {ano_ant} × {ano_atual}", expanded=False):
    rows = []
    for prefixo, label, _, _ in KPIS:
        v_at  = rtn_valor(df_rtn, prefixo, ano_atual, mes_atual, col_val)
        v_an  = rtn_valor(df_rtn, prefixo, ano_ant,   mes_atual, col_val)
        diff  = None
        if v_at is not None and v_an is not None and v_an != 0:
            diff = round((v_at - v_an) / abs(v_an) * 100, 1)
        rows.append({
            "Indicador": label,
            str(ano_ant):   fv(v_an),
            str(ano_atual): fv(v_at),
            "Var. a/a":     (f"{'+' if diff and diff >= 0 else ''}{fmt_br(diff, 1)}%" if diff is not None else "—"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')


# ── Seção 2: Receita × Despesa × Resultado ────────────────────────────────

st.markdown(
    f"<div style='font-size:13px;font-weight:600;color:{C['text']};margin-bottom:8px;'>"
    "Receita × Despesa × Resultado Primário</div>",
    unsafe_allow_html=True,
)

p_sel = ano_atual * 100 + mes_atual
y_label = "% do PIB" if is_pib else "R$ Milhões"

linhas = []
for prefixo, nome, _, _ in KPIS[:3]:
    sub = df_rtn[df_rtn["discriminacao"].str.startswith(prefixo)].copy()
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

import pandas as _pd
data_fim = _pd.Timestamp(f"{ano_atual}-{mes_atual:02d}-01")
data_ini = data_fim - _pd.DateOffset(years=3)

fig.update_layout(
    legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5, title=""),
    xaxis=dict(
        tickformat="%m/%Y",
        range=[str(data_ini.date()), str((data_fim + _pd.DateOffset(months=1)).date())],
        rangeslider=dict(**_RSLD),
        rangeselector=dict(**_RSEL, buttons=rangeselector_buttons()),
    ),
)
plotly_dark(fig, height=420, margin=dict(l=10, r=10, t=20, b=60))
st.plotly_chart(fig, width='stretch', key="fed_receita_despesa")

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)


# ── Seção 3: Composição da despesa ────────────────────────────────────────

import textwrap as _tw

st.markdown(
    f"<div style='font-size:13px;font-weight:600;color:{C['text']};margin-bottom:8px;'>"
    "Composição da Despesa Federal</div>",
    unsafe_allow_html=True,
)

tab_mes, tab_12m = st.tabs([
    f"Mês ({MES_LABELS.get(mes_atual,'')}/{ano_atual})",
    "Acumulado 12 meses",
])

def _comp_chart(fn_val, titulo_key):
    items = [{"Categoria": n, "Valor": fn_val(p)} for p, n in COMP_DESPESA]
    items = [i for i in items if i["Valor"] is not None and pd.notna(i["Valor"])]
    if not items:
        st.info("Sem dados de composição.")
        return
    df_c = pd.DataFrame(items).sort_values("Valor", ascending=True)
    df_c["label"] = df_c["Categoria"].apply(
        lambda s: "<br>".join(_tw.wrap(str(s), 24))
    )
    if is_pib:
        x_vals = df_c["Valor"]
        x_title = "% do PIB"
        texts = df_c["Valor"].apply(lambda v: f"{fmt_br(v, 1)}%")
    else:
        x_vals = df_c["Valor"] / 1e3
        x_title = "R$ bilhões"
        texts = df_c["Valor"].apply(lambda v: f"R$ {fmt_br(v / 1e3, 1)} bi")

    fig = go.Figure(go.Bar(
        x=x_vals, y=df_c["label"], orientation="h",
        marker_color=C["despesa"], marker_line_width=0,
        text=texts, textposition="outside",
        textfont=dict(size=11, color=C["text_dim"]),
        cliponaxis=False,
    ))
    x_max = float(x_vals.max())
    fig.update_layout(
        xaxis_title=x_title, xaxis=dict(range=[0, x_max * 1.55]),
        showlegend=False,
    )
    plotly_dark(fig, height=max(220, len(items) * 72 + 70),
                margin=dict(l=185, r=20, t=10, b=30))
    st.plotly_chart(fig, width='stretch', key=titulo_key)

with tab_mes:
    _comp_chart(
        fn_val=lambda p: rtn_valor(df_rtn, p, ano_atual, mes_atual, col_val),
        titulo_key="comp_mes",
    )
with tab_12m:
    _comp_chart(
        fn_val=lambda p: rtn_soma_12m(df_rtn, p, ano_atual, mes_atual, col_val),
        titulo_key="comp_12m",
    )

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)


# ── Seção 4: Trajetória fiscal (primário acumulado 12m) ───────────────────

st.markdown(
    f"<div style='font-size:13px;font-weight:600;color:{C['text']};margin-bottom:4px;'>"
    "Trajetória do Resultado Primário — acumulado 12 meses</div>",
    unsafe_allow_html=True,
)
st.caption("Soma rolling de 12 meses. Linha abaixo de zero = déficit acumulado.")

sub_res = df_rtn[df_rtn["discriminacao"].str.startswith("5. ")].sort_values(["ano", "mes"]).copy()
traj = []
for _, row in sub_res.iterrows():
    a, m = int(row["ano"]), int(row["mes"])
    if a * 100 + m > p_sel:
        break
    v = rtn_soma_12m(df_rtn, "5. ", a, m, col_val)
    if v is not None:
        traj.append({
            "data":   _pd.Timestamp(f"{a}-{m:02d}-01"),
            "valor":  v / (1 if is_pib else 1e3),
        })

if traj:
    df_traj = _pd.DataFrame(traj)
    y_title = "% do PIB (12m)" if is_pib else "R$ bilhões (12m)"
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=df_traj["data"], y=df_traj["valor"],
        mode="lines", fill="tozeroy",
        line=dict(color=C["resultado"], width=2),
        fillcolor="rgba(56,189,248,0.07)",
    ))
    fig2.add_hline(y=0, line_dash="dot", line_color=C["negative"], opacity=0.5)
    d2_fim = _pd.Timestamp(f"{ano_atual}-{mes_atual:02d}-01")
    d2_ini = d2_fim - _pd.DateOffset(years=5)
    fig2.update_layout(
        yaxis_title=y_title, showlegend=False,
        xaxis=dict(
            tickformat="%m/%Y",
            range=[str(d2_ini.date()), str((d2_fim + _pd.DateOffset(months=1)).date())],
            rangeslider=dict(**_RSLD),
            rangeselector=dict(**_RSEL, buttons=rangeselector_buttons()),
        ),
    )
    plotly_dark(fig2, height=380, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig2, width='stretch', key="fed_trajetoria")

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)


# ── Seção 5: Alertas ──────────────────────────────────────────────────────

with st.expander("🚨 Alertas de anomalia (z-score)", expanded=False):
    alertas = []
    for prefixo, nome in SERIES_ALERTA:
        sub = df_rtn[df_rtn["discriminacao"].str.startswith(prefixo)].sort_values(["ano", "mes"]).copy()
        if len(sub) < 8:
            continue
        vals  = sub[col_val].fillna(0)
        media = vals.shift(1).rolling(24, min_periods=6).mean()
        std   = vals.shift(1).rolling(24, min_periods=6).std()
        sub["z"] = (vals - media) / std.replace(0, np.nan)
        row_sel = sub[(sub["ano"] == ano_atual) & (sub["mes"] == mes_atual)]
        if row_sel.empty:
            continue
        z = row_sel["z"].iloc[0]
        v = row_sel[col_val].iloc[0]
        if pd.isna(z) or abs(z) < 2.0:
            continue
        nivel = "vermelho" if abs(z) >= 3.0 else "amarelo"
        alertas.append({"serie": nome, "zscore": z, "valor": v, "nivel": nivel})

    if not alertas:
        st.success(
            f"Nenhuma anomalia detectada em {MES_LABELS.get(mes_atual,'')}/{ano_atual}."
        )
    else:
        for a in sorted(alertas, key=lambda x: abs(x["zscore"]), reverse=True):
            css  = f"alerta-{a['nivel']}"
            icone = "●" if a["nivel"] == "vermelho" else "◆"
            cor   = C["negative"] if a["nivel"] == "vermelho" else C["warning"]
            st.markdown(
                f'<div class="{css}">'
                f'<span style="color:{cor}">{icone}</span> '
                f'<strong>{a["serie"]}</strong>'
                f' &nbsp;|&nbsp; Z-score: <strong style="color:{cor}">{a["zscore"]:.1f}σ</strong>'
                f' &nbsp;|&nbsp; Valor: <strong>{fv(a["valor"])}</strong>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ── Seção 6: Explorador de séries ────────────────────────────────────────

with st.expander("📋 Explorador de séries RTN", expanded=False):
    series_disp = sorted(df_rtn["discriminacao"].unique().tolist())
    serie_sel   = st.selectbox("Série fiscal", series_disp, key="fed_serie_sel")
    sub_exp     = df_rtn[df_rtn["discriminacao"] == serie_sel].sort_values(["ano", "mes"]).copy()

    if not sub_exp.empty:
        sub_exp["data"] = pd.to_datetime(
            sub_exp["ano"].astype(str) + "-" + sub_exp["mes"].astype(str).str.zfill(2) + "-01"
        )
        fig3 = px.line(
            sub_exp, x="data", y=col_val,
            labels={col_val: "% do PIB" if is_pib else "R$ Milhões", "data": ""},
            title=serie_sel,
            color_discrete_sequence=[C["resultado"]],
        )
        fig3.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.15)", opacity=0.8)
        d_fim = _pd.Timestamp(f"{ano_atual}-{mes_atual:02d}-01")
        d_ini = d_fim - _pd.DateOffset(years=3)
        fig3.update_layout(
            xaxis=dict(
                tickformat="%m/%Y",
                range=[str(d_ini.date()), str((d_fim + _pd.DateOffset(months=1)).date())],
                rangeslider=dict(**_RSLD),
                rangeselector=dict(**_RSEL, buttons=rangeselector_buttons()),
            ),
        )
        plotly_dark(fig3, height=380, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig3, width='stretch', key="fed_explorador")

        csv = sub_exp.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Baixar CSV",
            data=csv,
            file_name=f"rtn_{serie_sel[:40].replace(' ','_').replace('.','')}.csv",
            mime="text/csv",
        )

render_footer("RTN · Secretaria do Tesouro Nacional")
