"""
pipelines/estados/load.py  —  Gastos dos estados brasileiros via API SICONFI/RREO
──────────────────────────────────────────────────────────────────────────────────
O QUE É O RREO?
  RREO = Relatório Resumido da Execução Orçamentária.
  É um relatório bimestral obrigatório (Lei de Responsabilidade Fiscal, art. 52)
  publicado por estados, municípios e União. Consolida receitas e despesas
  realizadas no período, com abertura por natureza de despesa.

  O SICONFI (Sistema de Informações Contábeis e Fiscais do Setor Público)
  é o sistema do Tesouro Nacional que centraliza esses dados desde 2015.
  Antes de 2015, os dados eram coletados pelo sistema SISTN (papel/diskete)
  e não estão disponíveis via API.

O QUE ESTE SCRIPT FAZ?
  1. Busca a lista de todos os estados e DF na API SICONFI (/entes)
  2. Para cada estado × ano × bimestre, baixa o Anexo 01 do RREO
  3. Filtra apenas as contas de despesa relevantes (ver CONTAS_DESPESA)
  4. Salva incrementalmente em Parquet (retoma de onde parou se interrompido)

POR QUE APENAS O ANEXO 01?
  O Anexo 01 (Balanço Orçamentário) traz todas as naturezas de despesa
  que nos interessam em uma única requisição por estado/período:
    - Total de despesas
    - Despesas Correntes
    - Despesas de Capital
    - Investimentos (obras, equipamentos)
    - Inversões Financeiras (aquisição de ativos já existentes)
    - Amortização da Dívida (excluída do conceito de investimento)

  Isso evita múltiplas requisições por combinação (estado, ano, bimestre).

CLASSIFICAÇÃO DE INVESTIMENTO (Lei 4.320/1964, Art. 12):
  Investimento Público = Investimentos (§4) + Inversões Financeiras (§5)
  Amortização da Dívida (§6) é pagamento do principal da dívida — NÃO
  é investimento produtivo, mas é salvo no parquet para análise da dívida.

ESTÁGIOS DE EXECUÇÃO DAS DESPESAS:
  O RREO reporta a despesa em três estágios (todos salvos no parquet):
    - Empenhada: orçamento comprometido (autorizado, serviço ainda não entregue)
    - Liquidada: bem/serviço entregue e verificado (base padrão para comparação)
    - Paga: efetivamente transferida (pode diferir por Restos a Pagar)

  Para análises fiscais comparativas, use "DESPESAS LIQUIDADAS ATÉ O BIMESTRE".

ESCALA E TEMPO DE EXECUÇÃO:
  - 27 entidades (26 estados + DF)
  - 2015 a hoje → ~11 anos × 6 bimestres = ~1.800 requisições
  - A 1 req/s: ~30 minutos na primeira execução
  - Execuções subsequentes são incrementais (só busca o que falta)

SAÍDA:
  data/estados/gastos_estados.parquet — colunas:
    ano, periodo, cod_ibge, uf, ente, populacao,
    cod_conta, conta, coluna, valor_milhoes
  data/estados/metadata.json — data da última extração, contagem de linhas

COMO RODAR:
  python pipelines/estados/load.py
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

# A rede corporativa da FIESP usa um proxy que re-assina certificados SSL,
# gerando erro de validação. verify=False contorna isso — mesma solução do
# pipeline federal. Em produção, o TI deve instalar o certificado raiz no servidor.
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

# ── Configurações de extração ──────────────────────────────────────────────────
ANO_INICIO = 2015  # primeiro ano disponível no SICONFI (antes era SISTN/papel)

# Intervalo entre requisições para respeitar o rate limit da API (1 req/s)
INTERVALO_REQUISICAO = 1.1   # ligeiramente acima de 1s para margem de segurança

# Tentativas antes de registrar falha e pular uma combinação
MAX_TENTATIVAS = 3

# Frequência de salvamento incremental durante a extração.
# A cada SALVAR_A_CADA requisições bem-sucedidas, os dados são gravados no disco.
# Isso garante que uma interrupção não perca mais do que SALVAR_A_CADA registros.
SALVAR_A_CADA = 100

# ── Contas de despesa a extrair (confirmadas via API real — SP, 2024, bimestre 1) ──
#
# Estrutura de Despesas de Capital (Lei 4.320/1964, Art. 12):
#   DespesasDeCapital
#   ├── Investimentos            (§4º: obras, equipamentos — INVESTIMENTO PÚBLICO)
#   ├── InversoesFinanceiras     (§5º: aquisição de ativos já em uso — INVESTIMENTO PÚBLICO)
#   └── AmortizacaoDaDivida      (§6º: devolução do principal da dívida — NÃO é investimento)
#
# "InversoesFinanceiras" inclui também as versões Intra (DespesasCorrentesIntra, etc.),
# que representam transações dentro do mesmo nível de governo. Não as extraímos aqui
# porque DespesasExcetoIntraOrcamentarias já exclui essas movimentações internas.
CONTAS_DESPESA = {
    "DespesasExcetoIntraOrcamentarias",  # total consolidado (excluindo intra-orçamentário)
    "DespesasCorrentes",                  # pessoal, custeio, juros
    "DespesasDeCapital",                  # subtotal de capital (investimentos + amortização)
    "Investimentos",                      # obras e equipamentos
    "InversoesFinanceiras",               # aquisição de ativos já existentes
    "AmortizacaoDaDivida",                # pagamento do principal da dívida
}

# ── Estágios de execução a salvar (confirmados via API real) ───────────────────
# Salvamos todos os estágios para dar flexibilidade nas análises.
# O mais usado para comparação fiscal é "DESPESAS LIQUIDADAS ATÉ O BIMESTRE".
COLUNAS_DESPESA = {
    "DESPESAS EMPENHADAS NO BIMESTRE",
    "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)",
    "DESPESAS LIQUIDADAS NO BIMESTRE",
    "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)",
    "DESPESAS PAGAS ATÉ O BIMESTRE (j)",
}

# ── Destinos de saída ─────────────────────────────────────────────────────────
ESTADOS_DIR = DATA_DIR / "estados"
ESTADOS_DIR.mkdir(parents=True, exist_ok=True)
DESTINO   = ESTADOS_DIR / "gastos_estados.parquet"
META_FILE = ESTADOS_DIR / "metadata.json"

# Colunas do parquet final (subset do que a API retorna)
COLS_SAIDA = ["ano", "periodo", "cod_ibge", "uf", "ente", "populacao",
              "cod_conta", "conta", "coluna", "valor_milhoes"]


# ── Etapa 1: Busca de entidades ──────────────────────────────────────────────

def buscar_entes_estados() -> pd.DataFrame:
    """
    Busca a lista de todos os estados e DF no endpoint /entes do SICONFI.

    O endpoint retorna todas as ~5.600 entidades (estados + municípios).
    Filtramos apenas esfera="E" para obter os 27 estados/DF.

    Retorna DataFrame com: cod_ibge, uf, ente, populacao
    """
    log.info("Buscando lista de estados no SICONFI...")
    r = requests.get(URL_ENTES, params={"co_tipo_ente": "E"}, timeout=30, verify=False)
    r.raise_for_status()

    df = pd.DataFrame(r.json()["items"])

    # O endpoint retorna todos os tipos; filtramos apenas estados (esfera="E")
    estados = (
        df[df["esfera"] == "E"]
        [["cod_ibge", "uf", "ente", "populacao"]]
        .copy()
        .sort_values("uf")
        .reset_index(drop=True)
    )

    log.info("Encontrados %d estados/DF", len(estados))
    return estados


# ── Etapa 2: Download do RREO por estado/período ─────────────────────────────

def _bimestre_maximo_atual() -> tuple[int, int]:
    """
    Retorna o (ano, bimestre) máximo que provavelmente já foi publicado.

    O RREO é publicado até 30 dias após o fim de cada bimestre.
    Usamos uma estimativa conservadora: bimestre do mês anterior.
    Ex: em maio de 2026 → bimestre 2 (mar-abr) de 2026 provavelmente disponível.
    """
    hoje = datetime.now()
    mes_anterior = hoje.month - 1 if hoje.month > 1 else 12
    ano_ref = hoje.year if hoje.month > 1 else hoje.year - 1
    bimestre = (mes_anterior + 1) // 2  # mês → bimestre (1-6)
    return ano_ref, bimestre


def buscar_rreo_estado(cod_ibge: int, ano: int, periodo: int) -> pd.DataFrame:
    """
    Baixa o Anexo 01 do RREO para um estado, ano e bimestre específicos.

    Filtra na origem (antes de retornar) apenas as contas e colunas relevantes,
    reduzindo o volume de dados transportado para a memória.

    Retorna DataFrame vazio se:
      - Não há dados (bimestre futuro, estado ainda não publicou)
      - A requisição falhar após MAX_TENTATIVAS tentativas

    POR QUE co_tipo_demonstrativo="RREO"?
      Testado empiricamente: o valor "RREO" funciona para estados. A documentação
      menciona "COMPLETO" e "SIMPLIFICADO" (para municípios pequenos), mas para
      estados o valor correto parece ser "RREO".
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
            r = requests.get(URL_RREO, params=params, timeout=30, verify=False)
            r.raise_for_status()
            payload = r.json()

            # API retorna items vazio para combinações sem dados (bimestre futuro, etc.)
            if not payload.get("items"):
                return pd.DataFrame()

            df = pd.DataFrame(payload["items"])

            # ── Filtra contas e estágios relevantes ─────────────────────────
            df = df[df["cod_conta"].isin(CONTAS_DESPESA)].copy()
            df = df[df["coluna"].isin(COLUNAS_DESPESA)].copy()

            if df.empty:
                return pd.DataFrame()

            # ── Renomeia e converte ──────────────────────────────────────────
            df = df.rename(columns={
                "exercicio":   "ano",
                "instituicao": "ente",
            })
            # Converte de R$ para R$ milhões (mantém consistência com o pipeline federal)
            df["valor_milhoes"] = pd.to_numeric(df["valor"], errors="coerce") / 1e6

            return df[COLS_SAIDA].copy()

        except requests.exceptions.RequestException as exc:
            if tentativa == MAX_TENTATIVAS:
                log.error(
                    "Falha após %d tentativas: cod_ibge=%d ano=%d periodo=%d | %s",
                    MAX_TENTATIVAS, cod_ibge, ano, periodo, exc,
                )
                return pd.DataFrame()

            # Backoff exponencial: 2s → 4s → 8s antes de cada nova tentativa
            espera = 2 ** tentativa
            log.warning(
                "Tentativa %d/%d falhou (cod_ibge=%d, %d/%d). Aguardando %ds...",
                tentativa, MAX_TENTATIVAS, cod_ibge, ano, periodo, espera,
            )
            time.sleep(espera)

    return pd.DataFrame()  # nunca deve chegar aqui, mas satisfaz o type checker


# ── Etapa 3: Controle incremental ────────────────────────────────────────────

def _combinacoes_ja_carregadas() -> set:
    """
    Lê o parquet existente e retorna o conjunto de (cod_ibge, ano, periodo)
    já presentes, para evitar requisições repetidas.

    Por que usar um set de tuplas?
      Verificar pertencimento em um set Python é O(1) — muito mais rápido
      do que fazer pd.merge() ou filtros a cada iteração.
    """
    if not DESTINO.exists():
        return set()

    # Lê apenas as colunas de chave (evita carregar todo o parquet na memória)
    df = pd.read_parquet(DESTINO, columns=["cod_ibge", "ano", "periodo"])
    return set(zip(df["cod_ibge"], df["ano"], df["periodo"]))


def _construir_combinacoes(estados: pd.DataFrame) -> list[tuple]:
    """
    Gera a lista de todas as combinações (estado, ano, bimestre) a extrair,
    excluindo bimestres futuros que a API ainda não publicou.

    Retorna lista de tuplas: (cod_ibge, uf, ente, populacao, ano, periodo)
    """
    ano_limite, periodo_limite = _bimestre_maximo_atual()
    ano_atual = datetime.now().year
    combinacoes = []

    for _, estado in estados.iterrows():
        for ano in range(ANO_INICIO, ano_atual + 1):
            for periodo in range(1, 7):
                # Pula bimestres futuros (API retornaria vazio, mas poupa tempo)
                if ano == ano_limite and periodo > periodo_limite:
                    continue
                if ano > ano_limite:
                    continue
                combinacoes.append((
                    int(estado["cod_ibge"]),
                    estado["uf"],
                    estado["ente"],
                    int(estado["populacao"]),
                    ano,
                    periodo,
                ))

    return combinacoes


# ── Etapa 4: Salvamento incremental ──────────────────────────────────────────

def _salvar_lote(novos_dfs: list[pd.DataFrame]) -> int:
    """
    Concatena os DataFrames novos e os adiciona ao parquet existente.

    Por que re-ler o parquet a cada save?
      É a forma mais simples de garantir consistência sem ter de manter
      o DataFrame inteiro em memória. Para estados (~100k linhas), o custo
      é negligível. Para municípios em produção, considerar DuckDB ou
      particionamento por ano.

    Retorna o total de linhas no parquet após o save.
    """
    df_novo = pd.concat(novos_dfs, ignore_index=True)

    if DESTINO.exists():
        df_existente = pd.read_parquet(DESTINO)
        df_final = pd.concat([df_existente, df_novo], ignore_index=True)
    else:
        df_final = df_novo

    # Deduplica por segurança (chave: estado + ano + bimestre + conta + estágio)
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
    Extrai o histórico completo de gastos de todos os estados.

    Fluxo:
      1. Busca lista de estados
      2. Calcula quais combinações (estado, ano, bimestre) já existem no parquet
      3. Para cada combinação faltante: baixa, filtra, acumula
      4. Salva em lotes de SALVAR_A_CADA requisições (proteção contra interrupções)
    """
    estados = buscar_entes_estados()
    todas_combinacoes = _construir_combinacoes(estados)
    ja_carregadas = _combinacoes_ja_carregadas()

    pendentes = [
        c for c in todas_combinacoes
        if (c[0], c[4], c[5]) not in ja_carregadas  # (cod_ibge, ano, periodo)
    ]

    log.info(
        "Total: %d combinações | Já no parquet: %d | A buscar: %d",
        len(todas_combinacoes), len(ja_carregadas), len(pendentes),
    )

    if not pendentes:
        log.info("Parquet já está atualizado. Nada a fazer.")
        return

    lote_atual: list[pd.DataFrame] = []
    total_salvo = len(ja_carregadas) // (len(CONTAS_DESPESA) * len(COLUNAS_DESPESA)) or 0
    erros = 0

    for i, (cod_ibge, uf, ente, populacao, ano, periodo) in enumerate(pendentes, 1):
        # ── Log de progresso a cada 50 requisições ──────────────────────
        if i % 50 == 0 or i == 1:
            pct = 100 * i / len(pendentes)
            log.info(
                "Progresso: %d/%d (%.1f%%) | lote atual: %d | erros: %d",
                i, len(pendentes), pct, len(lote_atual), erros,
            )

        df = buscar_rreo_estado(cod_ibge, ano, periodo)

        if not df.empty:
            lote_atual.append(df)
        elif i > 1:  # Bimestres sem dados são esperados; só conta erro se tentou
            # (buscar_rreo_estado já logou o erro com detalhes)
            pass

        # ── Salva lote periodicamente ────────────────────────────────────
        if len(lote_atual) >= SALVAR_A_CADA:
            n_linhas = _salvar_lote(lote_atual)
            log.info("Lote salvo: parquet agora tem %d linhas", n_linhas)
            lote_atual = []

        time.sleep(INTERVALO_REQUISICAO)

    # ── Salva o restante que ficou no lote ───────────────────────────────
    if lote_atual:
        n_linhas = _salvar_lote(lote_atual)
        log.info("Lote final salvo: parquet tem %d linhas", n_linhas)


# ── Etapa 6: Ponto de entrada ─────────────────────────────────────────────────

def main() -> None:
    """Baixa o histórico de gastos estaduais e salva em parquet."""
    inicio = datetime.now()
    log.info("=== Início da extração de gastos estaduais ===")

    extrair_historico()

    # Salva metadados
    if DESTINO.exists():
        df_meta = pd.read_parquet(DESTINO, columns=["ano", "periodo", "cod_ibge"])
        meta = {
            "ultima_extracao":     datetime.now().isoformat(timespec="seconds"),
            "total_linhas":        len(df_meta),
            "total_combinacoes":   df_meta.drop_duplicates(["cod_ibge", "ano", "periodo"]).shape[0],
            "ano_mais_antigo":     int(df_meta["ano"].min()),
            "ano_mais_recente":    int(df_meta["ano"].max()),
            "duracao_segundos":    round((datetime.now() - inicio).total_seconds()),
        }
        META_FILE.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Metadados salvos: %s", meta)

    log.info("=== Extração concluída ===")


if __name__ == "__main__":
    main()
