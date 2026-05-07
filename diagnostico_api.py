"""
diagnostico_api.py  —  Mapeamento de status de todos os endpoints da API
do Portal da Transparência relevantes para o projeto.

Uso:
  python diagnostico_api.py

Saída: tabela no terminal + arquivo diagnostico_api_resultado.txt
"""

import os
import sys
import time
import json
from pathlib import Path
from datetime import datetime

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.settings import API_BASE, API_KEY_ENV

# ── Chave de API ───────────────────────────────────────────────────
API_KEY = os.environ.get(API_KEY_ENV, "")
HEADERS = {"Accept": "application/json"}
if API_KEY:
    HEADERS["chave-api-dados"] = API_KEY

ANO  = 2025
MES  = "03"
ORGAO_SUPERIOR = "20000"   # Presidência da República (código curto)
MUNICIPIO_IBGE = "3550308" # São Paulo
UF             = "SP"

# ── Endpoints a testar ─────────────────────────────────────────────
# Formato: (rótulo, path, params)
ENDPOINTS = [
    # ── Despesas ──────────────────────────────────────────────────
    ("despesas/por-orgao",
     "/despesas/por-orgao",
     {"ano": ANO, "mes": MES, "orgaoSuperior": ORGAO_SUPERIOR, "pagina": 1}),

    ("despesas/por-programa",
     "/despesas/por-programa",
     {"ano": ANO, "mes": MES, "pagina": 1}),

    ("despesas/por-acao",
     "/despesas/por-acao",
     {"ano": ANO, "mes": MES, "pagina": 1}),

    ("despesas/por-funcao",
     "/despesas/por-funcao",
     {"ano": ANO, "mes": MES, "pagina": 1}),

    ("despesas/por-subfuncao",
     "/despesas/por-subfuncao",
     {"ano": ANO, "mes": MES, "pagina": 1}),

    ("despesas/por-elemento",
     "/despesas/por-elemento",
     {"ano": ANO, "mes": MES, "pagina": 1}),

    ("despesas/empenhos",
     "/despesas/empenhos",
     {"ano": ANO, "mes": MES, "pagina": 1}),

    ("despesas/empenhos (com orgao)",
     "/despesas/empenhos",
     {"ano": ANO, "mes": MES, "codigoOrgao": ORGAO_SUPERIOR, "pagina": 1}),

    # ── Receitas ───────────────────────────────────────────────────
    ("receitas",
     "/receitas",
     {"ano": ANO, "mes": MES, "pagina": 1}),

    ("receitas/por-orgao",
     "/receitas/por-orgao",
     {"ano": ANO, "mes": MES, "pagina": 1}),

    # ── Transferências ─────────────────────────────────────────────
    ("transferencias/natureza",
     "/transferencias/natureza",
     {"ano": ANO, "mes": MES, "pagina": 1}),

    ("transferencias/municipio",
     "/transferencias/municipio",
     {"ano": ANO, "mesAno": f"{MES}{ANO}", "codigoIbge": MUNICIPIO_IBGE, "pagina": 1}),

    ("transferencias/uf",
     "/transferencias/uf",
     {"anoMes": f"{ANO}{MES}", "uf": UF, "pagina": 1}),

    # ── Órgãos ─────────────────────────────────────────────────────
    ("orgaos-siafi",
     "/orgaos-siafi",
     {"pagina": 1}),

    ("orgaos-siafi (com busca)",
     "/orgaos-siafi",
     {"pagina": 1, "descricao": "presidencia"}),

    # ── Servidores / RH ────────────────────────────────────────────
    ("servidores",
     "/servidores",
     {"pagina": 1}),

    ("remuneracao",
     "/servidores/remuneracao",
     {"pagina": 1, "mesAno": f"{MES}{ANO}"}),

    # ── Benefícios sociais ─────────────────────────────────────────
    ("bolsa-familia-disponibilidade",
     "/bolsa-familia-disponibilidade",
     {"mesAno": f"{ANO}{MES}"}),

    ("bolsa-familia-por-municipio",
     "/bolsa-familia-por-municipio",
     {"mesAno": f"{ANO}{MES}", "codigoIbge": MUNICIPIO_IBGE, "pagina": 1}),

    # ── Licitações / Contratos ─────────────────────────────────────
    ("licitacoes",
     "/licitacoes",
     {"dataInicial": "01/03/2025", "dataFinal": "31/03/2025", "pagina": 1}),

    ("contratos",
     "/contratos",
     {"dataInicial": "01/01/2025", "dataFinal": "31/03/2025", "pagina": 1}),
]


# ── Funções ────────────────────────────────────────────────────────

def _status_label(code: int) -> str:
    return {
        200: "[OK]  200 OK",
        400: "[!]   400 Bad Request",
        401: "[KEY] 401 Nao autenticado",
        403: "[BLQ] 403 Proibido (sem permissao)",
        404: "[404] 404 Nao encontrado",
        422: "[!]   422 Parametro invalido",
        429: "[LIM] 429 Rate limit",
        500: "[ERR] 500 Erro interno",
        503: "[ERR] 503 Servico indisponivel",
    }.get(code, f"[?]   {code}")


def _resumo_resposta(resp: requests.Response) -> str:
    """Extrai um resumo legível da resposta."""
    try:
        corpo = resp.json()
        if isinstance(corpo, list):
            return f"{len(corpo)} registros"
        if isinstance(corpo, dict):
            # Tenta extrair mensagem de erro
            for chave in ("message", "mensagem", "erro", "error", "detail", "title"):
                if chave in corpo:
                    msg = str(corpo[chave])[:120]
                    return f'"{msg}"'
            return f"dict com chaves: {list(corpo.keys())[:5]}"
    except Exception:
        texto = resp.text[:120].strip().replace("\n", " ")
        return f'"{texto}"' if texto else "(sem corpo)"
    return str(corpo)[:120]


def testar_endpoint(rotulo: str, path: str, params: dict) -> dict:
    url = API_BASE + path
    try:
        resp = requests.get(url, params=params, headers=HEADERS,
                            timeout=20, verify=False)
        return {
            "rotulo":  rotulo,
            "path":    path,
            "status":  resp.status_code,
            "label":   _status_label(resp.status_code),
            "resumo":  _resumo_resposta(resp),
            "params":  params,
        }
    except requests.exceptions.ConnectTimeout:
        return {"rotulo": rotulo, "path": path, "status": -1,
                "label": "[TIM] Timeout de conexao", "resumo": "", "params": params}
    except requests.exceptions.RequestException as exc:
        return {"rotulo": rotulo, "path": path, "status": -1,
                "label": "[NET] Erro de rede", "resumo": str(exc)[:80], "params": params}


def main():
    if not API_KEY:
        print("⚠️  Aviso: variável TRANSPARENCIA_API_KEY não definida. "
              "Rodando sem chave (rate limit baixo).\n")

    print(f"Diagnóstico iniciado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Base URL : {API_BASE}")
    print(f"Chave    : {'sim (***' + API_KEY[-4:] + ')' if API_KEY else 'NÃO DEFINIDA'}")
    print(f"Ano/Mês  : {ANO}/{MES}\n")
    print("-" * 100)

    resultados = []
    for rotulo, path, params in ENDPOINTS:
        print(f"  Testando: {rotulo}...", end=" ", flush=True)
        r = testar_endpoint(rotulo, path, params)
        resultados.append(r)
        print(r["label"])
        time.sleep(0.3)   # respeita rate limit

    # ── Tabela final ──────────────────────────────────────────────
    print("\n" + "═" * 100)
    print(f"{'ENDPOINT':<40} {'STATUS':<32} {'RESPOSTA / OBSERVAÇÃO'}")
    print("-" * 100)

    linhas_txt = []
    linhas_txt.append(f"{'ENDPOINT':<40} {'STATUS':<32} {'RESPOSTA / OBSERVAÇÃO'}")
    linhas_txt.append("─" * 100)

    for r in resultados:
        linha = f"{r['rotulo']:<40} {r['label']:<32} {r['resumo']}"
        print(linha)
        linhas_txt.append(linha)

    print("=" * 100)

    # ── Resumo por categoria ──────────────────────────────────────
    ok      = [r for r in resultados if r["status"] == 200]
    negados = [r for r in resultados if r["status"] in (401, 403)]
    erros   = [r for r in resultados if r["status"] not in (200, 401, 403) and r["status"] != -1]
    rede    = [r for r in resultados if r["status"] == -1]

    print(f"\nResumo: {len(ok)} OK | {len(negados)} sem permissão | "
          f"{len(erros)} outros erros | {len(rede)} falha de rede")

    if negados:
        print("\nEndpoints bloqueados (solicitar liberação):")
        for r in negados:
            print(f"  • {r['path']}  →  {r['label']}  |  {r['resumo']}")

    # ── Salva resultado em arquivo ────────────────────────────────
    saida = Path(__file__).parent / "diagnostico_api_resultado.txt"
    conteudo = "\n".join(linhas_txt)
    conteudo += f"\n\nGerado em: {datetime.now().isoformat()}"
    conteudo += f"\nChave API: {'sim' if API_KEY else 'nao'}"
    saida.write_text(conteudo, encoding="utf-8")
    print(f"\nResultado salvo em: {saida.name}")


if __name__ == "__main__":
    main()
