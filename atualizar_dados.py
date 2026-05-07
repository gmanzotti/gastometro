"""
atualizar_dados.py  —  O Gerente
──────────────────────────────────
Orquestra a execução completa do pipeline: extract → silver → gold.
Ponto único de entrada para rodar manualmente ou via cron/Airflow.

Exemplos de uso:
  python atualizar_dados.py                        # atualiza mês atual
  python atualizar_dados.py --ano 2024 --mes 3     # mês específico
  python atualizar_dados.py --historico            # carga histórica completa (demorado)
  python atualizar_dados.py --apenas-gold          # recalcula gold sem re-extrair
  python atualizar_dados.py --fontes despesas cartao  # fontes específicas

Cron sugerido (atualiza todo dia às 8h, após janela de atualização da API):
  0 8 * * * cd /caminho/projeto && python atualizar_dados.py >> logs/cron.log 2>&1
"""

import argparse
import importlib
import logging
import sys
import traceback
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent / "logs" / "pipeline.log", mode="a"),
    ],
)
log = logging.getLogger("gerente")

# Garante que logs/ existe
(Path(__file__).parent / "logs").mkdir(exist_ok=True)

# ── Registro de fontes disponíveis ────────────────────────────────
# Para adicionar nova fonte: implemente extract/silver/gold em pipelines/<fonte>/
# e adicione a entrada abaixo.
FONTES_DISPONIVEIS = {
    "despesas": {
        "extract": "pipelines.despesas.extract",
        "silver":  "pipelines.despesas.silver",
        "gold":    "pipelines.despesas.gold",
        "descricao": "Empenhos, liquidações e pagamentos (D+1)",
    },
    # Próximas iterações:
    # "cartao_corporativo": {
    #     "extract": "pipelines.cartao_corporativo.extract",
    #     "silver":  "pipelines.cartao_corporativo.silver",
    #     "gold":    "pipelines.cartao_corporativo.gold",
    #     "descricao": "Gastos com cartão corporativo (mensal)",
    # },
    # "emendas": {
    #     "extract": "pipelines.emendas.extract",
    #     "silver":  "pipelines.emendas.silver",
    #     "gold":    "pipelines.emendas.gold",
    #     "descricao": "Emendas parlamentares (D+1 execução)",
    # },
}


def _executar_etapa(modulo_path: str, fn_name: str = "main", **kwargs) -> bool:
    """Importa e chama main() do módulo indicado. Retorna True se bem-sucedido."""
    try:
        modulo = importlib.import_module(modulo_path)
        # Injeta argumentos no sys.argv para scripts que usam argparse
        sys.argv = [modulo_path]
        for k, v in kwargs.items():
            if v is not None:
                sys.argv += [f"--{k}", str(v)]
            elif k.startswith("flag_") and v:
                sys.argv.append(f"--{k[5:]}")
        getattr(modulo, fn_name)()
        return True
    except SystemExit as e:
        if e.code != 0:
            log.error("Módulo %s saiu com código %s", modulo_path, e.code)
            return False
        return True
    except Exception:
        log.error("Erro em %s:\n%s", modulo_path, traceback.format_exc())
        return False


def pipeline_completo(
    fontes: list[str],
    ano: int,
    mes: int | None,
    historico: bool = False,
    apenas_gold: bool = False,
) -> dict[str, bool]:
    """
    Executa extract → silver → gold para cada fonte listada.
    Retorna dict {fonte: sucesso}.
    """
    resultados = {}
    kwargs_tempo = {}
    if historico:
        kwargs_tempo["flag_historico"] = True
    else:
        kwargs_tempo["ano"] = ano
        if mes:
            kwargs_tempo["mes"] = mes

    for fonte in fontes:
        cfg = FONTES_DISPONIVEIS[fonte]
        log.info("═" * 60)
        log.info("FONTE: %s — %s", fonte.upper(), cfg["descricao"])
        log.info("═" * 60)

        sucesso = True

        if not apenas_gold:
            log.info("[1/3] Extract...")
            if not _executar_etapa(cfg["extract"], **kwargs_tempo):
                log.error("Extract falhou para %s. Abortando silver/gold desta fonte.", fonte)
                resultados[fonte] = False
                continue

            log.info("[2/3] Silver...")
            if not _executar_etapa(cfg["silver"], **kwargs_tempo):
                log.error("Silver falhou para %s. Abortando gold desta fonte.", fonte)
                resultados[fonte] = False
                continue

        log.info("[3/3] Gold...")
        sucesso = _executar_etapa(cfg["gold"])
        resultados[fonte] = sucesso

    return resultados


def main():
    parser = argparse.ArgumentParser(
        description="Gerente do pipeline de acompanhamento fiscal"
    )
    parser.add_argument("--ano", type=int, default=date.today().year)
    parser.add_argument("--mes", type=int, default=date.today().month)
    parser.add_argument("--historico", action="store_true",
                        help="Executa carga histórica completa (demorado)")
    parser.add_argument("--apenas-gold", action="store_true",
                        help="Recalcula gold sem re-extrair dados")
    parser.add_argument(
        "--fontes", nargs="+",
        choices=list(FONTES_DISPONIVEIS.keys()),
        default=list(FONTES_DISPONIVEIS.keys()),
        help="Fontes a processar. Default: todas as ativas",
    )
    args = parser.parse_args()

    log.info("Iniciando pipeline | fontes=%s | ano=%d | mes=%s | historico=%s",
             args.fontes, args.ano, args.mes, args.historico)

    resultados = pipeline_completo(
        fontes=args.fontes,
        ano=args.ano,
        mes=args.mes,
        historico=args.historico,
        apenas_gold=args.apenas_gold,
    )

    # Resumo final
    log.info("═" * 60)
    log.info("RESUMO DO PIPELINE:")
    for fonte, ok in resultados.items():
        status = "✓ OK" if ok else "✗ FALHOU"
        log.info("  %s: %s", fonte, status)

    falhas = [f for f, ok in resultados.items() if not ok]
    if falhas:
        log.error("Pipeline concluído COM FALHAS: %s", falhas)
        sys.exit(1)
    else:
        log.info("Pipeline concluído com sucesso.")


if __name__ == "__main__":
    main()
