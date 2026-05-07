"""
pipelines/despesas/gold.py  —  Operário 3: Regras de Negócio
──────────────────────────────────────────────────────────────
Lê todos os Parquet silver e produz tabelas gold otimizadas para o dashboard:

  gold/despesas_mensal_orgao.parquet      — gasto mensal por órgão
  gold/despesas_mensal_natureza.parquet   — gasto mensal por natureza de despesa
  gold/despesas_vigilancia.parquet        — rubricas/órgãos de alta vigilância
  gold/anomalias.parquet                  — registros com z-score acima do threshold

Regras de negócio desta camada:
  - Agregação mensal (empenho → gasto total)
  - Cálculo de variação percentual mês/mês e ano/ano
  - Cálculo de z-score para detecção de anomalia
  - Acumulado no ano (YTD)
  - Percentual do orçamento consumido (requer dados SIOP — stub por ora)

Uso:
  python pipelines/despesas/gold.py
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import (
    ALERTA_ZSCORE_CRITICO,
    ALERTA_ZSCORE_PADRAO,
    DATA_DIR,
    JANELA_ANOMALIA_MESES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SILVER_DIR = DATA_DIR / "despesas" / "silver"
GOLD_DIR   = DATA_DIR / "despesas" / "gold"
GOLD_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────

def _carregar_silver_completo() -> pd.DataFrame:
    """Carrega todos os Parquet silver em um único DataFrame."""
    arquivos = sorted(SILVER_DIR.glob("despesas_*.parquet"))
    if not arquivos:
        log.error("Nenhum arquivo silver encontrado. Execute silver.py primeiro.")
        sys.exit(1)

    dfs = [pd.read_parquet(arq) for arq in arquivos]
    df = pd.concat(dfs, ignore_index=True)
    log.info("Silver carregado: %d registros, %d arquivos", len(df), len(arquivos))

    # Normaliza codigo_orgao: bulk_load grava 5 dígitos ("20101"), silver.py
    # zero-padeia para 6 ("020101"). Stripping leading zeros garante que ambas
    # as origens agrupem no mesmo órgão durante a agregação gold.
    if "codigo_orgao" in df.columns:
        df["codigo_orgao"] = (
            df["codigo_orgao"].astype(str).str.strip().str.lstrip("0")
        )

    return df


def _calcular_zscore_mensal(
    df_mensal: pd.DataFrame,
    coluna_grupo: str,
    coluna_valor: str = "valor_empenhado",
    janela: int = JANELA_ANOMALIA_MESES,
) -> pd.DataFrame:
    """
    Calcula z-score rolling por grupo (ex: por órgão).

    Z-score mede quantos desvios-padrão um valor está acima ou abaixo da média
    histórica. É a métrica central de detecção de anomalia do projeto.

    Fórmula: z = (valor_mês - média_janela) / desvio_padrão_janela

    O .shift(1) garante que o mês atual NÃO entra no cálculo da própria média
    (evita "vazar" o futuro para o passado — data leakage).
    min_periods=3: exige ao menos 3 meses de histórico para calcular.
    Grupos com menos de 3 obs recebem z-score NaN (sem histórico suficiente).
    """
    df = df_mensal.sort_values([coluna_grupo, "ano", "mes"]).copy()
    df["periodo"] = df["ano"] * 100 + df["mes"]

    def zscore_grupo(g: pd.DataFrame) -> pd.Series:
        vals  = g[coluna_valor]
        media = vals.shift(1).rolling(janela, min_periods=3).mean()
        std   = vals.shift(1).rolling(janela, min_periods=3).std()
        return (vals - media) / std.replace(0, np.nan)

    # Nota: .apply() retorna uma Series com o índice original do df.
    # Versões recentes do pandas não aceitam indexar o resultado por nome de coluna.
    df["zscore"] = df.groupby(coluna_grupo, group_keys=False).apply(zscore_grupo)
    return df


def _variacao_pct(atual: pd.Series, anterior: pd.Series) -> pd.Series:
    """Variação percentual segura (evita divisão por zero)."""
    return ((atual - anterior) / anterior.abs().replace(0, np.nan) * 100).round(2)


# ── Tabelas Gold ───────────────────────────────────────────────────

def gerar_mensal_orgao(df: pd.DataFrame) -> pd.DataFrame:
    """Gasto mensal agregado por órgão, com variações e z-score."""
    log.info("Gerando gold: mensal por órgão...")

    # Usa apenas empenhos individuais do bulk_load (id_empenho numérico).
    # Registros da API /por-orgao (prefixo "api_") são excluídos: a API retorna
    # o total anual idêntico para qualquer mês consultado no passado, tornando
    # inviável derivar valores mensais por delta.
    df = df[~df["id_empenho"].astype(str).str.startswith("api_")]

    # Nome mais longo = mais completo (API tem nome completo, CSV pode truncar)
    nomes = (
        df.dropna(subset=["codigo_orgao", "nome_orgao"])
        .assign(_len=df["nome_orgao"].str.len())
        .sort_values("_len", ascending=False)
        .drop_duplicates("codigo_orgao")[["codigo_orgao", "nome_orgao"]]
    )

    agg = (
        df.groupby(["ano", "mes", "codigo_orgao"], as_index=False)
        .agg(
            valor_empenhado=("valor_empenhado", "sum"),
            valor_liquidado=("valor_liquidado", "sum"),
            valor_pago=("valor_pago", "sum"),
            qtd_empenhos=("id_empenho", "count"),
        )
    )

    agg = agg.merge(nomes, on="codigo_orgao", how="left")

    agg = agg.sort_values(["codigo_orgao", "ano", "mes"]).reset_index(drop=True)

    # Variação mês/mês
    agg["valor_pago_mes_anterior"] = agg.groupby("codigo_orgao")["valor_pago"].shift(1)
    agg["variacao_mom_pct"] = _variacao_pct(agg["valor_pago"], agg["valor_pago_mes_anterior"])

    # Variação ano/ano (mesmo mês, ano anterior)
    agg["periodo"] = agg["ano"] * 100 + agg["mes"]
    agg_anterior = agg[["codigo_orgao", "ano", "mes", "valor_pago"]].copy()
    agg_anterior["ano"] += 1
    agg_anterior = agg_anterior.rename(columns={"valor_pago": "valor_pago_ano_anterior"})
    agg = agg.merge(agg_anterior, on=["codigo_orgao", "ano", "mes"], how="left")
    agg["variacao_yoy_pct"] = _variacao_pct(agg["valor_pago"], agg["valor_pago_ano_anterior"])

    # Acumulado no ano (YTD)
    agg["ytd_valor_pago"] = agg.groupby(["codigo_orgao", "ano"])["valor_pago"].cumsum()

    # Z-score
    agg = _calcular_zscore_mensal(agg, "codigo_orgao")

    # Nível de alerta
    agg["nivel_alerta"] = "normal"
    agg.loc[agg["zscore"].abs() >= ALERTA_ZSCORE_PADRAO,  "nivel_alerta"] = "amarelo"
    agg.loc[agg["zscore"].abs() >= ALERTA_ZSCORE_CRITICO, "nivel_alerta"] = "vermelho"

    saida = GOLD_DIR / "despesas_mensal_orgao.parquet"
    agg.to_parquet(saida, index=False)
    log.info("Salvo: %s (%d linhas)", saida.name, len(agg))
    return agg


def gerar_mensal_natureza(df: pd.DataFrame) -> pd.DataFrame:
    """Gasto mensal por natureza de despesa (elemento de despesa)."""
    log.info("Gerando gold: mensal por natureza de despesa...")

    agg = (
        df.groupby(
            ["ano", "mes", "codigo_natureza_despesa", "nome_natureza_despesa",
             "fonte_rubrica_flag"],
            as_index=False,
        )
        .agg(
            valor_empenhado=("valor_empenhado", "sum"),
            valor_liquidado=("valor_liquidado", "sum"),
            valor_pago=("valor_pago", "sum"),
            qtd_empenhos=("id_empenho", "count"),
        )
    )

    agg = agg.sort_values(["codigo_natureza_despesa", "ano", "mes"]).reset_index(drop=True)

    # Variação mês/mês (mesmo cálculo do órgão — facilita comparação no dashboard)
    agg["valor_emp_mes_anterior"] = agg.groupby("codigo_natureza_despesa")["valor_empenhado"].shift(1)
    agg["variacao_mom_pct"] = _variacao_pct(agg["valor_empenhado"], agg["valor_emp_mes_anterior"])
    agg = agg.drop(columns="valor_emp_mes_anterior")

    agg = _calcular_zscore_mensal(agg, "codigo_natureza_despesa")

    agg["nivel_alerta"] = "normal"
    agg.loc[agg["zscore"].abs() >= ALERTA_ZSCORE_PADRAO,  "nivel_alerta"] = "amarelo"
    agg.loc[agg["zscore"].abs() >= ALERTA_ZSCORE_CRITICO, "nivel_alerta"] = "vermelho"

    saida = GOLD_DIR / "despesas_mensal_natureza.parquet"
    agg.to_parquet(saida, index=False)
    log.info("Salvo: %s (%d linhas)", saida.name, len(agg))
    return agg


def gerar_vigilancia(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tabela focada: apenas registros de alta vigilância (rubricas sensíveis × órgãos sensíveis).
    Granularidade de empenho — não agrega — para auditoria individual.
    """
    log.info("Gerando gold: tabela de vigilância...")

    mask = df["fonte_rubrica_flag"] | df["orgao_vigilancia_flag"]
    df_vig = df[mask].copy()

    # Ordena por valor descrescente para facilitar visualização
    df_vig = df_vig.sort_values(["ano", "mes", "valor_pago"], ascending=[True, True, False])

    saida = GOLD_DIR / "despesas_vigilancia.parquet"
    df_vig.to_parquet(saida, index=False)
    log.info("Salvo: %s (%d registros de vigilância)", saida.name, len(df_vig))
    return df_vig


def gerar_anomalias(df_orgao: pd.DataFrame, df_natureza: pd.DataFrame) -> pd.DataFrame:
    """Consolida todos os alertas ativos (z-score ≥ threshold) em uma tabela única."""
    log.info("Gerando gold: tabela de anomalias...")

    # Anomalias por órgão
    mask_o = df_orgao["nivel_alerta"].isin(["amarelo", "vermelho"])
    anoms_orgao = df_orgao[mask_o][
        ["ano", "mes", "codigo_orgao", "nome_orgao",
         "valor_pago", "variacao_mom_pct", "variacao_yoy_pct",
         "zscore", "nivel_alerta"]
    ].copy()
    anoms_orgao["tipo_anomalia"] = "orgao"
    anoms_orgao = anoms_orgao.rename(columns={
        "codigo_orgao": "codigo", "nome_orgao": "nome"
    })

    # Anomalias por natureza de despesa
    mask_n = df_natureza["nivel_alerta"].isin(["amarelo", "vermelho"])
    anoms_nat = df_natureza[mask_n][
        ["ano", "mes", "codigo_natureza_despesa", "nome_natureza_despesa",
         "valor_pago", "zscore", "nivel_alerta", "fonte_rubrica_flag"]
    ].copy()
    anoms_nat["tipo_anomalia"] = "natureza_despesa"
    anoms_nat["variacao_mom_pct"] = None
    anoms_nat["variacao_yoy_pct"] = None
    anoms_nat = anoms_nat.rename(columns={
        "codigo_natureza_despesa": "codigo",
        "nome_natureza_despesa": "nome",
    })

    anomalias = pd.concat(
        [anoms_orgao, anoms_nat[anoms_orgao.columns]], ignore_index=True
    )
    anomalias = anomalias.sort_values(
        ["nivel_alerta", "zscore"], ascending=[True, False]
    )

    saida = GOLD_DIR / "anomalias.parquet"
    anomalias.to_parquet(saida, index=False)
    log.info(
        "Salvo: %s | %d anomalias (%d vermelhas, %d amarelas)",
        saida.name,
        len(anomalias),
        (anomalias["nivel_alerta"] == "vermelho").sum(),
        (anomalias["nivel_alerta"] == "amarelo").sum(),
    )
    return anomalias


def main():
    df_silver = _carregar_silver_completo()

    df_orgao    = gerar_mensal_orgao(df_silver)
    df_natureza = gerar_mensal_natureza(df_silver)
    _             = gerar_vigilancia(df_silver)
    _             = gerar_anomalias(df_orgao, df_natureza)

    log.info("Gold completo. Tabelas disponíveis em: %s", GOLD_DIR)


if __name__ == "__main__":
    main()
