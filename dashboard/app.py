"""
dashboard/app.py  —  Gastômetro FIESP · Página Inicial

Estrutura da home:
  1. Navbar fixa
  2. Hero: contador animado multi-esfera (Total / Federal / Estados / Municípios)
  3. Termômetro de Investimento: proporção de cada R$ 100 gastos
  4. KPIs de destaque do ano corrente
  5. Cards de navegação para as demais seções
  6. Rodapé

Como rodar:
  streamlit run dashboard/app.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from components.theme import (
    C, MES_LABELS, inject_css, render_navbar, render_footer,
    fmt_bi, fmt_br, fmt_pct, plotly_dark,
    carregar_dados, calcular_ratio_investimento_estados,
    rtn_valor, rtn_soma_12m,
)

st.set_page_config(
    page_title="Gastômetro FIESP",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_css()
render_navbar("home")

dados     = carregar_dados()
df_rtn    = dados.get("rtn", pd.DataFrame())
contador  = dados.get("contador", {})
df_est    = dados.get("estados", pd.DataFrame())
meta      = dados.get("meta_rtn", {})

tem_rtn    = not df_rtn.empty
tem_est    = not df_est.empty
tem_cont   = bool(contador)


# ── Hero: contador em tempo real ──────────────────────────────────────────

def _bloco_contador(label: str, chave: str) -> dict:
    """Extrai acc_base_rs, taxa_por_segundo_rs, start_ms de contador[chave]."""
    bloco = contador.get(chave, {})
    if isinstance(bloco, dict) and "_consolidado" in bloco:
        bloco = bloco["_consolidado"]
    return bloco


def _render_hero():
    total_c  = _bloco_contador("total",     "total")
    fed_c    = _bloco_contador("federal",   "federal")
    est_c    = _bloco_contador("_consol_e", "estados")
    mun_c    = _bloco_contador("_consol_m", "municipios")

    # Usa total se disponível, senão federal
    ref_c = total_c if total_c else fed_c

    if not ref_c:
        st.info("Execute `python pipelines/contador_fiscal.py` para habilitar o contador.")
        return

    acc_total = ref_c.get("acc_base_rs", 0)
    taxa_tot  = ref_c.get("taxa_por_segundo_rs", 0)
    start_ms  = ref_c.get("start_ms", 0)
    mes_ref   = ref_c.get("mes_referencia", "")
    ano_ref   = mes_ref[:4] if mes_ref else str(datetime.now().year)

    acc_fed   = fed_c.get("acc_base_rs", 0) if fed_c else 0
    taxa_fed  = fed_c.get("taxa_por_segundo_rs", 0) if fed_c else 0
    start_fed = fed_c.get("start_ms", start_ms) if fed_c else start_ms

    acc_est   = est_c.get("acc_base_rs", 0) if est_c else 0
    taxa_est  = est_c.get("taxa_por_segundo_rs", 0) if est_c else 0
    start_est = est_c.get("start_ms", start_ms) if est_c else start_ms

    acc_mun   = mun_c.get("acc_base_rs", 0) if mun_c else 0
    taxa_mun  = mun_c.get("taxa_por_segundo_rs", 0) if mun_c else 0
    start_mun = mun_c.get("start_ms", start_ms) if mun_c else start_ms

    ult_fed   = fed_c.get("ultimo_dado", "—") if fed_c else "—"
    ult_est   = est_c.get("ultimo_dado", "aguardando") if est_c else "aguardando"
    ult_mun   = mun_c.get("ultimo_dado", "aguardando") if mun_c else "aguardando"

    sub_federal   = "" if not fed_c  else ""
    sub_estados   = "aguardando TI" if not est_c  else ""
    sub_municipios= "aguardando TI" if not mun_c  else ""

    st.components.v1.html(f"""
<div style="
    background: linear-gradient(135deg,{C['bg']} 0%,{C['bg3']} 100%);
    border: 1px solid {C['border']};
    border-radius: 16px;
    padding: 36px 48px 28px;
    text-align: center;
    margin-bottom: 4px;
">
  <div style="font-size:11px;letter-spacing:3px;color:{C['accent']};
              font-weight:700;text-transform:uppercase;margin-bottom:10px;">
    Gastos Totais Acumulados do Setor Público — {ano_ref}
  </div>
  <div id="cnt-total" style="
    font-size:58px;font-weight:800;color:{C['text']};
    font-family:'Courier New',monospace;letter-spacing:-1.5px;line-height:1.1;
  ">R$&nbsp;—</div>
  <div style="font-size:12px;color:{C['text_muted']};margin-top:6px;margin-bottom:28px;">
    Federal + Estados + Municípios &nbsp;·&nbsp;
    Federal até {ult_fed} &nbsp;·&nbsp;
    Estados: {ult_est} &nbsp;·&nbsp;
    Municípios: {ult_mun}
  </div>

  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;max-width:780px;margin:0 auto;">
    <div style="background:rgba(14,26,46,0.7);border:1px solid {C['border']};
                border-radius:10px;padding:16px 12px;">
      <div style="font-size:9px;letter-spacing:2px;color:{C['text_muted']};
                  text-transform:uppercase;margin-bottom:6px;">Federal</div>
      <div id="cnt-federal" style="font-size:22px;font-weight:700;color:{C['accent']};
           font-family:'Courier New',monospace;">—</div>
      {'<div style="font-size:10px;color:#64748B;margin-top:4px;">'+sub_federal+'</div>' if sub_federal else ''}
    </div>
    <div style="background:rgba(14,26,46,0.7);border:1px solid {C['border']};
                border-radius:10px;padding:16px 12px;">
      <div style="font-size:9px;letter-spacing:2px;color:{C['text_muted']};
                  text-transform:uppercase;margin-bottom:6px;">Estados</div>
      <div id="cnt-estados" style="font-size:22px;font-weight:700;color:{C['accent']};
           font-family:'Courier New',monospace;">—</div>
      {'<div style="font-size:10px;color:#64748B;margin-top:4px;">'+sub_estados+'</div>' if sub_estados else ''}
    </div>
    <div style="background:rgba(14,26,46,0.7);border:1px solid {C['border']};
                border-radius:10px;padding:16px 12px;">
      <div style="font-size:9px;letter-spacing:2px;color:{C['text_muted']};
                  text-transform:uppercase;margin-bottom:6px;">Municípios</div>
      <div id="cnt-municipios" style="font-size:22px;font-weight:700;color:{C['accent']};
           font-family:'Courier New',monospace;">—</div>
      {'<div style="font-size:10px;color:#64748B;margin-top:4px;">'+sub_municipios+'</div>' if sub_municipios else ''}
    </div>
  </div>
</div>

<script>
(function() {{
  const spheres = {{
    "total":      {{ acc: {acc_total:.2f},  taxa: {taxa_tot:.4f},  start: {start_ms}  }},
    "federal":    {{ acc: {acc_fed:.2f},    taxa: {taxa_fed:.4f},  start: {start_fed} }},
    "estados":    {{ acc: {acc_est:.2f},    taxa: {taxa_est:.4f},  start: {start_est} }},
    "municipios": {{ acc: {acc_mun:.2f},    taxa: {taxa_mun:.4f},  start: {start_mun} }}
  }};

  function fmtBr(n) {{
    return n.toLocaleString('pt-BR', {{minimumFractionDigits:2,maximumFractionDigits:2}});
  }}
  function fmtBi(n) {{
    if (n >= 1e12) return 'R$ ' + (n/1e12).toLocaleString('pt-BR',{{minimumFractionDigits:1,maximumFractionDigits:1}}) + ' tri';
    if (n >= 1e9)  return 'R$ ' + (n/1e9).toLocaleString('pt-BR',{{minimumFractionDigits:1,maximumFractionDigits:1}}) + ' bi';
    return 'R$ ' + (n/1e6).toLocaleString('pt-BR',{{minimumFractionDigits:1,maximumFractionDigits:1}}) + ' mi';
  }}

  function update() {{
    const now = Date.now();
    for (const [id, c] of Object.entries(spheres)) {{
      const elapsed = Math.max(0, (now - c.start) / 1000);
      const total   = c.acc + elapsed * c.taxa;
      const el      = document.getElementById('cnt-' + id);
      if (!el) continue;
      if (id === 'total') {{
        el.innerHTML = 'R$ ' + fmtBr(total);
      }} else {{
        el.innerHTML = c.taxa > 0 ? fmtBi(total) : '—';
      }}
    }}
  }}
  setInterval(update, 100);
  update();
}})();
</script>
""", height=290)


# ── Termômetro de investimento ────────────────────────────────────────────

def _render_termometro():
    st.markdown(
        f"<div style='font-size:13px;font-weight:600;color:{C['text']};"
        "margin-bottom:4px;margin-top:8px;'>Termômetro de Investimento</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "De cada R$ 100 gastos pelo setor público, quanto vai para investimento "
        "(obras e equipamentos) vs. despesas correntes (custeio, previdência, pessoal)."
    )

    if tem_est:
        ratio_df = calcular_ratio_investimento_estados(df_est)
        if not ratio_df.empty:
            invest_pct  = ratio_df["invest_ratio"].mean()
            corrente_pct = 100 - invest_pct
            # Comparação com ano anterior
            ano_max = df_est["ano"].max()
            ratio_ant = None
            if ano_max > df_est["ano"].min():
                df_est_ant = df_est[df_est["ano"] == ano_max - 1]
                if not df_est_ant.empty:
                    ratio_ant_df = calcular_ratio_investimento_estados(
                        df_est[df_est["ano"] <= ano_max - 1]
                    )
                    if not ratio_ant_df.empty:
                        ratio_ant = ratio_ant_df["invest_ratio"].mean()

            delta_txt = ""
            if ratio_ant is not None:
                diff = invest_pct - ratio_ant
                cor  = C["positive"] if diff >= 0 else C["negative"]
                sinal = "+" if diff >= 0 else ""
                delta_txt = (
                    f'<span style="font-size:11px;color:{cor};margin-left:12px;">'
                    f'{sinal}{fmt_br(diff, 1)} p.p. vs. ano anterior</span>'
                )

            _termometro_html(invest_pct, corrente_pct, delta_txt, "Estados")
            return

    if tem_rtn:
        # Proxy federal: Discricionárias / Total
        anos_disp = sorted(df_rtn["ano"].unique(), reverse=True)
        if not anos_disp:
            _termometro_placeholder()
            return
        ano = anos_disp[0]
        meses_disp = sorted(df_rtn[df_rtn["ano"] == ano]["mes"].unique(), reverse=True)
        if not meses_disp:
            _termometro_placeholder()
            return
        mes = meses_disp[0]

        total_v = rtn_valor(df_rtn, "4. ",   ano, mes, "corrente_milhoes")
        disc_v  = rtn_valor(df_rtn, "4.4.2", ano, mes, "corrente_milhoes")

        if total_v and disc_v and total_v > 0:
            invest_pct   = disc_v / total_v * 100
            corrente_pct = 100 - invest_pct
            nota = "Proxy: Discricionárias / Despesa Total (RTN federal)"
            _termometro_html(invest_pct, corrente_pct, "", nota)
            return

    _termometro_placeholder()


def _termometro_html(invest_pct: float, corrente_pct: float, delta_txt: str, nota: str):
    st.markdown(f"""
<div style="margin:8px 0 4px 0;">
  <div style="display:flex;align-items:center;margin-bottom:6px;">
    <span style="font-size:24px;font-weight:800;color:{C['investimento']};">
      {fmt_br(invest_pct, 1)}%
    </span>
    <span style="font-size:13px;color:{C['text_dim']};margin-left:8px;">Investimento</span>
    {delta_txt}
  </div>
  <div style="width:100%;height:44px;border-radius:8px;overflow:hidden;
              display:flex;border:1px solid {C['border']};">
    <div style="width:{invest_pct:.2f}%;
                background:linear-gradient(90deg,#166534,{C['investimento']});
                display:flex;align-items:center;justify-content:center;
                min-width:60px;padding:0 8px;">
      <span style="color:white;font-weight:700;font-size:12px;white-space:nowrap;">
        R$ {fmt_br(invest_pct, 1)} de cada R$ 100
      </span>
    </div>
    <div style="flex:1;
                background:linear-gradient(90deg,{C['corrente']},#7f1d1d);
                display:flex;align-items:center;justify-content:center;padding:0 8px;">
      <span style="color:white;font-weight:700;font-size:12px;white-space:nowrap;">
        Corrente &amp; Obrigatório: {fmt_br(corrente_pct, 1)}%
      </span>
    </div>
  </div>
  <div style="font-size:10px;color:{C['text_muted']};margin-top:4px;">{nota}</div>
</div>
""", unsafe_allow_html=True)


def _termometro_placeholder():
    st.info("Dados subnacionais ainda não disponíveis. Execute os pipelines de estados e municípios.")


# ── KPIs do ano ──────────────────────────────────────────────────────────

def _render_kpis():
    if not tem_rtn:
        return
    anos_disp = sorted(df_rtn["ano"].unique(), reverse=True)
    if not anos_disp:
        return
    ano = anos_disp[0]
    meses_disp = sorted(df_rtn[df_rtn["ano"] == ano]["mes"].unique(), reverse=True)
    if not meses_disp:
        return
    mes = meses_disp[0]

    col = "corrente_milhoes"

    def v(p):  return rtn_valor(df_rtn, p, ano, mes, col)
    def d(p):
        at = rtn_valor(df_rtn, p, ano, mes, col)
        an = rtn_valor(df_rtn, p, ano - 1, mes, col)
        if at is None or an is None or an == 0:
            return None
        return round((at - an) / abs(an) * 100, 1)
    def ds(val):
        if val is None:
            return None
        return f"{'+' if val >= 0 else ''}{fmt_br(val, 1)}% a/a"

    periodo = f"{MES_LABELS.get(mes, mes)}/{ano}"
    st.markdown(
        f"<div class='kpi-sub'>Governo Federal — {periodo}</div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Receita Líquida", fmt_bi(v("3. ")), delta=ds(d("3. ")))
    with c2:
        st.metric("Despesa Total",   fmt_bi(v("4. ")), delta=ds(d("4. ")))
    with c3:
        vp = v("5. ")
        st.metric(
            "Result. Primário",
            fmt_bi(vp),
            delta=ds(d("5. ")),
            delta_color="normal",
            help="Receita Líquida - Despesa Total. Negativo = déficit.",
        )
    with c4:
        st.metric(
            "Discricionárias",
            fmt_bi(v("4.4.2")),
            delta=ds(d("4.4.2")),
            delta_color="inverse",
            help="Proxy para investimento/gastos controláveis no orçamento federal.",
        )


# ── Cards de navegação ────────────────────────────────────────────────────

def _render_nav_cards():
    st.markdown(
        f"<div style='font-size:13px;font-weight:600;color:{C['text']};"
        "margin:28px 0 12px;'>Explorar o Gastômetro</div>",
        unsafe_allow_html=True,
    )
    cards = [
        ("/federal",     "📊", "Federal",
         "Receita, despesa e resultado primário. Composição dos gastos e alertas de anomalias."),
        ("/subnacional", "🗺️", "Subnacional",
         "Gastos e investimentos dos 26 estados, DF e municípios. Mapa comparativo."),
        ("/projecoes",   "🔭", "Projeções",
         "Trajetória fiscal projetada pelos próximos anos. Cenários de ajuste interativos."),
    ]
    cols = st.columns(3)
    for (href, icone, titulo, desc), col in zip(cards, cols):
        with col:
            st.markdown(f"""
<a href="{href}" style="text-decoration:none;">
  <div style="
    background:{C['bg2']};border:1px solid {C['border']};border-radius:14px;
    padding:28px 24px;cursor:pointer;transition:border-color 0.2s;height:160px;
    display:flex;flex-direction:column;justify-content:space-between;
  " onmouseover="this.style.borderColor='{C['primary']}'"
     onmouseout="this.style.borderColor='{C['border']}'">
    <div>
      <div style="font-size:28px;margin-bottom:10px;">{icone}</div>
      <div style="font-size:16px;font-weight:700;color:{C['text']};margin-bottom:6px;">{titulo}</div>
      <div style="font-size:12px;color:{C['text_dim']};line-height:1.5;">{desc}</div>
    </div>
  </div>
</a>
""", unsafe_allow_html=True)


# ── Montagem da página ────────────────────────────────────────────────────

_render_hero()

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

col_left, col_right = st.columns([1, 1])
with col_left:
    _render_termometro()
with col_right:
    _render_kpis()

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

_render_nav_cards()

render_footer("RTN · STN · SICONFI · Tesouro Nacional")
