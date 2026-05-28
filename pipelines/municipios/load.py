"""
pipelines/municipios/load.py  —  Gastos municipais via API SICONFI/RREO
────────────────────────────────────────────────────────────────────────
MESMO PRINCÍPIO DO PIPELINE DE ESTADOS (pipelines/estados/load.py),
com duas diferenças principais:

  1. ESCOPO DE ENTIDADES
     - Protótipo (padrão): 27 capitais estaduais + DF
       → ~27 municípios × 6 bimestres × ~11,5 anos ≈ 1.860 req. (~35 min)
       → Objetivo: validar as visualizações no Streamlit antes da extração completa
     - Produção: todos os ~5.571 municípios brasileiros
       → ~5.571 × 6 × ~11,5 anos ≈ 384.000 req. (~4,5 dias)
       → Executar na VM/servidor dedicado da TI
     Para alternar entre os modos, mude a constante EXTRAIR_TODOS abaixo.

  2. SIMPLIFICADO vs. COMPLETO
     Municípios com menos de 50.000 habitantes podem publicar o RREO
     Simplificado (Anexo 14), com estrutura diferente do Anexo 01.
     Para o protótipo isso não é problema: todas as capitais são grandes
     e publicam o Anexo 01 completo (co_tipo_demonstrativo="RREO").
     Na extração completa (EXTRAIR_TODOS=True), o pipeline detecta
     automaticamente pelo campo `populacao` qual tipo usar.

SAÍDA:
  data/municipios/gastos_municipios.parquet — mesmas colunas do parquet estadual:
    ano, periodo, cod_ibge, uf, ente, populacao,
    cod_conta, conta, coluna, valor_milhoes
  data/municipios/metadata.json

COMO RODAR:
  Protótipo (capitais):  python pipelines/municipios/load.py
  Produção (todos):      altere EXTRAIR_TODOS = True e rode o script
"""

import json
import logging
import sys
import time
from datetime import datetime
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

# ── Endpoints da API SICONFI ──────────────────────────────────────────────────
URL_ENTES = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/entes"
URL_RREO  = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/rreo"

# ── Modo de extração ──────────────────────────────────────────────────────────
# False → apenas as 27 capitais (protótipo, ~35 min)
# True  → todos os ~5.571 municípios (produção, ~4,5 dias na VM)
EXTRAIR_TODOS = False

ANO_INICIO            = 2015   # primeiro ano disponível no SICONFI
INTERVALO_REQUISICAO  = 1.1    # segundos entre requisições (rate limit: 1 req/s)
MAX_TENTATIVAS        = 3
SALVAR_A_CADA         = 100    # salva em disco a cada N requisições bem-sucedidas

# ── Capitais estaduais (protótipo) ────────────────────────────────────────────
# Todas as capitais têm população > 50k e publicam o RREO Completo (Anexo 01).
# Códigos IBGE de 7 dígitos conforme tabela oficial do IBGE.
#
# Nota sobre Brasília (DF): o DF é classificado como estado no SICONFI e já
# aparece no pipeline de estados. Está incluído aqui também para que o
# painel municipal mostre cobertura nacional completa. Se retornar vazio,
# o pipeline ignora silenciosamente.
CAPITAIS: dict[str, int] = {
    "AC": 1200401,   # Rio Branco
    "AL": 2704302,   # Maceió
    "AM": 1302603,   # Manaus
    "AP": 1600303,   # Macapá
    "BA": 2927408,   # Salvador
    "CE": 2304400,   # Fortaleza
    "DF": 5300108,   # Brasília
    "ES": 3205309,   # Vitória
    "GO": 5208707,   # Goiânia
    "MA": 2111300,   # São Luís
    "MG": 3106200,   # Belo Horizonte
    "MS": 5002704,   # Campo Grande
    "MT": 5103403,   # Cuiabá
    "PA": 1501402,   # Belém
    "PB": 2507507,   # João Pessoa
    "PE": 2611606,   # Recife
    "PI": 2211001,   # Teresina
    "PR": 4106902,   # Curitiba
    "RJ": 3304557,   # Rio de Janeiro
    "RN": 2408102,   # Natal
    "RO": 1100205,   # Porto Velho
    "RR": 1400100,   # Boa Vista
    "RS": 4314902,   # Porto Alegre
    "SC": 4205407,   # Florianópolis
    "SE": 2800308,   # Aracaju
    "SP": 3550308,   # São Paulo
    "TO": 1721000,   # Palmas
}

# Limiar de população para determinar se um município usa o RREO completo.
# Abaixo desse valor → co_tipo_demonstrativo deve ser "RREO-Simplificado" (Anexo 14),
# que tem estrutura diferente. Para o protótipo, todas as capitais são > 50k, então
# este limiar só importa na extração completa (EXTRAIR_TODOS=True).
POP_MINIMA_COMPLETO = 50_000

# ── Contas de despesa a extrair (idênticas ao pipeline de estados) ────────────
# Confirmadas via API real para município de SP (cod_ibge=3550308, 2024, bim.1)
CONTAS_DESPESA = {
    "DespesasExcetoIntraOrcamentarias",
    "DespesasCorrentes",
    "DespesasDeCapital",
    "Investimentos",
    "InversoesFinanceiras",
    "AmortizacaoDaDivida",
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
DESTINO   = MUNICIPIOS_DIR / "gastos_municipios.parquet"
META_FILE = MUNICIPIOS_DIR / "metadata.json"


# ── Etapa 1: Lista de municípios a extrair ────────────────────────────────────

def buscar_entes_municipios() -> pd.DataFrame:
    """
    Retorna o DataFrame de municípios a processar.

    Modo protótipo (EXTRAIR_TODOS=False):
      Retorna as 27 capitais hardcoded em CAPITAIS. Não faz chamada à API.
      Rápido — ideal para desenvolvimento e validação do painel.

    Modo produção (EXTRAIR_TODOS=True):
      Consulta o endpoint /entes e retorna todos os ~5.571 municípios.
      Use apenas na VM/servidor dedicado (extração leva ~4,5 dias).
    """
    if not EXTRAIR_TODOS:
        df = pd.DataFrame([
            {"cod_ibge": cod_ibge, "uf": uf, "populacao": POP_MINIMA_COMPLETO + 1}
            for uf, cod_ibge in CAPITAIS.items()
        ])
        log.info("Modo protótipo: %d capitais estaduais selecionadas", len(df))
        return df

    # Modo produção: busca todos os municípios no SICONFI
    log.info("Modo produção: buscando todos os municípios no SICONFI...")
    r = requests.get(URL_ENTES, params={"co_tipo_ente": "E"}, timeout=30, verify=False)
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

def _tipo_demonstrativo(populacao: int) -> str:
    """
    Retorna o co_tipo_demonstrativo correto baseado na população.

    Municípios < 50k hab. podem usar o RREO Simplificado. Acima desse limiar,
    o RREO completo (Anexo 01) é obrigatório e retorna a estrutura padrão.

    Para o protótipo (todas capitais > 50k), sempre retorna "RREO".
    Para a extração completa, isso evita requisições que retornariam vazias.
    """
    return "RREO" if populacao >= POP_MINIMA_COMPLETO else "RREO-Simplificado"


def buscar_rreo_municipio(cod_ibge: int, ano: int, periodo: int,
                          populacao: int = POP_MINIMA_COMPLETO + 1) -> pd.DataFrame:
    """
    Baixa o Anexo 01 do RREO para um município, ano e bimestre específicos.
    Lógica idêntica ao pipeline de estados, com detecção de tipo por população.
    """
    tipo = _tipo_demonstrativo(populacao)

    # Municípios pequenos usam o Simplificado — não tem Anexo 01, pulamos por ora
    if tipo == "RREO-Simplificado":
        return pd.DataFrame()

    params = {
        "an_exercicio":          ano,
        "nr_periodo":            periodo,
        "co_tipo_demonstrativo": tipo,
        "no_anexo":              "RREO-Anexo 01",
        "id_ente":               cod_ibge,
    }

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            r = requests.get(URL_RREO, params=params, timeout=30, verify=False)
            r.raise_for_status()
            payload = r.json()

            if not payload.get("items"):
                return pd.DataFrame()

            df = pd.DataFrame(payload["items"])
            df = df[df["cod_conta"].isin(CONTAS_DESPESA)].copy()
            df = df[df["coluna"].isin(COLUNAS_DESPESA)].copy()

            if df.empty:
                return pd.DataFrame()

            df = df.rename(columns={"exercicio": "ano", "instituicao": "ente"})
            df["valor_milhoes"] = pd.to_numeric(df["valor"], errors="coerce") / 1e6

            return df[COLS_SAIDA].copy()

        except requests.exceptions.RequestException as exc:
            if tentativa == MAX_TENTATIVAS:
                log.error(
                    "Falha após %d tentativas: cod_ibge=%d ano=%d periodo=%d | %s",
                    MAX_TENTATIVAS, cod_ibge, ano, periodo, exc,
                )
                return pd.DataFrame()

            espera = 2 ** tentativa
            log.warning(
                "Tentativa %d/%d falhou (cod_ibge=%d, %d/%d). Aguardando %ds...",
                tentativa, MAX_TENTATIVAS, cod_ibge, ano, periodo, espera,
            )
            time.sleep(espera)

    return pd.DataFrame()


# ── Etapas 3-4: Controle incremental e salvamento (idêntico ao de estados) ────

def _bimestre_maximo_atual() -> tuple[int, int]:
    """Retorna (ano, bimestre) máximo provavelmente publicado."""
    hoje = datetime.now()
    mes_anterior = hoje.month - 1 if hoje.month > 1 else 12
    ano_ref = hoje.year if hoje.month > 1 else hoje.year - 1
    return ano_ref, (mes_anterior + 1) // 2


def _combinacoes_ja_carregadas() -> set:
    """Set de (cod_ibge, ano, periodo) já presentes no parquet."""
    if not DESTINO.exists():
        return set()
    df = pd.read_parquet(DESTINO, columns=["cod_ibge", "ano", "periodo"])
    return set(zip(df["cod_ibge"], df["ano"], df["periodo"]))


def _construir_combinacoes(municipios: pd.DataFrame) -> list[tuple]:
    """Gera todas as combinações (município, ano, bimestre) a extrair."""
    ano_limite, periodo_limite = _bimestre_maximo_atual()
    ano_atual = datetime.now().year
    combinacoes = []

    for _, mun in municipios.iterrows():
        for ano in range(ANO_INICIO, ano_atual + 1):
            for periodo in range(1, 7):
                if ano == ano_limite and periodo > periodo_limite:
                    continue
                if ano > ano_limite:
                    continue
                combinacoes.append((
                    int(mun["cod_ibge"]),
                    int(mun["populacao"]),
                    ano,
                    periodo,
                ))

    return combinacoes


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

def extrair_historico() -> None:
    """
    Extrai o histórico de gastos municipais de forma incremental.
    Retoma automaticamente de onde parou se interrompida.
    """
    municipios = buscar_entes_municipios()
    todas = _construir_combinacoes(municipios)
    ja_carregadas = _combinacoes_ja_carregadas()

    # (cod_ibge, populacao, ano, periodo) → chave de dedup usa só os 3 últimos
    pendentes = [c for c in todas if (c[0], c[2], c[3]) not in ja_carregadas]

    log.info(
        "Total: %d combinações | Já no parquet: %d | A buscar: %d",
        len(todas), len(ja_carregadas), len(pendentes),
    )

    if not pendentes:
        log.info("Parquet já está atualizado. Nada a fazer.")
        return

    lote_atual: list[pd.DataFrame] = []
    erros = 0

    for i, (cod_ibge, populacao, ano, periodo) in enumerate(pendentes, 1):
        if i % 50 == 0 or i == 1:
            log.info(
                "Progresso: %d/%d (%.1f%%) | lote: %d | erros: %d",
                i, len(pendentes), 100 * i / len(pendentes), len(lote_atual), erros,
            )

        df = buscar_rreo_municipio(cod_ibge, ano, periodo, populacao)

        if not df.empty:
            lote_atual.append(df)

        if len(lote_atual) >= SALVAR_A_CADA:
            n = _salvar_lote(lote_atual)
            log.info("Lote salvo: parquet agora tem %d linhas", n)
            lote_atual = []

        time.sleep(INTERVALO_REQUISICAO)

    if lote_atual:
        n = _salvar_lote(lote_atual)
        log.info("Lote final salvo: parquet tem %d linhas", n)


# ── Etapa 6: Ponto de entrada ─────────────────────────────────────────────────

def main() -> None:
    """Baixa o histórico de gastos municipais e salva em parquet."""
    inicio = datetime.now()
    modo = "produção (todos os municípios)" if EXTRAIR_TODOS else "protótipo (capitais)"
    log.info("=== Início da extração de gastos municipais — %s ===", modo)

    extrair_historico()

    if DESTINO.exists():
        df_meta = pd.read_parquet(DESTINO, columns=["ano", "periodo", "cod_ibge"])
        meta = {
            "ultima_extracao":   datetime.now().isoformat(timespec="seconds"),
            "modo":              modo,
            "total_linhas":      len(df_meta),
            "total_combinacoes": df_meta.drop_duplicates(["cod_ibge", "ano", "periodo"]).shape[0],
            "ano_mais_antigo":   int(df_meta["ano"].min()),
            "ano_mais_recente":  int(df_meta["ano"].max()),
            "duracao_segundos":  round((datetime.now() - inicio).total_seconds()),
        }
        META_FILE.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Metadados salvos: %s", meta)

    log.info("=== Extração concluída ===")


if __name__ == "__main__":
    main()
