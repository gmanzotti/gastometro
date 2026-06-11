"""
pipelines/municipios/load_producao.py  —  Gastos municipais via API SICONFI/RREO
(versão PRODUÇÃO — extração completa dos ~5.570 municípios, não-assistida)
──────────────────────────────────────────────────────────────────────────────────
DIFERENÇAS EM RELAÇÃO AO load_prototipo.py:

  1. ESCOPO: extrai TODOS os municípios por padrão (sem editar código).
     Para um teste rápido de fumaça, use a flag --capitais.

  2. CHECKPOINT DE COMBINAÇÕES PROCESSADAS (data/municipios/checkpoint_municipios.csv)
     O protótipo só registrava combinações COM dados (no parquet). Municípios
     <50k hab. que publicam apenas o RREO Simplificado retornam vazio — e são
     a maioria das requisições. Sem checkpoint, qualquer interrupção forçava
     re-consultar todas as vazias (horas de retrabalho). Agora:
       - status "vazio" é registrado e PULADO nas retomadas, desde que o
         bimestre tenha encerrado há mais de DIAS_VAZIO_DEFINITIVO dias
         (prazo legal de publicação já passou → vazio é definitivo).
         Vazios recentes são re-consultados (município pode publicar com atraso).
       - status "erro" é registrado apenas para diagnóstico e NUNCA é pulado:
         basta rodar o script de novo ao final para repescar as falhas.

  3. ISOLAMENTO DE ERROS POR COMBINAÇÃO
     Além de falhas de rede (já tratadas com retry + backoff), agora também
     respostas não-JSON (proxy corporativo devolvendo HTML com status 200) e
     mudanças de schema (KeyError) são capturadas: loga, marca "erro" e segue.
     Um processo de ~24h não pode morrer por causa de 1 município.

  4. CONFIGURAÇÃO VIA LINHA DE COMANDO (sem editar o código-fonte):
       --ano-inicio N      primeiro ano a extrair (padrão: 2024)
       --intervalo S       segundos entre requisições (padrão: 1.1)
       --no-verify-ssl     desliga a verificação de certificado SSL
                           (necessário APENAS atrás de proxy corporativo
                            com certificado próprio, como o da FIESP)
       --capitais          modo teste: só as 27 capitais (~10 min)

  5. requests.Session — reusa a conexão TLS entre requisições (mais rápido
     e estável do que abrir ~78 mil conexões novas).

  6. LOG EM ARQUIVO além do console: logs/municipios_producao.log na raiz
     do projeto — essencial quando rodando via agendador/serviço.

ESCALA E TEMPO DE EXECUÇÃO (ano-inicio=2024, em jun/2026):
  ~5.570 municípios × ~14 bimestres ≈ 78.000 requisições
  A ~1,1 s/req ≈ 24 h corridas (+ retries) → planejar ~24–36 h.
  O processo pode ser interrompido e retomado quantas vezes for preciso:
  o que já foi processado não é re-consultado (ver item 2).

SAÍDA:
  data/municipios/gastos_municipios.parquet — colunas:
    ano, periodo, cod_ibge, uf, ente, populacao,
    cod_conta, conta, coluna, valor_milhoes
  data/municipios/metadata.json
  data/municipios/checkpoint_municipios.csv (controle interno de retomada)

COMO RODAR (TI / produção):
  python pipelines/municipios/load_producao.py
  # atrás de proxy corporativo com certificado próprio:
  python pipelines/municipios/load_producao.py --no-verify-ssl
  # teste de fumaça antes da extração completa (~10 min):
  python pipelines/municipios/load_producao.py --capitais

  Ao final, verifique no log o nº de erros. Se houver, rode o script
  novamente: apenas as combinações com erro serão re-consultadas.
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

# Sessão HTTP compartilhada: reusa a conexão TLS entre as ~78 mil requisições
SESSION = requests.Session()

# ── Configuração de execução (sobrescrita por argumentos de linha de comando) ─
CFG = {
    "ano_inicio": 2024,   # últimos 2 anos (extração de produção)
    "intervalo":  1.1,    # segundos entre requisições (rate limit da API: 1 req/s)
    "verify_ssl": True,   # False apenas atrás de proxy com certificado próprio
    "capitais":   False,  # True = teste de fumaça com as 27 capitais
}

MAX_TENTATIVAS = 3     # tentativas por combinação antes de marcar "erro"
SALVAR_A_CADA  = 100   # grava o parquet a cada N requisições com dados

# Um "vazio" só é definitivo se o prazo legal de publicação já passou.
# O RREO deve ser publicado até 30 dias após o fim do bimestre; usamos 90 dias
# de margem para municípios atrasados. Vazios mais recentes são re-consultados.
DIAS_VAZIO_DEFINITIVO = 90

# ── Capitais estaduais (modo --capitais, teste de fumaça) ─────────────────────
CAPITAIS: dict[str, int] = {
    "AC": 1200401, "AL": 2704302, "AM": 1302603, "AP": 1600303,
    "BA": 2927408, "CE": 2304400, "DF": 5300108, "ES": 3205309,
    "GO": 5208707, "MA": 2111300, "MG": 3106200, "MS": 5002704,
    "MT": 5103403, "PA": 1501402, "PB": 2507507, "PE": 2611606,
    "PI": 2211001, "PR": 4106902, "RJ": 3304557, "RN": 2408102,
    "RO": 1100205, "RR": 1400100, "RS": 4314902, "SC": 4205407,
    "SE": 2800308, "SP": 3550308, "TO": 1721000,
}

# ── Contas e estágios de despesa (idênticos ao protótipo e ao pipeline estadual) ──
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
MUNICIPIOS_DIR = DATA_DIR / "municipios"
MUNICIPIOS_DIR.mkdir(parents=True, exist_ok=True)
DESTINO    = MUNICIPIOS_DIR / "gastos_municipios.parquet"
META_FILE  = MUNICIPIOS_DIR / "metadata.json"
CHECKPOINT = MUNICIPIOS_DIR / "checkpoint_municipios.csv"

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


# ── Etapa 1: Lista de municípios a extrair ────────────────────────────────────

def buscar_entes_municipios() -> pd.DataFrame:
    """Retorna DataFrame (cod_ibge, uf, populacao) dos municípios a processar."""
    if CFG["capitais"]:
        df = pd.DataFrame([
            {"cod_ibge": cod_ibge, "uf": uf, "populacao": 0}
            for uf, cod_ibge in CAPITAIS.items()
        ])
        log.info("Modo teste (--capitais): %d capitais selecionadas", len(df))
        return df

    # co_tipo_ente="E" retorna TODAS as entidades; o filtro real é esfera=="M"
    log.info("Buscando lista completa de municípios no SICONFI...")
    r = SESSION.get(URL_ENTES, params={"co_tipo_ente": "E"},
                    timeout=30, verify=CFG["verify_ssl"])
    r.raise_for_status()

    df = pd.DataFrame(r.json()["items"])
    municipios = (
        df[df["esfera"] == "M"]
        [["cod_ibge", "uf", "populacao"]]
        .copy()
        .sort_values(["uf", "populacao"], ascending=[True, False])
        .reset_index(drop=True)
    )
    log.info("Encontrados %d municípios", len(municipios))
    return municipios


# ── Etapa 2: Download do RREO por município/período ───────────────────────────

def buscar_rreo_municipio(cod_ibge: int, ano: int, periodo: int) -> tuple[pd.DataFrame, str]:
    """
    Baixa o Anexo 01 do RREO para um município/ano/bimestre.

    Retorna (DataFrame, status), onde status é:
      "ok"    — dados obtidos e filtrados com sucesso
      "vazio" — API respondeu sem itens (município publica só o Simplificado,
                ou ainda não publicou o bimestre)
      "erro"  — falha após MAX_TENTATIVAS (rede) ou resposta/schema inesperado

    TRATAMENTO DE ERROS (por que três camadas?):
      - RequestException (timeout, 5xx, conexão): transitório → retry com
        backoff exponencial 2s/4s/8s
      - ValueError no r.json(): proxy corporativo pode devolver página HTML
        com status 200 → também tratado como transitório (retry)
      - Exception no processamento (ex: KeyError se a API mudar o schema
        para algum município): permanente → marca "erro" e segue em frente.
        Um processo de ~24h não pode morrer por causa de 1 município.
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

        # ── Resposta recebida: processa fora do loop de retry ────────────────
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
            # Schema inesperado: não adianta tentar de novo — loga e segue
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
    mes_seguinte = 2 * periodo + 1   # 1º dia do mês seguinte ao fim do bimestre
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
    Set de (cod_ibge, ano, periodo) que NÃO precisam ser re-consultadas:

      1. Combinações já no parquet (têm dados — fonte da verdade).
      2. Combinações "vazio" no checkpoint cujo bimestre encerrou há mais de
         DIAS_VAZIO_DEFINITIVO dias (prazo de publicação passou → definitivo).

    "erro" nunca entra aqui: rodar o script de novo repesca as falhas.
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


def _construir_combinacoes(municipios: pd.DataFrame) -> list[tuple]:
    """Gera todas as combinações (cod_ibge, ano, bimestre) até o último publicado."""
    ano_limite, periodo_limite = _bimestre_maximo_atual()
    combinacoes = []
    for _, mun in municipios.iterrows():
        for ano in range(CFG["ano_inicio"], ano_limite + 1):
            for periodo in range(1, 7):
                if ano == ano_limite and periodo > periodo_limite:
                    continue
                combinacoes.append((int(mun["cod_ibge"]), ano, periodo))
    return combinacoes


# ── Etapa 4: Salvamento incremental ───────────────────────────────────────────

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
    """
    Extrai os gastos municipais de forma incremental e retomável.
    Retorna dicionário com contadores para o resumo/metadata.
    """
    municipios = buscar_entes_municipios()
    todas  = _construir_combinacoes(municipios)
    pular  = _combinacoes_a_pular()
    pendentes = [c for c in todas if c not in pular]

    log.info(
        "Total: %d combinações | Puladas (parquet + vazios definitivos): %d | A buscar: %d",
        len(todas), len(todas) - len(pendentes), len(pendentes),
    )
    if pendentes:
        horas = len(pendentes) * CFG["intervalo"] / 3600
        log.info("Estimativa: ~%.1f h a %.1f s/requisição", horas, CFG["intervalo"])

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

        df, status = buscar_rreo_municipio(cod_ibge, ano, periodo)
        contagem[status] += 1

        if status == "ok":
            lote_atual.append(df)
        else:
            # "vazio" alimenta o pulo nas retomadas; "erro" fica só para diagnóstico
            _checkpoint_registrar(cod_ibge, ano, periodo, status)

        if len(lote_atual) >= SALVAR_A_CADA:
            n = _salvar_lote(lote_atual)
            log.info("Lote salvo: parquet agora tem %d linhas", n)
            lote_atual = []

        time.sleep(CFG["intervalo"])

    if lote_atual:
        n = _salvar_lote(lote_atual)
        log.info("Lote final salvo: parquet tem %d linhas", n)

    # ── Resumo de cobertura ───────────────────────────────────────────────────
    total = sum(contagem.values())
    log.info(
        "=== RESUMO ==="
        "\n  Requisições: %d | ok: %d (%.1f%%) | vazio: %d (%.1f%%) | erro: %d"
        "\n  (vazios incluem municípios <50k hab. que publicam só o RREO Simplificado)",
        total,
        contagem["ok"],    100 * contagem["ok"] / total if total else 0,
        contagem["vazio"], 100 * contagem["vazio"] / total if total else 0,
        contagem["erro"],
    )
    if contagem["erro"]:
        log.warning(
            "%d combinações falharam. RODE O SCRIPT NOVAMENTE para repescá-las "
            "(apenas as falhas serão re-consultadas).", contagem["erro"],
        )
    return contagem


# ── Etapa 6: Ponto de entrada ─────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extração de produção dos gastos municipais (SICONFI/RREO).",
    )
    p.add_argument("--ano-inicio", type=int, default=CFG["ano_inicio"],
                   help="primeiro ano a extrair (padrão: %(default)s)")
    p.add_argument("--intervalo", type=float, default=CFG["intervalo"],
                   help="segundos entre requisições (padrão: %(default)s)")
    p.add_argument("--no-verify-ssl", action="store_true",
                   help="desliga a verificação SSL (proxy corporativo)")
    p.add_argument("--capitais", action="store_true",
                   help="teste de fumaça: extrai só as 27 capitais")
    return p.parse_args()


def _configurar_logging() -> None:
    """Console + arquivo logs/municipios_producao.log (para execução não-assistida)."""
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "municipios_producao.log",
                                mode="a", encoding="utf-8"),
        ],
    )


def main() -> None:
    args = _parse_args()
    CFG["ano_inicio"] = args.ano_inicio
    CFG["intervalo"]  = args.intervalo
    CFG["verify_ssl"] = not args.no_verify_ssl
    CFG["capitais"]   = args.capitais

    _configurar_logging()
    if not CFG["verify_ssl"]:
        # Suprime avisos de SSL apenas quando o usuário pediu explicitamente
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    inicio = datetime.now()
    modo = "teste (capitais)" if CFG["capitais"] else "produção (todos os municípios)"
    log.info("=== Início da extração de gastos municipais — %s ===", modo)
    log.info("Config: ano_inicio=%d | intervalo=%.1fs | verify_ssl=%s",
             CFG["ano_inicio"], CFG["intervalo"], CFG["verify_ssl"])

    contagem = extrair_historico()

    if DESTINO.exists():
        df_meta = pd.read_parquet(DESTINO, columns=["ano", "periodo", "cod_ibge"])
        meta = {
            "ultima_extracao":   datetime.now().isoformat(timespec="seconds"),
            "modo":              modo,
            "total_linhas":      len(df_meta),
            "total_combinacoes": df_meta.drop_duplicates(["cod_ibge", "ano", "periodo"]).shape[0],
            "municipios":        int(df_meta["cod_ibge"].nunique()),
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
