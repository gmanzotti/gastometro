"""
testes/testes_load_municipios.py  —  Testes para pipelines/municipios/load.py
──────────────────────────────────────────────────────────────────────────────

COMO RODAR
  pytest testes/testes_load_municipios.py -v
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.municipios.load import (
    CAPITAIS,
    COLUNAS_DESPESA,
    COLS_SAIDA,
    CONTAS_DESPESA,
    INTERVALO_REQUISICAO,
    POP_MINIMA_COMPLETO,
    _bimestre_maximo_atual,
    _combinacoes_ja_carregadas,
    _construir_combinacoes,
    _salvar_lote,
    _tipo_demonstrativo,
    buscar_entes_municipios,
    buscar_rreo_municipio,
)


# ══════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def municipios_df():
    """DataFrame mínimo simulando retorno de buscar_entes_municipios()."""
    return pd.DataFrame([
        {"cod_ibge": 3550308, "uf": "SP", "populacao": 12_200_180},
        {"cod_ibge": 3304557, "uf": "RJ", "populacao":  6_748_000},
    ])


@pytest.fixture
def rreo_item_municipio():
    """Item de resposta da API para um município (SP, 2024, bim 1)."""
    return {
        "exercicio":   2024,
        "demonstrativo": "RREO",
        "periodo":     1,
        "periodicidade": "B",
        "instituicao": "Prefeitura Municipal de São Paulo - SP",
        "cod_ibge":    3550308,
        "uf":          "SP",
        "populacao":   12_200_180,
        "anexo":       "RREO-Anexo 01",
        "esfera":      "M",
        "rotulo":      "Padrão",
        "cod_conta":   "DespesasCorrentes",
        "conta":       "DESPESAS CORRENTES",
        "coluna":      "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)",
        "valor":       80_000_000_000.0,
    }


@pytest.fixture
def rreo_response_ok(rreo_item_municipio):
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {"items": [rreo_item_municipio], "hasMore": False, "count": 1}
    return mock


@pytest.fixture
def rreo_response_vazia():
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {"items": [], "hasMore": False, "count": 0}
    return mock


# ══════════════════════════════════════════════════════════════════════════
# 1. CAPITAIS — integridade da lista hardcoded
# ══════════════════════════════════════════════════════════════════════════

class TestCapitais:
    def test_tem_27_entradas(self):
        """Deve ter 26 estados + DF."""
        assert len(CAPITAIS) == 27

    def test_todos_os_ufs_presentes(self):
        """Todos os 26 estados + DF devem estar representados."""
        ufs_esperados = {
            "AC","AL","AM","AP","BA","CE","DF","ES","GO",
            "MA","MG","MS","MT","PA","PB","PE","PI","PR",
            "RJ","RN","RO","RR","RS","SC","SE","SP","TO",
        }
        assert set(CAPITAIS.keys()) == ufs_esperados

    def test_codigos_ibge_sao_inteiros_de_7_digitos(self):
        """Todos os cod_ibge devem ser inteiros com 7 dígitos."""
        for uf, cod in CAPITAIS.items():
            assert isinstance(cod, int), f"{uf}: esperado int, got {type(cod)}"
            assert 1_000_000 <= cod <= 9_999_999, f"{uf}: {cod} não tem 7 dígitos"

    def test_codigos_sao_unicos(self):
        """Nenhum cod_ibge deve se repetir."""
        codigos = list(CAPITAIS.values())
        assert len(codigos) == len(set(codigos))

    def test_sp_tem_codigo_correto(self):
        """São Paulo (município) deve ter o cod_ibge oficial correto."""
        assert CAPITAIS["SP"] == 3550308


# ══════════════════════════════════════════════════════════════════════════
# 2. _tipo_demonstrativo()
# ══════════════════════════════════════════════════════════════════════════

class TestTipoDemonstrativo:
    def test_municipio_grande_usa_rreo(self):
        """Municípios acima do limiar devem usar 'RREO' (Anexo 01 completo)."""
        assert _tipo_demonstrativo(POP_MINIMA_COMPLETO + 1) == "RREO"

    def test_municipio_pequeno_usa_simplificado(self):
        """Municípios abaixo do limiar devem usar 'RREO-Simplificado'."""
        assert _tipo_demonstrativo(POP_MINIMA_COMPLETO - 1) == "RREO-Simplificado"

    def test_exatamente_no_limiar_usa_rreo(self):
        """No limiar exato, deve usar RREO (>= é completo)."""
        assert _tipo_demonstrativo(POP_MINIMA_COMPLETO) == "RREO"


# ══════════════════════════════════════════════════════════════════════════
# 3. buscar_entes_municipios()
# ══════════════════════════════════════════════════════════════════════════

class TestBuscarEntesMunicipios:
    def test_modo_prototipo_retorna_capitais_sem_api(self):
        """No modo protótipo, não deve chamar a API — usa CAPITAIS hardcoded."""
        with patch("pipelines.municipios.load.EXTRAIR_TODOS", False):
            with patch("pipelines.municipios.load.requests.get") as mock_get:
                resultado = buscar_entes_municipios()

        mock_get.assert_not_called()
        assert len(resultado) == 27

    def test_modo_prototipo_contem_todos_os_ufs(self):
        """O DataFrame do protótipo deve ter uma entrada por UF."""
        with patch("pipelines.municipios.load.EXTRAIR_TODOS", False):
            resultado = buscar_entes_municipios()

        assert set(resultado["uf"]) == set(CAPITAIS.keys())

    def test_modo_producao_chama_api(self):
        """No modo produção, deve chamar o endpoint /entes."""
        items = [
            {"cod_ibge": 3550308, "uf": "SP", "populacao": 12_000_000, "esfera": "M"},
            {"cod_ibge": 3304557, "uf": "RJ", "populacao":  6_000_000, "esfera": "M"},
            {"cod_ibge": 35,      "uf": "SP", "populacao": 46_000_000, "esfera": "E"},  # estado → excluir
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"items": items}

        with patch("pipelines.municipios.load.EXTRAIR_TODOS", True):
            with patch("pipelines.municipios.load.requests.get", return_value=mock_resp):
                resultado = buscar_entes_municipios()

        assert len(resultado) == 2  # apenas municípios (esfera="M")


# ══════════════════════════════════════════════════════════════════════════
# 4. buscar_rreo_municipio()
# ══════════════════════════════════════════════════════════════════════════

class TestBuscarRrreoMunicipio:
    def test_retorna_dataframe_com_colunas_corretas(self, rreo_response_ok):
        with patch("pipelines.municipios.load.requests.get", return_value=rreo_response_ok):
            resultado = buscar_rreo_municipio(3550308, 2024, 1)

        assert set(resultado.columns) == set(COLS_SAIDA)

    def test_municipio_pequeno_retorna_vazio_sem_chamada_api(self):
        """Municípios pequenos devem ser pulados sem chamar a API."""
        with patch("pipelines.municipios.load.requests.get") as mock_get:
            resultado = buscar_rreo_municipio(9999999, 2024, 1, populacao=1000)

        mock_get.assert_not_called()
        assert resultado.empty

    def test_converte_valor_para_milhoes(self, rreo_response_ok):
        with patch("pipelines.municipios.load.requests.get", return_value=rreo_response_ok):
            resultado = buscar_rreo_municipio(3550308, 2024, 1)

        # fixture tem valor = 80_000_000_000 → 80_000.0 milhões
        assert resultado["valor_milhoes"].iloc[0] == pytest.approx(80_000.0)

    def test_filtra_contas_irrelevantes(self):
        """Contas de receita devem ser descartadas."""
        items = [
            {
                "exercicio": 2024, "periodo": 1, "cod_ibge": 3550308, "uf": "SP",
                "instituicao": "Prefeitura SP", "populacao": 12_000_000,
                "cod_conta": "DespesasCorrentes", "conta": "DESPESAS CORRENTES",
                "coluna": "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)", "valor": 1e10,
            },
            {
                "exercicio": 2024, "periodo": 1, "cod_ibge": 3550308, "uf": "SP",
                "instituicao": "Prefeitura SP", "populacao": 12_000_000,
                "cod_conta": "ReceitasCorrentes", "conta": "RECEITAS CORRENTES",
                "coluna": "Até o Bimestre (c)", "valor": 1.2e10,
            },
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"items": items}

        with patch("pipelines.municipios.load.requests.get", return_value=mock_resp):
            resultado = buscar_rreo_municipio(3550308, 2024, 1)

        assert len(resultado) == 1
        assert resultado["cod_conta"].iloc[0] == "DespesasCorrentes"

    def test_retorna_vazio_quando_api_sem_dados(self, rreo_response_vazia):
        with patch("pipelines.municipios.load.requests.get", return_value=rreo_response_vazia):
            resultado = buscar_rreo_municipio(3550308, 2024, 1)

        assert resultado.empty

    def test_renomeia_exercicio_para_ano(self, rreo_response_ok):
        with patch("pipelines.municipios.load.requests.get", return_value=rreo_response_ok):
            resultado = buscar_rreo_municipio(3550308, 2024, 1)

        assert "ano" in resultado.columns
        assert "exercicio" not in resultado.columns


# ══════════════════════════════════════════════════════════════════════════
# 5. _construir_combinacoes()
# ══════════════════════════════════════════════════════════════════════════

class TestConstruirCombinacoes:
    def test_gera_combinacoes_para_cada_municipio(self, municipios_df):
        combinacoes = _construir_combinacoes(municipios_df)
        codigos = {c[0] for c in combinacoes}

        assert 3550308 in codigos
        assert 3304557 in codigos

    def test_ano_inicio_e_2015(self, municipios_df):
        combinacoes = _construir_combinacoes(municipios_df)
        anos = {c[2] for c in combinacoes}
        assert min(anos) == 2015

    def test_estrutura_da_tupla(self, municipios_df):
        """Tupla deve ter 4 elementos: (cod_ibge, populacao, ano, periodo)."""
        combinacoes = _construir_combinacoes(municipios_df)
        cod_ibge, populacao, ano, periodo = combinacoes[0]

        assert isinstance(cod_ibge, int)
        assert isinstance(populacao, int)
        assert 2015 <= ano
        assert 1 <= periodo <= 6


# ══════════════════════════════════════════════════════════════════════════
# 6. _combinacoes_ja_carregadas()
# ══════════════════════════════════════════════════════════════════════════

class TestCombinacoesJaCarregadas:
    def test_retorna_set_vazio_se_parquet_nao_existe(self, tmp_path):
        with patch("pipelines.municipios.load.DESTINO", tmp_path / "nao_existe.parquet"):
            resultado = _combinacoes_ja_carregadas()

        assert resultado == set()

    def test_retorna_tuplas_existentes(self, tmp_path):
        df = pd.DataFrame({
            "cod_ibge":    [3550308, 3304557],
            "ano":         [2024,    2023],
            "periodo":     [1,       2],
            "uf":          ["SP",    "RJ"],
            "ente":        ["SP",    "RJ"],
            "populacao":   [12000000, 6000000],
            "cod_conta":   ["DespesasCorrentes"] * 2,
            "conta":       ["DESPESAS CORRENTES"] * 2,
            "coluna":      ["DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)"] * 2,
            "valor_milhoes": [50000.0, 30000.0],
        })
        parquet_path = tmp_path / "gastos.parquet"
        df.to_parquet(parquet_path, index=False)

        with patch("pipelines.municipios.load.DESTINO", parquet_path):
            resultado = _combinacoes_ja_carregadas()

        assert (3550308, 2024, 1) in resultado
        assert (3304557, 2023, 2) in resultado


# ══════════════════════════════════════════════════════════════════════════
# 7. _salvar_lote()
# ══════════════════════════════════════════════════════════════════════════

class TestSalvarLote:
    def _df_valido(self, cod_ibge=3550308, ano=2024, periodo=1):
        return pd.DataFrame([{
            "ano":          ano,
            "periodo":      periodo,
            "cod_ibge":     cod_ibge,
            "uf":           "SP",
            "ente":         "Prefeitura Municipal de São Paulo - SP",
            "populacao":    12_200_180,
            "cod_conta":    "DespesasCorrentes",
            "conta":        "DESPESAS CORRENTES",
            "coluna":       "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)",
            "valor_milhoes": 80000.0,
        }])

    def test_cria_parquet_se_nao_existe(self, tmp_path):
        parquet_path = tmp_path / "gastos.parquet"
        with patch("pipelines.municipios.load.DESTINO", parquet_path):
            _salvar_lote([self._df_valido()])
        assert parquet_path.exists()

    def test_adiciona_ao_parquet_existente(self, tmp_path):
        parquet_path = tmp_path / "gastos.parquet"
        self._df_valido(cod_ibge=3304557).to_parquet(parquet_path, index=False)

        with patch("pipelines.municipios.load.DESTINO", parquet_path):
            _salvar_lote([self._df_valido(cod_ibge=3550308)])
            df = pd.read_parquet(parquet_path)

        assert len(df) == 2

    def test_deduplica_reprocessamento(self, tmp_path):
        parquet_path = tmp_path / "gastos.parquet"
        df = self._df_valido()
        df.to_parquet(parquet_path, index=False)

        with patch("pipelines.municipios.load.DESTINO", parquet_path):
            _salvar_lote([self._df_valido()])
            df_lido = pd.read_parquet(parquet_path)

        assert len(df_lido) == 1


# ══════════════════════════════════════════════════════════════════════════
# 8. Constantes e invariantes
# ══════════════════════════════════════════════════════════════════════════

class TestConstantes:
    def test_contas_essenciais_presentes(self):
        essenciais = {"DespesasCorrentes", "Investimentos", "InversoesFinanceiras"}
        assert essenciais.issubset(CONTAS_DESPESA)

    def test_contas_identicas_ao_pipeline_estados(self):
        """Garante que os dois pipelines usam as mesmas contas para comparabilidade."""
        from pipelines.estados.load import CONTAS_DESPESA as CONTAS_ESTADOS
        assert CONTAS_DESPESA == CONTAS_ESTADOS

    def test_colunas_identicas_ao_pipeline_estados(self):
        """Mesmas colunas de execução para permitir concatenar estados + municípios."""
        from pipelines.estados.load import COLUNAS_DESPESA as COLUNAS_ESTADOS
        assert COLUNAS_DESPESA == COLUNAS_ESTADOS

    def test_cols_saida_identicas_ao_pipeline_estados(self):
        """Schema idêntico para facilitar o concat no dashboard."""
        from pipelines.estados.load import COLS_SAIDA as COLS_ESTADOS
        assert COLS_SAIDA == COLS_ESTADOS

    def test_intervalo_requisicao_respeita_rate_limit(self):
        assert INTERVALO_REQUISICAO >= 1.0

    def test_pop_minima_completo_e_50k(self):
        assert POP_MINIMA_COMPLETO == 50_000
