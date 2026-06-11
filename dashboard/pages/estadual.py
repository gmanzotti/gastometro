"""
dashboard/pages/estadual.py  —  Gastômetro · Estadual
"""

import sys
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from components.theme import (
    C, inject_css, render_navbar, render_footer,
    fmt_bi, fmt_br, plotly_dark,
    carregar_dados, carregar_geojson_estados,
    calcular_ratio_investimento_estados,
    calcular_scatter_correntes_invest,
    calcular_categorias_rolling,
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
cont   = dados.get("contador", {})

COLUNA_PADRAO = "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)"

CATS_COMP = [
    ("PessoalEEncargosSociais",  "Pessoal e Encargos",    C["corrente"]),
    ("JurosEEncargosDaDivida",   "Juros da Dívida",       "#F97316"),
    ("OutrasDespesasCorrentes",  "Outras Correntes",      "#FB923C"),
    ("Investimentos",            "Investimentos",         C["investimento"]),
    ("InversoesFinanceiras",     "Inversões Financeiras", "#16A34A"),
    ("AmortizacaoDaDivida",      "Amort. Dívida",         C["warning"]),
]
COR_MAP = {c: cor for c, _, cor in CATS_COMP}


def _section_title(txt: str):
    st.markdown(
        f"<div style='font-size:13px;font-weight:600;color:{C['text']};"
        f"margin-bottom:8px;'>{txt}</div>",
        unsafe_allow_html=True,
    )


def _render_contador_animado(cont_data: dict, label: str):
    acc      = cont_data.get("acc_base_rs", 0)
    taxa     = cont_data.get("taxa_por_segundo_rs", 0)
    start_ms = cont_data.get("start_ms", 0)
    ult      = cont_data.get("ultimo_dado", "—")
    ref      = cont_data.get("bim_referencia_fim", cont_data.get("bim_referencia", "—"))
    prev     = cont_data.get("previsao_total_rs", 0)
    # Valor projetado ao final do período do contador (bim_referencia_fim)
    meta_rs  = acc + prev
    meta_str = fmt_bi(meta_rs / 1e6)

    # Pré-computa o valor atual em Python para evitar flash de "R$ —" no re-render
    elapsed_s   = max(0.0, time.time() - start_ms / 1000)
    initial_rs  = acc + elapsed_s * taxa
    initial_str = fmt_br(initial_rs, 2)

    st.html(f"""
<div style="text-align:center;padding:32px 40px;
            background:linear-gradient(160deg,{C['bg']} 0%,{C['bg3']} 100%);
            border:1px solid {C['border']};border-radius:16px;margin-bottom:4px;">
  <div style="font-size:10px;letter-spacing:3px;color:{C['accent']};font-weight:700;
              text-transform:uppercase;margin-bottom:12px;">
    Gastos Acumulados — {label}
  </div>
  <div id="cnt-est-main" style="
    font-size:48px;font-weight:800;color:{C['text']};
    font-family:'Courier New',monospace;letter-spacing:-2px;line-height:1;
    margin-bottom:14px;">R$&nbsp;{initial_str}</div>
  <div style="font-size:11px;color:{C['text_muted']};line-height:1.8;">
    Despesas liquidadas acumuladas no ano, projetadas ao segundo<br/>
    Último dado: <b style="color:{C['text_dim']}">{ult}</b>
    &nbsp;·&nbsp;
    Projetado até {ref}: <b style="color:{C['accent']}">{meta_str}</b>
  </div>
</div>
<script>
(function() {{
  if (window._cntEstInterval) {{ clearInterval(window._cntEstInterval); window._cntEstInterval = null; }}
  const acc = {acc:.2f}, taxa = {taxa:.4f}, start = {start_ms};
  function fmtBr(n) {{
    return n.toLocaleString('pt-BR', {{minimumFractionDigits:2, maximumFractionDigits:2}});
  }}
  function update() {{
    const elapsed = Math.max(0, (Date.now() - start) / 1000);
    const el = document.getElementById('cnt-est-main');
    if (el) el.innerHTML = 'R$&nbsp;' + fmtBr(acc + elapsed * taxa);
  }}
  window._cntEstInterval = setInterval(update, 100);
  update();
}})();
</script>
""", unsafe_allow_javascript=True)


def _render_coropletico(ratio_df: pd.DataFrame, uf_sel: str) -> tuple[int, int]:
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
            mapbox_zoom=2.5,
            mapbox_center={"lat": -14.5, "lon": -51.5},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=460,
            margin=dict(l=0, r=0, t=0, b=0),
        )
        st.plotly_chart(fig_map, width="stretch", key="est_mapa")
    else:
        st.info("GeoJSON não disponível.")

    return ano_max, per_max


def _render_invest_correntes(scatter_df: pd.DataFrame, uf_sel: str):
    """Barra invest vs correntes para o ente selecionado (estilo termômetro da aba Geral)."""
    if scatter_df.empty:
        st.info("Sem dados suficientes.")
        return

    if uf_sel == "Consolidado":
        inv_mi  = float(scatter_df["invest_milhoes"].sum())
        cor_mi  = float(scatter_df["correntes_milhoes"].sum())
        tot_mi  = float(scatter_df["total_milhoes"].sum())
        nome    = "Todos os Estados (consolidado)"
    else:
        row = scatter_df[scatter_df["uf"] == uf_sel]
        if row.empty:
            st.info(f"Sem dados para {uf_sel}.")
            return
        inv_mi  = float(row["invest_milhoes"].iloc[0])
        cor_mi  = float(row["correntes_milhoes"].iloc[0])
        tot_mi  = float(row["total_milhoes"].iloc[0])
        nome    = str(row["ente"].iloc[0])

    inv_pct = round(inv_mi / tot_mi * 100, 1) if tot_mi > 0 else 0
    cor_pct = round(cor_mi / tot_mi * 100, 1) if tot_mi > 0 else 0

    st.html(f"""
<div style="background:{C['bg2']};border:1px solid {C['border']};border-radius:12px;
            padding:24px 28px;margin-bottom:12px;">
  <div style="font-size:11px;color:{C['text_muted']};margin-bottom:14px;
              text-transform:uppercase;letter-spacing:1.5px;font-weight:600;">
    {nome} · rolling 12 meses
  </div>
  <div style="display:grid;grid-template-columns:80px 1fr 80px;
              align-items:center;gap:16px;">
    <div style="text-align:right;">
      <div style="font-size:28px;font-weight:800;color:{C['investimento']};
                  font-family:'Courier New',monospace;line-height:1;">
        {fmt_br(inv_pct, 1)}%
      </div>
      <div style="font-size:10px;color:{C['text_muted']};margin-top:3px;">investimento</div>
      <div style="font-size:11px;color:{C['text_dim']};margin-top:4px;">{fmt_bi(inv_mi)}</div>
    </div>
    <div style="height:44px;border-radius:8px;overflow:hidden;display:flex;
                border:1px solid {C['border']};">
      <div style="width:{inv_pct:.2f}%;background:linear-gradient(90deg,#14532d,{C['investimento']});
                  min-width:4px;"></div>
      <div style="flex:1;background:linear-gradient(90deg,{C['corrente']},#7f1d1d);"></div>
    </div>
    <div>
      <div style="font-size:28px;font-weight:800;color:{C['corrente']};
                  font-family:'Courier New',monospace;line-height:1;">
        {fmt_br(cor_pct, 1)}%
      </div>
      <div style="font-size:10px;color:{C['text_muted']};margin-top:3px;">correntes</div>
      <div style="font-size:11px;color:{C['text_dim']};margin-top:4px;">{fmt_bi(cor_mi)}</div>
    </div>
  </div>
</div>
""")


def _render_tabela_comparativa(scatter_df: pd.DataFrame):
    """Tabela comparativa de todos os estados."""
    if scatter_df.empty:
        return
    _section_title("Comparativo — todos os estados")
    df_tab = scatter_df[["uf", "ente", "invest_ratio", "correntes_milhoes", "invest_milhoes", "total_milhoes"]].copy()
    df_tab.columns = ["UF", "Estado", "Invest. %", "Correntes (R$ mi)", "Invest. (R$ mi)", "Total (R$ mi)"]
    df_tab["Invest. %"]         = df_tab["Invest. %"].apply(lambda v: f"{fmt_br(v, 1)}%")
    df_tab["Correntes (R$ mi)"] = df_tab["Correntes (R$ mi)"].apply(lambda v: f"{fmt_br(v, 0)}")
    df_tab["Invest. (R$ mi)"]   = df_tab["Invest. (R$ mi)"].apply(lambda v: f"{fmt_br(v, 0)}")
    df_tab["Total (R$ mi)"]     = df_tab["Total (R$ mi)"].apply(lambda v: f"{fmt_br(v, 0)}")
    st.dataframe(df_tab, hide_index=True, width="stretch", height=380)


def _render_categorias(df: pd.DataFrame, cod_ibge_list, ratio_rolling: float, titulo: str):
    cats_df = calcular_categorias_rolling(df, cod_ibge_list, COLUNA_PADRAO, ratio_rolling)
    if cats_df.empty:
        st.info("Sem dados de categorias.")
        return

    ano = int(cats_df["ano"].iloc[0])
    bim = int(cats_df["periodo"].iloc[0])
    _section_title(f"Composição projetada — {titulo} · {ano} (acumulado B{bim} × fator sazonal)")

    cats_df["cor"] = cats_df["cod_conta"].map(COR_MAP).fillna(C["primary"])

    fig = go.Figure(go.Bar(
        x=cats_df["valor_projetado"] / 1e3,
        y=cats_df["nome"],
        orientation="h",
        marker_color=cats_df["cor"].tolist(),
        marker_line_width=0,
        text=cats_df["valor_projetado"].apply(fmt_bi),
        textposition="outside",
        textfont=dict(size=12, color=C["text_dim"]),
        cliponaxis=False,
    ))
    x_max = float((cats_df["valor_projetado"] / 1e3).max())
    fig.update_layout(
        xaxis_title="R$ bilhões (projeção anual)",
        xaxis=dict(range=[0, x_max * 1.7]),
        showlegend=False,
    )
    plotly_dark(fig, height=520, margin=dict(l=160, r=20, t=10, b=40))
    st.plotly_chart(fig, width="stretch", key="est_categorias")


# ── Montagem ─────────────────────────────────────────────────────────────────

if df_est.empty:
    st.info("Execute `python pipelines/estados/load_prototipo.py` para carregar os dados.")
    render_footer("SICONFI · Tesouro Nacional · Dados bimestrais RREO")
    st.stop()

ratio_df   = calcular_ratio_investimento_estados(df_est)
scatter_df = calcular_scatter_correntes_invest(df_est)

# ── Elemento 1: Seletor ───────────────────────────────────────────────────────
ufs_disp = sorted(df_est["uf"].unique().tolist())
uf_sel   = st.selectbox("Estado", ["Consolidado"] + ufs_disp, index=0, key="est_uf_sel")

# ── Contexto da seleção ───────────────────────────────────────────────────────
cont_est = cont.get("estados", {})
if uf_sel == "Consolidado":
    cont_data     = cont_est.get("_consolidado", {})
    cod_ibge_list = None
    label_cnt     = "Todos os Estados"
else:
    cont_data = cont_est.get(uf_sel, {})
    row_uf    = df_est[df_est["uf"] == uf_sel]
    cod_ibge_list = [int(row_uf["cod_ibge"].iloc[0])] if not row_uf.empty else None
    label_cnt     = row_uf["ente"].iloc[0] if not row_uf.empty else uf_sel

ratio_rolling = cont_data.get("ratio_rolling", 1.0)

# ── Elemento 2: Contador animado ─────────────────────────────────────────────
_render_contador_animado(cont_data, label_cnt)

# ── Elemento 3: Coroplético ───────────────────────────────────────────────────
st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)
ano_max, per_max = (
    _render_coropletico(ratio_df, uf_sel)
    if not ratio_df.empty else (2026, 2)
)

# ── Elementos 4 + 5 lado a lado ──────────────────────────────────────────────
st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)
col4, col5 = st.columns(2)

with col4:
    _section_title("Investimento vs Gastos Correntes")
    _render_invest_correntes(scatter_df, uf_sel)
    _render_tabela_comparativa(scatter_df)

with col5:
    _render_categorias(df_est, cod_ibge_list, ratio_rolling, label_cnt)

render_footer("SICONFI · Tesouro Nacional · Dados bimestrais RREO")
