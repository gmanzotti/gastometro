"""
pipelines/contador_fiscal.py  —  Gerador do contador fiscal em tempo real
──────────────────────────────────────────────────────────────────────────
Calcula a previsão de Despesa Total do governo federal para o próximo mês
e converte o resultado em taxa por segundo, para alimentar um contador
em tempo real (independente da tecnologia de exibição: HTML, Power BI, etc.).

Metodologia:
  previsao_t  = pago_{t-12} × ratio_rolling
  ratio_rolling = Σpago(últimos 12m) / Σpago(12m anteriores)

Fonte de dados: RTN - Secretaria do Tesouro Nacional (série "4. Despesa Total").
Usar RTN em vez de somar empenhos por órgão garante o total consolidado oficial
e elimina o problema de cobertura parcial dos dados de empenho.

Saída:
  data/contador_fiscal.json — lido pelo frontend do contador

Uso:
  python pipelines/contador_fiscal.py

Quando rodar:
  Sempre após `python pipelines/rtn/load.py` (RTN atualizada).
  Tipicamente 1x por mês, quando o Tesouro publica os novos dados.
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

RTN_PATH     = DATA_DIR / "rtn" / "rtn_mensal.parquet"
SAIDA        = DATA_DIR / "contador_fiscal.json"
PREFIXO_DESP = "4. "   # linha "Despesa Total" na RTN
MIN_MESES    = 24      # mínimo de histórico para o ratio ser confiável


def _proximo_mes(ano: int, mes: int) -> tuple[int, int]:
    """Retorna (ano, mes) do mês seguinte."""
    return (ano + 1, 1) if mes == 12 else (ano, mes + 1)


def calcular_contador() -> dict:
    """
    Lê RTN, aplica previsao_t = pago_{t-12} × ratio_rolling para o
    próximo mês e retorna dicionário com previsão mensal e taxa por segundo.

    Retorno (todas as chaves monetárias em R$, não em R$ milhões):
      mes_referencia           : "YYYY-MM" do mês previsto
      previsao_total_mensal_rs : total previsto em R$
      taxa_por_segundo_rs      : R$ / segundo
      dias_no_mes              : dias no mês previsto
      segundos_no_mes          : total de segundos no mês previsto
      ratio_rolling            : fator de tendência (1.05 = crescimento de 5%)
      pago_base_rs             : gasto real do mesmo mês no ano anterior (R$)
      ultimo_dado_rtn          : "YYYY-MM" do último mês com dado disponível
      gerado_em                : timestamp ISO 8601 UTC de geração do arquivo
    """
    if not RTN_PATH.exists():
        raise FileNotFoundError(
            f"RTN não encontrada em {RTN_PATH}. "
            "Execute `python pipelines/rtn/load.py` primeiro."
        )

    df = pd.read_parquet(RTN_PATH)
    serie_df = df[df["discriminacao"].str.startswith(PREFIXO_DESP)].copy()

    if len(serie_df) < MIN_MESES:
        raise ValueError(
            f"Histórico insuficiente: {len(serie_df)} meses "
            f"(mínimo necessário: {MIN_MESES})."
        )

    serie_df = serie_df.sort_values(["ano", "mes"]).reset_index(drop=True)
    serie_df["data"] = pd.to_datetime(
        {"year": serie_df["ano"], "month": serie_df["mes"], "day": 1}
    )
    # Series indexada por Timestamp para lookups diretos por data
    serie = serie_df.set_index("data")["corrente_milhoes"]

    ultima_data = serie.index.max()
    log.info("Último dado RTN disponível: %s", ultima_data.strftime("%Y-%m"))

    # ── Ratio rolling: Σ(últimos 12m) / Σ(12m anteriores) ────────────
    # Janelas ancoradas em ultima_data, mesmo método do gold.py.
    # Compara janelas anuais completas para eliminar sazonalidade.
    ini_recente  = ultima_data - pd.DateOffset(months=11)   # t-11 … t
    ini_anterior = ultima_data - pd.DateOffset(months=23)   # t-23 … t-12

    def _soma_janela(ini: pd.Timestamp, n: int) -> tuple[float, int]:
        """Retorna (soma_milhoes, n_meses_com_dados). Meses ausentes são ignorados."""
        datas     = pd.date_range(ini, periods=n, freq="MS")
        valores   = [float(serie[d]) for d in datas if d in serie.index]
        return float(np.sum(valores)), len(valores)

    soma_rec, n_rec = _soma_janela(ini_recente,  12)
    soma_ant, n_ant = _soma_janela(ini_anterior, 12)

    if n_rec < 6 or n_ant < 6:
        raise ValueError(
            f"Meses com dados insuficientes: recente={n_rec}, anterior={n_ant} "
            "(mínimo: 6 em cada janela de 12 meses)."
        )
    if soma_ant <= 0 or soma_rec <= 0:
        raise ValueError(
            f"Somas inválidas para ratio: recente={soma_rec:.0f} "
            f"anterior={soma_ant:.0f} (em R$ milhões)."
        )

    ratio = soma_rec / soma_ant

    # Ratio fora dessa faixa indica anomalia estrutural nos dados (ex: mudança
    # de metodologia da RTN) — melhor interromper e investigar manualmente.
    if not (0.5 <= ratio <= 3.0):
        raise ValueError(
            f"Ratio fora da faixa esperada [0.5, 3.0]: {ratio:.4f}. "
            "Verificar se há descontinuidade na série RTN."
        )

    log.info(
        "Ratio rolling: %.4f | janela recente: %.0f bi (n=%d) | "
        "janela anterior: %.0f bi (n=%d)",
        ratio,
        soma_rec / 1e3, n_rec,
        soma_ant / 1e3, n_ant,
    )

    # ── Previsão para o próximo mês ───────────────────────────────────
    # Mês a prever (t) = mês seguinte ao último dado disponível
    ano_prev, mes_prev = _proximo_mes(ultima_data.year, ultima_data.month)

    # pago_{t-12} = gasto real do mesmo mês no ano anterior
    data_base = pd.Timestamp(year=ano_prev, month=mes_prev, day=1) - pd.DateOffset(years=1)
    if data_base not in serie.index:
        raise ValueError(
            f"Sem dado RTN para o mês base ({data_base.strftime('%Y-%m')}). "
            "Histórico RTN insuficiente para prever o próximo mês."
        )

    pago_base_milhoes = float(serie[data_base])
    if pago_base_milhoes <= 0:
        raise ValueError(
            f"Valor base ({data_base.strftime('%Y-%m')}) negativo ou zero: "
            f"R$ {pago_base_milhoes:.2f} milhões. Revisar dados RTN."
        )

    previsao_milhoes = pago_base_milhoes * ratio
    previsao_rs      = previsao_milhoes * 1_000_000

    # ── Taxa por segundo ──────────────────────────────────────────────
    dias_no_mes      = monthrange(ano_prev, mes_prev)[1]
    segundos_no_mes  = dias_no_mes * 24 * 3600
    taxa_por_segundo = previsao_rs / segundos_no_mes

    log.info(
        "Previsão %04d-%02d: R$ %.2f bilhões | "
        "base: R$ %.2f bi × ratio %.4f | R$ %.2f/segundo",
        ano_prev, mes_prev,
        previsao_rs / 1e9,
        pago_base_milhoes / 1e3,
        ratio,
        taxa_por_segundo,
    )

    return {
        "mes_referencia":           f"{ano_prev:04d}-{mes_prev:02d}",
        "previsao_total_mensal_rs": round(previsao_rs, 2),
        "taxa_por_segundo_rs":      round(taxa_por_segundo, 4),
        "dias_no_mes":              dias_no_mes,
        "segundos_no_mes":          segundos_no_mes,
        "ratio_rolling":            round(ratio, 6),
        "pago_base_rs":             round(pago_base_milhoes * 1_000_000, 2),
        "ultimo_dado_rtn":          ultima_data.strftime("%Y-%m"),
        "gerado_em":                datetime.now(timezone.utc).isoformat(),
    }


def main():
    resultado = calcular_contador()
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)
    log.info("Salvo: %s", SAIDA)
    log.info("Taxa por segundo: R$ %.2f", resultado["taxa_por_segundo_rs"])


if __name__ == "__main__":
    main()
