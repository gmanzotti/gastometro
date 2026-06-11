"""
pipelines/rtn/load.py  —  Baixa e processa a RTN do Tesouro Nacional
──────────────────────────────────────────────────────────────────────
O QUE É A RTN?
  RTN significa "Resultado do Tesouro Nacional". É um relatório mensal
  publicado pela Secretaria do Tesouro Nacional (STN) que consolida todos
  os dados fiscais do Governo Federal: quanto entrou de receita, quanto
  saiu de despesa, e qual foi o resultado (superávit ou déficit).

  É a fonte oficial mais confiável para acompanhamento fiscal, pois passa
  pela auditoria do Tesouro antes de ser publicada.

O QUE ESTE SCRIPT FAZ?
  1. Baixa o arquivo Excel da RTN direto do site do Tesouro Nacional
  2. Lê 6 abas relevantes do Excel:

     SÉRIES MENSAIS (uma linha por indicador, uma coluna por mês):
       1.2   → série mensal DETALHADA em R$ correntes (nominal)
       1.2-A → série mensal DETALHADA em R$ constantes deflacionados pelo IPCA
               (usamos 1.2 em vez de 1.1 porque a 1.1 é uma versão resumida —
               a 1.2 tem mais rubricas desagregadas, permitindo análises futuras)

     INVESTIMENTO PÚBLICO (todas as rubricas, prefixadas com "INV "):
       1.3   → investimento público em R$ correntes: total, por função
               orçamentária e por natureza da despesa (obras, equipamentos...)
       1.3-A → as mesmas rubricas em R$ constantes
               (o investimento aparece diluído nas rubricas de despesa nas abas
               1.2/1.2-A; as abas 1.3/1.3-A isolam esse dado como agregado,
               o que nos permite separar gastos correntes de investimento)

     SÉRIES ANUAIS (usadas só para calcular o PIB, base para % do PIB):
       2.2   → série anual DETALHADA em R$ correntes
       2.2-A → série anual DETALHADA como % do PIB

  3. Transforma de "formato wide" para "formato tidy/long" (ver abaixo)
  4. Calcula o PIB anual e o % que cada item representa do PIB
  5. Salva tudo em um único arquivo Parquet

FORMATO WIDE vs. FORMATO TIDY (LONG):
  O Excel da RTN vem em "formato wide": cada coluna é um mês.
      indicador | jan/23 | fev/23 | mar/23 | ...
      Receita   | 200    | 210    | 220    | ...
      Despesa   | 180    | 190    | 195    | ...

  O formato "tidy/long" (que usamos) tem uma linha por observação:
      indicador | mes   | valor
      Receita   | jan23 | 200
      Receita   | fev23 | 210
      Despesa   | jan23 | 180
      ...

  O formato tidy é muito mais fácil de filtrar, plotar e cruzar com outros dados.
  A função `melt` do pandas faz essa transformação.

SAÍDA:
  data/rtn/rtn_mensal.parquet — arquivo Parquet com colunas:
    ano, mes, data, discriminacao, corrente_milhoes, constante_milhoes, pct_pib

  As linhas de investimento (originadas das abas 1.3/1.3-A) aparecem no
  mesmo parquet, com o prefixo "INV " na coluna `discriminacao`. Para filtrá-las:
    df[df["discriminacao"].str.startswith("INV ")]

  data/rtn/metadata.json — período-base do deflator IPCA e data do último dado

COMO RODAR:
  python pipelines/rtn/load.py
  (normalmente chamado via atualizar_dados.py)
"""

import io
import json
import logging
import re
import sys
from datetime import datetime as _dt
from pathlib import Path

import pandas as pd
import requests
import urllib3

# Suprime os avisos de SSL — o site do Tesouro tem certificado com problema
# de cadeia que o Python rejeita em redes corporativas. verify=False contorna
# isso, mas em produção o ideal é resolver com o TI.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Adiciona a raiz do projeto ao caminho do Python para que o import abaixo funcione
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# URL do arquivo Excel da série histórica da RTN no site do Tesouro Nacional
URL_RTN = (
    "http://sisweb.tesouro.gov.br/apex/cosis/thot/link/rtn/serie-historica?conteudo=cdn"
)

RTN_DIR  = DATA_DIR / "rtn"
RTN_DIR.mkdir(parents=True, exist_ok=True)   # cria a pasta se não existir

DESTINO   = RTN_DIR / "rtn_mensal.parquet"   # arquivo de saída principal
META_FILE = RTN_DIR / "metadata.json"         # metadados (base IPCA, última data)

# Prefixo aplicado às rubricas extraídas das abas 1.3/1.3-A.
# POR QUE PREFIXAR? A numeração das abas 1.2 e 1.3 colide: "2.1" significa
# "FPM/FPE/IPI-EE" na aba 1.2, mas "Investimentos (GND 4)" na aba 1.3.
# Como todas as séries vivem na mesma coluna `discriminacao` do parquet,
# o prefixo "INV " garante que filtros por str.startswith() nunca misturem
# rubricas de abas diferentes.
PREFIXO_INVESTIMENTO = "INV "


# ── Etapa 1: Download ─────────────────────────────────────────────────────

def baixar_excel() -> bytes:
    """Faz o download do Excel da RTN e retorna o conteúdo em bytes."""
    log.info("Baixando RTN: %s", URL_RTN)
    # verify=False ignora validação de certificado SSL (problema na rede corporativa)
    r = requests.get(URL_RTN, timeout=120, verify=False)
    r.raise_for_status()   # lança exceção se o status HTTP não for 200 OK
    log.info("Download concluído: %.1f MB", len(r.content) / 1e6)
    return r.content


# ── Etapa 2: Leitura das abas do Excel ───────────────────────────────────

def _ler_aba_mensal(xl: pd.ExcelFile, aba: str) -> pd.DataFrame:
    """
    Lê uma aba de série mensal do Excel da RTN.

    O Excel da RTN tem um layout não-padrão: as primeiras 4 linhas são cabeçalho
    institucional (título, subtítulo, unidade), e os dados começam na linha 5
    (índice 4 em Python, que começa do zero). Por isso header=4.

    A primeira coluna (índice 0) é o nome do indicador ('discriminacao').
    As demais colunas são datas (um mês por coluna) — formato "wide".

    A função remove linhas de notas de rodapé verificando se a segunda coluna
    é numérica: linhas de nota têm texto na segunda coluna.
    """
    df = pd.read_excel(xl, sheet_name=aba, header=4)
    # Renomeia a primeira coluna para 'discriminacao' (independente do nome original)
    df = df.rename(columns={df.columns[0]: "discriminacao"})
    # Mantém apenas as linhas onde a segunda coluna é um número
    # errors="coerce" transforma texto não-numérico em NaN; notna() filtra só os números
    df = df[pd.to_numeric(df.iloc[:, 1], errors="coerce").notna()].copy()
    return df


def _ler_aba_anual(xl: pd.ExcelFile, aba: str) -> pd.DataFrame:
    """
    Lê uma aba de série anual (colunas = anos inteiros, ex: 2010, 2011...).
    Mesma lógica de limpeza de rodapé da versão mensal.
    Usada apenas para derivar o PIB anual (abas 2.2 e 2.2-A).
    """
    df = pd.read_excel(xl, sheet_name=aba, header=4)
    df = df.rename(columns={df.columns[0]: "discriminacao"})
    df = df[pd.to_numeric(df.iloc[:, 1], errors="coerce").notna()].copy()
    return df


def _melt_mensal(df_wide: pd.DataFrame, col_valor: str) -> pd.DataFrame:
    """
    Transforma o DataFrame "wide" em formato "tidy/long".

    Antes (wide):
      discriminacao | jan/2023 | fev/2023 | ...
      Receita Total |   200    |   210    | ...

    Depois (tidy/long):
      discriminacao | data       | corrente_milhoes
      Receita Total | 2023-01-01 | 200
      Receita Total | 2023-02-01 | 210

    O argumento col_valor define o nome da coluna de valor resultante.
    O pandas chama isso de "melt" (derreter — transforma colunas em linhas).
    """
    # Identifica apenas as colunas que são datas (timestamps), ignorando 'discriminacao'
    colunas_data = [c for c in df_wide.columns if isinstance(c, (pd.Timestamp, _dt))]
    df = df_wide.melt(
        id_vars=["discriminacao"],    # coluna que permanece fixa
        value_vars=colunas_data,      # colunas que viram linhas
        var_name="data",              # nome da nova coluna com as datas
        value_name=col_valor,         # nome da nova coluna com os valores
    )
    df["data"]         = pd.to_datetime(df["data"])
    df[col_valor]      = pd.to_numeric(df[col_valor], errors="coerce")
    df["discriminacao"] = df["discriminacao"].astype(str).str.strip()
    return df


# ── Etapa 2b: Extração do investimento público agregado ───────────────────

def _extrair_investimento(xl: pd.ExcelFile) -> pd.DataFrame:
    """
    Extrai TODAS as rubricas das abas 1.3 e 1.3-A (investimento público).

    POR QUE 1.3 E NÃO 1.2?
      Nas abas 1.2/1.2-A, o investimento aparece como uma entre várias
      rubricas de despesa, sem destaque. As abas 1.3/1.3-A foram criadas
      pelo Tesouro justamente para isolar o investimento como agregado
      independente — ideal para análises que separam gasto corrente de
      investimento (ex: comparar ajuste fiscal via corte de investimento
      vs corte de custeio).

    O QUE A ABA 1.3 CONTÉM? (dados a partir de 2008)
      Seção 1 — Investimento por FUNÇÃO orçamentária:
        1. INVESTIMENTO TOTAL (1 + 2 + 3)
        1.1 Investimentos (GND 4) por função (Saúde, Educação, Transporte...)
        1.2 Inversões Financeiras (GND 5) por função
        1.3 Ajuste de Ordem Bancária
      Seção 2 — Memorando: investimento por NATUREZA da despesa:
        2.1 Investimentos (GND 4): Obras, Equipamentos, Serviços,
            Transferências a Estados/DF e a Municípios etc.
        2.2 Inversões Financeiras (GND 5) por ação

    O filtro de _ler_aba_mensal já descarta cabeçalhos de seção e notas de
    rodapé (linhas sem valor numérico na segunda coluna), então basta ler a
    aba inteira. Cada rubrica recebe o prefixo "INV " (ver comentário em
    PREFIXO_INVESTIMENTO) antes de entrar no parquet.
    """
    # Lê as abas inteiras e filtra notas de rodapé (mesma lógica das outras abas)
    df_corr_wide = _ler_aba_mensal(xl, "1.3")
    df_cons_wide = _ler_aba_mensal(xl, "1.3-A")

    # Transforma wide → tidy para cada série
    df_c = _melt_mensal(df_corr_wide, "corrente_milhoes")
    df_k = _melt_mensal(df_cons_wide, "constante_milhoes")

    # Une as duas séries pelo par (indicador, data)
    df = df_c.merge(
        df_k[["discriminacao", "data", "constante_milhoes"]],
        on=["discriminacao", "data"],
        how="left",
    )

    # Prefixo "INV " evita colisão de numeração com as séries da aba 1.2
    df["discriminacao"] = PREFIXO_INVESTIMENTO + df["discriminacao"]
    return df


# ── Etapa 3: Cálculo do PIB anual ─────────────────────────────────────────

def _computar_pib_por_ano(
    df_anual_corr: pd.DataFrame, df_anual_pib: pd.DataFrame
) -> dict:
    """
    Deriva o PIB anual (em R$ milhões) a partir de duas abas da RTN.

    POR QUE PRECISAMOS DO PIB?
      Expressar valores como "% do PIB" é a forma padrão de comparar
      indicadores fiscais entre anos e entre países. R$ 100 bilhões de
      déficit tem significados muito diferentes em 2005 e em 2025 — como
      % do PIB, a comparação fica justa.

    COMO É CALCULADO?
      A aba 2.2 traz a Receita Total em R$ correntes por ano.
      A aba 2.2-A traz a mesma receita como proporção decimal do PIB.
      (ex: 0.2274 significa que a receita foi 22,74% do PIB naquele ano)

      Portanto: PIB_ano = Receita_corrente / Proporção_decimal
      Ex: se receita = R$ 5.000 bi e proporção = 0.25, o PIB = R$ 20.000 bi

      Usamos "Receita Total" (prefixo '1. ') como série de referência
      porque ela está sempre disponível nas duas abas.
    """
    # Filtra apenas as linhas de "Receita Total" e usa 'discriminacao' como índice
    rec_c = df_anual_corr[df_anual_corr["discriminacao"].str.startswith("1. ")].set_index("discriminacao")
    rec_p = df_anual_pib[df_anual_pib["discriminacao"].str.startswith("1. ")].set_index("discriminacao")

    # Encontra os anos disponíveis nas duas abas (interseção)
    anos_c = {int(c) for c in rec_c.columns if isinstance(c, (int, float)) and not pd.isna(c)}
    anos_p = {int(c) for c in rec_p.columns if isinstance(c, (int, float)) and not pd.isna(c)}
    anos   = sorted(anos_c & anos_p)

    pib: dict = {}
    for ano in anos:
        try:
            v_corr = float(rec_c[ano].iloc[0])   # receita em R$ milhões
            v_pct  = float(rec_p[ano].iloc[0])   # receita como fração decimal do PIB
            if v_pct and v_pct != 0:
                pib[ano] = v_corr / v_pct         # PIB = receita / fração
        except Exception:
            pass  # ignora anos com dados incompletos

    if pib:
        ano_max = max(pib)
        log.info(
            "PIB anual calculado: %d anos (último: %d = R$ %.0f bi)",
            len(pib), ano_max, pib[ano_max] / 1e3,
        )

    # Projeção para o ano seguinte ao último disponível.
    # Usamos crescimento nominal histórico de ~8% como estimativa conservadora.
    # Esse valor é usado apenas para calcular % do PIB de meses ainda sem dado anual.
    if pib:
        ultimo = max(pib)
        if ultimo + 1 not in pib:
            pib[ultimo + 1] = pib[ultimo] * 1.08

    return pib


# ── Etapa 4: Transformação e montagem do DataFrame final ──────────────────

def transformar(conteudo: bytes) -> tuple:
    """
    Recebe o conteúdo binário do Excel e retorna:
      - DataFrame final tidy com todas as séries mensais + investimento + % PIB
      - Dicionário de metadados (base IPCA, última data disponível)
    """
    # pd.ExcelFile lê o Excel na memória sem extrair para disco
    xl = pd.ExcelFile(io.BytesIO(conteudo))

    # ── Séries mensais principais (abas 1.2 e 1.2-A) ──────────────────────
    # Usamos 1.2 (detalhada) em vez de 1.1 (resumida) para ter mais rubricas
    df_corr_wide = _ler_aba_mensal(xl, "1.2")    # R$ correntes (nominais)
    df_cons_wide = _ler_aba_mensal(xl, "1.2-A")  # R$ constantes (deflacionados pelo IPCA)

    # Extrai o rótulo do período-base do deflator IPCA da terceira linha da aba
    # Ex: "Valores de Mar/2026" → base_constante = "Mar/2026"
    titulo_unidade = str(pd.read_excel(xl, sheet_name="1.2-A", header=None).iloc[2, 0])
    m = re.search(r"Valores de (\w{3}/\d{4})", titulo_unidade)
    base_constante = m.group(1) if m else "base IPCA"

    # ── PIB anual (abas 2.2 e 2.2-A) ─────────────────────────────────────
    # Usamos 2.2 (detalhada) em vez de 2.1 (resumida) — consistente com a
    # escolha das séries mensais. A lógica de cálculo do PIB é a mesma.
    df_anual_corr = _ler_aba_anual(xl, "2.2")
    df_anual_pib  = _ler_aba_anual(xl, "2.2-A")
    pib_por_ano   = _computar_pib_por_ano(df_anual_corr, df_anual_pib)

    # ── Melt wide → tidy para as séries mensais ───────────────────────────
    df_c = _melt_mensal(df_corr_wide, "corrente_milhoes")
    df_k = _melt_mensal(df_cons_wide, "constante_milhoes")

    # Une as duas séries num único DataFrame pelo par (indicador, data)
    # how="left" garante que todos os registros da série corrente sejam mantidos
    df = df_c.merge(
        df_k[["discriminacao", "data", "constante_milhoes"]],
        on=["discriminacao", "data"],
        how="left",
    )

    # ── Investimento público agregado (abas 1.3 e 1.3-A) ─────────────────
    # Concatemos as linhas de investimento antes de calcular ano/mes/pct_pib
    # para que o investimento receba o mesmo tratamento que as demais séries.
    df_inv = _extrair_investimento(xl)
    df = pd.concat([df, df_inv], ignore_index=True)

    # ── Colunas derivadas ─────────────────────────────────────────────────
    df["ano"] = df["data"].dt.year
    df["mes"] = df["data"].dt.month

    # Calcula % do PIB para cada mês:
    #   pct_pib = (valor_mensal / (PIB_anual / 12)) × 100
    #
    # Divisão por 12 porque o PIB_anual é o total do ano; dividindo por 12
    # obtemos o "PIB mensal médio". Comparar um mês com 1/12 do PIB anual
    # é o padrão no monitoramento fiscal (permite somar 12 meses e obter o % anual).
    pib_mensal = df["ano"].map(pib_por_ano) / 12
    df["pct_pib"] = (df["corrente_milhoes"] / pib_mensal * 100).round(4)

    # Converte o timestamp para apenas a data (sem hora)
    df["data"] = df["data"].dt.date

    ultima   = df["data"].max()
    n_series = df["discriminacao"].nunique()
    log.info(
        "RTN: %d séries × %d meses = %d linhas | até %s",
        n_series, df["data"].nunique(), len(df), ultima,
    )

    meta = {"base_constante": base_constante, "ultima_data": str(ultima)}

    # Seleciona e ordena as colunas finais
    cols = ["ano", "mes", "data", "discriminacao",
            "corrente_milhoes", "constante_milhoes", "pct_pib"]
    return (
        df[cols].sort_values(["discriminacao", "ano", "mes"]).reset_index(drop=True),
        meta,
    )


# ── Etapa 5: Salvamento ───────────────────────────────────────────────────

def main():
    """Ponto de entrada: baixa, transforma e salva a RTN."""
    conteudo = baixar_excel()
    df, meta = transformar(conteudo)

    # Salva o DataFrame em formato Parquet (mais eficiente que CSV para leitura)
    df.to_parquet(DESTINO, index=False)

    # Salva os metadados em JSON (lido pelo dashboard para exibir a base do deflator)
    META_FILE.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Salvo: %s | %d linhas", DESTINO.name, len(df))
    log.info("Metadados: %s", meta)


if __name__ == "__main__":
    main()
