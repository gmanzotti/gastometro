"""
dashboard/pages/municipal.py  —  Gastômetro · Municipal
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from components.theme import (
    C, inject_css, render_navbar, render_footer,
    fmt_bi, fmt_br, plotly_dark,
    carregar_dados, carregar_geojson_municipios,
    calcular_scatter_correntes_invest,
    calcular_categorias_projetadas,
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
cont   = dados.get("contador", {})

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
    meta_rs  = acc + prev
    meta_str = fmt_bi(meta_rs / 1e6)

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
  <div id="cnt-mun-main" style="
    font-size:48px;font-weight:800;color:{C['text']};
    font-family:'Courier New',monospace;letter-spacing:-2px;line-height:1;
    margin-bottom:14px;">R$&nbsp;{initial_str}</div>
  <div style="font-size:11px;color:{C['text_muted']};line-height:1.8;">
    Despesas empenhadas acumuladas no ano, projetadas ao segundo<br/>
    Último dado: <b style="color:{C['text_dim']}">{ult}</b>
    &nbsp;·&nbsp;
    Projetado até {ref}: <b style="color:{C['accent']}">{meta_str}</b>
  </div>
</div>
<script>
(function() {{
  if (window._cntMunInterval) {{ clearInterval(window._cntMunInterval); window._cntMunInterval = null; }}
  const acc = {acc:.2f}, taxa = {taxa:.4f}, start = {start_ms};
  function fmtBr(n) {{
    return n.toLocaleString('pt-BR', {{minimumFractionDigits:2, maximumFractionDigits:2}});
  }}
  function update() {{
    const elapsed = Math.max(0, (Date.now() - start) / 1000);
    const el = document.getElementById('cnt-mun-main');
    if (el) el.innerHTML = 'R$&nbsp;' + fmtBr(acc + elapsed * taxa);
  }}
  window._cntMunInterval = setInterval(update, 100);
  update();
}})();
</script>
""", unsafe_allow_javascript=True)


def _gerar_dados_sinteticos(geojson: dict, ratio_reais: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    if not ratio_reais.empty:
        media_uf    = ratio_reais.groupby("uf")["invest_ratio"].mean().to_dict()
        media_geral = ratio_reais["invest_ratio"].mean()
    else:
        media_uf, media_geral = {}, 6.0

    uf_por_prefixo = {
        "12":"AC","27":"AL","13":"AM","16":"AP","29":"BA","23":"CE","53":"DF",
        "32":"ES","52":"GO","21":"MA","51":"MT","50":"MS","31":"MG","15":"PA",
        "25":"PB","41":"PR","26":"PE","22":"PI","33":"RJ","24":"RN","43":"RS",
        "11":"RO","14":"RR","42":"SC","35":"SP","28":"SE","17":"TO",
    }
    rows = []
    for feat in geojson.get("features", []):
        cod = feat.get("properties", {}).get("codarea", "")
        if not cod:
            continue
        uf    = uf_por_prefixo.get(str(cod)[:2], "")
        media = media_uf.get(uf, media_geral)
        ratio = float(np.clip(rng.normal(media, 2.0), 0.5, 30.0))
        rows.append({"cod_str": str(cod), "invest_ratio": round(ratio, 2)})
    return pd.DataFrame(rows)


def _render_mapa_interativo(ratio_reais: pd.DataFrame):
    ano_max = int(ratio_reais["ano"].iloc[0]) if not ratio_reais.empty else 2026
    per_max = int(ratio_reais["periodo"].iloc[0]) if not ratio_reais.empty else 1

    _section_title(
        f"Proporção de Investimento por Município — {ano_max} B{per_max} "
        f"<span style='font-size:11px;font-weight:400;color:{C['warning']};'>"
        f"⚠ dados sintéticos — protótipo visual</span>"
    )
    st.caption(
        "Mapa interativo demonstrativo: valores gerados artificialmente com base nas médias "
        "das capitais estaduais. O painel de detalhe abaixo usa dados reais das capitais."
    )

    geojson = carregar_geojson_municipios()
    if geojson is None:
        st.warning("Malha municipal não disponível.")
        return

    df_sint = _gerar_dados_sinteticos(geojson, ratio_reais)
    fig_map = go.Figure(go.Choroplethmapbox(
        geojson=geojson,
        featureidkey="properties.codarea",
        locations=df_sint["cod_str"],
        z=df_sint["invest_ratio"],
        colorscale="RdYlGn",
        zmin=0, zmax=20,
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
        mapbox_zoom=2.5,
        mapbox_center={"lat": -14.5, "lon": -51.5},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=460,
        margin=dict(l=0, r=0, t=0, b=0),
    )
    st.plotly_chart(fig_map, width="stretch", key="mun_mapa")


def _render_invest_correntes(inv_mi: float, cor_mi: float, tot_mi: float, nome: str):
    """Barra invest vs correntes e obrigatórias (mesma base/projeção da composição)."""
    if tot_mi <= 0:
        st.info("Sem dados suficientes.")
        return

    inv_pct = round(inv_mi / tot_mi * 100, 1)
    cor_pct = round(cor_mi / tot_mi * 100, 1)

    st.html(f"""
<div style="background:{C['bg2']};border:1px solid {C['border']};border-radius:12px;
            padding:24px 28px;margin-bottom:12px;">
  <div style="font-size:11px;color:{C['text_muted']};margin-bottom:14px;
              text-transform:uppercase;letter-spacing:1.5px;font-weight:600;">
    {nome} · projeção até o bimestre corrente
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


def _render_tabela_comparativa(scatter_df: pd.DataFrame, uf_sel: str):
    """Top 5 maiores + 5 menores em invest%; se estado selecionado filtra por estado."""
    if scatter_df.empty:
        return

    if uf_sel == "Consolidado":
        top5 = scatter_df.nlargest(5,  "invest_ratio")
        bot5 = scatter_df.nsmallest(5, "invest_ratio")
        df_tab_base = pd.concat([top5, bot5]).drop_duplicates("cod_ibge")
        titulo = "Top 5 maiores / menores invest. — capitais"
    else:
        df_tab_base = scatter_df[scatter_df["uf"] == uf_sel]
        titulo = f"Capitais disponíveis — {uf_sel}"

    _section_title(titulo)
    df_tab = df_tab_base[["uf", "ente", "invest_ratio", "correntes_obrig_milhoes", "invest_milhoes", "total_milhoes"]].copy()
    df_tab.columns = ["UF", "Capital", "Invest. %", "Corr. e obrig. (R$ mi)", "Invest. (R$ mi)", "Total (R$ mi)"]
    df_tab["Invest. %"]              = df_tab["Invest. %"].apply(lambda v: f"{fmt_br(v, 1)}%")
    df_tab["Corr. e obrig. (R$ mi)"] = df_tab["Corr. e obrig. (R$ mi)"].apply(lambda v: f"{fmt_br(v, 0)}")
    df_tab["Invest. (R$ mi)"]   = df_tab["Invest. (R$ mi)"].apply(lambda v: f"{fmt_br(v, 0)}")
    df_tab["Total (R$ mi)"]     = df_tab["Total (R$ mi)"].apply(lambda v: f"{fmt_br(v, 0)}")
    st.dataframe(df_tab, hide_index=True, width="stretch", height=380)


def _render_categorias(cats_df: pd.DataFrame, titulo: str):
    if cats_df.empty:
        st.info("Sem dados de categorias.")
        return

    ano = int(cats_df["ano"].iloc[0])
    bim = int(cats_df["periodo"].iloc[0])
    _section_title(f"Composição projetada até B{bim}/{ano} — {titulo}")

    cats_df = cats_df.copy()
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
        xaxis_title="R$ bilhões (projeção até o bimestre corrente)",
        xaxis=dict(range=[0, x_max * 1.7]),
        showlegend=False,
    )
    plotly_dark(fig, height=520, margin=dict(l=160, r=20, t=10, b=40))
    st.plotly_chart(fig, width="stretch", key="mun_categorias")


# ── Montagem ─────────────────────────────────────────────────────────────────

if df_mun.empty:
    st.info("Execute `python pipelines/municipios/load_prototipo.py` para carregar os dados.")
    render_footer("SICONFI · Tesouro Nacional · Capitais estaduais · RREO bimestral")
    st.stop()

cont_mun = cont.get("municipios", {})
# {cod_ibge: bloco do contador} — o contador de municípios já é chaveado por cod_ibge.
blocos_por_cod = {int(k): v for k, v in cont_mun.items() if k != "_consolidado"}
scatter_df = calcular_scatter_correntes_invest(df_mun, blocos_por_cod)

# ── Elemento 1: Seletores ─────────────────────────────────────────────────────
ufs_disp = sorted(df_mun["uf"].unique().tolist())

col_s1, col_s2 = st.columns(2)
with col_s1:
    uf_sel = st.selectbox("Estado", ["Consolidado"] + ufs_disp, index=0, key="mun_uf_sel")

with col_s2:
    if uf_sel == "Consolidado":
        mun_cod = None
        st.selectbox("Município", ["Todas as capitais"], index=0, key="mun_mun_sel", disabled=True)
    else:
        df_uf    = df_mun[df_mun["uf"] == uf_sel][["cod_ibge", "ente"]].drop_duplicates()
        mun_opts = dict(zip(df_uf["ente"], df_uf["cod_ibge"]))
        mun_sel  = st.selectbox("Município", list(mun_opts.keys()), index=0, key="mun_mun_sel")
        mun_cod  = int(mun_opts[mun_sel])

# ── Contexto da seleção ───────────────────────────────────────────────────────
if mun_cod is None:
    cont_data     = cont_mun.get("_consolidado", {})
    cod_ibge_list = None
    label_cnt     = "Todas as Capitais"
else:
    cont_data     = cont_mun.get(str(mun_cod), {})
    cod_ibge_list = [mun_cod]
    label_cnt     = (
        df_mun[df_mun["cod_ibge"] == mun_cod]["ente"].iloc[0]
        if not df_mun[df_mun["cod_ibge"] == mun_cod].empty
        else str(mun_cod)
    )

# Composição projetada + barra derivada do mesmo cats_df (três elementos batem).
cats_df = calcular_categorias_projetadas(df_mun, cod_ibge_list, cont_data)
inv_mi  = float(cats_df[cats_df["cod_conta"].isin(["Investimentos", "InversoesFinanceiras"])]["valor_projetado"].sum()) if not cats_df.empty else 0.0
tot_mi  = float(cats_df["valor_projetado"].sum()) if not cats_df.empty else 0.0
cor_mi  = tot_mi - inv_mi

# ── Elemento 2: Contador animado ─────────────────────────────────────────────
_render_contador_animado(cont_data, label_cnt)

# ── Elemento 3: Mapa interativo ───────────────────────────────────────────────
st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)
_render_mapa_interativo(scatter_df)

# ── Elementos 4 + 5 lado a lado ──────────────────────────────────────────────
st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)
col4, col5 = st.columns(2)

with col4:
    _section_title("Investimento vs Despesas Correntes e Obrigatórias")
    _render_invest_correntes(inv_mi, cor_mi, tot_mi, label_cnt)
    _render_tabela_comparativa(scatter_df, uf_sel)

with col5:
    _render_categorias(cats_df, label_cnt)

render_footer("SICONFI · Tesouro Nacional · Capitais estaduais · RREO bimestral")
