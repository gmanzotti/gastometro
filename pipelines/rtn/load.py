"""
pipelines/rtn/load.py  —  RTN: Resultado do Tesouro Nacional
─────────────────────────────────────────────────────────────
Baixa o Excel da série histórica do RTN do Tesouro Nacional e produz um
único Parquet tidy com três métricas:

  corrente_milhoes  — R$ milhões correntes (nominal)
  constante_milhoes — R$ milhões deflacionados pelo IPCA (base = mês mais recente)
  pct_pib           — valor mensal como % do PIB anual  (corrente / pib_mensal)

Uso:
  python pipelines/rtn/load.py
"""

import io
import json
import logging
import re
import sys
from datetime import datetime as _dt
from pathlib import Path

import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

URL_RTN = (
    "http://sisweb.tesouro.gov.br/apex/cosis/thot/link/rtn/serie-historica?conteudo=cdn"
)

RTN_DIR  = DATA_DIR / "rtn"
RTN_DIR.mkdir(parents=True, exist_ok=True)
DESTINO   = RTN_DIR / "rtn_mensal.parquet"
META_FILE = RTN_DIR / "metadata.json"


# ── Download ───────────────────────────────────────────────────────

def baixar_excel() -> bytes:
    log.info("Baixando RTN: %s", URL_RTN)
    r = requests.get(URL_RTN, timeout=120, verify=False)
    r.raise_for_status()
    log.info("Download concluído: %.1f MB", len(r.content) / 1e6)
    return r.content


# ── Leitura de abas ────────────────────────────────────────────────

def _ler_aba_mensal(xl: pd.ExcelFile, aba: str) -> pd.DataFrame:
    """
    Lê uma aba de série mensal (cabeçalho na linha 4).
    Retorna DataFrame wide: col 'discriminacao' + colunas datetime por mês.
    Remove notas de rodapé verificando se a 2ª coluna é numérica.
    """
    df = pd.read_excel(xl, sheet_name=aba, header=4)
    df = df.rename(columns={df.columns[0]: "discriminacao"})
    df = df[pd.to_numeric(df.iloc[:, 1], errors="coerce").notna()].copy()
    return df


def _ler_aba_anual(xl: pd.ExcelFile, aba: str) -> pd.DataFrame:
    """
    Lê uma aba de série anual (cabeçalho na linha 4, colunas = anos inteiros).
    """
    df = pd.read_excel(xl, sheet_name=aba, header=4)
    df = df.rename(columns={df.columns[0]: "discriminacao"})
    df = df[pd.to_numeric(df.iloc[:, 1], errors="coerce").notna()].copy()
    return df


def _melt_mensal(df_wide: pd.DataFrame, col_valor: str) -> pd.DataFrame:
    """Transforma DataFrame wide mensal em formato long (tidy)."""
    colunas_data = [c for c in df_wide.columns if isinstance(c, (pd.Timestamp, _dt))]
    df = df_wide.melt(
        id_vars=["discriminacao"],
        value_vars=colunas_data,
        var_name="data",
        value_name=col_valor,
    )
    df["data"] = pd.to_datetime(df["data"])
    df[col_valor] = pd.to_numeric(df[col_valor], errors="coerce")
    df["discriminacao"] = df["discriminacao"].astype(str).str.strip()
    return df


# ── Derivação do PIB anual ─────────────────────────────────────────

def _computar_pib_por_ano(
    df_anual_corr: pd.DataFrame, df_anual_pib: pd.DataFrame
) -> dict:
    """
    Deriva o PIB anual (R$ milhões) a partir das abas 2.1 e 2.1-A.

    A aba 2.1-A armazena os valores como proporção decimal do PIB
    (ex: 0.2274 = 22,74% do PIB), não como porcentagem.
    Portanto: PIB_ano = corrente_ano / decimal_pib

    Usa 'Receita Total' (prefixo '1. ') como série de referência.
    """
    rec_c = df_anual_corr[df_anual_corr["discriminacao"].str.startswith("1. ")].set_index("discriminacao")
    rec_p = df_anual_pib[df_anual_pib["discriminacao"].str.startswith("1. ")].set_index("discriminacao")

    anos_c = {int(c) for c in rec_c.columns if isinstance(c, (int, float)) and not pd.isna(c)}
    anos_p = {int(c) for c in rec_p.columns if isinstance(c, (int, float)) and not pd.isna(c)}
    anos   = sorted(anos_c & anos_p)

    pib: dict = {}
    for ano in anos:
        try:
            v_corr = float(rec_c[ano].iloc[0])
            v_pct  = float(rec_p[ano].iloc[0])
            if v_pct and v_pct != 0:
                pib[ano] = v_corr / v_pct
        except Exception:
            pass

    if pib:
        ano_max = max(pib)
        log.info(
            "PIB anual calculado: %d anos (último: %d = R$ %.0f bi)",
            len(pib), ano_max, pib[ano_max] / 1e3,
        )

    # Projeção para ano seguinte ao último disponível (crescimento nominal histórico ~8%)
    if pib:
        ultimo = max(pib)
        if ultimo + 1 not in pib:
            pib[ultimo + 1] = pib[ultimo] * 1.08

    return pib


# ── Transformação principal ────────────────────────────────────────

def transformar(conteudo: bytes) -> tuple:
    xl = pd.ExcelFile(io.BytesIO(conteudo))

    # Série nominal e real mensais
    df_corr_wide = _ler_aba_mensal(xl, "1.1")
    df_cons_wide = _ler_aba_mensal(xl, "1.1-A")

    # Extrai rótulo do período-base da constante (ex: "Mar/2026")
    titulo_unidade = str(pd.read_excel(xl, sheet_name="1.1-A", header=None).iloc[2, 0])
    m = re.search(r"Valores de (\w{3}/\d{4})", titulo_unidade)
    base_constante = m.group(1) if m else "base IPCA"

    # Séries anuais para derivar PIB
    df_anual_corr = _ler_aba_anual(xl, "2.1")
    df_anual_pib  = _ler_aba_anual(xl, "2.1-A")
    pib_por_ano   = _computar_pib_por_ano(df_anual_corr, df_anual_pib)

    # Melt de cada série
    df_c = _melt_mensal(df_corr_wide, "corrente_milhoes")
    df_k = _melt_mensal(df_cons_wide, "constante_milhoes")

    # Merge pelo par (discriminacao, data)
    df = df_c.merge(
        df_k[["discriminacao", "data", "constante_milhoes"]],
        on=["discriminacao", "data"],
        how="left",
    )

    df["ano"] = df["data"].dt.year
    df["mes"] = df["data"].dt.month

    # % do PIB: valor mensal / (PIB anual / 12)
    # Expressa quanto aquele gasto/receita representa do PIB mensal médio.
    # Multiplicando por 12 obtém-se o valor anualizado — padrão no monitoramento fiscal.
    pib_mensal = df["ano"].map(pib_por_ano) / 12
    df["pct_pib"] = (df["corrente_milhoes"] / pib_mensal * 100).round(4)

    df["data"] = df["data"].dt.date

    ultima  = df["data"].max()
    n_series = df["discriminacao"].nunique()
    log.info(
        "RTN: %d séries × %d meses = %d linhas | até %s",
        n_series, df["data"].nunique(), len(df), ultima,
    )

    meta = {"base_constante": base_constante, "ultima_data": str(ultima)}
    cols = ["ano", "mes", "data", "discriminacao",
            "corrente_milhoes", "constante_milhoes", "pct_pib"]
    return (
        df[cols].sort_values(["discriminacao", "ano", "mes"]).reset_index(drop=True),
        meta,
    )


# ── Main ───────────────────────────────────────────────────────────

def main():
    conteudo = baixar_excel()
    df, meta = transformar(conteudo)
    df.to_parquet(DESTINO, index=False)
    META_FILE.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Salvo: %s | %d linhas", DESTINO.name, len(df))
    log.info("Metadados: %s", meta)


if __name__ == "__main__":
    main()
