"""
dashboard/pages/municipal.py  —  Gastômetro · Municipal

Choroplético: todos os 5.570 municípios com dados sintéticos (protótipo).
Ranking + detalhe: capitais estaduais com dados reais extraídos do SICONFI.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from components.theme import (
    C, inject_css, render_navbar, render_footer,
    fmt_br, plotly_dark,
    carregar_dados, carregar_geojson_municipios,
    calcular_ratio_investimento_municipios, calcular_serie_estado,
)

st.set_page_config(
    page_title="Municipal · Gastômetro FIESP",
    page_icon="🏙️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_css()
render_navbar("municipal")

dados  = carregar_dados()
df_mun = dados.get("municipios", pd.DataFrame())

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


def _gerar_dados_sinteticos(geojson: dict, ratio_reais: pd.DataFrame) -> pd.DataFrame:
    """
    Gera invest_ratio sintético para todos os municípios do GeoJSON.
    Valores são distribuídos em torno da média das capitais por estado,
    com ruído gaussiano — apenas para demonstração visual.
    """
    rng = np.random.default_rng(42)

    # Média por UF a partir das capitais reais (fallback = 6.0%)
    if not ratio_reais.empty:
        media_uf = ratio_reais.groupby("uf")["invest_ratio"].mean().to_dict()
        media_geral = ratio_reais["invest_ratio"].mean()
    else:
        media_uf   = {}
        media_geral = 6.0

    # Mapeia cod_ibge (7 dígitos) → UF via prefixo de 2 dígitos do código IBGE estadual
    # Ex: 3550308 (São Paulo-SP) → prefixo "35" → UF "SP"
    uf_por_prefixo2 = {
        "12": "AC", "27": "AL", "13": "AM", "16": "AP", "29": "BA",
        "23": "CE", "53": "DF", "32": "ES", "52": "GO", "21": "MA",
        "51": "MT", "50": "MS", "31": "MG", "15": "PA", "25": "PB",
        "41": "PR", "26": "PE", "22": "PI", "33": "RJ", "24": "RN",
        "43": "RS", "11": "RO", "14": "RR", "42": "SC", "35": "SP",
        "28": "SE", "17": "TO",
    }

    rows = []
    for feat in geojson.get("features", []):
        cod = feat.get("properties", {}).get("codarea", "")
        if not cod:
            continue
        prefixo = str(cod)[:2]
        uf = uf_por_prefixo2.get(prefixo, "")
        media = media_uf.get(uf, media_geral)
        # Ruído gaussiano com desvio-padrão de 2 pontos percentuais
        ratio = float(np.clip(rng.normal(media, 2.0), 0.5, 30.0))
        rows.append({"cod_str": str(cod), "invest_ratio": round(ratio, 2)})

    return pd.DataFrame(rows)


def _render_coropletico_municipal(ratio_reais: pd.DataFrame) -> tuple[int, int]:
    """Choroplético com dados sintéticos para todos os municípios."""
    if not ratio_reais.empty:
        ano_max = int(ratio_reais["ano"].iloc[0])
        per_max = int(ratio_reais["periodo"].iloc[0])
    else:
        ano_max, per_max = 2026, 1

    _section_title(
        f"Proporção de Investimento por Município — {ano_max} B{per_max} "
        f"<span style='font-size:11px;font-weight:400;color:{C['warning']};'>"
        f"⚠ dados sintéticos — protótipo visual</span>"
    )
    st.caption(
        "Coroplético demonstrativo: os valores municipais são gerados artificialmente "
        "com base nas médias estaduais das capitais. "
        "O painel de detalhe abaixo usa dados reais das capitais estaduais."
    )

    geojson = carregar_geojson_municipios()

    if geojson is None:
        st.warning(
            "Malha municipal não disponível. "
            "Conecte à internet para baixar automaticamente do IBGE (~15 MB)."
        )
        return ano_max, per_max

    df_sint = _gerar_dados_sinteticos(geojson, ratio_reais)

    fig_map = go.Figure(go.Choroplethmapbox(
        geojson=geojson,
        featureidkey="properties.codarea",
        locations=df_sint["cod_str"],
        z=df_sint["invest_ratio"],
        colorscale="RdYlGn",
        zmin=0,
        zmax=20,
        colorbar=dict(
            title=dict(text="%", font=dict(color=C["text_dim"], size=11)),
            thickness=14,
            bgcolor="rgba(13,27,46,0.85)",
            tickfont=dict(color=C["text_dim"], size=10),
        ),
        hovertemplate="Município %{location}<br>Invest.: <b>%{z:.1f}%</b><extra></extra>",
        marker_line_color="rgba(0,0,0,0)",
        marker_line_width=0,
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
    st.plotly_chart(fig_map, width="stretch", key="mun_mapa")

    return ano_max, per_max


def _render_composicao(df_mun_sel: pd.DataFrame, nome: str, ano: int, bim: int):
    st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)
    _section_title(f"Composição do gasto — {nome} · {ano} B{bim}")

    contas_set = {c for c, _, _ in CATS_COMP}
    df_comp = df_mun_sel[
        (df_mun_sel["ano"]     == ano) &
        (df_mun_sel["periodo"] == bim) &
        (df_mun_sel["coluna"]  == COLUNA_PADRAO) &
        (df_mun_sel["cod_conta"].isin(contas_set))
    ].copy()

    if df_comp.empty:
        st.info("Sem dados de composição para o período.")
        return

    df_comp = df_comp.groupby("cod_conta")["valor_milhoes"].sum().reset_index()
    df_comp = df_comp.sort_values("valor_milhoes", ascending=True)

    cor_map  = {c: cor for c, _, cor in CATS_COMP}
    nome_map = {c: n   for c, n, _ in CATS_COMP}
    df_comp["nome"] = df_comp["cod_conta"].map(nome_map)
    df_comp["cor"]  = df_comp["cod_conta"].map(cor_map).fillna(C["primary"])

    fig = go.Figure(go.Bar(
        x=df_comp["valor_milhoes"],
        y=df_comp["nome"],
        orientation="h",
        marker_color=df_comp["cor"].tolist(),
        marker_line_width=0,
        text=df_comp["valor_milhoes"].apply(lambda v: f"R$ {fmt_br(v, 1)} mi"),
        textposition="outside",
        textfont=dict(size=11, color=C["text_dim"]),
        cliponaxis=False,
    ))
    x_max = float(df_comp["valor_milhoes"].max())
    fig.update_layout(
        xaxis_title="R$ milhões",
        xaxis=dict(range=[0, x_max * 1.6]),
        showlegend=False,
    )
    plotly_dark(fig, height=280, margin=dict(l=140, r=20, t=10, b=30))
    st.plotly_chart(fig, width="stretch", key="mun_composicao")


# ── Montagem da página ───────────────────────────────────────────────────────

ratio_reais = calcular_ratio_investimento_municipios(df_mun) if not df_mun.empty else pd.DataFrame()

ano_max, per_max = _render_coropletico_municipal(ratio_reais)

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

if df_mun.empty:
    st.info(
        "Dados de capitais não encontrados. "
        "Execute `python pipelines/municipios/load.py` para baixar."
    )
else:
    col_rank, col_detail = st.columns([1, 2])

    with col_rank:
        _section_title("Ranking: capitais estaduais")
        if not ratio_reais.empty:
            df_rank = ratio_reais[["uf", "ente", "invest_ratio", "invest_milhoes", "total_milhoes"]].copy()
            df_rank.columns = ["UF", "Capital", "Invest. %", "Invest. (R$ mi)", "Total (R$ mi)"]
            df_rank["Invest. %"] = df_rank["Invest. %"].apply(lambda v: f"{fmt_br(v, 1)}%")
            df_rank["Invest. (R$ mi)"] = df_rank["Invest. (R$ mi)"].apply(lambda v: f"{fmt_br(v, 0)}")
            df_rank["Total (R$ mi)"] = df_rank["Total (R$ mi)"].apply(lambda v: f"{fmt_br(v, 0)}")
            st.dataframe(df_rank, hide_index=True, width="stretch", height=450)
        else:
            st.info("Aguardando cálculo dos ratios.")

    with col_detail:
        _section_title("Detalhe por capital")

        ufs_mun  = sorted(df_mun["uf"].unique())
        uf_sel   = st.selectbox("UF", ufs_mun, key="mun_uf_sel",
                                index=ufs_mun.index("SP") if "SP" in ufs_mun else 0)

        df_uf_mun = df_mun[df_mun["uf"] == uf_sel]
        muns_disp = (
            df_uf_mun[["cod_ibge", "ente"]]
            .drop_duplicates()
            .sort_values("ente")
        )

        mun_opts = dict(zip(muns_disp["ente"], muns_disp["cod_ibge"]))
        mun_nome = st.selectbox("Capital", list(mun_opts.keys()), key="mun_nome_sel")
        mun_cod  = mun_opts[mun_nome]

        df_sel = df_mun[df_mun["cod_ibge"] == mun_cod]

        contas_disp  = sorted(df_sel["cod_conta"].unique())
        _default     = "DespesasExcetoIntraOrcamentarias"
        _conta_idx   = contas_disp.index(_default) if _default in contas_disp else 0
        conta_sel    = st.selectbox(
            "Conta",
            contas_disp,
            index=_conta_idx,
            format_func=lambda c: CONTAS_NOME.get(c, c),
            key="mun_conta_sel",
        )
        col_viz = st.selectbox(
            "Visualizar",
            [COLUNA_PADRAO, COLUNA_BIMESTRAL],
            key="mun_coluna_sel",
        )

        serie = calcular_serie_estado(df_mun, mun_cod, conta_sel, col_viz)

        if not serie.empty:
            _idx = list(range(len(serie)))
            fig_mun = go.Figure(go.Bar(
                x=_idx,
                y=serie["valor_milhoes"].tolist(),
                marker_color=C["accent"],
                marker_line_width=0,
                customdata=serie["label"].tolist(),
                hovertemplate="<b>%{customdata}</b><br>R$ %{y:,.1f} mi<extra></extra>",
            ))
            fig_mun.update_layout(
                title=f"{mun_nome} ({uf_sel}) — {CONTAS_NOME.get(conta_sel, conta_sel)}",
                xaxis_title="Bimestre",
                yaxis_title="R$ milhões",
            )
            fig_mun.update_xaxes(
                tickvals=_idx,
                ticktext=serie["label"].tolist(),
                tickangle=45,
            )
            plotly_dark(fig_mun, height=300, margin=dict(l=10, r=10, t=40, b=60))
            st.plotly_chart(fig_mun, width="stretch", key="mun_serie")

        _render_composicao(df_sel, mun_nome, ano_max, per_max)

render_footer("SICONFI · Tesouro Nacional · Capitais estaduais · RREO bimestral")
