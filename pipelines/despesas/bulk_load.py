"""
pipelines/despesas/bulk_load.py  —  Carga histórica em bulk
─────────────────────────────────────────────────────────────
Baixa os ZIPs mensais do Portal da Transparência e converte
diretamente para Parquet no formato silver (sem camada raw).

Por que sem raw? Os CSVs têm dezenas de MB por mês; guardá-los
duplicaria o armazenamento sem ganho — o Parquet já é o arquivo
canônico comprimido e consultável.

URL de download:
  https://portaldatransparencia.gov.br/download-de-dados/despesas/{YYYYMM}{lote}
  Lote = número disponível no dropdown da página (normalmente "01").

Arquivos extraídos do ZIP:
  {YYYYMMDD}_Despesas_Empenho.csv   ← usado (granularidade por empenho)
  {YYYYMMDD}_Despesas_Liquidacao.csv
  {YYYYMMDD}_Despesas_Pagamento.csv
  (demais ignorados)

Estratégia de merge com incremental diário:
  - bulk_load escreve o Parquet inicial do mês
  - silver.py (incremental) faz upsert por id_empenho sobre esse Parquet
  - gold.py não muda — lê silver/*.parquet normalmente

Uso:
  python pipelines/despesas/bulk_load.py --ano 2024 --mes 1
  python pipelines/despesas/bulk_load.py --ano 2024
  python pipelines/despesas/bulk_load.py --historico
  python pipelines/despesas/bulk_load.py --ano 2024 --mes 1 --lote 02
"""

import argparse
import calendar
import io
import logging
import sys
import time
import urllib3
import warnings
import zipfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import (
    ANOS_HISTORICO,
    DATA_DIR,
    ORGAOS_ALTA_VIGILANCIA,
    RUBRICAS_ALTA_VIGILANCIA,
    COLUNAS_SILVER_DESPESAS,
    SSL_VERIFY_BULK,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SILVER_DIR = DATA_DIR / "despesas" / "silver"
SILVER_DIR.mkdir(parents=True, exist_ok=True)

BULK_BASE = "https://portaldatransparencia.gov.br/download-de-dados/despesas"

# ── Mapeamento coluna CSV → silver ─────────────────────────────────
# Baseado no dicionário de dados oficial do Portal da Transparência.
# Se um CSV vier com nomes diferentes, ajuste aqui (apenas aqui).
CAMPO_MAP_CSV = {
    "Id Empenho":                 "id_empenho",
    "Data Emissão":               "data_empenho",
    "Código Órgão":               "codigo_orgao",
    "Órgão":                      "nome_orgao",
    "Código Unidade Gestora":     "codigo_ug",
    "Unidade Gestora":            "nome_ug",
    "Código Função":              "codigo_funcao",
    "Função":                     "nome_funcao",
    # Atenção: CSV usa "SubFunção" com F maiúsculo
    "Código SubFunção":           "codigo_subfuncao",
    "SubFunção":                  "nome_subfuncao",
    "Código Programa":            "codigo_programa",
    "Programa":                   "nome_programa",
    "Código Ação":                "codigo_acao",
    "Ação":                       "nome_acao",
    # Elemento sozinho tem 2 dígitos; o código completo de 6 dígitos
    # (ex: "339039") é montado em _construir_codigo_natureza()
    "Código Elemento de Despesa": "_elem",
    "Elemento de Despesa":        "nome_natureza_despesa",
    "Código Categoria de Despesa": "_cat",
    "Código Grupo de Despesa":    "_grupo",
    "Código Modalidade de Aplicação": "_mod",
    "Modalidade de Licitação":    "modalidade_licitacao",
    "Valor Original do Empenho":  "valor_empenhado",
}

# Colunas auxiliares usadas apenas para montar codigo_natureza_despesa
_COLS_RUBRICA = ["_cat", "_grupo", "_mod", "_elem"]

# Arquivos e colunas de valor nos CSVs auxiliares do Portal da Transparência.
# A ligação empenho ↔ liquidação/pagamento fica nos arquivos _EmpenhosImpactados.csv.
_TIPO_LIQ = "Liquidacao_EmpenhosImpactados"
_TIPO_PAG  = "Pagamento_EmpenhosImpactados"
_COL_EMPENHO_IMPACTADO = "Código Empenho"
_COL_VALOR_LIQ = "Valor Liquidado (R$)"
_COL_VALOR_PAG = "Valor Pago (R$)"


# ── Extração de valores de liquidação/pagamento ────────────────────

def _extrair_valores_por_empenho(
    zip_bytes: bytes, tipo_arquivo: str, col_csv_valor: str
) -> "pd.Series":
    """
    Lê o CSV de liquidação ou pagamento do ZIP e retorna Series
    {id_empenho → soma dos valores} para fazer merge com o empenho.
    Retorna Series vazia se o arquivo ou a coluna não forem encontrados.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        candidatos = [
            n for n in zf.namelist()
            if tipo_arquivo in n and n.endswith(".csv") and "Item" not in n
        ]
        if not candidatos:
            log.warning("CSV '%s' não encontrado no ZIP.", tipo_arquivo)
            return pd.Series(dtype=float)

        nome_csv = candidatos[0]
        log.info("Lendo %s: %s", tipo_arquivo, nome_csv)
        with zf.open(nome_csv) as f:
            df = pd.read_csv(
                f, sep=";", encoding="latin-1", dtype=str, on_bad_lines="warn"
            )

    log.info("CSV %s: %d linhas, %d colunas", tipo_arquivo, len(df), len(df.columns))

    col_id = _COL_EMPENHO_IMPACTADO
    if col_id not in df.columns:
        log.warning("'%s' ausente em %s. Colunas: %s", col_id, tipo_arquivo, list(df.columns))
        return pd.Series(dtype=float)

    if col_csv_valor not in df.columns:
        alternativas = [c for c in df.columns if "Valor" in c and "Restos" not in c]
        if not alternativas:
            log.warning("Coluna de valor não encontrada em %s. Colunas: %s", tipo_arquivo, list(df.columns))
            return pd.Series(dtype=float)
        col_csv_valor = alternativas[0]
        log.warning("Coluna de valor ausente — usando '%s' como alternativa.", col_csv_valor)

    df["_id"] = df[col_id].astype(str).str.strip()
    df["_valor"] = df[col_csv_valor].apply(_parse_valor_br)
    return df.groupby("_id")["_valor"].sum()


# ── Download ───────────────────────────────────────────────────────

def _data_snapshot(ano: int, mes: int, lote: str | None) -> str:
    """
    Retorna a data do snapshot a usar no download.
    - lote explícito (ex: "15"): usa YYYYMM{lote}
    - mês já encerrado: dia "01" do mês — o Portal publica o snapshot
      mensal completo sempre no dia 1 de cada mês; os demais dias são
      incrementos diários com apenas um punhado de empenhos.
    - mês corrente: ontem (incremento mais recente disponível)
    """
    if lote:
        return f"{ano}{mes:02d}{lote}"
    hoje = date.today()
    if (ano, mes) < (hoje.year, hoje.month):
        return f"{ano}{mes:02d}01"
    else:
        ontem = hoje.replace(day=max(1, hoje.day - 1))
        return f"{ontem.year}{ontem.month:02d}{ontem.day:02d}"


def _baixar_zip(ano: int, mes: int, lote: str | None) -> bytes:
    data = _data_snapshot(ano, mes, lote)
    url = f"{BULK_BASE}/{data}"
    log.info("Baixando: %s", url)
    for tentativa in range(1, 4):
        try:
            resp = requests.get(url, verify=SSL_VERIFY_BULK, timeout=300, stream=True)
            if resp.status_code == 200:
                conteudo = resp.content
                log.info("Download concluído: %.1f MB", len(conteudo) / 1e6)
                return conteudo
            elif resp.status_code == 404:
                log.error("Arquivo não encontrado: %s (lote=%s disponível?)", url, lote)
                return b""
            else:
                log.error("HTTP %s para %s", resp.status_code, url)
                time.sleep(2 ** tentativa)
        except requests.exceptions.RequestException as exc:
            log.error("Erro de rede (tentativa %d/3): %s", tentativa, exc)
            time.sleep(2 ** tentativa)
    return b""


def _extrair_csv_empenho(zip_bytes: bytes, ano: int, mes: int, lote: str | None) -> pd.DataFrame:
    """Extrai e lê o CSV de empenhos de dentro do ZIP."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        nomes = zf.namelist()
        log.info("Arquivos no ZIP: %s", nomes)

        candidatos = [n for n in nomes if "Empenho" in n and n.endswith(".csv")
                      and "Item" not in n]
        if not candidatos:
            log.error("CSV de empenhos não encontrado no ZIP. Arquivos: %s", nomes)
            return pd.DataFrame()

        nome_csv = candidatos[0]
        log.info("Lendo: %s", nome_csv)

        with zf.open(nome_csv) as f:
            # Portal usa latin-1 e separador ";"
            df = pd.read_csv(
                f,
                sep=";",
                encoding="latin-1",
                dtype=str,
                on_bad_lines="warn",
            )

    log.info("CSV carregado: %d linhas, %d colunas", len(df), len(df.columns))

    # Alerta sobre colunas não mapeadas (útil para detectar mudanças no schema)
    nao_mapeadas = [c for c in df.columns if c not in CAMPO_MAP_CSV]
    if nao_mapeadas:
        log.debug("Colunas do CSV não mapeadas para silver (ignoradas): %s", nao_mapeadas)

    ausentes = [c for c in CAMPO_MAP_CSV if c not in df.columns]
    if ausentes:
        log.warning("Colunas esperadas ausentes no CSV: %s", ausentes)

    return df


# ── Transformação ──────────────────────────────────────────────────

def _parse_valor_br(v) -> float:
    if v is None or (isinstance(v, float)):
        return v or 0.0
    s = str(v).strip()
    if not s or s == "nan":
        return 0.0
    return float(s.replace(".", "").replace(",", "."))


def _construir_codigo_natureza(df: pd.DataFrame) -> pd.Series:
    """
    Monta o código de 6 dígitos da natureza de despesa.
    Formato SIAFI: Categoria(1) + Grupo(1) + Modalidade(2) + Elemento(2)
    Exemplo: 3 + 3 + 90 + 39 = "339039"
    """
    partes = [
        df["_cat"].fillna("").astype(str).str.strip().str.zfill(1),
        df["_grupo"].fillna("").astype(str).str.strip().str.zfill(1),
        df["_mod"].fillna("").astype(str).str.strip().str.zfill(2),
        df["_elem"].fillna("").astype(str).str.strip().str.zfill(2),
    ]
    return partes[0] + partes[1] + partes[2] + partes[3]


def _transformar_csv_para_silver(
    df_raw: pd.DataFrame,
    ano: int,
    mes: int,
    liq_por_empenho: "pd.Series | None" = None,
    pag_por_empenho: "pd.Series | None" = None,
) -> pd.DataFrame:
    if df_raw.empty:
        return pd.DataFrame(columns=COLUNAS_SILVER_DESPESAS)

    df = df_raw.rename(columns=CAMPO_MAP_CSV)

    # Monta código completo da natureza de despesa antes de remover colunas auxiliares
    if all(c in df.columns for c in _COLS_RUBRICA):
        df["codigo_natureza_despesa"] = _construir_codigo_natureza(df)
        df["codigo_elemento"] = df["_elem"].fillna("").astype(str).str.strip().str.zfill(2)
        df = df.drop(columns=_COLS_RUBRICA)
    else:
        log.warning("Colunas de rubrica ausentes: %s", [c for c in _COLS_RUBRICA if c not in df.columns])

    for col in COLUNAS_SILVER_DESPESAS:
        if col not in df.columns:
            df[col] = None

    df["data_empenho"] = pd.to_datetime(df["data_empenho"], dayfirst=True, errors="coerce")
    df["ano"] = ano
    df["mes"] = mes

    df["valor_empenhado"] = df["valor_empenhado"].apply(_parse_valor_br)

    # Liquidado/pago vêm dos arquivos _EmpenhosImpactados.csv, que usam o código
    # SIAFI ("Código Empenho"), não o "Id Empenho" numérico do CSV de empenhos.
    siafi_col = (
        df_raw["Código Empenho"].astype(str).str.strip()
        if "Código Empenho" in df_raw.columns
        else None
    )
    if liq_por_empenho is not None and not liq_por_empenho.empty and siafi_col is not None:
        df["valor_liquidado"] = pd.Series(siafi_col.values).map(liq_por_empenho).fillna(0.0).values
    else:
        df["valor_liquidado"] = 0.0

    if pag_por_empenho is not None and not pag_por_empenho.empty and siafi_col is not None:
        df["valor_pago"] = pd.Series(siafi_col.values).map(pag_por_empenho).fillna(0.0).values
    else:
        df["valor_pago"] = 0.0

    for col_cod in ["codigo_orgao", "codigo_ug", "codigo_subfuncao", "codigo_funcao"]:
        if col_cod in df.columns and df[col_cod].notna().any():
            df[col_cod] = df[col_cod].astype(str).str.strip()

    antes = len(df)
    df = df.drop_duplicates(subset=["id_empenho"], keep="last")
    if (antes - len(df)):
        log.warning("Removidas %d duplicatas por id_empenho", antes - len(df))

    df["fonte_rubrica_flag"] = df["codigo_natureza_despesa"].isin(
        RUBRICAS_ALTA_VIGILANCIA.keys()
    )
    df["orgao_vigilancia_flag"] = df["codigo_orgao"].isin(
        ORGAOS_ALTA_VIGILANCIA.keys()
    )
    df["ingested_at"] = datetime.utcnow()

    colunas_presentes = [c for c in COLUNAS_SILVER_DESPESAS if c in df.columns]
    return df[colunas_presentes]


# ── Persistência ───────────────────────────────────────────────────

def _salvar_silver(df: pd.DataFrame, ano: int, mes: int) -> Path:
    destino = SILVER_DIR / f"despesas_{ano}_{mes:02d}.parquet"

    if destino.exists():
        # Upsert: carrega existente, substitui registros com mesmo id_empenho
        df_existente = pd.read_parquet(destino)
        ids_novos = set(df["id_empenho"].dropna())
        df_existente = df_existente[~df_existente["id_empenho"].isin(ids_novos)]
        df_final = pd.concat([df_existente, df], ignore_index=True)
        log.info("Upsert: %d novos/atualizados sobre %d existentes → %d total",
                 len(df), len(df_existente), len(df_final))
    else:
        df_final = df

    df_final.to_parquet(destino, index=False, engine="pyarrow", compression="snappy")
    log.info("Silver salvo: %s | %d registros | %.1f MB",
             destino.name, len(df_final), destino.stat().st_size / 1e6)
    return destino


# ── Orquestração ───────────────────────────────────────────────────

def processar_mes(ano: int, mes: int, lote: str | None) -> None:
    zip_bytes = _baixar_zip(ano, mes, lote)
    if not zip_bytes:
        log.error("Falha ao baixar %d/%02d — pulando.", ano, mes)
        return

    df_raw = _extrair_csv_empenho(zip_bytes, ano, mes, lote)
    if df_raw.empty:
        log.error("CSV vazio para %d/%02d — pulando.", ano, mes)
        return

    liq_por_empenho = _extrair_valores_por_empenho(zip_bytes, _TIPO_LIQ, _COL_VALOR_LIQ)
    pag_por_empenho = _extrair_valores_por_empenho(zip_bytes, _TIPO_PAG, _COL_VALOR_PAG)

    df_silver = _transformar_csv_para_silver(df_raw, ano, mes, liq_por_empenho, pag_por_empenho)
    _salvar_silver(df_silver, ano, mes)


def main():
    parser = argparse.ArgumentParser(
        description="Carga histórica bulk de despesas (Portal da Transparência)"
    )
    parser.add_argument("--ano", type=int, default=date.today().year)
    parser.add_argument("--mes", type=int, default=None,
                        help="Mês específico (1-12). Omitir para todos os meses do ano.")
    parser.add_argument("--historico", action="store_true",
                        help=f"Baixa os últimos {ANOS_HISTORICO} anos completos")
    parser.add_argument("--lote", default=None,
                        help="Dia do snapshot (DD). Padrão: último dia do mês (meses encerrados) "
                             "ou ontem (mês corrente).")
    args = parser.parse_args()

    if args.historico:
        hoje = date.today()
        pares = [
            (ano, mes)
            for ano in range(hoje.year - ANOS_HISTORICO, hoje.year + 1)
            for mes in range(1, 13)
            if date(ano, mes, 1) <= hoje
        ]
    elif args.mes is None:
        hoje = date.today()
        pares = [
            (args.ano, mes) for mes in range(1, 13)
            if date(args.ano, mes, 1) <= hoje
        ]
    else:
        pares = [(args.ano, args.mes)]

    log.info("Bulk load: %d meses a processar (lote=%s)", len(pares), args.lote)
    for ano, mes in pares:
        log.info("── %d/%02d ──────────────────────────────", ano, mes)
        processar_mes(ano, mes, args.lote)

    log.info("Bulk load concluído.")


if __name__ == "__main__":
    main()
