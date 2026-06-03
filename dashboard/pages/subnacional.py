"""
dashboard/pages/subnacional.py  —  Gastômetro · Subnacional

Seções:
  Aba Estados:
    - Mapa coroplético: proporção de investimento por estado
    - Seletor de estado com série temporal e composição
    - Comparação com a média nacional
  Aba Municípios:
    - Seletor UF → município com série temporal e composição
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from components.theme import (
    C, inject_css, render_navbar, render_footer,
    fmt_bi, fmt_br, plotly_dark,
    carregar_dados, carregar_geojson_estados,
    calcular_ratio_investimento_estados, calcular_serie_estado,
)

st.set_page_config(
    page_title="Subnacional · Gastômetro FIESP",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_css()
render_navbar("subnacional")

dados       = carregar_dados()
df_est      = dados.get("estados",   pd.DataFrame())
df_mun      = dados.get("municipios", pd.DataFrame())

CONTAS_NOME = {
    "DespesasExcetoIntraOrcamentarias": "Despesa Total",
    "DespesasCorrentes":                "Desp. Correntes",
    "DespesasDeCapital":                "Desp. de Capital",
    "Investimentos":                    "Investimentos",
    "InversoesFinanceiras":             "Inversões Financeiras",
    "AmortizacaoDaDivida":              "Amort. Dívida",
}

COLUNA_PADRAO  = "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)"
COLUNA_BIMESTRAL = "DESPESAS LIQUIDADAS NO BIMESTRE"


def _section_title(txt: str):
    st.markdown(
        f"<div style='font-size:13px;font-weight:600;color:{C['text']};"
        f"margin-bottom:8px;'>{txt}</div>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════
# ABA ESTADOS
# ══════════════════════════════════════════════════════════════════════════

def _aba_estados():
    if df_est.empty:
        st.info(
            "Dados de estados não encontrados. "
            "Execute `python pipelines/estados/load.py` para baixar."
        )
        return

    ratio_df = calcular_ratio_investimento_estados(df_est)
    # Usa o período que ratio_df calculou (maior cobertura), não o global
    ano_max = int(ratio_df["ano"].iloc[0]) if not ratio_df.empty else df_est["ano"].max()
    per_max = int(ratio_df["periodo"].iloc[0]) if not ratio_df.empty else 1

    # ── Mapa coroplético ──────────────────────────────────────────────────
    _section_title(f"Proporção de Investimento por Estado — {ano_max} B{per_max}")
    st.caption(
        "% de investimento (Investimentos + Inversões Financeiras) sobre a "
        "Despesa Total Liquidada até o bimestre mais recente."
    )

    geojson = carregar_geojson_estados()

    if geojson and not ratio_df.empty:
        ratio_df_map = ratio_df.copy()
        ratio_df_map["cod_str"] = ratio_df_map["cod_ibge"].astype(str)

        fig_map = go.Figure(go.Choroplethmapbox(
            geojson=geojson,
            featureidkey="properties.codarea",
            locations=ratio_df_map["cod_str"],
            z=ratio_df_map["invest_ratio"],
            colorscale="RdYlGn",
            zmin=0,
            zmax=ratio_df_map["invest_ratio"].max() * 1.1,
            colorbar=dict(
                title=dict(text="%", font=dict(color=C["text_dim"], size=11)),
                thickness=14,
                bgcolor="rgba(13,27,46,0.85)",
                tickfont=dict(color=C["text_dim"], size=10),
            ),
            text=ratio_df_map["uf"],
            customdata=ratio_df_map[["ente", "invest_ratio", "invest_milhoes", "total_milhoes"]].values,
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
        st.plotly_chart(fig_map, width='stretch', key="mapa_estados")
    elif not ratio_df.empty:
        st.info("GeoJSON não disponível. Exibindo tabela.")
        st.dataframe(
            ratio_df[["uf", "ente", "invest_ratio", "invest_milhoes", "total_milhoes"]]
            .rename(columns={
                "uf": "UF", "ente": "Estado",
                "invest_ratio": "Invest. %",
                "invest_milhoes": "Invest. (R$ mi)",
                "total_milhoes": "Total (R$ mi)",
            }),
            hide_index=True, width='stretch',
        )

    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

    # ── Ranking ────────────────────────────────────────────────────────────
    if not ratio_df.empty:
        col_rank, col_detail = st.columns([1, 2])

        with col_rank:
            _section_title("Ranking: proporção de investimento")
            df_rank = ratio_df[["uf", "ente", "invest_ratio"]].copy()
            df_rank.columns = ["UF", "Estado", "Invest. %"]
            df_rank["Invest. %"] = df_rank["Invest. %"].apply(
                lambda v: f"{fmt_br(v, 1)}%"
            )
            st.dataframe(df_rank, hide_index=True, width='stretch', height=420)

        with col_detail:
            _section_title("Detalhe por estado")
            ufs      = sorted(df_est["uf"].unique())
            uf_sel   = st.selectbox("Estado", ufs, key="est_uf_sel",
                                    index=ufs.index("SP") if "SP" in ufs else 0)

            df_uf    = df_est[df_est["uf"] == uf_sel]
            nome_est = df_uf["ente"].iloc[0] if not df_uf.empty else uf_sel

            # Série temporal: investimento vs. despesas correntes
            contas_disp = sorted(df_uf["cod_conta"].unique())
            _default_conta = "DespesasExcetoIntraOrcamentarias"
            _conta_idx = contas_disp.index(_default_conta) if _default_conta in contas_disp else 0
            conta_sel   = st.selectbox(
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

            serie = calcular_serie_estado(df_est, int(df_uf["cod_ibge"].iloc[0]),
                                          conta_sel, col_viz)

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
                plotly_dark(fig_est, height=320, margin=dict(l=10, r=10, t=40, b=60))
                st.plotly_chart(fig_est, width='stretch', key="est_serie")

                # Investimento vs. corrente para o estado selecionado
                _render_composicao_estado(df_uf, nome_est, ano_max, per_max)


def _render_composicao_estado(df_uf: pd.DataFrame, nome: str, ano: int, bim: int):
    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)
    _section_title(f"Composição — {nome} · {ano} B{bim}")

    df_comp = df_uf[
        (df_uf["ano"]    == ano) &
        (df_uf["periodo"] == bim) &
        (df_uf["coluna"] == COLUNA_PADRAO) &
        (df_uf["cod_conta"].isin(CONTAS_NOME.keys()))
    ].copy()

    if df_comp.empty:
        st.info("Sem dados de composição para o período.")
        return

    df_comp = df_comp.groupby("cod_conta")["valor_milhoes"].sum().reset_index()
    df_comp["nome"]  = df_comp["cod_conta"].map(CONTAS_NOME)
    df_comp = df_comp[
        df_comp["cod_conta"].isin({
            "DespesasCorrentes","Investimentos","InversoesFinanceiras","AmortizacaoDaDivida"
        })
    ].sort_values("valor_milhoes", ascending=True)

    colors = {
        "DespesasCorrentes":    C["corrente"],
        "Investimentos":        C["investimento"],
        "InversoesFinanceiras": "#16A34A",
        "AmortizacaoDaDivida":  C["warning"],
    }
    df_comp["cor"] = df_comp["cod_conta"].map(colors).fillna(C["primary"])

    fig = go.Figure(go.Bar(
        x=df_comp["valor_milhoes"] / 1e3,
        y=df_comp["nome"],
        orientation="h",
        marker_color=df_comp["cor"],
        marker_line_width=0,
        text=df_comp["valor_milhoes"].apply(lambda v: f"R$ {fmt_br(v/1e3, 1)} bi"),
        textposition="outside",
        textfont=dict(size=11, color=C["text_dim"]),
        cliponaxis=False,
    ))
    x_max = float((df_comp["valor_milhoes"] / 1e3).max())
    fig.update_layout(
        xaxis_title="R$ bilhões",
        xaxis=dict(range=[0, x_max * 1.5]),
        showlegend=False,
    )
    plotly_dark(fig, height=240, margin=dict(l=130, r=20, t=10, b=30))
    st.plotly_chart(fig, width='stretch', key="est_composicao")


# ══════════════════════════════════════════════════════════════════════════
# ABA MUNICÍPIOS
# ══════════════════════════════════════════════════════════════════════════

def _aba_municipios():
    st.markdown(
        f"""<div style="background:rgba(245,158,11,0.08);border-left:3px solid {C['warning']};
        border-radius:6px;padding:10px 16px;margin-bottom:16px;font-size:12px;color:{C['text_dim']};">
        <strong style="color:{C['warning']};">Protótipo</strong> — exibe apenas as
        <strong>26 capitais estaduais</strong>. O pipeline completo (~5.570 municípios)
        está em produção e levará alguns dias para concluir a extração histórica.
        Os valores aqui <em>não representam</em> o total municipal do Brasil.
        </div>""",
        unsafe_allow_html=True,
    )

    if df_mun.empty:
        st.info(
            "Dados de municípios não encontrados. "
            "Execute `python pipelines/municipios/load.py` (EXTRAIR_TODOS=True) para baixar."
        )
        return

    col_sel1, col_sel2 = st.columns([1, 2])

    with col_sel1:
        ufs_mun  = sorted(df_mun["uf"].unique())
        uf_mun   = st.selectbox("UF", ufs_mun, key="mun_uf_sel",
                                index=ufs_mun.index("SP") if "SP" in ufs_mun else 0)

    df_uf_mun = df_mun[df_mun["uf"] == uf_mun]
    muns_disp = (
        df_uf_mun[["cod_ibge", "ente"]]
        .drop_duplicates()
        .sort_values("ente")
    )

    with col_sel2:
        mun_opts  = dict(zip(muns_disp["ente"], muns_disp["cod_ibge"]))
        mun_nome  = st.selectbox("Município", list(mun_opts.keys()), key="mun_nome_sel")
        mun_cod   = mun_opts[mun_nome]

    df_mun_sel = df_mun[df_mun["cod_ibge"] == mun_cod]

    # Série temporal
    contas_mun = sorted(df_mun_sel["cod_conta"].unique())
    _default_mun = "DespesasExcetoIntraOrcamentarias"
    _mun_idx = contas_mun.index(_default_mun) if _default_mun in contas_mun else 0
    conta_mun  = st.selectbox(
        "Conta",
        contas_mun,
        index=_mun_idx,
        format_func=lambda c: CONTAS_NOME.get(c, c),
        key="mun_conta_sel",
    )

    df_serie_mun = (
        df_mun_sel[
            (df_mun_sel["cod_conta"] == conta_mun) &
            (df_mun_sel["coluna"]    == COLUNA_PADRAO)
        ]
        .groupby(["ano", "periodo"], as_index=False)["valor_milhoes"]
        .sum()
        .sort_values(["ano", "periodo"])
    )
    df_serie_mun["label"] = (
        df_serie_mun["ano"].astype(str) + "-B" + df_serie_mun["periodo"].astype(str)
    )

    if not df_serie_mun.empty:
        _section_title(f"{mun_nome} ({uf_mun}) — {CONTAS_NOME.get(conta_mun, conta_mun)}")
        _idx_mun = list(range(len(df_serie_mun)))
        fig_mun = go.Figure(go.Bar(
            x=_idx_mun,
            y=df_serie_mun["valor_milhoes"].tolist(),
            marker_color=C["accent"],
            marker_line_width=0,
            customdata=df_serie_mun["label"].tolist(),
            hovertemplate="<b>%{customdata}</b><br>R$ %{y:,.1f} mi<extra></extra>",
        ))
        fig_mun.update_layout(xaxis_title="Bimestre", yaxis_title="R$ milhões")
        fig_mun.update_xaxes(
            tickvals=_idx_mun,
            ticktext=df_serie_mun["label"].tolist(),
            tickangle=45,
        )
        plotly_dark(fig_mun, height=300, margin=dict(l=10, r=10, t=20, b=60))
        st.plotly_chart(fig_mun, width='stretch', key="mun_serie")

    # Composição do município no último bimestre disponível
    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)
    _section_title(f"Composição — {mun_nome}")

    ano_max_mun = df_mun_sel["ano"].max()
    bim_max_mun = df_mun_sel[df_mun_sel["ano"] == ano_max_mun]["periodo"].max()

    df_comp_mun = df_mun_sel[
        (df_mun_sel["ano"]     == ano_max_mun) &
        (df_mun_sel["periodo"] == bim_max_mun) &
        (df_mun_sel["coluna"]  == COLUNA_PADRAO) &
        (df_mun_sel["cod_conta"].isin({
            "DespesasCorrentes","Investimentos","InversoesFinanceiras","AmortizacaoDaDivida"
        }))
    ].copy()

    if df_comp_mun.empty:
        st.info("Sem dados de composição para o último bimestre disponível.")
        return

    df_comp_mun = df_comp_mun.groupby("cod_conta")["valor_milhoes"].sum().reset_index()
    df_comp_mun["nome"] = df_comp_mun["cod_conta"].map(CONTAS_NOME)
    df_comp_mun = df_comp_mun.sort_values("valor_milhoes", ascending=True)

    colors_mun = {
        "DespesasCorrentes":    C["corrente"],
        "Investimentos":        C["investimento"],
        "InversoesFinanceiras": "#16A34A",
        "AmortizacaoDaDivida":  C["warning"],
    }
    df_comp_mun["cor"] = df_comp_mun["cod_conta"].map(colors_mun).fillna(C["primary"])

    fig_comp_mun = go.Figure(go.Bar(
        x=df_comp_mun["valor_milhoes"],
        y=df_comp_mun["nome"],
        orientation="h",
        marker_color=df_comp_mun["cor"],
        marker_line_width=0,
        text=df_comp_mun["valor_milhoes"].apply(lambda v: f"R$ {fmt_br(v, 1)} mi"),
        textposition="outside",
        textfont=dict(size=11, color=C["text_dim"]),
        cliponaxis=False,
    ))
    x_max_mun = float(df_comp_mun["valor_milhoes"].max())
    fig_comp_mun.update_layout(
        xaxis_title="R$ milhões",
        xaxis=dict(range=[0, x_max_mun * 1.5]),
        showlegend=False,
    )
    plotly_dark(fig_comp_mun, height=220, margin=dict(l=130, r=20, t=10, b=30))
    st.plotly_chart(fig_comp_mun, width='stretch', key="mun_composicao")

    st.caption(
        f"Município: {mun_nome} (cod. IBGE {mun_cod}) · "
        f"Último bimestre disponível: {ano_max_mun} B{bim_max_mun}"
    )


# ── Montagem ──────────────────────────────────────────────────────────────

tab_estados, tab_municipios = st.tabs(["🗺️ Estados", "🏙️ Capitais (protótipo)"])

with tab_estados:
    _aba_estados()

with tab_municipios:
    _aba_municipios()

render_footer("SICONFI · Tesouro Nacional · Dados bimestrais RREO")
