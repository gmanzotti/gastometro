"""
dashboard/pages/estadual.py  —  Gastômetro · Estadual
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from components.theme import (
    C, inject_css, render_navbar, render_footer,
    fmt_br, plotly_dark,
    carregar_dados, carregar_geojson_estados,
    calcular_ratio_investimento_estados, calcular_serie_estado,
)

st.set_page_config(
    page_title="Estadual · Gastômetro FIESP",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_css()
render_navbar("estadual")

dados  = carregar_dados()
df_est = dados.get("estados", pd.DataFrame())

CONTAS_NOME = {
    "DespesasExcetoIntraOrcamentarias": "Despesa Total",
    "DespesasCorrentes":                "Desp. Correntes",
    "PessoalEEncargosSociais":          "Pessoal e Encargos",
    "JurosEEncargosDaDivida":           "Juros da Dívida",
    "OutrasDespesasCorrentes":          "Outras Desp. Correntes",
    "DespesasDeCapital":                "Desp. de Capital",
    "Investimentos":                    "Investimentos",
    "InversoesFinanceiras":             "Inversões Financeiras",
    "AmortizacaoDaDivida":              "Amort. Dívida",
}

COLUNA_PADRAO    = "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)"
COLUNA_BIMESTRAL = "DESPESAS LIQUIDADAS NO BIMESTRE"

CATS_COMP = [
    ("PessoalEEncargosSociais",  "Pessoal e Encargos",   C["corrente"]),
    ("JurosEEncargosDaDivida",   "Juros da Dívida",       "#F97316"),
    ("OutrasDespesasCorrentes",  "Outras Correntes",      "#FB923C"),
    ("Investimentos",            "Investimentos",         C["investimento"]),
    ("InversoesFinanceiras",     "Inversões Financeiras", "#16A34A"),
    ("AmortizacaoDaDivida",      "Amort. Dívida",         C["warning"]),
]


def _section_title(txt: str):
    st.markdown(
        f"<div style='font-size:13px;font-weight:600;color:{C['text']};"
        f"margin-bottom:8px;'>{txt}</div>",
        unsafe_allow_html=True,
    )


def _render_coropletico(ratio_df: pd.DataFrame) -> tuple[int, int]:
    """Renderiza o mapa coroplético. Retorna (ano, bimestre) do período exibido."""
    ano_max = int(ratio_df["ano"].iloc[0])
    per_max = int(ratio_df["periodo"].iloc[0])

    _section_title(
        f"Proporção de Investimento por Estado — {ano_max} B{per_max} "
        f"<span style='font-size:11px;font-weight:400;color:{C['text_muted']};'>"
        f"(rolling 12 meses)</span>"
    )

    geojson = carregar_geojson_estados()

    if geojson:
        df_map = ratio_df.copy()
        df_map["cod_str"] = df_map["cod_ibge"].astype(str)

        fig_map = go.Figure(go.Choroplethmapbox(
            geojson=geojson,
            featureidkey="properties.codarea",
            locations=df_map["cod_str"],
            z=df_map["invest_ratio"],
            colorscale="RdYlGn",
            zmin=0,
            zmax=df_map["invest_ratio"].max() * 1.1,
            colorbar=dict(
                title=dict(text="%", font=dict(color=C["text_dim"], size=11)),
                thickness=14,
                bgcolor="rgba(13,27,46,0.85)",
                tickfont=dict(color=C["text_dim"], size=10),
            ),
            text=df_map["uf"],
            customdata=df_map[["ente", "invest_ratio", "invest_milhoes", "total_milhoes"]].values,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Investimento: <b>%{customdata[1]:.1f}%</b><br>"
                "R$ %{customdata[2]:,.0f} mi investidos<br>"
                "R$ %{customdata[3]:,.0f} mi total<br>"
                "<extra></extra>"
            ),
            marker_line_color=C["border"],
            marker_line_width=0.5,
        ))
        fig_map.update_layout(
            mapbox_style="carto-darkmatter",
            mapbox_zoom=3.2,
            mapbox_center={"lat": -14.5, "lon": -51.5},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=460,
            margin=dict(l=0, r=0, t=0, b=0),
        )
        st.plotly_chart(fig_map, width="stretch", key="est_mapa")
    else:
        st.info("GeoJSON não disponível. Exibindo tabela.")
        st.dataframe(
            ratio_df[["uf", "ente", "invest_ratio", "invest_milhoes", "total_milhoes"]]
            .rename(columns={
                "uf": "UF", "ente": "Estado",
                "invest_ratio": "Invest. %",
                "invest_milhoes": "Invest. (R$ mi)",
                "total_milhoes": "Total (R$ mi)",
            }),
            hide_index=True, width="stretch",
        )

    return ano_max, per_max


def _render_composicao(df_uf: pd.DataFrame, nome: str, ano: int, bim: int):
    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)
    _section_title(f"Composição do gasto — {nome} · {ano} B{bim}")

    contas_set = {c for c, _, _ in CATS_COMP}
    df_comp = df_uf[
        (df_uf["ano"]     == ano) &
        (df_uf["periodo"] == bim) &
        (df_uf["coluna"]  == COLUNA_PADRAO) &
        (df_uf["cod_conta"].isin(contas_set))
    ].copy()

    if df_comp.empty:
        st.info("Sem dados de composição para o período.")
        return

    df_comp = df_comp.groupby("cod_conta")["valor_milhoes"].sum().reset_index()
    ordem = {c: i for i, (c, _, _) in enumerate(CATS_COMP)}
    df_comp = df_comp.sort_values("valor_milhoes", ascending=True)

    cor_map  = {c: cor for c, _, cor in CATS_COMP}
    nome_map = {c: n   for c, n, _ in CATS_COMP}

    df_comp["nome"] = df_comp["cod_conta"].map(nome_map)
    df_comp["cor"]  = df_comp["cod_conta"].map(cor_map).fillna(C["primary"])

    fig = go.Figure(go.Bar(
        x=df_comp["valor_milhoes"] / 1e3,
        y=df_comp["nome"],
        orientation="h",
        marker_color=df_comp["cor"].tolist(),
        marker_line_width=0,
        text=df_comp["valor_milhoes"].apply(lambda v: f"R$ {fmt_br(v/1e3, 1)} bi"),
        textposition="outside",
        textfont=dict(size=11, color=C["text_dim"]),
        cliponaxis=False,
    ))
    x_max = float((df_comp["valor_milhoes"] / 1e3).max())
    fig.update_layout(
        xaxis_title="R$ bilhões",
        xaxis=dict(range=[0, x_max * 1.6]),
        showlegend=False,
    )
    plotly_dark(fig, height=280, margin=dict(l=140, r=20, t=10, b=30))
    st.plotly_chart(fig, width="stretch", key="est_composicao")


# ── Montagem da página ───────────────────────────────────────────────────────

if df_est.empty:
    st.info(
        "Dados de estados não encontrados. "
        "Execute `python pipelines/estados/load.py` para baixar."
    )
else:
    ratio_df = calcular_ratio_investimento_estados(df_est)

    if ratio_df.empty:
        st.warning("Não foi possível calcular ratios de investimento.")
    else:
        ano_max, per_max = _render_coropletico(ratio_df)

        st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

        col_rank, col_detail = st.columns([1, 2])

        with col_rank:
            _section_title("Ranking: proporção de investimento")
            df_rank = ratio_df[["uf", "ente", "invest_ratio", "invest_milhoes", "total_milhoes"]].copy()
            df_rank.columns = ["UF", "Estado", "Invest. %", "Invest. (R$ mi)", "Total (R$ mi)"]
            df_rank["Invest. %"] = df_rank["Invest. %"].apply(lambda v: f"{fmt_br(v, 1)}%")
            df_rank["Invest. (R$ mi)"] = df_rank["Invest. (R$ mi)"].apply(lambda v: f"{fmt_br(v, 0)}")
            df_rank["Total (R$ mi)"] = df_rank["Total (R$ mi)"].apply(lambda v: f"{fmt_br(v, 0)}")
            st.dataframe(df_rank, hide_index=True, width="stretch", height=450)

        with col_detail:
            _section_title("Detalhe por estado")
            ufs    = sorted(df_est["uf"].unique())
            uf_sel = st.selectbox("Estado", ufs, key="est_uf_sel",
                                  index=ufs.index("SP") if "SP" in ufs else 0)

            df_uf    = df_est[df_est["uf"] == uf_sel]
            nome_est = df_uf["ente"].iloc[0] if not df_uf.empty else uf_sel
            cod_ibge = int(df_uf["cod_ibge"].iloc[0]) if not df_uf.empty else 0

            contas_disp  = sorted(df_uf["cod_conta"].unique())
            _default     = "DespesasExcetoIntraOrcamentarias"
            _conta_idx   = contas_disp.index(_default) if _default in contas_disp else 0
            conta_sel    = st.selectbox(
                "Conta",
                contas_disp,
                index=_conta_idx,
                format_func=lambda c: CONTAS_NOME.get(c, c),
                key="est_conta_sel",
            )
            col_viz = st.selectbox(
                "Visualizar",
                [COLUNA_PADRAO, COLUNA_BIMESTRAL],
                key="est_coluna_sel",
            )

            serie = calcular_serie_estado(df_est, cod_ibge, conta_sel, col_viz)

            if not serie.empty:
                _idx = list(range(len(serie)))
                fig_est = go.Figure(go.Bar(
                    x=_idx,
                    y=serie["valor_milhoes"].tolist(),
                    marker_color=C["primary"],
                    marker_line_width=0,
                    customdata=serie["label"].tolist(),
                    hovertemplate="<b>%{customdata}</b><br>R$ %{y:,.1f} mi<extra></extra>",
                ))
                fig_est.update_layout(
                    title=f"{nome_est} — {CONTAS_NOME.get(conta_sel, conta_sel)}",
                    xaxis_title="Bimestre",
                    yaxis_title="R$ milhões",
                )
                fig_est.update_xaxes(
                    tickvals=_idx,
                    ticktext=serie["label"].tolist(),
                    tickangle=45,
                )
                plotly_dark(fig_est, height=300, margin=dict(l=10, r=10, t=40, b=60))
                st.plotly_chart(fig_est, width="stretch", key="est_serie")

            _render_composicao(df_uf, nome_est, ano_max, per_max)

render_footer("SICONFI · Tesouro Nacional · Dados bimestrais RREO")
