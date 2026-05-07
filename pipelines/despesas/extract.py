"""
pipelines/despesas/extract.py  —  Operário 1: Coleta
─────────────────────────────────────────────────────
Consome a API do Portal da Transparência e salva JSON bruto em data/despesas/raw/.

Endpoint atual (aguardando liberação de /empenhos pelo suporte do portal):
  GET /api-de-dados/despesas/por-orgao

Estratégia: itera todos os órgãos SIAFI como orgaoSuperior.
  Códigos subordinados retornam [] e são ignorados automaticamente.
  Resultados são deduplicados por codigoOrgao antes de salvar.

Granularidade: agregado por órgão (empenhado / liquidado / pago por mês).
  Quando /empenhos for liberado, trocar extrair_despesas_mes() pela versão
  original (granularidade por empenho individual).

Uso:
  python pipelines/despesas/extract.py --ano 2024 --mes 5
  python pipelines/despesas/extract.py --ano 2024          # extrai todos os meses do ano
  python pipelines/despesas/extract.py --historico          # extrai ANOS_HISTORICO anos

Ambientes corporativos com proxy SSL:
  # Opção A — certificado da empresa (recomendado, peça para a TI):
  python pipelines/despesas/extract.py --ano 2024 --mes 1 --ssl-cert config/fiesp-ca.pem

  # Opção B — desabilitar verificação SSL (só para desenvolvimento):
  python pipelines/despesas/extract.py --ano 2024 --mes 1 --sem-ssl
"""

import argparse
import json
import logging
import os
import sys
import time
import warnings
from datetime import date, datetime
from pathlib import Path

import requests
import urllib3

# Permite rodar o script direto da raiz do projeto
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import (
    API_BASE,
    API_KEY_ENV,
    ANOS_HISTORICO,
    DATA_DIR,
    MAX_RETRIES,
    REQUEST_DELAY_SECONDS,
    TIMEOUT_SECONDS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RAW_DIR = DATA_DIR / "despesas" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# Configuração SSL — definida em main() e usada globalmente no módulo
# True = verifica SSL (padrão seguro)
# False = desabilita verificação (só desenvolvimento em rede corporativa)
# "caminho/cert.pem" = usa certificado corporativo fornecido pela TI
# TEMPORÁRIO: False enquanto aguarda TI liberar domínio portaldatransparencia.gov.br
SSL_VERIFY = False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── Cliente HTTP ───────────────────────────────────────────────────

def _build_headers() -> dict:
    api_key = os.environ.get(API_KEY_ENV, "")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["chave-api-dados"] = api_key
    else:
        log.warning(
            "Variável %s não definida. Requisições sem chave têm rate limit baixo (~90/min). "
            "Cadastre em https://portaldatransparencia.gov.br/api-de-dados/cadastrar",
            API_KEY_ENV,
        )
    return headers


def _get_with_retry(url: str, params: dict, headers: dict) -> list[dict]:
    """GET com retry exponencial. Retorna lista de registros ou [] em caso de falha."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url, params=params, headers=headers,
                timeout=TIMEOUT_SECONDS, verify=SSL_VERIFY,
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = 2 ** attempt
                log.warning("Rate limit atingido. Aguardando %ss...", wait)
                time.sleep(wait)
            elif resp.status_code == 404:
                log.info("Sem dados para os parâmetros %s", params)
                return []
            else:
                log.error("HTTP %s para %s | %s", resp.status_code, url, resp.text[:200])
                time.sleep(2 ** attempt)
        except requests.exceptions.RequestException as exc:
            log.error("Erro de rede (tentativa %d/%d): %s", attempt, MAX_RETRIES, exc)
            time.sleep(2 ** attempt)
    return []


# ── Funções de extração ────────────────────────────────────────────

def _listar_orgaos(headers: dict) -> list[str]:
    """Retorna todos os códigos de órgão do SIAFI."""
    url = f"{API_BASE}/orgaos-siafi"
    codigos = []
    pagina = 1
    while True:
        registros = _get_with_retry(url, {"pagina": pagina}, headers)
        if not registros:
            break
        codigos.extend(r["codigo"] for r in registros)
        if len(registros) < 15:
            break
        pagina += 1
        time.sleep(REQUEST_DELAY_SECONDS)
    log.info("Órgãos SIAFI carregados: %d códigos", len(codigos))
    return codigos


def extrair_despesas_mes(ano: int, mes: int, headers: dict) -> list[dict]:
    """
    Extrai despesas agregadas por órgão para um mês via /despesas/por-orgao.

    Itera todos os órgãos SIAFI como orgaoSuperior. Códigos subordinados
    retornam [] e são pulados. Resultados deduplicados por codigoOrgao.

    Substitui temporariamente o endpoint /empenhos (bloqueado na chave atual).
    """
    url = f"{API_BASE}/despesas/por-orgao"
    mes_str = f"{mes:02d}"
    log.info("Extraindo despesas %d/%s...", ano, mes_str)

    orgaos = _listar_orgaos(headers)
    vistos: dict[str, dict] = {}   # codigoOrgao -> registro (dedup)
    orgaos_com_dados = 0

    for i, codigo in enumerate(orgaos, 1):
        pagina = 1
        while True:
            params = {
                "ano": ano,
                "mes": mes_str,
                "orgaoSuperior": codigo,
                "pagina": pagina,
            }
            registros = _get_with_retry(url, params, headers)
            if not registros:
                break
            novos = 0
            for r in registros:
                chave = r["codigoOrgao"]
                if chave not in vistos:
                    vistos[chave] = r
                    novos += 1
            if novos:
                orgaos_com_dados += 1
                log.info("  [%d/%d] orgaoSuperior=%s — %d subordinados | total acumulado: %d",
                         i, len(orgaos), codigo, len(registros), len(vistos))
            if len(registros) < 500:
                break
            pagina += 1
            time.sleep(REQUEST_DELAY_SECONDS)

        time.sleep(REQUEST_DELAY_SECONDS)

    log.info("Extração concluída: %d órgãos superiores com dados, %d órgãos únicos",
             orgaos_com_dados, len(vistos))
    return list(vistos.values())


def salvar_raw(dados: list[dict], ano: int, mes: int) -> Path:
    """Salva JSON bruto com metadados de coleta. Nunca sobrescreve sem backup."""
    mes_str = f"{mes:02d}"
    nome_arquivo = RAW_DIR / f"despesas_{ano}_{mes_str}.json"

    # Backup se arquivo já existir (re-execução)
    if nome_arquivo.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = nome_arquivo.with_suffix(f".{ts}.bak.json")
        nome_arquivo.rename(backup)
        log.info("Backup do arquivo anterior: %s", backup.name)

    envelope = {
        "meta": {
            "fonte": "portaldatransparencia.gov.br",
            "endpoint": "/api-de-dados/despesas/por-orgao",
            "ano": ano,
            "mes": mes,
            "total_registros": len(dados),
            "extraido_em": datetime.utcnow().isoformat() + "Z",
        },
        "dados": dados,
    }

    with open(nome_arquivo, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2)

    log.info("Raw salvo: %s (%d registros, %.1f MB)",
             nome_arquivo.name, len(dados), nome_arquivo.stat().st_size / 1e6)
    return nome_arquivo


# ── Ponto de entrada ───────────────────────────────────────────────

def main():
    global SSL_VERIFY

    parser = argparse.ArgumentParser(description="Extrai despesas do Portal da Transparência")
    parser.add_argument("--ano", type=int, default=date.today().year)
    parser.add_argument("--mes", type=int, default=None,
                        help="Mês específico (1-12). Omitir para extrair todos os meses do ano.")
    parser.add_argument("--historico", action="store_true",
                        help=f"Extrai os últimos {ANOS_HISTORICO} anos completos")

    # ── Opções para redes corporativas com proxy SSL ───────────────
    ssl_group = parser.add_mutually_exclusive_group()
    ssl_group.add_argument(
        "--sem-ssl", action="store_true",
        help="Desabilita verificação SSL. Use apenas em desenvolvimento em rede corporativa.",
    )
    ssl_group.add_argument(
        "--ssl-cert", metavar="CAMINHO",
        help="Caminho para o certificado CA corporativo (.pem). Peça para a TI.",
    )
    args = parser.parse_args()

    # Configura SSL antes de qualquer requisição
    if args.sem_ssl:
        SSL_VERIFY = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        warnings.warn(
            "\n⚠️  Verificação SSL desabilitada. Use apenas em desenvolvimento.\n"
            "    Solicite o certificado corporativo à TI para uso em produção.",
            stacklevel=2,
        )
        log.warning("SSL_VERIFY=False — conexões não verificadas.")
    elif args.ssl_cert:
        cert_path = Path(args.ssl_cert)
        if not cert_path.exists():
            log.error("Certificado não encontrado: %s", cert_path)
            sys.exit(1)
        SSL_VERIFY = str(cert_path)
        log.info("Usando certificado corporativo: %s", cert_path)
    else:
        SSL_VERIFY = False  # TEMPORÁRIO: aguardando TI liberar domínio

    headers = _build_headers()

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

    log.info("Iniciando extração: %d combinações ano/mês", len(pares))

    for ano, mes in pares:
        dados = extrair_despesas_mes(ano, mes, headers)
        if dados:
            salvar_raw(dados, ano, mes)
        else:
            log.warning("Nenhum dado retornado para %d/%02d", ano, mes)
        time.sleep(REQUEST_DELAY_SECONDS)

    log.info("Extração concluída.")


if __name__ == "__main__":
    main()
