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
# Coluna de fluxo bimestral (não cumulativa) — análogo ao dado mensal da RTN
COLUNA_FLUXO    = "DESPESAS LIQUIDADAS NO BIMESTRE"

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

def _calcular_bloco_bimestral(serie: dict, label: str) -> dict | None:
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

    # Bimestres a projetar
    ultimo_ano, ultimo_bim = all_keys[-1]
    ano_t1, bim_t1 = _proximo_bimestre(ultimo_ano, ultimo_bim)
    ano_t2, bim_t2 = _proximo_bimestre(ano_t1, bim_t1)

    # Âncoras sazonais: mesmo bimestre do ano anterior
    base_t1 = (ano_t1 - 1, bim_t1)
    base_t2 = (ano_t2 - 1, bim_t2)

    if base_t1 not in serie or base_t2 not in serie:
        log.warning("[%s] Sem âncora sazonal: t1=%s t2=%s", label, base_t1, base_t2)
        return None

    pago_t1 = serie[base_t1]
    pago_t2 = serie[base_t2]

    if pago_t1 <= 0 or pago_t2 <= 0:
        log.warning("[%s] Âncora inválida: t1=%.2f t2=%.2f", label, pago_t1, pago_t2)
        return None

    previsao_rs = (pago_t1 + pago_t2) * ratio * 1_000_000
    seg_t1 = _segundos_bimestre(ano_t1, bim_t1)
    seg_t2 = _segundos_bimestre(ano_t2, bim_t2)
    taxa   = previsao_rs / (seg_t1 + seg_t2)

    # acc_base: bimestres do ano corrente antes de T+1
    if bim_t1 == 1:
        # T+1 é B1 do próximo ano → todo o ano anterior já está confirmado
        ano_ref = ano_t1 - 1
        acc_rs  = sum(serie.get((ano_ref, b), 0.0) for b in range(1, 7)) * 1_000_000
    else:
        # Bimestres 1 … (bim_t1 − 1) do ano corrente
        acc_rs = sum(serie.get((ano_t1, b), 0.0) for b in range(1, bim_t1)) * 1_000_000

    return {
        "taxa_por_segundo_rs":    round(taxa, 4),
        "acc_base_rs":            round(acc_rs, 2),
        "start_ms":               _start_ms_bim(ano_t1, bim_t1),
        "bim_referencia":         f"{ano_t1}-B{bim_t1}",
        "bim_referencia_fim":     f"{ano_t2}-B{bim_t2}",
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

    # Consolidado: soma de todos os estados
    bloco = _calcular_bloco_bimestral(_agg_bimestral(df), "estados._consolidado")
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
