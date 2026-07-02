# -*- coding: utf-8 -*-
"""
testes/testes_consistencia_estadual.py

Trava a INVARIANTE central da aba estadual/municipal (decisão de 02/07/2026):
os três elementos que mostram R$ — contador, composição e tabela — devem contar
a MESMA história, na mesma base (projeção "intervalo móvel até o bimestre
corrente"). Concretamente, para cada ente:

    soma das 6 barras da composição  ==  meta do contador (acc + previsão)
    total da tabela (por ente)       ==  soma da composição

Antes desta correção, cada elemento usava um método diferente (composição pegava
B1 × ratio; tabela usava rolling-12m), e os números divergiam em centenas de %.

Rodar:
    pytest testes/testes_consistencia_estadual.py
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "dashboard"))

from components.theme import (  # noqa: E402
    calcular_categorias_projetadas,
    calcular_scatter_correntes_invest,
)

ESTADOS_PARQUET = BASE / "data" / "estados" / "gastos_estados.parquet"
CONTADOR_JSON   = BASE / "data" / "contador_fiscal.json"

# Tolerância relativa: o dashboard lê o ratio já arredondado (6 casas) do JSON,
# o que gera diferença ínfima (< 0,01%) contra a previsão de precisão plena do
# contador. Nada além disso é aceitável.
TOL_REL = 1e-4


@pytest.fixture(scope="module")
def dados():
    if not ESTADOS_PARQUET.exists() or not CONTADOR_JSON.exists():
        pytest.skip("parquet de estados ou contador_fiscal.json ausente")
    df   = pd.read_parquet(ESTADOS_PARQUET)
    cont = json.loads(CONTADOR_JSON.read_text(encoding="utf-8"))
    return df, cont["estados"]


def _meta_mi(bloco: dict) -> float:
    """Meta do contador (realizado + projeção) em R$ milhões."""
    return (bloco["acc_base_rs"] + bloco["previsao_total_rs"]) / 1e6


def _cod_de_uf(df: pd.DataFrame, uf: str) -> int:
    return int(df[df["uf"] == uf]["cod_ibge"].iloc[0])


@pytest.mark.parametrize("uf", ["SP", "RJ", "MG", "BA", "CE"])
def test_composicao_bate_contador_por_estado(dados, uf):
    df, est = dados
    bloco = est.get(uf)
    assert bloco, f"{uf} ausente no contador"

    cats = calcular_categorias_projetadas(df, [_cod_de_uf(df, uf)], bloco)
    soma_comp = float(cats["valor_projetado"].sum())
    meta = _meta_mi(bloco)

    assert soma_comp == pytest.approx(meta, rel=TOL_REL), (
        f"{uf}: composição {soma_comp:,.0f} ≠ contador {meta:,.0f} mi"
    )


def test_composicao_bate_contador_consolidado(dados):
    df, est = dados
    bloco = est["_consolidado"]
    cats = calcular_categorias_projetadas(df, None, bloco)
    soma_comp = float(cats["valor_projetado"].sum())
    meta = _meta_mi(bloco)
    assert soma_comp == pytest.approx(meta, rel=TOL_REL), (
        f"consolidado: composição {soma_comp:,.0f} ≠ contador {meta:,.0f} mi"
    )


@pytest.mark.parametrize("uf", ["SP", "RJ", "MG", "BA", "CE"])
def test_tabela_bate_composicao_por_estado(dados, uf):
    """Total da tabela (scatter) == soma da composição, para o mesmo ente."""
    df, est = dados
    cod = _cod_de_uf(df, uf)
    blocos = {cod: est[uf]}
    scatter = calcular_scatter_correntes_invest(df, blocos)
    total_tab = float(scatter[scatter["uf"] == uf]["total_milhoes"].iloc[0])

    cats = calcular_categorias_projetadas(df, [cod], est[uf])
    soma_comp = float(cats["valor_projetado"].sum())

    assert total_tab == pytest.approx(soma_comp, rel=TOL_REL), (
        f"{uf}: tabela {total_tab:,.0f} ≠ composição {soma_comp:,.0f} mi"
    )


def test_invest_mais_correntes_fecha_total(dados):
    """Investimento + correntes/obrigatórias = total (complemento fecha 100%)."""
    df, est = dados
    blocos = {}
    for uf, blk in est.items():
        if uf == "_consolidado":
            continue
        blocos[_cod_de_uf(df, uf)] = blk
    scatter = calcular_scatter_correntes_invest(df, blocos)
    soma = scatter["invest_milhoes"] + scatter["correntes_obrig_milhoes"]
    assert (soma - scatter["total_milhoes"]).abs().max() < 1e-6


def test_tabela_consolidada_proxima_do_contador(dados):
    """Soma dos estados na tabela ≈ contador consolidado.

    Diferença esperada < 0,1%: o contador consolidado usa UM ratio agregado,
    enquanto a soma da tabela usa o ratio próprio de cada estado. É imaterial,
    mas fica travado para não crescer silenciosamente.
    """
    df, est = dados
    blocos = {}
    for uf, blk in est.items():
        if uf == "_consolidado":
            continue
        blocos[_cod_de_uf(df, uf)] = blk
    scatter = calcular_scatter_correntes_invest(df, blocos)
    soma_tabela = float(scatter["total_milhoes"].sum())
    meta_cons = _meta_mi(est["_consolidado"])
    assert soma_tabela == pytest.approx(meta_cons, rel=2e-3), (
        f"tabela soma {soma_tabela:,.0f} vs contador {meta_cons:,.0f} mi"
    )
