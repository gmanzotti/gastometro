"""
pipelines/contador_fiscal.py  —  Gerador do contador de gastos em tempo real
─────────────────────────────────────────────────────────────────────────────
Versão multi-esfera: Federal (RTN mensal), Estados e Municípios (SICONFI bimestral)

METODOLOGIA:
  Federal    → ratio rolling 12 meses sobre dados mensais (RTN/Tesouro Nacional)
  Estados    → ratio rolling 6 bimestres sobre dados bimestrais (SICONFI/RREO)
  Municípios → idem Estados (protótipo: 26 capitais estaduais)

  Em ambos os casos a lógica é idêntica:
    ratio = Σ(janela recente) / Σ(janela anterior)
    previsão_T+1 = base_sazonal(T+1 ano passado) × ratio
    taxa = (previsão_T+1 + previsão_T+2) / (segundos_T+1 + segundos_T+2)

  O acc_base_rs cobre os períodos com dado real no ano corrente antes de T+1,
  de modo que o contador JavaScript exiba:
    total = acc_base + max(0, elapsed_seconds) × taxa

SAÍDA:
  data/contador_fiscal.json — estrutura multi-esfera lida pelo dashboard:
    {
      "total":      { taxa_por_segundo_rs, acc_base_rs, start_ms, ... },
      "federal":    { idem, campos mensais },
      "estados":    { "_consolidado": {...}, "SP": {...}, "RJ": {...}, ... },
      "municipios": { "_consolidado": {...}, "3550308": {...}, ... }
    }

COMO RODAR:
  python pipelines/contador_fiscal.py
  (após atualizar os três parquets de entrada)
"""

import json
import logging
import sys
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Caminhos ──────────────────────────────────────────────────────────────
RTN_PATH        = DATA_DIR / "rtn"        / "rtn_mensal.parquet"
ESTADOS_PATH    = DATA_DIR / "estados"    / "gastos_estados.parquet"
MUNICIPIOS_PATH = DATA_DIR / "municipios" / "gastos_municipios.parquet"
SAIDA           = DATA_DIR / "contador_fiscal.json"

# ── Filtros SICONFI ───────────────────────────────────────────────────────
# Conta total de despesas (exclui intra-orçamentárias, evita dupla contagem interna)
COD_CONTA_TOTAL = "DespesasExcetoIntraOrcamentarias"
# Coluna de fluxo bimestral (não cumulativa) — análogo ao dado mensal da RTN.
# Fase EMPENHADA (decisão de 12/06/2026): painel de advocacy usa a fase mais
# abrangente do ciclo da despesa (empenhado ≥ liquidado ≥ pago no exercício).
COLUNA_FLUXO    = "DESPESAS EMPENHADAS NO BIMESTRE"

# ── Filtro RTN ────────────────────────────────────────────────────────────
PREFIXO_DESP_RTN = "4. "   # "4. DESPESA TOTAL" na RTN

# ── Limiares de validação ─────────────────────────────────────────────────
MIN_MESES     = 24   # meses mínimos para ratio federal ser confiável
MIN_BIMESTRES = 12   # bimestres mínimos = 2 anos de histórico bimestral

# Mapeamento bimestre → primeiro mês do par:
# B1=Jan, B2=Mar, B3=Mai, B4=Jul, B5=Set, B6=Nov
_BIM_MES_INI = {1: 1, 2: 3, 3: 5, 4: 7, 5: 9, 6: 11}


# ── Utilitários de calendário ─────────────────────────────────────────────

def _proximo_mes(ano: int, mes: int) -> tuple[int, int]:
    return (ano + 1, 1) if mes == 12 else (ano, mes + 1)


def _proximo_bimestre(ano: int, bim: int) -> tuple[int, int]:
    return (ano + 1, 1) if bim == 6 else (ano, bim + 1)


def _bimestre_corrente(hoje: datetime | None = None) -> tuple[int, int]:
    """Bimestre do calendário em que 'hoje' cai. B1=jan/fev … B6=nov/dez.

    Usado para o alvo da projeção: projetamos SEMPRE até o bimestre em curso no
    calendário, nunca o ano fechado (regra do "intervalo móvel do próximo
    bimestre"). Ex.: 2 de julho → mês 7 → (7+1)//2 = bimestre 4.
    """
    d = hoje or datetime.now()
    return d.year, (d.month + 1) // 2


def _segundos_bimestre(ano: int, bim: int) -> int:
    """Total de segundos no par de meses do bimestre."""
    m1 = _BIM_MES_INI[bim]
    m2 = m1 + 1
    return (monthrange(ano, m1)[1] + monthrange(ano, m2)[1]) * 24 * 3600


def _start_ms(ano: int, mes: int) -> int:
    """Timestamp Unix (ms) do primeiro instante do mês, UTC."""
    return int(datetime(ano, mes, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _start_ms_bim(ano: int, bim: int) -> int:
    """Timestamp Unix (ms) do primeiro instante do bimestre, UTC."""
    return _start_ms(ano, _BIM_MES_INI[bim])


# ── Esfera Federal ────────────────────────────────────────────────────────

def calcular_federal(df_rtn: pd.DataFrame) -> dict:
    """
    Ratio rolling 12 meses sobre a série 'Despesa Total' da RTN.
    Projeta T+1 e T+2 (meses seguintes ao último dado disponível).
    Retorna dicionário com taxa, acc_base, start_ms e metadados.
    """
    serie_df = df_rtn[df_rtn["discriminacao"].str.startswith(PREFIXO_DESP_RTN)].copy()

    if len(serie_df) < MIN_MESES:
        raise ValueError(
            f"RTN insuficiente: {len(serie_df)} meses (mínimo: {MIN_MESES})"
        )

    serie_df = serie_df.sort_values(["ano", "mes"]).reset_index(drop=True)
    serie_df["data"] = pd.to_datetime(
        {"year": serie_df["ano"], "month": serie_df["mes"], "day": 1}
    )
    serie = serie_df.set_index("data")["corrente_milhoes"]
    ultima_data = serie.index.max()
    log.info("Federal — último dado RTN: %s", ultima_data.strftime("%Y-%m"))

    # Janelas do ratio (sem sobreposição)
    ini_rec = ultima_data - pd.DateOffset(months=11)
    ini_ant = ultima_data - pd.DateOffset(months=23)

    def _soma_janela(ini: pd.Timestamp, n: int) -> tuple[float, int]:
        datas = pd.date_range(ini, periods=n, freq="MS")
        vals  = [float(serie[d]) for d in datas if d in serie.index]
        return float(np.sum(vals)), len(vals)

    soma_rec, n_rec = _soma_janela(ini_rec, 12)
    soma_ant, n_ant = _soma_janela(ini_ant, 12)

    if n_rec < 6 or n_ant < 6:
        raise ValueError(f"Meses insuficientes: recente={n_rec}, anterior={n_ant}")
    if soma_ant <= 0 or soma_rec <= 0:
        raise ValueError(f"Somas inválidas: rec={soma_rec:.0f}, ant={soma_ant:.0f}")

    ratio = soma_rec / soma_ant
    if not (0.5 <= ratio <= 3.0):
        raise ValueError(f"Ratio fora da faixa [0.5, 3.0]: {ratio:.4f}")

    log.info(
        "Federal — ratio=%.4f | rec=%.0f bi (n=%d) | ant=%.0f bi (n=%d)",
        ratio, soma_rec / 1e3, n_rec, soma_ant / 1e3, n_ant,
    )

    # T+1 e T+2 (meses a projetar)
    ano_t1, mes_t1 = _proximo_mes(ultima_data.year, ultima_data.month)
    ano_t2, mes_t2 = _proximo_mes(ano_t1, mes_t1)

    base_t1 = pd.Timestamp(year=ano_t1, month=mes_t1, day=1) - pd.DateOffset(years=1)
    base_t2 = pd.Timestamp(year=ano_t2, month=mes_t2, day=1) - pd.DateOffset(years=1)

    for lbl, d in [("T+1", base_t1), ("T+2", base_t2)]:
        if d not in serie.index:
            raise ValueError(f"Sem base sazonal {lbl}: {d.strftime('%Y-%m')}")

    pago_t1 = float(serie[base_t1])
    pago_t2 = float(serie[base_t2])

    for lbl, v in [("T+1", pago_t1), ("T+2", pago_t2)]:
        if v <= 0:
            raise ValueError(f"Base sazonal {lbl} inválida: {v:.2f} R$ milhões")

    previsao_rs = (pago_t1 + pago_t2) * ratio * 1_000_000
    seg_t1 = monthrange(ano_t1, mes_t1)[1] * 24 * 3600
    seg_t2 = monthrange(ano_t2, mes_t2)[1] * 24 * 3600
    taxa   = previsao_rs / (seg_t1 + seg_t2)

    # acc_base: soma dos meses do ano corrente anteriores a T+1
    acc_rs = float(
        serie_df[
            (serie_df["ano"] == ano_t1) & (serie_df["mes"] < mes_t1)
        ]["corrente_milhoes"].sum()
    ) * 1_000_000

    log.info(
        "Federal — R$%.2f/s | acc R$%.0f bi | T+1=%04d-%02d | T+2=%04d-%02d",
        taxa, acc_rs / 1e9, ano_t1, mes_t1, ano_t2, mes_t2,
    )

    return {
        "taxa_por_segundo_rs":  round(taxa, 4),
        "acc_base_rs":          round(acc_rs, 2),
        "start_ms":             _start_ms(ano_t1, mes_t1),
        "mes_referencia":       f"{ano_t1:04d}-{mes_t1:02d}",
        "mes_referencia_fim":   f"{ano_t2:04d}-{mes_t2:02d}",
        "ultimo_dado":          ultima_data.strftime("%Y-%m"),
        "ratio_rolling":        round(ratio, 6),
        "previsao_total_rs":    round(previsao_rs, 2),
    }


# ── Núcleo bimestral (reutilizado por estados e municípios) ───────────────

def _calcular_bloco_bimestral(serie: dict, label: str, hoje: datetime | None = None) -> dict | None:
    """
    Ratio rolling 6 bimestres sobre uma série bimestral arbitrária.

    serie : dict {(ano, periodo): valor_milhoes}  — bimestres em qualquer ordem
    label : string de identificação para os logs

    Retorna dicionário com taxa/acc_base/start_ms/metadados, ou None se os dados
    forem insuficientes ou estatisticamente inválidos.
    """
    if len(serie) < MIN_BIMESTRES:
        log.warning("[%s] Insuficiente: %d bimestres (mín. %d)", label, len(serie), MIN_BIMESTRES)
        return None

    all_keys = sorted(serie.keys())   # lista de (ano, periodo) em ordem cronológica

    if len(all_keys) < 12:
        return None

    # Janelas sem sobreposição: últimos 6 vs. 6 anteriores
    rec_keys = all_keys[-6:]
    ant_keys = all_keys[-12:-6]

    soma_rec = sum(serie[k] for k in rec_keys)
    soma_ant = sum(serie[k] for k in ant_keys)

    if soma_ant <= 0 or soma_rec <= 0:
        log.warning("[%s] Somas inválidas: rec=%.0f ant=%.0f", label, soma_rec, soma_ant)
        return None

    ratio = soma_rec / soma_ant
    if not (0.5 <= ratio <= 3.0):
        log.warning("[%s] Ratio fora da faixa [0.5, 3.0]: %.4f", label, ratio)
        return None

    # ── Bimestres a projetar (regra do "intervalo móvel até o bimestre corrente")
    # Projetamos do primeiro bimestre após o último dado real ATÉ o bimestre em
    # curso no calendário — nunca o ano fechado. Ex.: último real = B2 e hoje
    # estamos em B4 → projeta B3 e B4. Quando o B3 é publicado, projeta só B4. Só
    # passa a incluir B5 quando o calendário entra em set/out. Isso minimiza o
    # horizonte projetado (menor erro) e evita divulgar projeção anual fechada.
    ultimo_ano, ultimo_bim = all_keys[-1]
    ano_alvo, bim_alvo = _bimestre_corrente(hoje)

    bimestres_proj: list[tuple[int, int]] = []
    a, b = _proximo_bimestre(ultimo_ano, ultimo_bim)
    while (a, b) <= (ano_alvo, bim_alvo) and len(bimestres_proj) < 6:
        bimestres_proj.append((a, b))
        a, b = _proximo_bimestre(a, b)

    if not bimestres_proj:
        # O dado real já cobre (ou passa) o bimestre corrente: nada a projetar.
        # O contador fica estático no realizado do ano corrente.
        acc_rs = sum(
            serie.get((ultimo_ano, bb), 0.0) for bb in range(1, ultimo_bim + 1)
        ) * 1_000_000
        return {
            "taxa_por_segundo_rs": 0.0,
            "acc_base_rs":         round(acc_rs, 2),
            "start_ms":            _start_ms_bim(ultimo_ano, ultimo_bim),
            "bim_referencia":      f"{ultimo_ano}-B{ultimo_bim}",
            "bim_referencia_fim":  f"{ultimo_ano}-B{ultimo_bim}",
            "ultimo_dado":         f"{ultimo_ano}-B{ultimo_bim}",
            "ratio_rolling":       round(ratio, 6),
            "previsao_total_rs":   0.0,
        }

    # Âncoras sazonais: cada bimestre projetado usa o MESMO bimestre do ano
    # anterior (captura a sazonalidade daquele bimestre específico — inclusive a
    # arrancada de investimento de nov/dez quando o alvo chega a B6).
    ancoras = [(ap - 1, bp) for (ap, bp) in bimestres_proj]
    faltando = [k for k in ancoras if k not in serie]
    if faltando:
        log.warning("[%s] Sem âncora sazonal para: %s", label, faltando)
        return None

    soma_ancoras = sum(serie[k] for k in ancoras)
    if soma_ancoras <= 0:
        log.warning("[%s] Âncoras somam ≤ 0: %.2f", label, soma_ancoras)
        return None

    previsao_rs = soma_ancoras * ratio * 1_000_000
    segundos    = sum(_segundos_bimestre(ap, bp) for (ap, bp) in bimestres_proj)
    taxa        = previsao_rs / segundos

    # acc_base: realizado do ano corrente antes do primeiro bimestre projetado.
    ano_t1, bim_t1 = bimestres_proj[0]
    if bim_t1 == 1:
        # Primeiro projetado é B1 → todo o ano anterior já está confirmado.
        acc_rs = sum(serie.get((ano_t1 - 1, bb), 0.0) for bb in range(1, 7)) * 1_000_000
    else:
        acc_rs = sum(serie.get((ano_t1, bb), 0.0) for bb in range(1, bim_t1)) * 1_000_000

    ano_fim, bim_fim = bimestres_proj[-1]
    return {
        "taxa_por_segundo_rs":    round(taxa, 4),
        "acc_base_rs":            round(acc_rs, 2),
        "start_ms":               _start_ms_bim(ano_t1, bim_t1),
        "bim_referencia":         f"{ano_t1}-B{bim_t1}",
        "bim_referencia_fim":     f"{ano_fim}-B{bim_fim}",
        "ultimo_dado":            f"{ultimo_ano}-B{ultimo_bim}",
        "ratio_rolling":          round(ratio, 6),
        "previsao_total_rs":      round(previsao_rs, 2),
    }


def _agg_bimestral(df: pd.DataFrame) -> dict:
    """
    Agrega DataFrame SICONFI (já filtrado por conta e coluna) em série bimestral.
    Retorna dict {(ano, periodo): valor_milhoes} com a soma por bimestre.
    """
    agg = df.groupby(["ano", "periodo"])["valor_milhoes"].sum()
    return {(int(a), int(p)): float(v) for (a, p), v in agg.items()}


# ── Imputação de bimestres incompletos ───────────────────────────────────

def _imputar_bimestres_incompletos(df: pd.DataFrame, n_anos: int = 3) -> pd.DataFrame:
    """
    Estima valores dos estados que não enviaram o bimestre mais recente.

    Contexto: o SICONFI recebe os dados com defasagem — no início de um bimestre,
    apenas alguns estados já entregaram o relatório. Se usarmos o consolidado bruto,
    o acc_base fica severamente subestimado (ex: B2/2026 com só AM e SC entregues).

    Algoritmo por estado faltante:
      ratio_historico = média(B_n / B_(n-1)) nos últimos n_anos para aquele estado
      B_n_estimado    = B_(n-1)_real × ratio_historico

    Aplicada apenas ao consolidado — os blocos individuais por UF usam só dados reais.
    """
    max_ano = df["ano"].max()
    max_per = int(df[df["ano"] == max_ano]["periodo"].max())

    if max_per == 1:
        return df  # B1 não tem período anterior no mesmo ano

    per_ant = max_per - 1

    estados_com_max = set(df[(df["ano"] == max_ano) & (df["periodo"] == max_per)]["uf"].unique())
    todos_os_estados = set(df[df["ano"] == max_ano]["uf"].unique())
    faltantes = todos_os_estados - estados_com_max

    if not faltantes:
        return df

    log.info(
        "Imputação: %d estados sem B%d/%d — estimando via razão histórica (n=%d anos)",
        len(faltantes), max_per, max_ano, n_anos,
    )

    anos_hist = sorted(a for a in df["ano"].unique() if a < max_ano)[-n_anos:]
    if not anos_hist:
        log.warning("Sem histórico disponível para imputação — retornando dados brutos.")
        return df

    rows_novos: list[pd.DataFrame] = []

    for uf in sorted(faltantes):
        df_uf = df[df["uf"] == uf]

        df_ant = df_uf[(df_uf["ano"] == max_ano) & (df_uf["periodo"] == per_ant)]
        if df_ant.empty:
            log.warning("  %s: sem dado de B%d/%d — pulando imputação", uf, per_ant, max_ano)
            continue

        ratios = []
        for ano_h in anos_hist:
            v_n = df_uf[(df_uf["ano"] == ano_h) & (df_uf["periodo"] == max_per)]["valor_milhoes"].sum()
            v_a = df_uf[(df_uf["ano"] == ano_h) & (df_uf["periodo"] == per_ant)]["valor_milhoes"].sum()
            if v_a > 0 and v_n > 0:
                ratios.append(v_n / v_a)

        if not ratios:
            log.warning("  %s: sem histórico para B%d/B%d — pulando", uf, max_per, per_ant)
            continue

        ratio = float(np.mean(ratios))
        ratio = max(0.4, min(ratio, 2.5))  # guarda de sanidade

        df_est = df_ant.copy()
        df_est["periodo"] = max_per
        df_est["valor_milhoes"] = df_est["valor_milhoes"] * ratio
        rows_novos.append(df_est)

        log.info(
            "  %s: B%d estimado = R$%.0f mi (ratio=%.4f, %d anos)",
            uf, max_per, df_est["valor_milhoes"].sum(), ratio, len(ratios),
        )

    if not rows_novos:
        return df

    log.info("Imputação concluída: %d estados estimados para B%d/%d.", len(rows_novos), max_per, max_ano)
    return pd.concat([df] + rows_novos, ignore_index=True)


# ── Esfera Estados ────────────────────────────────────────────────────────

def calcular_estados() -> dict:
    """
    Calcula contador para cada estado individual e para o consolidado.
    Retorna dict com "_consolidado" e uma chave por UF (ex: "SP", "RJ", ...).
    """
    if not ESTADOS_PATH.exists():
        log.warning("Parquet de estados não encontrado: %s", ESTADOS_PATH)
        return {}

    df = pd.read_parquet(ESTADOS_PATH)
    df = df[
        (df["cod_conta"] == COD_CONTA_TOTAL) &
        (df["coluna"]    == COLUNA_FLUXO)
    ].copy()

    if df.empty:
        log.warning(
            "Estados: nenhum dado após filtro — verifique cod_conta=%s e coluna=%s",
            COD_CONTA_TOTAL, COLUNA_FLUXO,
        )
        return {}

    log.info("Estados: %d linhas após filtro (%d UFs)", len(df), df["uf"].nunique())

    resultado: dict[str, dict] = {}

    # Consolidado: imputa bimestres faltantes antes de agregar, para evitar que
    # acc_base seja subestimado quando poucos estados enviaram o bimestre mais recente.
    # Os blocos individuais por UF usam apenas dados reais (df sem imputação).
    df_cons = _imputar_bimestres_incompletos(df)
    bloco = _calcular_bloco_bimestral(_agg_bimestral(df_cons), "estados._consolidado")
    if bloco:
        resultado["_consolidado"] = bloco
        log.info(
            "Estados consolidado — R$%.2f/s | acc R$%.0f bi | T+1=%s",
            bloco["taxa_por_segundo_rs"], bloco["acc_base_rs"] / 1e9, bloco["bim_referencia"],
        )

    # Por UF
    n_ok = 0
    for uf in sorted(df["uf"].unique()):
        bloco = _calcular_bloco_bimestral(_agg_bimestral(df[df["uf"] == uf]), f"estado.{uf}")
        if bloco:
            resultado[uf] = bloco
            n_ok += 1

    log.info("Estados individuais calculados: %d / %d UFs", n_ok, df["uf"].nunique())
    return resultado


# ── Esfera Municípios ─────────────────────────────────────────────────────

def calcular_municipios() -> dict:
    """
    Calcula contador para cada capital e para o consolidado das capitais.
    Retorna dict com "_consolidado" e uma chave por cod_ibge (como string).
    """
    if not MUNICIPIOS_PATH.exists():
        log.warning("Parquet de municípios não encontrado: %s", MUNICIPIOS_PATH)
        return {}

    df = pd.read_parquet(MUNICIPIOS_PATH)
    df = df[
        (df["cod_conta"] == COD_CONTA_TOTAL) &
        (df["coluna"]    == COLUNA_FLUXO)
    ].copy()

    if df.empty:
        log.warning(
            "Municípios: nenhum dado após filtro — verifique cod_conta=%s e coluna=%s",
            COD_CONTA_TOTAL, COLUNA_FLUXO,
        )
        return {}

    log.info(
        "Municípios: %d linhas após filtro (%d capitais)",
        len(df), df["cod_ibge"].nunique(),
    )

    resultado: dict[str, dict] = {}

    # Consolidado das capitais
    bloco = _calcular_bloco_bimestral(_agg_bimestral(df), "municipios._consolidado")
    if bloco:
        bloco["nota"] = "26 capitais estaduais — protótipo (DF sem dados no SICONFI)"
        resultado["_consolidado"] = bloco
        log.info(
            "Municípios consolidado — R$%.2f/s | acc R$%.0f bi | T+1=%s",
            bloco["taxa_por_segundo_rs"], bloco["acc_base_rs"] / 1e9, bloco["bim_referencia"],
        )

    # Por capital
    capitais = (
        df[["cod_ibge", "uf", "ente"]]
        .drop_duplicates("cod_ibge")
        .sort_values("uf")
    )
    n_ok = 0
    for _, row in capitais.iterrows():
        cod  = int(row["cod_ibge"])
        uf   = row["uf"]
        ente = row["ente"]
        bloco = _calcular_bloco_bimestral(
            _agg_bimestral(df[df["cod_ibge"] == cod]),
            f"capital.{uf}",
        )
        if bloco:
            bloco["uf"]   = uf
            bloco["ente"] = ente
            resultado[str(cod)] = bloco
            n_ok += 1

    log.info("Capitais individuais calculadas: %d / %d", n_ok, len(capitais))
    return resultado


# ── Total consolidado ─────────────────────────────────────────────────────

def calcular_total(federal: dict, estados: dict, municipios: dict) -> dict:
    """
    Soma Federal + Estados (consolidado) + Municípios (consolidado).

    Usa o start_ms do federal como âncora (dado mensal = granularidade maior).
    O acc_base das esferas subnacionais tem defasagem de até 1 bimestre (≈ 2 meses)
    em relação ao ponto de ancoragem — aproximação aceitável para um painel fiscal.

    Ausência de dupla contagem significativa: os grandes repasses constitucionais
    (FPM, FPE, fundos regionais) aparecem como dedução de receita na RTN federal,
    não como despesa — portanto não constam no "4. DESPESA TOTAL" usado aqui.
    Residual mínimo: FUNDEB complementação da União, Lei Kandir, Apoio Fin. EE/MM.
    """
    est = estados.get("_consolidado")
    mun = municipios.get("_consolidado")

    taxa = federal["taxa_por_segundo_rs"]
    acc  = federal["acc_base_rs"]

    if est:
        taxa += est["taxa_por_segundo_rs"]
        acc  += est["acc_base_rs"]
    if mun:
        taxa += mun["taxa_por_segundo_rs"]
        acc  += mun["acc_base_rs"]

    return {
        "taxa_por_segundo_rs":    round(taxa, 4),
        "acc_base_rs":            round(acc, 2),
        "start_ms":               federal["start_ms"],
        "mes_referencia":         federal["mes_referencia"],
        "mes_referencia_fim":     federal["mes_referencia_fim"],
        "ultimo_dado_federal":    federal["ultimo_dado"],
        "ultimo_dado_estados":    est["ultimo_dado"] if est else "N/D",
        "ultimo_dado_municipios": mun["ultimo_dado"] if mun else "N/D",
        "nota": (
            "Federal (RTN) + 26 estados (SICONFI) + 26 capitais (SICONFI) · "
            "DF sem dados no SICONFI · "
            "Municípios: protótipo com capitais estaduais"
        ),
    }


# ── Ponto de entrada ──────────────────────────────────────────────────────

def main() -> None:
    inicio = datetime.now()
    log.info("=== Contador fiscal multi-esfera — início ===")

    if not RTN_PATH.exists():
        raise FileNotFoundError(f"RTN não encontrada: {RTN_PATH}")

    df_rtn     = pd.read_parquet(RTN_PATH)
    federal    = calcular_federal(df_rtn)
    estados    = calcular_estados()
    municipios = calcular_municipios()
    total      = calcular_total(federal, estados, municipios)

    log.info(
        "Total consolidado — R$%.2f/s | acc R$%.0f bi",
        total["taxa_por_segundo_rs"], total["acc_base_rs"] / 1e9,
    )

    saida = {
        "gerado_em":        datetime.now(timezone.utc).isoformat(),
        "duracao_segundos": round((datetime.now() - inicio).total_seconds()),
        "total":            total,
        "federal":          federal,
        "estados":          estados,
        "municipios":       municipios,
    }

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump(saida, f, indent=2, ensure_ascii=False)

    log.info("JSON salvo: %s", SAIDA)
    log.info("=== Contador fiscal concluído em %.1fs ===",
             (datetime.now() - inicio).total_seconds())


if __name__ == "__main__":
    main()
