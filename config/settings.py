"""
Configurações centrais do projeto.
Edite este arquivo para ajustar thresholds, janelas de análise e alertas.
"""
from dotenv import load_dotenv
load_dotenv()

from pathlib import Path

# ── Raiz do projeto ────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# ── API Portal da Transparência ────────────────────────────────────
API_BASE = "https://api.portaldatransparencia.gov.br/api-de-dados"
# Chave de API: cadastre-se em https://portaldatransparencia.gov.br/api-de-dados/cadastrar
# e substitua a variável de ambiente abaixo, ou preencha diretamente (não commitar em git).
API_KEY_ENV = "TRANSPARENCIA_API_KEY"

# Rate limit: 400 req/min (horário comercial) / 700 req/min (00h-06h) com chave
REQUEST_DELAY_SECONDS = 0.15    # pausa entre chamadas paginadas
MAX_RETRIES = 5
TIMEOUT_SECONDS = 30

# SSL: False enquanto aguarda TI liberar certificado corporativo
# Trocar para True (ou caminho do .pem) após resolução com TI
SSL_VERIFY_BULK = False

# ── Janelas de tempo padrão ────────────────────────────────────────
ANOS_HISTORICO = 11              # anos retroativos para linha de base
JANELA_ANOMALIA_MESES = 12      # meses usados no cálculo de z-score

# ── Thresholds de alerta (configuráveis por rubrica) ──────────────
ALERTA_ZSCORE_PADRAO = 2.0      # desvios-padrão para alerta amarelo
ALERTA_ZSCORE_CRITICO = 3.0     # desvios-padrão para alerta vermelho

# Rubricas de alta vigilância política (Nature of Expense codes)
# Formato: {código_natureza: "descrição legível"}
RUBRICAS_ALTA_VIGILANCIA = {
    "339039": "Outros Serviços de Terceiros - PJ (cartão corporativo)",
    "339014": "Diárias - Civil",
    "339033": "Passagens e Despesas com Locomoção",
    "339036": "Outros Serviços de Terceiros - PF",
    "339040": "Serviços de Tecnologia da Informação",
    "318001": "Transferências a Municípios - Emendas",
    "339092": "Despesas de Exercícios Anteriores",
    "444042": "Contribuições - Publicidade Institucional",
}

# Órgãos de alta vigilância (UG / código SIAFI)
ORGAOS_ALTA_VIGILANCIA = {
    "200001": "Presidência da República",
    "200999": "Gabinete do Presidente",
    "110404": "Câmara dos Deputados",
    "110403": "Senado Federal",
    "201001": "Casa Civil",
    "201002": "Secretaria de Comunicação Social",
}

# ── Colunas padronizadas (Silver) ──────────────────────────────────
COLUNAS_SILVER_DESPESAS = [
    "id_empenho",
    "data_empenho",
    "ano",
    "mes",
    "codigo_orgao",
    "nome_orgao",
    "codigo_ug",
    "nome_ug",
    "codigo_funcao",
    "nome_funcao",
    "codigo_subfuncao",
    "nome_subfuncao",
    "codigo_programa",
    "nome_programa",
    "codigo_acao",
    "nome_acao",
    "codigo_natureza_despesa",
    "nome_natureza_despesa",
    "codigo_elemento",
    "nome_elemento",
    "modalidade_licitacao",
    "valor_empenhado",
    "valor_liquidado",
    "valor_pago",
    "ingested_at",          # timestamp de ingestão — nunca remover
    "fonte_rubrica_flag",   # True se está em RUBRICAS_ALTA_VIGILANCIA
    "orgao_vigilancia_flag",# True se está em ORGAOS_ALTA_VIGILANCIA
]
