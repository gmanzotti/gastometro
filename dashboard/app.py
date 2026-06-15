"""
dashboard/app.py  —  Gastômetro FIESP · Página Inicial

Estrutura da home:
  1. Navbar fixa
  2. Hero: contador total animado + 3 sub-contadores (Federal / Estados / Municípios)
  3. Termômetro de Investimento (largura total, 4 esferas)
  4. Cards de navegação
  5. Rodapé

Como rodar:
  streamlit run dashboard/app.py
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from components.theme import (
    C, inject_css, render_navbar, render_footer,
    carregar_dados,
    calcular_ratio_investimento_estados,
    calcular_ratio_investimento_municipios,
    ratio_federal, linha_termometro, termometro_header,
)

st.set_page_config(
    page_title="Gastômetro FIESP",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_css()
render_navbar("home")

dados   = carregar_dados()
df_rtn  = dados.get("rtn",        pd.DataFrame())
contador= dados.get("contador",   {})
df_est  = dados.get("estados",    pd.DataFrame())
df_mun  = dados.get("municipios", pd.DataFrame())

tem_rtn = not df_rtn.empty
tem_est = not df_est.empty
tem_mun = not df_mun.empty


# ── Hero: contador total + 3 sub-contadores animados ─────────────────────

def _bloco_contador(chave: str) -> dict:
    bloco = contador.get(chave, {})
    if isinstance(bloco, dict) and "_consolidado" in bloco:
        bloco = bloco["_consolidado"]
    return bloco


def _render_hero():
    total_c = _bloco_contador("total")
    fed_c   = _bloco_contador("federal")
    est_c   = _bloco_contador("estados")
    mun_c   = _bloco_contador("municipios")

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
    ult_fed   = fed_c.get("ultimo_dado", "—") if fed_c else "—"

    acc_est   = est_c.get("acc_base_rs", 0) if est_c else 0
    taxa_est  = est_c.get("taxa_por_segundo_rs", 0) if est_c else 0
    start_est = est_c.get("start_ms", start_ms) if est_c else start_ms
    ult_est   = est_c.get("ultimo_dado", "—") if est_c else "aguardando"

    acc_mun   = mun_c.get("acc_base_rs", 0) if mun_c else 0
    taxa_mun  = mun_c.get("taxa_por_segundo_rs", 0) if mun_c else 0
    start_mun = mun_c.get("start_ms", start_ms) if mun_c else start_ms
    ult_mun   = mun_c.get("ultimo_dado", "—") if mun_c else "aguardando"

    # Label da esfera municipal reflete cobertura atual vs. produção
    n_mun = df_mun["cod_ibge"].nunique() if tem_mun else 0
    if n_mun > 500:
        label_mun = "Municípios"
        sub_mun   = f"{n_mun:,} municípios".replace(",", ".")
    elif n_mun > 0:
        label_mun = "Municípios"
        sub_mun   = f"{n_mun} capitais · protótipo"
    else:
        label_mun = "Municípios"
        sub_mun   = "aguardando dados"

    st.html(f"""
<div style="
    background: linear-gradient(160deg, {C['bg']} 0%, {C['bg3']} 100%);
    border: 1px solid {C['border']};
    border-radius: 20px;
    padding: 44px 56px 40px;
    text-align: center;
">
  <!-- Eyebrow -->
  <div style="font-size:10px;letter-spacing:3.5px;color:{C['accent']};
              font-weight:700;text-transform:uppercase;margin-bottom:16px;">
    Gastos Acumulados do Setor Público Brasileiro — {ano_ref}
  </div>

  <!-- Contador total -->
  <div id="cnt-total" style="
    font-size:66px;font-weight:800;color:{C['text']};
    font-family:'Courier New',monospace;letter-spacing:-2px;line-height:1;
    margin-bottom:16px;
  ">R$&nbsp;—</div>

  <div style="font-size:11px;color:{C['text_muted']};margin-bottom:32px;opacity:0.85;">
    Federal · Estados + DF · Municípios &nbsp;·&nbsp;
    Despesas acumuladas no ano, projetadas ao segundo
  </div>

  <!-- Divisor -->
  <div style="height:1px;
    background:linear-gradient(90deg,transparent 0%,{C['border']} 20%,{C['border']} 80%,transparent 100%);
    margin-bottom:32px;">
  </div>

  <div style="max-width:700px;margin:0 auto;border:1px solid {C['border']};
              border-radius:10px;overflow:hidden;">
    <table style="width:100%;border-collapse:collapse;background:{C['bg2']};">
      <thead>
        <tr style="background:{C['border']};">
          <th style="padding:10px 20px;text-align:left;font-size:10px;letter-spacing:1.5px;
                     text-transform:uppercase;color:{C['text_dim']};font-weight:600;">
            Entes Federativos
          </th>
          <th style="padding:10px 20px;text-align:right;font-size:10px;letter-spacing:1.5px;
                     text-transform:uppercase;color:{C['text_dim']};font-weight:600;">
            Total de Gastos no Ano (R$)
          </th>
        </tr>
      </thead>
      <tbody>
        <tr style="border-bottom:1px solid {C['border']};">
          <td style="padding:14px 20px;">
            <div style="font-size:15px;font-weight:600;color:{C['text']};">Governo Federal</div>
            <div style="font-size:10px;color:{C['text_muted']};margin-top:2px;">até {ult_fed}</div>
          </td>
          <td style="padding:14px 20px;text-align:right;vertical-align:middle;">
            <span id="cnt-federal" style="font-size:17px;font-weight:700;color:{C['accent']};
                  font-family:'Courier New',monospace;white-space:nowrap;">—</span>
          </td>
        </tr>
        <tr style="border-bottom:1px solid {C['border']};background:rgba(30,58,95,0.2);">
          <td style="padding:14px 20px;">
            <div style="font-size:15px;font-weight:600;color:{C['text']};">Estados + DF</div>
            <div style="font-size:10px;color:{C['text_muted']};margin-top:2px;">{ult_est}</div>
          </td>
          <td style="padding:14px 20px;text-align:right;vertical-align:middle;">
            <span id="cnt-estados" style="font-size:17px;font-weight:700;color:{C['accent']};
                  font-family:'Courier New',monospace;white-space:nowrap;">—</span>
          </td>
        </tr>
        <tr>
          <td style="padding:14px 20px;">
            <div style="font-size:15px;font-weight:600;color:{C['text']};">{label_mun}</div>
            <div style="font-size:10px;color:{C['text_muted']};margin-top:2px;">{sub_mun}</div>
          </td>
          <td style="padding:14px 20px;text-align:right;vertical-align:middle;">
            <span id="cnt-municipios" style="font-size:17px;font-weight:700;color:{C['accent']};
                  font-family:'Courier New',monospace;white-space:nowrap;">—</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</div>

<script>
(function() {{
  const spheres = {{
    "total":      {{ acc: {acc_total:.2f}, taxa: {taxa_tot:.4f},  start: {start_ms}  }},
    "federal":    {{ acc: {acc_fed:.2f},   taxa: {taxa_fed:.4f},  start: {start_fed} }},
    "estados":    {{ acc: {acc_est:.2f},   taxa: {taxa_est:.4f},  start: {start_est} }},
    "municipios": {{ acc: {acc_mun:.2f},   taxa: {taxa_mun:.4f},  start: {start_mun} }}
  }};

  function fmtBr(n) {{
    return n.toLocaleString('pt-BR', {{minimumFractionDigits:2, maximumFractionDigits:2}});
  }}
  function update() {{
    const now = Date.now();
    for (const [id, c] of Object.entries(spheres)) {{
      const elapsed = Math.max(0, (now - c.start) / 1000);
      const val     = c.acc + elapsed * c.taxa;
      const el      = document.getElementById('cnt-' + id);
      if (!el) continue;
      el.innerHTML = (c.taxa > 0 || id === 'total') ? 'R$&nbsp;' + fmtBr(val) : '—';
    }}
  }}
  setInterval(update, 100);
  update();
}})();
</script>
""", unsafe_allow_javascript=True)


# ── Termômetro de investimento — largura total, 4 esferas ────────────────

def _ratio_esfera(ratio_df: pd.DataFrame | None) -> tuple | None:
    """(invest_pct, invest_mi, total_mi) a partir de um DataFrame de ratio por ente."""
    if ratio_df is None or ratio_df.empty:
        return None
    inv = ratio_df["invest_milhoes"].sum()
    tot = ratio_df["total_milhoes"].sum()
    if tot == 0:
        return None
    return round(inv / tot * 100, 1), inv, tot


def _ratio_total(*ratios: tuple | None) -> tuple | None:
    """Média ponderada de todas as esferas disponíveis."""
    inv_sum = sum(r[1] for r in ratios if r is not None)
    tot_sum = sum(r[2] for r in ratios if r is not None)
    if tot_sum == 0:
        return None
    return round(inv_sum / tot_sum * 100, 1), inv_sum, tot_sum


def _render_termometro():
    ratio_est = calcular_ratio_investimento_estados(df_est) if tem_est else None
    ratio_mun = calcular_ratio_investimento_municipios(df_mun) if tem_mun else None

    r_est = _ratio_esfera(ratio_est)
    r_mun = _ratio_esfera(ratio_mun)
    r_fed = ratio_federal(df_rtn) if tem_rtn else None
    r_tot = _ratio_total(r_fed, r_est, r_mun)

    # Notas de fonte para cada linha
    nota_tot = "Setor público consolidado · rolling 12 meses"
    nota_fed = "Fonte: RTN/STN · abas 1.3/1.3-A · rolling 12 meses"
    if ratio_est is not None and not ratio_est.empty:
        _ano_e = int(ratio_est["ano"].iloc[0])
        _bim_e = int(ratio_est["periodo"].iloc[0])
        nota_est = f"Fonte: SICONFI · rolling 12m até {_ano_e}-B{_bim_e} · {len(ratio_est)} estados + DF"
    else:
        nota_est = "Fonte: SICONFI · dados não disponíveis"
    if ratio_mun is not None and not ratio_mun.empty:
        _ano_m = int(ratio_mun["ano"].iloc[0])
        _bim_m = int(ratio_mun["periodo"].iloc[0])
        _n_mun = int(df_mun["cod_ibge"].nunique()) if tem_mun else 0
        escopo = f"{_n_mun:,} municípios".replace(",", ".") if _n_mun > 500 else f"{_n_mun} capitais (protótipo)"
        nota_mun = f"Fonte: SICONFI · rolling 12m até {_ano_m}-B{_bim_m} · {escopo}"
    else:
        nota_mun = "Fonte: SICONFI · dados não disponíveis"

    linhas = (
        linha_termometro("Setor Público Total", nota_tot, r_tot, destaque=True)
        + f'<div style="height:1px;background:rgba(30,58,95,0.5);margin:8px 0 20px 0;"></div>'
        + linha_termometro("Governo Federal",  nota_fed, r_fed)
        + linha_termometro("Estados + DF",     nota_est, r_est)
        + linha_termometro("Municípios",        nota_mun, r_mun)
    )

    html = (
        f'<style>*{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
        f'box-sizing:border-box;margin:0;padding:0;}}body{{background:transparent;}}</style>'
        f'<div style="background:{C["bg2"]};border:1px solid {C["border"]};'
        f'border-radius:16px;padding:36px 44px;">'
        f'<div style="border-left:4px solid {C["accent"]};padding-left:24px;margin-bottom:36px;">'
        f'<p style="font-size:17px;line-height:1.85;color:{C["text"]};font-weight:700;'
        f'text-align:center;margin:0;">'
        f'Quando o governo gasta mais do que arrecada, quem paga a conta é a sociedade — '
        f'na forma de juros mais altos, crédito mais caro e menos investimento em infraestrutura, '
        f'saúde e educação. O descontrole das contas públicas funciona como um freio na economia: '
        f'os juros aumentam, o empreendedor hesita em investir, o trabalhador vê seus '
        f'financiamentos encarecerem e o país como um todo perde competitividade. '
        f'O equilíbrio fiscal não é um fim em si mesmo — é o que garante ao Estado e ao '
        f'setor privado a capacidade de investir no futuro, condição para o crescimento econômico '
        f'e a geração de empregos sustentáveis. Sem espaço para investir, o país cresce menos, '
        f'gera menos empregos e oferece menos oportunidades à sua população.'
        f'</p>'
        f'<p style="font-size:17px;line-height:1.85;color:{C["text"]};font-weight:700;'
        f'text-align:center;text-decoration:underline;margin:16px 0 0 0;">'
        f'A disciplina fiscal não penaliza a sociedade: ela a protege.'
        f'</p>'
        f'</div>'
        f'<div style="margin-bottom:28px;">'
        f'<div style="font-size:10px;letter-spacing:3px;color:{C["accent"]};font-weight:700;'
        f'text-transform:uppercase;margin-bottom:8px;">Termômetro de Investimento</div>'
        f'<div style="font-size:20px;font-weight:700;color:{C["text"]};margin-bottom:6px;">'
        f'Composição do Gasto Público</div>'
        f'<div style="font-size:13px;color:{C["text_dim"]};line-height:1.6;max-width:680px;">'
        f'De cada R$&nbsp;100 gastos pelo setor público, quanto vai para '
        f'<span style="color:{C["investimento"]};font-weight:600;">investimento produtivo</span>'
        f' (obras e equipamentos) versus '
        f'<span style="color:{C["corrente"]};font-weight:600;">despesas correntes e obrigatórias</span>'
        f' (pessoal, previdência, juros)?</div>'
        f'</div>'
        f'{termometro_header()}'
        f'{linhas}'
        f'</div>'
    )
    st.html(html)


# ── Cards de navegação ────────────────────────────────────────────────────

def _render_nav_cards():
    st.markdown(
        f"<div style='font-size:13px;font-weight:600;color:{C['text']};"
        "margin:28px 0 12px;'>Explorar o Gastômetro</div>",
        unsafe_allow_html=True,
    )
    cards = [
        ("/federal",   "📊", "Federal"),
        ("/estadual",  "🗺️", "Estadual"),
        ("/municipal", "🏙️", "Municipal"),
    ]
    cols = st.columns(3)
    for (href, icone, titulo), col in zip(cards, cols):
        with col:
            st.markdown(f"""
<a href="{href}" style="text-decoration:none;" target="_self">
  <div style="
    background:{C['bg2']};border:1px solid {C['border']};border-radius:14px;
    padding:32px 24px;cursor:pointer;transition:border-color 0.2s;height:160px;
    display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;
  " onmouseover="this.style.borderColor='{C['primary']}'"
     onmouseout="this.style.borderColor='{C['border']}'">
    <div style="font-size:52px;line-height:1;">{icone}</div>
    <div style="font-size:24px;font-weight:700;color:{C['text']};">{titulo}</div>
  </div>
</a>
""", unsafe_allow_html=True)


# ── Montagem da página ────────────────────────────────────────────────────

_render_hero()

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

_render_termometro()

st.markdown("<div class='section-divider'></div>", unsafe_allow_html=True)

_render_nav_cards()

render_footer("RTN · STN · SICONFI · Tesouro Nacional")
