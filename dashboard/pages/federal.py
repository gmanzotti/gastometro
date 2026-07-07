"""
dashboard/pages/federal.py  —  Gastômetro · Governo Federal

Layout fixo com 6 elementos (esboço de 11/06/2026):
  1. Contador animado em R$ (idêntico ao da aba Geral, esfera federal)
  2. Linhas: despesas obrigatórias × discricionárias × investimentos,
     em % da Despesa Total, de 2010 em diante
     + texto explicativo (investimento é subcategoria das discricionárias)
  3. Termômetro: despesas correntes vs investimento (idêntico ao da aba Geral)
  4. Barras horizontais: composição das despesas obrigatórias em %
  5. Barras horizontais: composição dos investimentos em %
  6. Resultado primário — acumulado 12 meses

Decisões metodológicas:
  - "Despesas obrigatórias" = 4.1 Previdência + 4.2 Pessoal + 4.3 Outras
    Obrigatórias + 4.4.1 Obrigatórias com Controle de Fluxo (saúde,
    educação, Bolsa Família). Mesma definição no Elemento 2 (linhas) e
    no Elemento 4 (composição).
  - "Investimentos" = série memo 'Investimento' da aba 1.2 da RTN, que é
    idêntica ao total da aba 1.3 (GND 4 Investimentos + GND 5 Inversões
    Financeiras + ajuste de ordem bancária). Disponível desde 1997.
  - Elemento 2 em % da Despesa Total e acumulado 12 meses: shares eliminam
    o ruído de inflação; a janela de 12m neutraliza a sazonalidade
    (dezembro concentra pagamentos). Início em 2010: antes de ~2008 a
    abertura de discricionárias da RTN é ~zero (mudança metodológica).
  - Somas de 12 meses SEMPRE em R$ constantes (constante_milhoes): somar
    R$ correntes subestima o total em moeda de hoje (~2,5% com IPCA ~5%).
    É o padrão da STN na tabela 1.2-B — os valores dos Elementos 4 e 5
    batem com ela ao centavo.
  - No Termômetro mantemos o termo "correntes": lá o contraste é
    investimento vs todo o resto da despesa (que inclui custeio
    discricionário), conceito distinto de "obrigatórias".
"""

import sys
import textwrap
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from components.theme import (
    C, MES_LABELS, _RSEL,
    inject_css, render_navbar, render_footer,
    fmt_bi, fmt_br, plotly_dark, rangeselector_buttons,
    carregar_dados,
    ratio_federal, linha_termometro, termometro_header,
    serie_12m_pct_total,
    composicao_obrigatorias_federal,
    composicao_investimentos_federal,
    serie_resultado_primario,
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

# Último mês disponível na RTN (referência para janelas de 12 meses)
ano_atual = int(df_rtn["ano"].max())
mes_atual = int(df_rtn[df_rtn["ano"] == ano_atual]["mes"].max())
base_label = meta.get("base_constante", "IPCA")


def _section_title(txt: str):
    st.markdown(
        f"<div style='font-size:13px;font-weight:600;color:{C['text']};"
        f"margin-bottom:8px;'>{txt}</div>",
        unsafe_allow_html=True,
    )


# Os cálculos das composições e séries (Elementos 2, 4, 5 e 6) vivem em
# components/theme.py desde 07/07/2026 — compartilhados com o pipeline
# pipelines/exportar_web.py (camada web do site da TI). Aqui fica só a renderização.


# ── Elemento 1: Contador federal animado ──────────────────────────────────

def _render_contador():
    fed = contador.get("federal", {})
    if not fed:
        st.info("Execute `python pipelines/contador_fiscal.py` para habilitar o contador.")
        return

    acc      = fed.get("acc_base_rs", 0)
    taxa     = fed.get("taxa_por_segundo_rs", 0)
    start_ms = fed.get("start_ms", 0)
    ult      = fed.get("ultimo_dado", "—")
    ref      = fed.get("mes_referencia_fim", fed.get("mes_referencia", "—"))
    prev     = fed.get("previsao_total_rs", 0)
    ano_ref  = str(ult)[:4] if ult != "—" else str(ano_atual)
    meta_str = fmt_bi((acc + prev) / 1e6)

    # Pré-computa o valor atual em Python para evitar flash de "R$ —" no re-render
    elapsed_s   = max(0.0, time.time() - start_ms / 1000)
    initial_str = fmt_br(acc + elapsed_s * taxa, 2)

    st.html(f"""
<div style="text-align:center;padding:44px 56px 40px;
            background:linear-gradient(160deg,{C['bg']} 0%,{C['bg3']} 100%);
            border:1px solid {C['border']};border-radius:20px;">
  <div style="font-size:10px;letter-spacing:3.5px;color:{C['accent']};font-weight:700;
              text-transform:uppercase;margin-bottom:16px;">
    Gastos Acumulados do Governo Federal — {ano_ref}
  </div>
  <div id="cnt-fed-main" style="
    font-size:66px;font-weight:800;color:{C['text']};
    font-family:'Courier New',monospace;letter-spacing:-2px;line-height:1;
    margin-bottom:16px;">R$&nbsp;{initial_str}</div>
  <div style="font-size:11px;color:{C['text_muted']};line-height:1.8;opacity:0.85;">
    Despesa Total do Tesouro Nacional acumulada no ano, projetada ao segundo<br/>
    Último dado: <b style="color:{C['text_dim']}">{ult}</b>
    &nbsp;·&nbsp;
    Projetado até {ref}: <b style="color:{C['accent']}">{meta_str}</b>
  </div>
</div>
<script>
(function() {{
  // Cancela o timer da renderização anterior — o JS roda no window pai, que
  // sobrevive aos re-renders do Streamlit (fix de flickering, ver estadual.py)
  if (window._cntFedInterval) {{ clearInterval(window._cntFedInterval); window._cntFedInterval = null; }}
  const acc = {acc:.2f}, taxa = {taxa:.4f}, start = {start_ms};
  function fmtBr(n) {{
    return n.toLocaleString('pt-BR', {{minimumFractionDigits:2, maximumFractionDigits:2}});
  }}
  function update() {{
    const elapsed = Math.max(0, (Date.now() - start) / 1000);
    const el = document.getElementById('cnt-fed-main');
    if (el) el.innerHTML = 'R$&nbsp;' + fmtBr(acc + elapsed * taxa);
  }}
  window._cntFedInterval = setInterval(update, 100);
  update();
}})();
</script>
""", unsafe_allow_javascript=True)


# ── Elemento 2: Obrigatórias × Discricionárias (% da despesa, 2010+) ──────

# (prefixos RTN, nome exibido, cor, estilo da linha)
# Obrigatórias = 4.1 Previdência + 4.2 Pessoal + 4.3 Outras Obrigatórias
#              + 4.4.1 Obrigatórias com Controle de Fluxo (saúde, educação,
#                Bolsa Família — obrigatórias por lei, sujeitas a programação)
SERIES_EL2 = [
    (("4.1 ", "4.2 ", "4.3 ", "4.4.1 "), "Despesas Obrigatórias",          C["corrente"],     "solid"),
    (("4.4.2 ",),                        "Despesas Discricionárias",        C["accent"],       "solid"),
    (("Investimento",),                  "Investimentos (incl. inversões)", C["investimento"], "dash"),
]


def _render_linhas_composicao():
    _section_title(
        "Despesas Obrigatórias vs Discricionárias "
        f"<span style='font-size:11px;font-weight:400;color:{C['text_muted']};'>"
        f"(% da Despesa Total · acumulado 12 meses)</span>"
    )

    fig = go.Figure()
    for prefixos, nome, cor, estilo in SERIES_EL2:
        s = serie_12m_pct_total(df_rtn, prefixos)
        s = s[s["data"] >= "2010-01-01"]
        fig.add_trace(go.Scatter(
            x=s["data"], y=s["pct"],
            mode="lines", name=nome,
            line=dict(color=cor, width=2.5, dash=estilo),
            hovertemplate="%{y:.1f}%",
        ))

    fig.update_layout(
        yaxis_title="% da Despesa Total (12m)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="center", x=0.5, title=""),
        xaxis=dict(
            tickformat="%m/%Y",
            hoverformat="%m/%Y",
            rangeselector=dict(**_RSEL, buttons=rangeselector_buttons()),
        ),
    )
    plotly_dark(fig, height=460, margin=dict(l=10, r=10, t=20, b=60))

    # Texto e gráfico no MESMO bloco visual, sem espaço entre eles:
    # st.container(key=...) gera a classe CSS .st-key-..., que usamos para
    # zerar o gap vertical padrão (~1rem) que o Streamlit põe entre elementos.
    st.markdown("""
<style>
.st-key-el2-bloco, .st-key-el2-bloco [data-testid="stVerticalBlock"] { gap: 0 !important; }
</style>
""", unsafe_allow_html=True)

    with st.container(key="el2-bloco"):
        st.html(f"""
<div style="border-left:3px solid {C['investimento']};background:{C['bg2']};
            border-radius:0 10px 10px 0;padding:16px 24px;margin:0;">
  <p style="font-size:14px;line-height:1.7;color:{C['text']};font-weight:600;
            text-align:center;margin:0;">
    O patamar elevado das despesas obrigatórias comprime o espaço para as despesas
    discricionárias, e, portanto, para o investimento público.
  </p>
</div>
""")
        st.plotly_chart(fig, width='stretch', key="fed_linhas_composicao")


# ── Elemento 3: Termômetro federal (idêntico ao da aba Geral) ─────────────

def _render_termometro_federal():
    # Mesma base YTD da aba Geral (decisão 07/07/2026): plano de projeção do
    # bloco federal do contador — os dois termômetros batem por construção.
    r_fed = ratio_federal(df_rtn, contador.get("federal", {}))
    if r_fed is not None:
        _ano_f, _mes_f = r_fed[3]
        nota = (f"Fonte: RTN/STN · no ano, projetado até "
                f"{MES_LABELS.get(_mes_f, _mes_f)}/{_ano_f}")
    else:
        nota = "Fonte: RTN/STN · dados não disponíveis"
    st.html(
        f'<div style="background:{C["bg2"]};border:1px solid {C["border"]};'
        f'border-radius:16px;padding:36px 44px;">'
        f'<div style="margin-bottom:28px;">'
        f'<div style="font-size:10px;letter-spacing:3px;color:{C["accent"]};font-weight:700;'
        f'text-transform:uppercase;margin-bottom:8px;">Termômetro de Investimento</div>'
        f'<div style="font-size:20px;font-weight:700;color:{C["text"]};margin-bottom:6px;">'
        f'Composição do Gasto Federal</div>'
        f'<div style="font-size:13px;color:{C["text_dim"]};line-height:1.6;max-width:680px;">'
        f'De cada R$&nbsp;100 gastos pelo Governo Federal, quanto vai para '
        f'<span style="color:{C["investimento"]};font-weight:600;">investimento produtivo</span>'
        f' (obras e equipamentos) versus '
        f'<span style="color:{C["corrente"]};font-weight:600;">despesas correntes e obrigatórias</span>'
        f' (pessoal, previdência, custeio)?</div>'
        f'</div>'
        f'{termometro_header()}'
        f'{linha_termometro("Governo Federal", nota, r_fed, destaque=True)}'
        f'</div>'
    )


# ── Elementos 4 e 5: composição das correntes e dos investimentos (%) ─────

def _grafico_composicao(itens: list[dict], cor: str, key: str):
    """Barras horizontais de composição em % (itens: [{nome, valor_mi, pct}])."""
    df_c = pd.DataFrame(itens).sort_values("pct", ascending=True)
    df_c["label"] = df_c["nome"].apply(lambda s: "<br>".join(textwrap.wrap(str(s), 26)))
    fig = go.Figure(go.Bar(
        x=df_c["pct"], y=df_c["label"], orientation="h",
        marker_color=cor, marker_line_width=0,
        text=df_c.apply(
            lambda r: f"{fmt_br(r['pct'], 1)}% · {fmt_bi(r['valor_mi'])}", axis=1
        ),
        textposition="outside",
        textfont=dict(size=11, color=C["text_dim"]),
        cliponaxis=False,
    ))
    fig.update_layout(
        xaxis_title="% do total (acumulado 12 meses)",
        xaxis=dict(range=[0, float(df_c["pct"].max()) * 1.45]),
        showlegend=False,
    )
    plotly_dark(fig, height=440, margin=dict(l=170, r=20, t=10, b=40))
    st.plotly_chart(fig, width='stretch', key=key)


def _render_composicao_obrigatorias():
    """Elemento 4: 4.1 + 4.2 + 4.4.1 + as 4 maiores rubricas de 3 díg. de 4.3.

    Cálculo em components/theme.py (composicao_obrigatorias_federal) —
    compartilhado com pipelines/exportar_web.py.
    """
    itens = composicao_obrigatorias_federal(df_rtn, ano_atual, mes_atual)
    if not itens:
        st.info("Sem dados suficientes para a composição das despesas obrigatórias.")
        return

    _section_title(
        "Composição das Despesas Obrigatórias "
        f"<span style='font-size:11px;font-weight:400;color:{C['text_muted']};'>"
        f"(% · 12m até {MES_LABELS.get(mes_atual, mes_atual)}/{ano_atual} · R$ de {base_label})</span>"
    )
    _grafico_composicao(itens, C["corrente"], "fed_comp_obrigatorias")
    st.caption(
        "Rubricas da aba 1.2 da RTN: 4.1, 4.2, 4.4.1 e as 4 maiores aberturas "
        "de 4.3 Outras Despesas Obrigatórias — por isso as barras não somam 100%."
    )


def _render_composicao_investimentos():
    """Elemento 5: composição dos investimentos (GND 4) por natureza da despesa.

    Cálculo em components/theme.py (composicao_investimentos_federal) —
    compartilhado com pipelines/exportar_web.py.
    """
    itens = composicao_investimentos_federal(df_rtn, ano_atual, mes_atual)
    if not itens:
        st.info("Sem dados da aba 1.3. Execute `python pipelines/federal/load.py`.")
        return

    _section_title(
        "Composição dos Investimentos "
        f"<span style='font-size:11px;font-weight:400;color:{C['text_muted']};'>"
        f"(% · 12m até {MES_LABELS.get(mes_atual, mes_atual)}/{ano_atual} · R$ de {base_label})</span>"
    )
    _grafico_composicao(itens, C["investimento"], "fed_comp_invest")
    st.caption(
        "Rubricas da aba 1.3 da RTN — investimentos (GND 4) por natureza da "
        "despesa. Exclui inversões financeiras (GND 5)."
    )


# ── Elemento 6: Resultado primário acumulado 12 meses ─────────────────────

def _render_resultado_primario():
    _section_title("Trajetória do Resultado Primário — acumulado 12 meses")
    st.caption("Soma rolling de 12 meses. Linha abaixo de zero = déficit acumulado.")

    # Cálculo em components/theme.py — compartilhado com pipelines/exportar_web.py
    df_traj = serie_resultado_primario(df_rtn, ano_atual, mes_atual)
    if df_traj.empty:
        st.info("Sem dados de resultado primário.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_traj["data"], y=df_traj["valor"],
        mode="lines", fill="tozeroy",
        line=dict(color=C["resultado"], width=2),
        fillcolor="rgba(56,189,248,0.07)",
    ))
    fig.add_hline(y=0, line_dash="dot", line_color=C["negative"], opacity=0.5)
    d_fim = pd.Timestamp(f"{ano_atual}-{mes_atual:02d}-01")
    d_ini = d_fim - pd.DateOffset(years=5)
    fig.update_layout(
        yaxis_title="R$ bilhões (12m)", showlegend=False,
        xaxis=dict(
            tickformat="%m/%Y",
            range=[str(d_ini.date()), str((d_fim + pd.DateOffset(months=1)).date())],
            rangeselector=dict(**_RSEL, buttons=rangeselector_buttons()),
        ),
    )
    plotly_dark(fig, height=380, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, width='stretch', key="fed_trajetoria")


# ── Montagem da página ────────────────────────────────────────────────────

_render_contador()                                                   # Elemento 1

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

_render_linhas_composicao()                                          # Elemento 2

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

_render_termometro_federal()                                         # Elemento 3

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

col_obr, col_inv = st.columns(2)                                     # Elementos 4 e 5
with col_obr:
    _render_composicao_obrigatorias()
with col_inv:
    _render_composicao_investimentos()

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

_render_resultado_primario()                                         # Elemento 6

render_footer("RTN · Secretaria do Tesouro Nacional")
