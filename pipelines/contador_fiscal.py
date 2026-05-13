"""
pipelines/contador_fiscal.py  —  Gerador do contador de gastos em tempo real
──────────────────────────────────────────────────────────────────────────────
O QUE É O CONTADOR?
  O contador é o número que aparece "girando" no topo do painel, mostrando
  quanto o Governo Federal já gastou no ano (ou está gastando a cada segundo).
  Para que ele avance em tempo real no navegador, precisamos saber:
    → Quanto o governo gastou até o mês anterior (valor real da RTN)
    → A que velocidade (R$/segundo) ele provavelmente está gastando agora

METODOLOGIA (ratio rolling 12 meses):
  Como o mês ainda está em curso, não temos o valor real — precisamos prever.
  A previsão é feita com uma fórmula simples:

    previsao_mês_t = gasto_mesmo_mês_ano_anterior × ratio_rolling

  O ratio_rolling captura a tendência recente de crescimento dos gastos:

    ratio = Σ(gastos nos últimos 12 meses) / Σ(gastos nos 12 meses anteriores)

  Exemplo: se os últimos 12 meses somaram R$ 2,1 tri e os 12 antes disso
  somaram R$ 2,0 tri, o ratio = 1,05 (crescimento de 5%).
  Se o governo gastou R$ 200 bi em janeiro do ano passado, a previsão para
  janeiro deste ano é R$ 200 bi × 1,05 = R$ 210 bi.

  Por que usar a sazonalidade (mesmo mês do ano anterior)?
  Gastos do governo são muito sazonais: dezembro tem muito mais gasto que
  fevereiro (13º salário, emendas no fim do ano, etc.). Usar o mesmo mês
  do ano anterior como base incorpora essa sazonalidade automaticamente.

FONTE DE DADOS:
  Série "4. Despesa Total" da RTN (Tesouro Nacional) — o total oficial
  consolidado. Sempre mais confiável do que somar empenhos por órgão.

SAÍDA:
  data/contador_fiscal.json  — lido pelo JavaScript no dashboard.
  Estrutura do JSON:
    mes_referencia           : "YYYY-MM" do mês que está sendo previsto
    previsao_total_mensal_rs : valor total previsto para o mês (R$)
    taxa_por_segundo_rs      : R$ por segundo (usado pelo contador em tempo real)
    dias_no_mes              : número de dias no mês previsto
    segundos_no_mes          : total de segundos no mês previsto
    ratio_rolling            : o fator de crescimento calculado (ex: 1.05)
    pago_base_rs             : gasto real do mesmo mês no ano anterior (R$)
    ultimo_dado_rtn          : "YYYY-MM" do último mês com dado real disponível
    gerado_em                : data/hora em que o arquivo foi gerado

COMO RODAR:
  python pipelines/contador_fiscal.py
  (sempre após python pipelines/rtn/load.py)
"""

import json
import logging
import sys
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# Adiciona a raiz do projeto ao caminho do Python para que o import funcione
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RTN_PATH     = DATA_DIR / "rtn" / "rtn_mensal.parquet"   # entrada: RTN processada
SAIDA        = DATA_DIR / "contador_fiscal.json"          # saída: JSON para o contador
PREFIXO_DESP = "4. "   # prefixo da linha "Despesa Total" na RTN
MIN_MESES    = 24      # mínimo de meses históricos para o ratio ser estatisticamente válido


def _proximo_mes(ano: int, mes: int) -> tuple[int, int]:
    """
    Retorna o mês seguinte ao informado.
    Exemplo: _proximo_mes(2024, 12) → (2025, 1)
    """
    return (ano + 1, 1) if mes == 12 else (ano, mes + 1)


def calcular_contador() -> dict:
    """
    Lê a RTN, aplica a metodologia ratio rolling sobre 2 meses e retorna
    um dicionário com a previsão e a taxa por segundo.

    POR QUE 2 MESES?
      A RTN é publicada com ~1 mês de defasagem. Se hoje é maio e o último
      dado disponível é março, o contador precisa cobrir abril E maio para
      não ficar sem projeção no mês corrente.

      Projetamos sempre T+1 e T+2 (os dois meses após o último dado real):
        taxa = (previsão_T+1 + previsão_T+2) / (segundos_T+1 + segundos_T+2)

      Isso garante que o contador corre suavemente até o fim de T+2.
      Quando o Tesouro publica T+1 como dado real, rodamos novamente e
      o contador passa a cobrir T+2 e T+3 — e assim sucessivamente.

    Todas as chaves monetárias estão em R$ (não em R$ milhões).
    """
    if not RTN_PATH.exists():
        raise FileNotFoundError(
            f"RTN não encontrada em {RTN_PATH}. "
            "Execute `python pipelines/rtn/load.py` primeiro."
        )

    df = pd.read_parquet(RTN_PATH)

    # Filtra apenas a série "Despesa Total" (prefixo "4. ")
    # str.startswith() é tolerante a variações de espaçamento no texto
    serie_df = df[df["discriminacao"].str.startswith(PREFIXO_DESP)].copy()

    if len(serie_df) < MIN_MESES:
        raise ValueError(
            f"Histórico insuficiente: {len(serie_df)} meses "
            f"(mínimo necessário: {MIN_MESES})."
        )

    # Ordena por data e cria uma coluna datetime para facilitar os lookups
    serie_df = serie_df.sort_values(["ano", "mes"]).reset_index(drop=True)
    serie_df["data"] = pd.to_datetime(
        {"year": serie_df["ano"], "month": serie_df["mes"], "day": 1}
    )

    # Cria uma Series indexada por data para lookups eficientes: serie[data] → valor
    serie = serie_df.set_index("data")["corrente_milhoes"]

    # O "último dado disponível" é o mês mais recente na RTN com dado real
    ultima_data = serie.index.max()
    log.info("Último dado RTN disponível: %s", ultima_data.strftime("%Y-%m"))

    # ── Cálculo do ratio rolling ───────────────────────────────────────────
    # Janela RECENTE:  últimos 12 meses  (de t-11 até t, onde t = último dado)
    # Janela ANTERIOR: os 12 meses antes (de t-23 até t-12)
    # As duas janelas juntas cobrem 24 meses sem sobreposição.
    ini_recente  = ultima_data - pd.DateOffset(months=11)   # início da janela recente
    ini_anterior = ultima_data - pd.DateOffset(months=23)   # início da janela anterior

    def _soma_janela(ini: pd.Timestamp, n: int) -> tuple[float, int]:
        """
        Soma os valores de 'n' meses consecutivos a partir de 'ini'.
        Retorna (soma em R$ milhões, quantidade de meses com dados disponíveis).
        Meses ausentes na série são simplesmente ignorados.
        """
        # pd.date_range com freq="MS" gera o primeiro dia de cada mês
        datas   = pd.date_range(ini, periods=n, freq="MS")
        valores = [float(serie[d]) for d in datas if d in serie.index]
        return float(np.sum(valores)), len(valores)

    soma_rec, n_rec = _soma_janela(ini_recente,  12)
    soma_ant, n_ant = _soma_janela(ini_anterior, 12)

    # Verificações de sanidade antes de calcular o ratio:
    # - Precisamos de pelo menos 6 meses em cada janela para ser estatisticamente válido
    # - As somas não podem ser zero ou negativas (indicaria erro nos dados)
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

    # O ratio é simplesmente quanto os gastos cresceram de uma janela para a outra
    ratio = soma_rec / soma_ant

    # Se o ratio estiver fora da faixa [0,5; 3,0], algo está muito errado nos dados.
    # Ratio < 0,5 significaria que os gastos caíram mais de 50% (improvável).
    # Ratio > 3,0 significaria que triplicaram (também improvável sem mudança estrutural).
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

    # ── Identificação dos dois meses a projetar ───────────────────────────
    # T+1 = mês imediatamente após o último dado real da RTN
    # T+2 = mês seguinte a T+1
    # O contador começa no dia 1 de T+1 e vai até o fim de T+2.
    ano_t1, mes_t1 = _proximo_mes(ultima_data.year, ultima_data.month)
    ano_t2, mes_t2 = _proximo_mes(ano_t1, mes_t1)

    # Âncoras sazonais: mesmo mês do ano anterior para cada período projetado.
    # Subtrair 1 ano garante que capturamos a sazonalidade correta
    # (ex: dezembro sempre gasta muito mais que fevereiro).
    data_base_t1 = pd.Timestamp(year=ano_t1, month=mes_t1, day=1) - pd.DateOffset(years=1)
    data_base_t2 = pd.Timestamp(year=ano_t2, month=mes_t2, day=1) - pd.DateOffset(years=1)

    for label, data_base in [("T+1", data_base_t1), ("T+2", data_base_t2)]:
        if data_base not in serie.index:
            raise ValueError(
                f"Sem dado RTN para o mês base {label} "
                f"({data_base.strftime('%Y-%m')}). "
                "Histórico RTN insuficiente."
            )

    pago_base_t1 = float(serie[data_base_t1])
    pago_base_t2 = float(serie[data_base_t2])

    for label, v, data_base in [
        ("T+1", pago_base_t1, data_base_t1),
        ("T+2", pago_base_t2, data_base_t2),
    ]:
        if v <= 0:
            raise ValueError(
                f"Valor base {label} ({data_base.strftime('%Y-%m')}) "
                f"negativo ou zero: R$ {v:.2f} milhões. Revisar dados RTN."
            )

    # ── Previsão combinada dos 2 meses ────────────────────────────────────
    # Somamos as duas bases sazonais e aplicamos o ratio uma única vez.
    # Isso é equivalente a prever cada mês separadamente e somar os resultados,
    # pois o ratio é o mesmo para ambos.
    pago_base_2m_milhoes = pago_base_t1 + pago_base_t2
    previsao_2m_rs       = pago_base_2m_milhoes * ratio * 1_000_000

    # ── Taxa por segundo sobre os 2 meses ─────────────────────────────────
    # monthrange(ano, mes)[1] retorna o número de dias do mês.
    # Exemplo: monthrange(2026, 2)[1] = 28
    segundos_t1 = monthrange(ano_t1, mes_t1)[1] * 24 * 3600
    segundos_t2 = monthrange(ano_t2, mes_t2)[1] * 24 * 3600
    segundos_2m = segundos_t1 + segundos_t2

    # Taxa média ponderada pelo tempo dos dois meses.
    # O contador corre a esta taxa a partir do dia 1 de T+1 até o fim de T+2.
    taxa_por_segundo = previsao_2m_rs / segundos_2m

    log.info(
        "Previsão %04d-%02d a %04d-%02d: R$ %.2f bilhões | "
        "bases: R$ %.2f bi + R$ %.2f bi × ratio %.4f | R$ %.2f/segundo",
        ano_t1, mes_t1, ano_t2, mes_t2,
        previsao_2m_rs / 1e9,
        pago_base_t1 / 1e3,
        pago_base_t2 / 1e3,
        ratio,
        taxa_por_segundo,
    )

    return {
        # mes_referencia = T+1: onde o contador começa (usado como start_ms no dashboard)
        "mes_referencia":        f"{ano_t1:04d}-{mes_t1:02d}",
        # mes_referencia_fim = T+2: até onde o contador cobre
        "mes_referencia_fim":    f"{ano_t2:04d}-{mes_t2:02d}",
        "previsao_total_2m_rs":  round(previsao_2m_rs, 2),
        "taxa_por_segundo_rs":   round(taxa_por_segundo, 4),
        "segundos_2m":           segundos_2m,
        "ratio_rolling":         round(ratio, 6),
        "pago_base_2m_rs":       round(pago_base_2m_milhoes * 1_000_000, 2),
        "ultimo_dado_rtn":       ultima_data.strftime("%Y-%m"),
        # datetime.now(timezone.utc).isoformat() gera timestamp ISO 8601
        # ex: "2026-05-13T14:30:00+00:00"
        "gerado_em":             datetime.now(timezone.utc).isoformat(),
    }


def main():
    """Ponto de entrada: calcula e salva o JSON do contador."""
    resultado = calcular_contador()

    # Garante que a pasta data/ existe antes de tentar salvar
    SAIDA.parent.mkdir(parents=True, exist_ok=True)

    with open(SAIDA, "w", encoding="utf-8") as f:
        # indent=2 formata o JSON com indentação legível; ensure_ascii=False
        # preserva acentos e caracteres especiais do português
        json.dump(resultado, f, indent=2, ensure_ascii=False)

    log.info("Salvo: %s", SAIDA)
    log.info("Taxa por segundo: R$ %.2f", resultado["taxa_por_segundo_rs"])


if __name__ == "__main__":
    main()
