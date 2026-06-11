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

import re
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
    carregar_dados, rtn_soma_12m,
    ratio_federal, linha_termometro, termometro_header,
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


def _limpar_rubrica(label: str) -> str:
    """Remove numeração, prefixo INV e notas de rodapé ('4/') do nome da rubrica."""
    s = re.sub(r"^INV\s+", "", label)        # prefixo das séries da aba 1.3
    s = re.sub(r"^[\d\.]+\s+", "", s)        # numeração ("4.3.14", "2.1.1.1"...)
    s = re.sub(r"\s+\d+/\s*$", "", s)        # nota de rodapé no fim ("4/")
    return s.strip()


def _soma_12m_label(label: str) -> float | None:
    """Soma 12 meses de uma série pelo nome EXATO, em R$ CONSTANTES.

    Diferente de rtn_soma_12m (que usa startswith), a igualdade exata evita
    dupla contagem com sub-rubricas: '4.3.01 Abono e Seguro Desemprego' tem
    filhas '4.3.01.1 Abono' e '4.3.01.2 Seguro Desemprego' que seriam
    capturadas por um filtro de prefixo.

    R$ constantes (IPCA) porque somar 12 meses em R$ correntes subestima o
    total em moeda de hoje (~2,5% com IPCA a ~5% a.a.) — é o padrão da
    própria STN na tabela 1.2-B da RTN ("Acumulado em 12 meses").
    """
    sub = df_rtn[df_rtn["discriminacao"] == label]
    m_ini, a_ini = (mes_atual + 1, ano_atual - 1) if mes_atual < 12 else (1, ano_atual)
    mask = (
        ((sub["ano"] > a_ini) | ((sub["ano"] == a_ini) & (sub["mes"] >= m_ini))) &
        ((sub["ano"] < ano_atual) | ((sub["ano"] == ano_atual) & (sub["mes"] <= mes_atual)))
    )
    vals = sub[mask]["constante_milhoes"].dropna()
    return float(vals.sum()) if len(vals) >= 6 else None


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


def _serie_12m_pct_total(prefixos: tuple) -> pd.DataFrame:
    """Acumulado 12 meses da série como % da Despesa Total (também em 12m).

    Em % da despesa, deflator e crescimento real se cancelam — a leitura vira
    pura composição do orçamento, sem ruído de inflação.
    """
    def _soma_12m(prefs: tuple) -> pd.DataFrame:
        sub = df_rtn[df_rtn["discriminacao"].str.startswith(prefs)]
        # R$ constantes: numerador e denominador na mesma moeda do mesmo mês
        # (ratio de somas nominais daria peso menor aos meses mais antigos)
        mensal = (
            sub.groupby(["ano", "mes"], as_index=False)["constante_milhoes"].sum()
            .sort_values(["ano", "mes"])
        )
        # rolling(12): soma janela móvel de 12 meses — neutraliza a sazonalidade
        # (dezembro concentra pagamentos de precatórios, 13º, restos a executar)
        mensal["v12"] = mensal["constante_milhoes"].rolling(12).sum()
        return mensal[["ano", "mes", "v12"]]

    serie = _soma_12m(prefixos)
    total = _soma_12m(("4. ",)).rename(columns={"v12": "v12_total"})
    m = serie.merge(total, on=["ano", "mes"])
    m["pct"]  = m["v12"] / m["v12_total"] * 100
    m["data"] = pd.to_datetime(
        m["ano"].astype(str) + "-" + m["mes"].astype(str).str.zfill(2) + "-01"
    )
    return m.dropna(subset=["pct"])


def _render_linhas_composicao():
    _section_title(
        "Despesas Obrigatórias vs Discricionárias "
        f"<span style='font-size:11px;font-weight:400;color:{C['text_muted']};'>"
        f"(% da Despesa Total · acumulado 12 meses)</span>"
    )

    fig = go.Figure()
    for prefixos, nome, cor, estilo in SERIES_EL2:
        s = _serie_12m_pct_total(prefixos)
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
    r_fed = ratio_federal(df_rtn)
    nota  = (
        f"Fonte: RTN/STN · rolling 12 meses até "
        f"{MES_LABELS.get(mes_atual, mes_atual)}/{ano_atual}"
    )
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

    O denominador (% do total) segue a MESMA definição de obrigatórias do
    Elemento 2 (4.1 + 4.2 + 4.3 + 4.4.1); as barras não somam 100% porque
    exibimos só as principais aberturas de 4.3, sem residual.
    """
    v41  = rtn_soma_12m(df_rtn, "4.1 ",   ano_atual, mes_atual, "constante_milhoes")
    v42  = rtn_soma_12m(df_rtn, "4.2 ",   ano_atual, mes_atual, "constante_milhoes")
    v43  = rtn_soma_12m(df_rtn, "4.3 ",   ano_atual, mes_atual, "constante_milhoes")
    v441 = rtn_soma_12m(df_rtn, "4.4.1 ", ano_atual, mes_atual, "constante_milhoes")
    if None in (v41, v42, v43, v441):
        st.info("Sem dados suficientes para a composição das despesas obrigatórias.")
        return
    total = v41 + v42 + v43 + v441

    # Rubricas de 3 dígitos dentro de 4.3 (ex: '4.3.14 Sentenças Judiciais...').
    # O regex exige espaço após os 2 dígitos para excluir sub-níveis ('4.3.15.1').
    rubricas_43 = [
        d for d in df_rtn["discriminacao"].unique()
        if re.match(r"^4\.3\.\d{2}\s", d)
    ]
    valores_43 = [
        (d, v) for d in rubricas_43
        if (v := _soma_12m_label(d)) is not None
    ]
    top4 = sorted(valores_43, key=lambda t: t[1], reverse=True)[:4]

    itens = [
        {"nome": "Benefícios Previdenciários", "valor_mi": v41},
        {"nome": "Pessoal e Encargos Sociais", "valor_mi": v42},
        {"nome": "Obrigatórias c/ Controle de Fluxo (saúde, educação, Bolsa Família)",
         "valor_mi": v441},
        *[{"nome": _limpar_rubrica(d), "valor_mi": v} for d, v in top4],
    ]
    for i in itens:
        i["pct"] = i["valor_mi"] / total * 100

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


# Rubricas de investimento por natureza da despesa (aba 1.3 da RTN, GND 4).
# Prefixos com espaço final para casar exatamente uma série cada.
RUBRICAS_INVEST = [
    "INV 2.1.1.1 ",   # Obras e instalações
    "INV 2.1.1.2 ",   # Equipamentos e material permanente
    "INV 2.1.1.3 ",   # Serviços
    "INV 2.1.1.4 ",   # Demais aplicações diretas da União
    "INV 2.1.2 ",     # Transferências a Estados/DF
    "INV 2.1.3 ",     # Transferências a Municípios
    "INV 2.1.4 ",     # Outras transferências
]


def _render_composicao_investimentos():
    """Elemento 5: composição dos investimentos (GND 4) por natureza da despesa."""
    itens = []
    for pref in RUBRICAS_INVEST:
        serie = df_rtn[df_rtn["discriminacao"].str.startswith(pref)]["discriminacao"]
        if serie.empty:
            continue
        v = rtn_soma_12m(df_rtn, pref, ano_atual, mes_atual, "constante_milhoes")
        if v is not None:
            itens.append({"nome": _limpar_rubrica(serie.iloc[0]), "valor_mi": v})
    if not itens:
        st.info("Sem dados da aba 1.3. Execute `python pipelines/federal/load.py`.")
        return

    total = sum(i["valor_mi"] for i in itens)
    for i in itens:
        i["pct"] = i["valor_mi"] / total * 100

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

    p_sel = ano_atual * 100 + mes_atual
    sub_res = (
        df_rtn[df_rtn["discriminacao"].str.startswith("5. ")]
        .sort_values(["ano", "mes"])
    )
    traj = []
    for _, row in sub_res.iterrows():
        a, m = int(row["ano"]), int(row["mes"])
        if a * 100 + m > p_sel:
            break
        v = rtn_soma_12m(df_rtn, "5. ", a, m, "corrente_milhoes")
        if v is not None:
            traj.append({"data": pd.Timestamp(f"{a}-{m:02d}-01"), "valor": v / 1e3})
    if not traj:
        st.info("Sem dados de resultado primário.")
        return

    df_traj = pd.DataFrame(traj)
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
