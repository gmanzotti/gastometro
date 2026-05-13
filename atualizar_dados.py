"""
atualizar_dados.py  —  Ponto único de entrada para atualizar o Gastômetro
──────────────────────────────────────────────────────────────────────────
Este é o único script que você precisa rodar para manter o painel em dia.

O que ele faz, em ordem:
  1. Baixa os dados mais recentes do Tesouro Nacional (RTN)
     → salva em data/rtn/rtn_mensal.parquet
  2. Recalcula a taxa de gastos por segundo (usado pelo contador em tempo real)
     → salva em data/contador_fiscal.json

Quando rodar:
  Idealmente 1 vez por mês, após a Secretaria do Tesouro Nacional publicar
  o novo boletim RTN (normalmente na segunda semana de cada mês).

Como rodar manualmente:
  python atualizar_dados.py

Como automatizar (Linux/Mac — agendamento cron):
  0 9 15 * * cd /caminho/do/projeto && python atualizar_dados.py >> logs/cron.log 2>&1
  (roda todo dia 15 às 9h, que geralmente já tem o RTN do mês anterior)

Dependências de dados:
  - Nenhuma entrada manual necessária.
  - O script baixa tudo da internet automaticamente.
  - Precisa de conexão com o site do Tesouro Nacional.
"""

import logging
import sys
import traceback
from pathlib import Path

# Garante que a pasta logs/ existe ANTES de configurar o FileHandler
(Path(__file__).parent / "logs").mkdir(exist_ok=True)

# Configura o sistema de log: mostra mensagens na tela E grava em arquivo
# O formato inclui data/hora, nível (INFO/ERROR) e a mensagem.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        # Exibe no terminal enquanto o script roda
        logging.StreamHandler(),
        # Também grava em arquivo para consulta posterior
        logging.FileHandler(Path(__file__).parent / "logs" / "pipeline.log", mode="a"),
    ],
)
log = logging.getLogger("atualizar_dados")

# Adiciona a raiz do projeto ao Python path para que os imports funcionem
# independentemente de onde o script é chamado.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _executar(nome: str, fn) -> bool:
    """
    Executa uma função de pipeline e captura erros sem derrubar o programa.
    Retorna True se bem-sucedido, False se houve qualquer erro.
    Assim, se a etapa 1 falhar, a etapa 2 ainda é tentada.
    """
    log.info("── Iniciando: %s", nome)
    try:
        fn()
        log.info("── OK: %s", nome)
        return True
    except Exception:
        # traceback.format_exc() captura a mensagem de erro completa,
        # útil para depurar o que deu errado.
        log.error("── FALHOU: %s\n%s", nome, traceback.format_exc())
        return False


def main():
    log.info("=" * 60)
    log.info("INICIANDO ATUALIZAÇÃO DO GASTÔMETRO FIESP")
    log.info("=" * 60)

    resultados = {}

    # ── Etapa 1: Baixar RTN (Resultado do Tesouro Nacional) ───────────────
    # A RTN é o relatório oficial mensal publicado pela Secretaria do Tesouro
    # Nacional com todos os dados de receita, despesa e resultado primário do
    # Governo Federal. É a fonte de todos os gráficos do painel.
    from pipelines.rtn.load import main as rtn_main
    resultados["RTN (Tesouro Nacional)"] = _executar("RTN (Tesouro Nacional)", rtn_main)

    # ── Etapa 2: Recalcular o contador fiscal ─────────────────────────────
    # Com base na RTN atualizada, recalcula a previsão de gasto mensal e
    # converte para "R$ por segundo" — o número que aparece girando no painel.
    from pipelines.contador_fiscal import main as contador_main
    resultados["Contador fiscal"] = _executar("Contador fiscal", contador_main)

    # ── Resumo final ──────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("RESULTADO DA ATUALIZAÇÃO:")
    falhas = []
    for etapa, ok in resultados.items():
        status = "✓ OK" if ok else "✗ FALHOU"
        log.info("  %s: %s", etapa, status)
        if not ok:
            falhas.append(etapa)

    if falhas:
        log.error("Atualização concluída COM FALHAS: %s", falhas)
        log.error("Verifique o log acima para detalhes.")
        sys.exit(1)  # código de saída 1 = erro (útil para scripts de automação)
    else:
        log.info("Atualização concluída com sucesso. O painel está em dia.")


if __name__ == "__main__":
    main()
