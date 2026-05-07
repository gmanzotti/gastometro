"""
pipelines/despesas/silver.py  —  Operário 2: Padronização
──────────────────────────────────────────────────────────
Lê os JSON brutos de data/despesas/raw/, limpa, tipifica e salva
Parquet completo (granularidade de empenho) em data/despesas/silver/.

Regras desta camada:
  - Renomear colunas para snake_case padronizado
  - Converter tipos (datas, valores monetários, códigos)
  - Remover duplicatas pelo id_empenho
  - Adicionar ingested_at, fonte_rubrica_flag, orgao_vigilancia_flag
  - NÃO aplicar regras de negócio nem agregações (isso é Gold)

Uso:
  python pipelines/despesas/silver.py --ano 2024 --mes 5
  python pipelines/despesas/silver.py --ano 2024
  python pipelines/despesas/silver.py --reprocessar-tudo
"""

import argparse
import json
import logging
import sys
from datetime import datetime, date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import (
    COLUNAS_SILVER_DESPESAS,
    DATA_DIR,
    ORGAOS_ALTA_VIGILANCIA,
    RUBRICAS_ALTA_VIGILANCIA,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RAW_DIR    = DATA_DIR / "despesas" / "raw"
SILVER_DIR = DATA_DIR / "despesas" / "silver"
SILVER_DIR.mkdir(parents=True, exist_ok=True)


# ── Mapeamento de campos API → silver ─────────────────────────────
# Os nomes dos campos da API podem mudar — centralize o mapeamento aqui.
# Chave = nome retornado pela API, Valor = nome padronizado no silver.
CAMPO_MAP = {
    # Campos camelCase do endpoint /empenhos (não liberado ainda — uso futuro)
    "id": "id_empenho",
    "dataEmissao": "data_empenho",
    "codigoOrgao": "codigo_orgao",
    "nomeOrgao": "nome_orgao",
    "codigoUnidadeGestora": "codigo_ug",
    "nomeUnidadeGestora": "nome_ug",
    "codigoFuncao": "codigo_funcao",
    "nomeFuncao": "nome_funcao",
    "codigoSubfuncao": "codigo_subfuncao",
    "nomeSubfuncao": "nome_subfuncao",
    "codigoPrograma": "codigo_programa",
    "nomePrograma": "nome_programa",
    "codigoAcao": "codigo_acao",
    "nomeAcao": "nome_acao",
    "codigoCategoriaDespesa": "codigo_natureza_despesa",
    "nomeCategoriaDespesa": "nome_natureza_despesa",
    "codigoElementoDespesa": "codigo_elemento",
    "nomeElementoDespesa": "nome_elemento",
    "modalidadeLicitacao": "modalidade_licitacao",
    "valorEmpenhado": "valor_empenhado",
    "valorLiquidado": "valor_liquidado",
    "valorPago": "valor_pago",
    # Campos do endpoint /despesas/por-orgao (em uso atualmente)
    # A API retorna nomes sem prefixo "valor" e sem camelCase para os totais
    "codigoOrgaoSuperior": "codigo_orgao_superior",
    "orgao": "nome_orgao",
    "orgaoSuperior": "nome_orgao_superior",
    "empenhado": "valor_empenhado",
    "liquidado": "valor_liquidado",
    "pago": "valor_pago",
}


def _parse_valor_br(v) -> float:
    """Converte string monetária BR ('1.234.567,89') para float."""
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace(".", "").replace(",", "."))


def transformar_raw_para_silver(dados_raw: list[dict], ano: int, mes: int) -> pd.DataFrame:
    """Aplica limpeza e padronização sobre lista de registros brutos."""

    if not dados_raw:
        return pd.DataFrame(columns=COLUNAS_SILVER_DESPESAS)

    df = pd.DataFrame(dados_raw)

    # 1. Renomear colunas conforme mapeamento (ignora colunas não mapeadas)
    df = df.rename(columns=CAMPO_MAP)

    # 2. Garantir que todas as colunas esperadas existam (preenche com NaN/None)
    for col in COLUNAS_SILVER_DESPESAS:
        if col not in df.columns:
            df[col] = None

    # 3. Tipagem
    df["data_empenho"] = pd.to_datetime(df["data_empenho"], dayfirst=True, errors="coerce")
    df["ano"] = ano
    df["mes"] = mes

    for col_val in ["valor_empenhado", "valor_liquidado", "valor_pago"]:
        df[col_val] = df[col_val].apply(_parse_valor_br)

    # Códigos sempre como string (evita int 0 vs "00" etc.)
    for col_cod in ["codigo_orgao", "codigo_ug", "codigo_elemento",
                    "codigo_natureza_despesa", "codigo_funcao", "codigo_subfuncao"]:
        if col_cod in df.columns:
            df[col_cod] = df[col_cod].astype(str).str.strip().str.zfill(6)

    # 4. Para registros da API /por-orgao que não têm id de empenho individual,
    #    usa um ID sintético baseado no órgão para o upsert funcionar corretamente.
    #    Prefixo "api_" distingue esses registros dos empenhos individuais do bulk_load.
    if df["id_empenho"].isna().all() and "codigo_orgao" in df.columns:
        df["id_empenho"] = "api_" + df["codigo_orgao"].astype(str)
        log.info("Dados da API /por-orgao: id_empenho sintético atribuído (%d registros)", len(df))

    antes = len(df)
    df = df.drop_duplicates(subset=["id_empenho"], keep="last")
    duplicatas = antes - len(df)
    if duplicatas:
        log.warning("Removidos %d registros duplicados (mesmo id_empenho)", duplicatas)

    # 5. Flags de vigilância
    df["fonte_rubrica_flag"] = df["codigo_natureza_despesa"].isin(
        RUBRICAS_ALTA_VIGILANCIA.keys()
    )
    df["orgao_vigilancia_flag"] = df["codigo_orgao"].isin(
        ORGAOS_ALTA_VIGILANCIA.keys()
    )

    # 6. Timestamp de ingestão (auditoria)
    df["ingested_at"] = datetime.utcnow()

    # 7. Selecionar apenas colunas do schema silver (ordem importa para leitura)
    colunas_presentes = [c for c in COLUNAS_SILVER_DESPESAS if c in df.columns]
    return df[colunas_presentes]


def processar_arquivo(caminho_raw: Path) -> None:
    """Lê um arquivo raw, transforma e salva silver com upsert por id_empenho."""
    log.info("Processando: %s", caminho_raw.name)

    with open(caminho_raw, encoding="utf-8") as f:
        envelope = json.load(f)

    meta = envelope.get("meta", {})
    dados = envelope.get("dados", [])
    ano = meta.get("ano")
    mes = meta.get("mes")

    if not dados:
        log.warning("Arquivo vazio: %s", caminho_raw.name)
        return

    df_novo = transformar_raw_para_silver(dados, ano, mes)

    destino = SILVER_DIR / f"despesas_{ano}_{mes:02d}.parquet"

    if destino.exists() and df_novo["id_empenho"].notna().any():
        # Upsert: substitui apenas os registros cujo id_empenho coincide com o novo lote.
        # Isso preserva dados do bulk_load (empenhos individuais) ao processar dados da
        # API /por-orgao (registros "api_XXXXX"), e vice-versa.
        df_existente = pd.read_parquet(destino)
        ids_novos = set(df_novo["id_empenho"].dropna())
        df_existente = df_existente[~df_existente["id_empenho"].isin(ids_novos)]
        df_final = pd.concat([df_existente, df_novo], ignore_index=True)
        log.info("Upsert: %d novos/atualizados sobre %d existentes → %d total",
                 len(df_novo), len(df_existente), len(df_final))
    else:
        df_final = df_novo

    df_final.to_parquet(destino, index=False, engine="pyarrow", compression="snappy")

    log.info(
        "Silver salvo: %s | %d registros | %.1f MB",
        destino.name, len(df_final), destino.stat().st_size / 1e6,
    )


def main():
    parser = argparse.ArgumentParser(description="Transforma raw → silver para despesas")
    parser.add_argument("--ano", type=int, default=date.today().year)
    parser.add_argument("--mes", type=int, default=None)
    parser.add_argument("--reprocessar-tudo", action="store_true",
                        help="Reprocessa todos os arquivos raw disponíveis")
    args = parser.parse_args()

    if args.reprocessar_tudo:
        arquivos = sorted(RAW_DIR.glob("despesas_*.json"))
    elif args.mes is None:
        arquivos = sorted(RAW_DIR.glob(f"despesas_{args.ano}_*.json"))
    else:
        arquivos = sorted(RAW_DIR.glob(f"despesas_{args.ano}_{args.mes:02d}.json"))

    if not arquivos:
        log.error("Nenhum arquivo raw encontrado. Execute extract.py primeiro.")
        sys.exit(1)

    log.info("Processando %d arquivo(s)...", len(arquivos))
    for arq in arquivos:
        processar_arquivo(arq)

    log.info("Silver completo.")


if __name__ == "__main__":
    main()
