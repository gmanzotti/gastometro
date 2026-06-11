"""
pipelines/estados/load_producao.py  —  Gastos estaduais via API SICONFI/RREO
(versão PRODUÇÃO — execução não-assistida; espelho do load_producao municipal)
──────────────────────────────────────────────────────────────────────────────────
DIFERENÇAS EM RELAÇÃO AO load_prototipo.py (mesmas do pipeline municipal):

  1. CHECKPOINT (data/estados/checkpoint_estados.csv): respostas "vazio" de
     bimestres encerrados há mais de DIAS_VAZIO_DEFINITIVO dias são puladas
     nas retomadas; vazios recentes (estado que publica com atraso, como já
     vimos com AM e SC) são re-consultados. "erro" nunca é pulado: rodar o
     script de novo repesca as falhas.

  2. ISOLAMENTO DE ERROS: respostas não-JSON (proxy devolvendo HTML) entram
     no retry; schema inesperado marca "erro" e segue — o processo não morre.

  3. CONFIGURAÇÃO VIA LINHA DE COMANDO:
       --ano-inicio N      primeiro ano a extrair (padrão: 2024)
       --intervalo S       segundos entre requisições (padrão: 1.1)
       --no-verify-ssl     desliga a verificação SSL (proxy corporativo)

  4. requests.Session (reuso de conexão) e log em arquivo
     (logs/estados_producao.log) além do console.

ESCALA E TEMPO DE EXECUÇÃO (ano-inicio=2024, em jun/2026):
  27 entes (26 estados + DF) × ~14 bimestres ≈ 380 requisições ≈ 7–10 min.

SAÍDA:
  data/estados/gastos_estados.parquet — colunas:
    ano, periodo, cod_ibge, uf, ente, populacao,
    cod_conta, conta, coluna, valor_milhoes
  data/estados/metadata.json
  data/estados/checkpoint_estados.csv (controle interno de retomada)

COMO RODAR (TI / produção):
  python pipelines/estados/load_producao.py
  # atrás de proxy corporativo com certificado próprio:
  python pipelines/estados/load_producao.py --no-verify-ssl
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import urllib3

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import DATA_DIR

log = logging.getLogger(__name__)

# ── Endpoints da API SICONFI ──────────────────────────────────────────────────
URL_ENTES = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/entes"
URL_RREO  = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/rreo"

# Sessão HTTP compartilhada: reusa a conexão TLS entre requisições
SESSION = requests.Session()

# ── Configuração de execução (sobrescrita por argumentos de linha de comando) ─
CFG = {
    "ano_inicio": 2024,   # últimos 2 anos (extração de produção)
    "intervalo":  1.1,    # segundos entre requisições (rate limit da API: 1 req/s)
    "verify_ssl": True,   # False apenas atrás de proxy com certificado próprio
}

MAX_TENTATIVAS = 3
SALVAR_A_CADA  = 100

# Prazo legal do RREO é 30 dias após o fim do bimestre; 90 dias de margem
# para estados atrasados (já vimos AM e SC publicarem com meses de atraso).
DIAS_VAZIO_DEFINITIVO = 90

# ── Contas e estágios de despesa (Lei 4.320/1964, Art. 12) ────────────────────
CONTAS_DESPESA = {
    "DespesasExcetoIntraOrcamentarias",  # total consolidado
    "DespesasCorrentes",                  # subtotal correntes
    "PessoalEEncargosSociais",            # 1.1 — folha + encargos patronais
    "JurosEEncargosDaDivida",             # 1.2 — juros e comissões da dívida
    "OutrasDespesasCorrentes",            # 1.3 — custeio, transferências, subvenções
    "DespesasDeCapital",                  # subtotal capital
    "Investimentos",                      # 2.1 — obras e equipamentos
    "InversoesFinanceiras",               # 2.2 — aquisição de ativos já existentes
    "AmortizacaoDaDivida",                # 2.3 — pagamento do principal da dívida
}

COLUNAS_DESPESA = {
    "DESPESAS EMPENHADAS NO BIMESTRE",
    "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)",
    "DESPESAS LIQUIDADAS NO BIMESTRE",
    "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)",
    "DESPESAS PAGAS ATÉ O BIMESTRE (j)",
}

COLS_SAIDA = ["ano", "periodo", "cod_ibge", "uf", "ente", "populacao",
              "cod_conta", "conta", "coluna", "valor_milhoes"]

# ── Destinos de saída ─────────────────────────────────────────────────────────
ESTADOS_DIR = DATA_DIR / "estados"
ESTADOS_DIR.mkdir(parents=True, exist_ok=True)
DESTINO    = ESTADOS_DIR / "gastos_estados.parquet"
META_FILE  = ESTADOS_DIR / "metadata.json"
CHECKPOINT = ESTADOS_DIR / "checkpoint_estados.csv"

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


# ── Etapa 1: Busca de entidades ──────────────────────────────────────────────

def buscar_entes_estados() -> pd.DataFrame:
    """
    Lista os 26 estados + DF no endpoint /entes do SICONFI.

    O DF tem esfera='D' (não 'E') no SICONFI, mas seu RREO é válido —
    adicionamos manualmente para garantir que seja sempre extraído.
    """
    log.info("Buscando lista de estados no SICONFI...")
    r = SESSION.get(URL_ENTES, params={"co_tipo_ente": "E"},
                    timeout=30, verify=CFG["verify_ssl"])
    r.raise_for_status()

    df = pd.DataFrame(r.json()["items"])
    estados = (
        df[df["esfera"] == "E"]
        [["cod_ibge", "uf", "ente", "populacao"]]
        .copy()
        .sort_values("uf")
        .reset_index(drop=True)
    )

    if 53 not in estados["cod_ibge"].values:
        df_df = df[df["cod_ibge"] == 53][["cod_ibge", "uf", "ente", "populacao"]].copy()
        if not df_df.empty:
            estados = (
                pd.concat([estados, df_df], ignore_index=True)
                .sort_values("cod_ibge")
                .reset_index(drop=True)
            )
            log.info("DF adicionado manualmente (esfera='D' no SICONFI, cod_ibge=53)")

    log.info("Encontrados %d estados/DF", len(estados))
    return estados


# ── Etapa 2: Download do RREO por estado/período ─────────────────────────────

def buscar_rreo_estado(cod_ibge: int, ano: int, periodo: int) -> tuple[pd.DataFrame, str]:
    """
    Baixa o Anexo 01 do RREO para um estado/ano/bimestre.

    Retorna (DataFrame, status): "ok" | "vazio" | "erro".
    Ver pipelines/municipios/load_producao.py para a lógica detalhada das
    três camadas de tratamento de erro (rede, JSON inválido, schema).
    """
    params = {
        "an_exercicio":          ano,
        "nr_periodo":            periodo,
        "co_tipo_demonstrativo": "RREO",
        "no_anexo":              "RREO-Anexo 01",
        "id_ente":               cod_ibge,
    }

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            r = SESSION.get(URL_RREO, params=params,
                            timeout=30, verify=CFG["verify_ssl"])
            r.raise_for_status()
            payload = r.json()   # ValueError se a resposta não for JSON

        except (requests.exceptions.RequestException, ValueError) as exc:
            if tentativa == MAX_TENTATIVAS:
                log.error(
                    "Falha após %d tentativas: cod_ibge=%d ano=%d periodo=%d | %s",
                    MAX_TENTATIVAS, cod_ibge, ano, periodo, exc,
                )
                return pd.DataFrame(), "erro"
            espera = 2 ** tentativa   # backoff exponencial: 2s → 4s → 8s
            log.warning(
                "Tentativa %d/%d falhou (cod_ibge=%d, %d/%d). Aguardando %ds...",
                tentativa, MAX_TENTATIVAS, cod_ibge, ano, periodo, espera,
            )
            time.sleep(espera)
            continue

        if not payload.get("items"):
            return pd.DataFrame(), "vazio"

        try:
            df = pd.DataFrame(payload["items"])
            df = df[df["cod_conta"].isin(CONTAS_DESPESA)].copy()
            df = df[df["coluna"].isin(COLUNAS_DESPESA)].copy()
            if df.empty:
                return pd.DataFrame(), "vazio"

            df = df.rename(columns={"exercicio": "ano", "instituicao": "ente"})
            df["valor_milhoes"] = pd.to_numeric(df["valor"], errors="coerce") / 1e6
            return df[COLS_SAIDA].copy(), "ok"

        except Exception as exc:
            log.error(
                "Schema inesperado: cod_ibge=%d ano=%d periodo=%d | %s",
                cod_ibge, ano, periodo, exc,
            )
            return pd.DataFrame(), "erro"

    return pd.DataFrame(), "erro"   # inalcançável; satisfaz o type checker


# ── Etapa 3: Checkpoint e controle incremental ────────────────────────────────

def _bimestre_maximo_atual() -> tuple[int, int]:
    """(ano, bimestre) máximo provavelmente já publicado (mês anterior)."""
    hoje = datetime.now()
    mes_anterior = hoje.month - 1 if hoje.month > 1 else 12
    ano_ref = hoje.year if hoje.month > 1 else hoje.year - 1
    return ano_ref, (mes_anterior + 1) // 2


def _dias_desde_fim_bimestre(ano: int, periodo: int) -> int:
    """Dias corridos desde o encerramento do bimestre (ex: B2 termina em 30/abr)."""
    mes_seguinte = 2 * periodo + 1
    ano_seg, mes_seg = (ano + 1, 1) if mes_seguinte > 12 else (ano, mes_seguinte)
    return (datetime.now() - datetime(ano_seg, mes_seg, 1)).days


def _checkpoint_registrar(cod_ibge: int, ano: int, periodo: int, status: str) -> None:
    """Acrescenta uma linha ao checkpoint (CSV em modo append — escrita barata)."""
    novo = not CHECKPOINT.exists()
    with open(CHECKPOINT, "a", encoding="utf-8") as f:
        if novo:
            f.write("cod_ibge,ano,periodo,status,timestamp\n")
        f.write(f"{cod_ibge},{ano},{periodo},{status},"
                f"{datetime.now().isoformat(timespec='seconds')}\n")


def _combinacoes_a_pular() -> set:
    """
    (cod_ibge, ano, periodo) a não re-consultar: já no parquet, ou "vazio"
    definitivo no checkpoint (bimestre encerrado há > DIAS_VAZIO_DEFINITIVO).
    """
    pular: set = set()

    if DESTINO.exists():
        df = pd.read_parquet(DESTINO, columns=["cod_ibge", "ano", "periodo"])
        pular |= set(zip(df["cod_ibge"], df["ano"], df["periodo"]))

    if CHECKPOINT.exists():
        ck = pd.read_csv(CHECKPOINT)
        vazios = ck[ck["status"] == "vazio"].drop_duplicates(
            subset=["cod_ibge", "ano", "periodo"]
        )
        pular |= {
            (int(r.cod_ibge), int(r.ano), int(r.periodo))
            for r in vazios.itertuples()
            if _dias_desde_fim_bimestre(int(r.ano), int(r.periodo)) > DIAS_VAZIO_DEFINITIVO
        }

    return pular


def _construir_combinacoes(estados: pd.DataFrame) -> list[tuple]:
    """Gera todas as combinações (cod_ibge, ano, bimestre) até o último publicado."""
    ano_limite, periodo_limite = _bimestre_maximo_atual()
    combinacoes = []
    for _, estado in estados.iterrows():
        for ano in range(CFG["ano_inicio"], ano_limite + 1):
            for periodo in range(1, 7):
                if ano == ano_limite and periodo > periodo_limite:
                    continue
                combinacoes.append((int(estado["cod_ibge"]), ano, periodo))
    return combinacoes


# ── Etapa 4: Salvamento incremental ──────────────────────────────────────────

def _salvar_lote(novos_dfs: list[pd.DataFrame]) -> int:
    """Adiciona novos registros ao parquet existente. Retorna total de linhas."""
    df_novo = pd.concat(novos_dfs, ignore_index=True)

    if DESTINO.exists():
        df_existente = pd.read_parquet(DESTINO)
        df_final = pd.concat([df_existente, df_novo], ignore_index=True)
    else:
        df_final = df_novo

    df_final = df_final.drop_duplicates(
        subset=["cod_ibge", "ano", "periodo", "cod_conta", "coluna"]
    )
    df_final = df_final.sort_values(
        ["uf", "ano", "periodo", "cod_conta", "coluna"]
    ).reset_index(drop=True)

    df_final.to_parquet(DESTINO, index=False)
    return len(df_final)


# ── Etapa 5: Orquestração principal ──────────────────────────────────────────

def extrair_historico() -> dict:
    """Extrai os gastos estaduais de forma incremental e retomável."""
    estados = buscar_entes_estados()
    todas  = _construir_combinacoes(estados)
    pular  = _combinacoes_a_pular()
    pendentes = [c for c in todas if c not in pular]

    log.info(
        "Total: %d combinações | Puladas (parquet + vazios definitivos): %d | A buscar: %d",
        len(todas), len(todas) - len(pendentes), len(pendentes),
    )

    contagem = {"ok": 0, "vazio": 0, "erro": 0}
    if not pendentes:
        log.info("Nada a fazer: parquet e checkpoint já cobrem todas as combinações.")
        return contagem

    lote_atual: list[pd.DataFrame] = []

    for i, (cod_ibge, ano, periodo) in enumerate(pendentes, 1):
        if i % 50 == 0 or i == 1:
            log.info(
                "Progresso: %d/%d (%.1f%%) | ok: %d | vazio: %d | erro: %d",
                i, len(pendentes), 100 * i / len(pendentes),
                contagem["ok"], contagem["vazio"], contagem["erro"],
            )

        df, status = buscar_rreo_estado(cod_ibge, ano, periodo)
        contagem[status] += 1

        if status == "ok":
            lote_atual.append(df)
        else:
            _checkpoint_registrar(cod_ibge, ano, periodo, status)

        if len(lote_atual) >= SALVAR_A_CADA:
            n = _salvar_lote(lote_atual)
            log.info("Lote salvo: parquet agora tem %d linhas", n)
            lote_atual = []

        time.sleep(CFG["intervalo"])

    if lote_atual:
        n = _salvar_lote(lote_atual)
        log.info("Lote final salvo: parquet tem %d linhas", n)

    total = sum(contagem.values())
    log.info(
        "=== RESUMO === Requisições: %d | ok: %d | vazio: %d | erro: %d",
        total, contagem["ok"], contagem["vazio"], contagem["erro"],
    )
    if contagem["erro"]:
        log.warning(
            "%d combinações falharam. RODE O SCRIPT NOVAMENTE para repescá-las.",
            contagem["erro"],
        )
    return contagem


# ── Etapa 6: Ponto de entrada ─────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extração de produção dos gastos estaduais (SICONFI/RREO).",
    )
    p.add_argument("--ano-inicio", type=int, default=CFG["ano_inicio"],
                   help="primeiro ano a extrair (padrão: %(default)s)")
    p.add_argument("--intervalo", type=float, default=CFG["intervalo"],
                   help="segundos entre requisições (padrão: %(default)s)")
    p.add_argument("--no-verify-ssl", action="store_true",
                   help="desliga a verificação SSL (proxy corporativo)")
    return p.parse_args()


def _configurar_logging() -> None:
    """Console + arquivo logs/estados_producao.log (para execução não-assistida)."""
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "estados_producao.log",
                                mode="a", encoding="utf-8"),
        ],
    )


def main() -> None:
    args = _parse_args()
    CFG["ano_inicio"] = args.ano_inicio
    CFG["intervalo"]  = args.intervalo
    CFG["verify_ssl"] = not args.no_verify_ssl

    _configurar_logging()
    if not CFG["verify_ssl"]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    inicio = datetime.now()
    log.info("=== Início da extração de gastos estaduais (produção) ===")
    log.info("Config: ano_inicio=%d | intervalo=%.1fs | verify_ssl=%s",
             CFG["ano_inicio"], CFG["intervalo"], CFG["verify_ssl"])

    contagem = extrair_historico()

    if DESTINO.exists():
        df_meta = pd.read_parquet(DESTINO, columns=["ano", "periodo", "cod_ibge"])
        meta = {
            "ultima_extracao":   datetime.now().isoformat(timespec="seconds"),
            "total_linhas":      len(df_meta),
            "total_combinacoes": df_meta.drop_duplicates(["cod_ibge", "ano", "periodo"]).shape[0],
            "ano_mais_antigo":   int(df_meta["ano"].min()),
            "ano_mais_recente":  int(df_meta["ano"].max()),
            "erros_na_execucao": contagem["erro"],
            "duracao_segundos":  round((datetime.now() - inicio).total_seconds()),
        }
        META_FILE.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Metadados salvos: %s", meta)

    log.info("=== Extração concluída ===")


if __name__ == "__main__":
    main()
