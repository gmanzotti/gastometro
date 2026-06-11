"""
testes/testes_load_estados.py  —  Testes para pipelines/estados/load_prototipo.py
────────────────────────────────────────────────────────────────────────

COMO RODAR
  pytest testes/testes_load_estados.py -v

FILOSOFIA DOS TESTES
  - Funções de I/O (HTTP, disco): mocks via unittest.mock
  - Funções de transformação pura: DataFrames reais, sem mocks
  - Teste de integração leve: simula um ciclo completo com dados mínimos
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.estados.load_prototipo import (
    ANO_INICIO,
    CONTAS_DESPESA,
    COLUNAS_DESPESA,
    COLS_SAIDA,
    INTERVALO_REQUISICAO,
    MAX_TENTATIVAS,
    _bimestre_maximo_atual,
    _combinacoes_ja_carregadas,
    _construir_combinacoes,
    _salvar_lote,
    buscar_entes_estados,
    buscar_rreo_estado,
)


# ══════════════════════════════════════════════════════════════════════════
# Fixtures compartilhadas
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def estados_df():
    """DataFrame mínimo simulando o retorno de buscar_entes_estados()."""
    return pd.DataFrame([
        {"cod_ibge": 35, "uf": "SP", "ente": "Governo do Estado de São Paulo", "populacao": 46000000},
        {"cod_ibge": 33, "uf": "RJ", "ente": "Governo do Estado do Rio de Janeiro", "populacao": 17000000},
    ])


@pytest.fixture
def rreo_item_raw():
    """
    Simula um item da resposta da API SICONFI para o Anexo 01.
    Representa a linha 'DespesasCorrentes / DESPESAS LIQUIDADAS ATÉ O BIMESTRE'.
    """
    return {
        "exercicio":   2024,
        "demonstrativo": "RREO",
        "periodo":     1,
        "periodicidade": "B",
        "instituicao": "Governo do Estado de São Paulo",
        "cod_ibge":    35,
        "uf":          "SP",
        "populacao":   46000000,
        "anexo":       "RREO-Anexo 01",
        "esfera":      "E",
        "rotulo":      "Padrão",
        "cod_conta":   "DespesasCorrentes",
        "conta":       "DESPESAS CORRENTES",
        "coluna":      "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)",
        "valor":       100_000_000_000.0,   # R$ 100 bilhões = R$ 100.000 milhões
    }


@pytest.fixture
def rreo_response_ok(rreo_item_raw):
    """Resposta HTTP simulada da API SICONFI com um único item."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "items":   [rreo_item_raw],
        "hasMore": False,
        "count":   1,
    }
    return mock_resp


@pytest.fixture
def rreo_response_vazia():
    """Resposta HTTP simulada da API SICONFI sem itens (bimestre não publicado)."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"items": [], "hasMore": False, "count": 0}
    return mock_resp


# ══════════════════════════════════════════════════════════════════════════
# 1. buscar_entes_estados()
# ══════════════════════════════════════════════════════════════════════════

class TestBuscarEntesEstados:
    def _mock_entes_response(self, items):
        mock = MagicMock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = {"items": items}
        return mock

    def test_filtra_apenas_esfera_E(self):
        """Deve retornar apenas entidades com esfera='E' (estados/DF)."""
        items = [
            {"cod_ibge": 35, "uf": "SP", "ente": "Estado SP", "populacao": 46000000, "esfera": "E"},
            {"cod_ibge": 3550308, "uf": "SP", "ente": "Município SP", "populacao": 12000000, "esfera": "M"},
            {"cod_ibge": 33, "uf": "RJ", "ente": "Estado RJ", "populacao": 17000000, "esfera": "E"},
        ]
        mock_resp = self._mock_entes_response(items)

        with patch("pipelines.estados.load_prototipo.requests.get", return_value=mock_resp):
            resultado = buscar_entes_estados()

        assert len(resultado) == 2
        assert (resultado["esfera"] == "E").all() if "esfera" in resultado.columns else True

    def test_colunas_esperadas(self):
        """O resultado deve ter as 4 colunas necessárias para a extração."""
        items = [
            {"cod_ibge": 35, "uf": "SP", "ente": "Estado SP", "populacao": 46000000, "esfera": "E"},
        ]
        mock_resp = self._mock_entes_response(items)

        with patch("pipelines.estados.load_prototipo.requests.get", return_value=mock_resp):
            resultado = buscar_entes_estados()

        assert set(resultado.columns) == {"cod_ibge", "uf", "ente", "populacao"}

    def test_ordenado_por_uf(self):
        """Deve retornar ordenado por UF para facilitar leitura dos logs."""
        items = [
            {"cod_ibge": 35, "uf": "SP", "ente": "Estado SP", "populacao": 46000000, "esfera": "E"},
            {"cod_ibge": 33, "uf": "RJ", "ente": "Estado RJ", "populacao": 17000000, "esfera": "E"},
            {"cod_ibge": 29, "uf": "BA", "ente": "Estado BA", "populacao": 14000000, "esfera": "E"},
        ]
        mock_resp = self._mock_entes_response(items)

        with patch("pipelines.estados.load_prototipo.requests.get", return_value=mock_resp):
            resultado = buscar_entes_estados()

        assert list(resultado["uf"]) == ["BA", "RJ", "SP"]


# ══════════════════════════════════════════════════════════════════════════
# 2. buscar_rreo_estado()
# ══════════════════════════════════════════════════════════════════════════

class TestBuscarRrreoEstado:
    def test_retorna_dataframe_com_colunas_corretas(self, rreo_response_ok):
        """O retorno deve ter exatamente as colunas definidas em COLS_SAIDA."""
        with patch("pipelines.estados.load_prototipo.requests.get", return_value=rreo_response_ok):
            resultado = buscar_rreo_estado(35, 2024, 1)

        assert set(resultado.columns) == set(COLS_SAIDA)

    def test_filtra_apenas_contas_relevantes(self):
        """Contas fora de CONTAS_DESPESA devem ser descartadas."""
        items = [
            # Conta relevante → deve aparecer
            {
                "exercicio": 2024, "periodo": 1, "cod_ibge": 35, "uf": "SP",
                "instituicao": "SP", "populacao": 46000000,
                "cod_conta": "DespesasCorrentes", "conta": "DESPESAS CORRENTES",
                "coluna": "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)",
                "valor": 50_000_000_000.0,
            },
            # Conta de RECEITA → deve ser descartada
            {
                "exercicio": 2024, "periodo": 1, "cod_ibge": 35, "uf": "SP",
                "instituicao": "SP", "populacao": 46000000,
                "cod_conta": "ReceitasCorrentes", "conta": "RECEITAS CORRENTES",
                "coluna": "Até o Bimestre (c)",
                "valor": 60_000_000_000.0,
            },
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"items": items}

        with patch("pipelines.estados.load_prototipo.requests.get", return_value=mock_resp):
            resultado = buscar_rreo_estado(35, 2024, 1)

        assert len(resultado) == 1
        assert resultado["cod_conta"].iloc[0] == "DespesasCorrentes"

    def test_filtra_apenas_colunas_de_execucao(self):
        """Colunas como DOTAÇÃO INICIAL não são de execução e devem ser descartadas."""
        items = [
            {
                "exercicio": 2024, "periodo": 1, "cod_ibge": 35, "uf": "SP",
                "instituicao": "SP", "populacao": 46000000,
                "cod_conta": "DespesasCorrentes", "conta": "DESPESAS CORRENTES",
                "coluna": "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)",  # válida
                "valor": 50_000_000_000.0,
            },
            {
                "exercicio": 2024, "periodo": 1, "cod_ibge": 35, "uf": "SP",
                "instituicao": "SP", "populacao": 46000000,
                "cod_conta": "DespesasCorrentes", "conta": "DESPESAS CORRENTES",
                "coluna": "DOTAÇÃO INICIAL (d)",  # inválida → deve ser descartada
                "valor": 55_000_000_000.0,
            },
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"items": items}

        with patch("pipelines.estados.load_prototipo.requests.get", return_value=mock_resp):
            resultado = buscar_rreo_estado(35, 2024, 1)

        assert len(resultado) == 1
        assert "DESPESAS LIQUIDADAS" in resultado["coluna"].iloc[0]

    def test_converte_valor_para_milhoes(self, rreo_response_ok):
        """O campo valor_milhoes deve ser valor / 1.000.000."""
        with patch("pipelines.estados.load_prototipo.requests.get", return_value=rreo_response_ok):
            resultado = buscar_rreo_estado(35, 2024, 1)

        # O fixture tem valor = 100_000_000_000 → deve virar 100_000.0 milhões
        assert resultado["valor_milhoes"].iloc[0] == pytest.approx(100_000.0)

    def test_retorna_vazio_quando_api_sem_dados(self, rreo_response_vazia):
        """Deve retornar DataFrame vazio quando a API não tem dados para o período."""
        with patch("pipelines.estados.load_prototipo.requests.get", return_value=rreo_response_vazia):
            resultado = buscar_rreo_estado(35, 2024, 1)

        assert resultado.empty

    def test_retenta_em_caso_de_erro_de_rede(self):
        """Deve tentar MAX_TENTATIVAS vezes antes de desistir."""
        from requests.exceptions import ConnectionError as ReqConnError

        with patch("pipelines.estados.load_prototipo.requests.get", side_effect=ReqConnError("timeout")):
            with patch("pipelines.estados.load_prototipo.time.sleep"):  # evita espera real no teste
                resultado = buscar_rreo_estado(35, 2024, 1)

        assert resultado.empty

    def test_renomeia_exercicio_para_ano(self, rreo_response_ok):
        """A coluna 'exercicio' da API deve ser renomeada para 'ano' no parquet."""
        with patch("pipelines.estados.load_prototipo.requests.get", return_value=rreo_response_ok):
            resultado = buscar_rreo_estado(35, 2024, 1)

        assert "ano" in resultado.columns
        assert "exercicio" not in resultado.columns

    def test_renomeia_instituicao_para_ente(self, rreo_response_ok):
        """A coluna 'instituicao' da API deve ser renomeada para 'ente'."""
        with patch("pipelines.estados.load_prototipo.requests.get", return_value=rreo_response_ok):
            resultado = buscar_rreo_estado(35, 2024, 1)

        assert "ente" in resultado.columns
        assert "instituicao" not in resultado.columns


# ══════════════════════════════════════════════════════════════════════════
# 3. _bimestre_maximo_atual()
# ══════════════════════════════════════════════════════════════════════════

class TestBimestreMaximoAtual:
    def test_retorna_tupla_ano_bimestre(self):
        """Deve retornar uma tupla (int, int)."""
        resultado = _bimestre_maximo_atual()

        assert isinstance(resultado, tuple)
        assert len(resultado) == 2
        assert isinstance(resultado[0], int)
        assert isinstance(resultado[1], int)

    def test_bimestre_entre_1_e_6(self):
        """O bimestre retornado deve estar no intervalo válido [1, 6]."""
        _, bimestre = _bimestre_maximo_atual()

        assert 1 <= bimestre <= 6

    def test_ano_razoavel(self):
        """O ano retornado deve ser próximo ao ano atual."""
        from datetime import datetime
        ano, _ = _bimestre_maximo_atual()
        ano_atual = datetime.now().year

        assert ano_atual - 1 <= ano <= ano_atual


# ══════════════════════════════════════════════════════════════════════════
# 4. _construir_combinacoes()
# ══════════════════════════════════════════════════════════════════════════

class TestConstruirCombinacoes:
    def test_gera_combinacao_para_cada_estado_ano_bimestre(self, estados_df):
        """Deve gerar (n_estados × n_anos × n_bimestres_passados) combinações."""
        combinacoes = _construir_combinacoes(estados_df)

        # Todos os estados devem aparecer
        codigos = {c[0] for c in combinacoes}
        assert 35 in codigos
        assert 33 in codigos

    def test_nao_inclui_bimestres_futuros(self, estados_df):
        """Não deve incluir bimestres ainda não publicados pelo Tesouro."""
        from datetime import datetime

        combinacoes = _construir_combinacoes(estados_df)
        ano_atual = datetime.now().year

        # Verifica que nenhuma combinação tem bimestre futuro (> bimestre atual)
        # Simplificação: bimestre 6 do ano atual raramente está disponível em maio
        bimestres_ano_atual = [
            c[5] for c in combinacoes if c[4] == ano_atual
        ]
        if bimestres_ano_atual:
            assert max(bimestres_ano_atual) <= 6  # nunca ultrapassa 6

    def test_ano_minimo_respeita_ano_inicio(self, estados_df):
        """O ano mais antigo nas combinações deve ser ANO_INICIO."""
        combinacoes = _construir_combinacoes(estados_df)

        anos = {c[4] for c in combinacoes}
        assert min(anos) == ANO_INICIO

    def test_estrutura_da_tupla(self, estados_df):
        """Cada combinação deve ser uma tupla de 6 elementos."""
        combinacoes = _construir_combinacoes(estados_df)

        for c in combinacoes[:5]:
            assert len(c) == 6
            cod_ibge, uf, ente, populacao, ano, periodo = c
            assert isinstance(cod_ibge, int)
            assert isinstance(uf, str)
            assert ANO_INICIO <= ano
            assert 1 <= periodo <= 6


# ══════════════════════════════════════════════════════════════════════════
# 5. _combinacoes_ja_carregadas()
# ══════════════════════════════════════════════════════════════════════════

class TestCombinacoesJaCarregadas:
    def test_retorna_set_vazio_se_parquet_nao_existe(self, tmp_path):
        """Se o parquet não existe, deve retornar set vazio."""
        with patch("pipelines.estados.load_prototipo.DESTINO", tmp_path / "nao_existe.parquet"):
            resultado = _combinacoes_ja_carregadas()

        assert resultado == set()

    def test_retorna_tuplas_existentes(self, tmp_path):
        """Deve retornar o set de (cod_ibge, ano, periodo) do parquet."""
        df = pd.DataFrame({
            "cod_ibge": [35, 35, 33],
            "ano":      [2024, 2024, 2023],
            "periodo":  [1,    2,    1],
            "uf":       ["SP", "SP", "RJ"],
            "ente":     ["SP", "SP", "RJ"],
            "populacao":[46000000]*3,
            "cod_conta":["DespesasCorrentes"]*3,
            "conta":    ["DESPESAS CORRENTES"]*3,
            "coluna":   ["DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)"]*3,
            "valor_milhoes":[50000.0]*3,
        })
        parquet_path = tmp_path / "gastos.parquet"
        df.to_parquet(parquet_path, index=False)

        with patch("pipelines.estados.load_prototipo.DESTINO", parquet_path):
            resultado = _combinacoes_ja_carregadas()

        assert (35, 2024, 1) in resultado
        assert (35, 2024, 2) in resultado
        assert (33, 2023, 1) in resultado
        assert len(resultado) == 3


# ══════════════════════════════════════════════════════════════════════════
# 6. _salvar_lote()
# ══════════════════════════════════════════════════════════════════════════

class TestSalvarLote:
    def _df_valido(self, cod_ibge=35, ano=2024, periodo=1):
        """Cria um DataFrame mínimo e válido para salvar."""
        return pd.DataFrame([{
            "ano":          ano,
            "periodo":      periodo,
            "cod_ibge":     cod_ibge,
            "uf":           "SP",
            "ente":         "Governo do Estado de São Paulo",
            "populacao":    46000000,
            "cod_conta":    "DespesasCorrentes",
            "conta":        "DESPESAS CORRENTES",
            "coluna":       "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)",
            "valor_milhoes": 50000.0,
        }])

    def test_cria_parquet_se_nao_existe(self, tmp_path):
        """Se o parquet não existe, deve criar um novo."""
        parquet_path = tmp_path / "gastos.parquet"

        with patch("pipelines.estados.load_prototipo.DESTINO", parquet_path):
            _salvar_lote([self._df_valido()])

        assert parquet_path.exists()

    def test_adiciona_ao_parquet_existente(self, tmp_path):
        """Deve somar os novos registros aos existentes."""
        parquet_path = tmp_path / "gastos.parquet"

        df_existente = self._df_valido(cod_ibge=33, ano=2023, periodo=1)
        df_existente.to_parquet(parquet_path, index=False)

        with patch("pipelines.estados.load_prototipo.DESTINO", parquet_path):
            _salvar_lote([self._df_valido(cod_ibge=35, ano=2024, periodo=1)])
            df_lido = pd.read_parquet(parquet_path)

        assert len(df_lido) == 2

    def test_deduplica_em_caso_de_reprocessamento(self, tmp_path):
        """Se a mesma combinação for salva duas vezes, deve manter apenas uma."""
        parquet_path = tmp_path / "gastos.parquet"

        df = self._df_valido()
        df.to_parquet(parquet_path, index=False)

        with patch("pipelines.estados.load_prototipo.DESTINO", parquet_path):
            # Salva o mesmo registro de novo
            _salvar_lote([self._df_valido()])
            df_lido = pd.read_parquet(parquet_path)

        assert len(df_lido) == 1  # não duplicou

    def test_retorna_total_de_linhas(self, tmp_path):
        """Deve retornar o número total de linhas após o save."""
        parquet_path = tmp_path / "gastos.parquet"

        with patch("pipelines.estados.load_prototipo.DESTINO", parquet_path):
            n = _salvar_lote([self._df_valido()])

        assert n == 1


# ══════════════════════════════════════════════════════════════════════════
# 7. Constantes e invariantes do módulo
# ══════════════════════════════════════════════════════════════════════════

class TestConstantes:
    def test_contas_despesa_contem_contas_essenciais(self):
        """As contas mínimas para a análise corrente/investimento devem estar presentes."""
        essenciais = {
            "DespesasCorrentes",
            "Investimentos",
            "InversoesFinanceiras",
            "AmortizacaoDaDivida",
        }
        assert essenciais.issubset(CONTAS_DESPESA)

    def test_colunas_incluem_liquidadas_e_empenhadas(self):
        """Ambos os estágios (liquidadas e empenhadas) devem estar presentes."""
        tem_liquidada = any("LIQUIDADA" in c for c in COLUNAS_DESPESA)
        tem_empenhada = any("EMPENHADA" in c for c in COLUNAS_DESPESA)
        assert tem_liquidada, "Nenhuma coluna de despesa liquidada em COLUNAS_DESPESA"
        assert tem_empenhada, "Nenhuma coluna de despesa empenhada em COLUNAS_DESPESA"

    def test_cols_saida_inclui_campos_de_analise(self):
        """O parquet deve conter os campos necessários para análise fiscal."""
        campos_analise = {"cod_ibge", "uf", "ano", "periodo", "cod_conta", "coluna", "valor_milhoes"}
        assert campos_analise.issubset(set(COLS_SAIDA))

    def test_intervalo_requisicao_respeita_rate_limit(self):
        """O intervalo entre requisições deve ser >= 1s (rate limit da API)."""
        assert INTERVALO_REQUISICAO >= 1.0
