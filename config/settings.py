"""
config/settings.py  —  Configurações centrais do projeto Gastômetro FIESP
──────────────────────────────────────────────────────────────────────────
Este arquivo funciona como um "painel de controle" do projeto: qualquer
valor numérico ou de configuração que possa precisar de ajuste no futuro
deve ficar aqui, não espalhado pelo código.

Ao centralizar aqui, basta mudar um número neste arquivo para que todo o
projeto se comporte de forma diferente — sem precisar abrir cada script.

Como usar:
  Em qualquer outro arquivo do projeto, importe assim:
    from config.settings import DATA_DIR
"""

from pathlib import Path

# ── Diretórios raiz ────────────────────────────────────────────────────────
# Path(__file__) é o caminho deste arquivo (config/settings.py).
# .resolve() transforma em caminho absoluto.
# .parent sobe um nível (de config/ para a raiz do projeto).
# .parent novamente sobe mais um nível (da raiz para onde o projeto está).
BASE_DIR = Path(__file__).resolve().parent.parent

# Pasta raiz de todos os dados do projeto.
# Todos os pipelines salvam e leem dados a partir desta pasta.
DATA_DIR = BASE_DIR / "data"
