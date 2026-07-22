# -*- coding: utf-8 -*-
"""
pipelines/exportar_web.py — Gera a CAMADA WEB do Gastômetro (JSONs do site).

POR QUE ESTE SCRIPT EXISTE
──────────────────────────
O site público (frontend da TI/Marketing) não lê os parquets: lê JSONs prontos
("camada web") publicados no Data Lake. Até jul/2026 esses JSONs eram gerados
por um script da própria TI que REIMPLEMENTAVA os cálculos do protótipo — e
qualquer mudança metodológica nossa exigia sincronizar dois códigos de duas
áreas. Foi a causa raiz das divergências entre o Streamlit e o site (a TI
espelhou a versão de 30/06/2026; a metodologia mudou em 02/07/2026).

Este script elimina a duplicação: TODA a matemática vem importada de
dashboard/components/theme.py — as mesmas funções, testadas (pytest), que
alimentam o protótipo Streamlit. Aqui só se orquestra leitura, agregação e
escrita dos arquivos. Nenhuma fórmula nova é definida neste arquivo.

O QUE A TI PRECISA FAZER
────────────────────────
1. Rodar após as cargas (RTN/SICONFI) e após pipelines/contador_fiscal.py:
       python pipelines/exportar_web.py
   Saída em data/web/ (~30 arquivos JSON, alguns segundos de execução).
2. Publicar o conteúdo de data/web/ no ADLS (mesmo destino que o script
   antigo usava: .../gastometro/data/web). Os arquivos têm o MESMO schema
   que o frontend atual já consome — nenhuma mudança no site é necessária.
3. Dependências: as mesmas do repositório (pandas, pyarrow, streamlit,
   plotly) — o import de theme.py puxa streamlit; fora do runtime do
   Streamlit ele só emite um aviso de cache inofensivo.

Os arquivos geo/ (malhas GeoJSON por UF) NÃO são gerados aqui — são estáticos
e já estão publicados; só mudam se o IBGE reatualizar as malhas (ver
pipelines/simplificar_geojson.py).

METODOLOGIA (resumo — detalhe em notas_metodologicas.docx e CLAUDE.md)
──────────────────────────────────────────────────────────────────────
- Base subnacional unificada (decisão 02/07/2026): contador, mapa, ranking,
  composição e termômetro usam a MESMA projeção "intervalo móvel até o
  bimestre corrente" — nunca o ano fechado. Rotular como "no ano, projetado
  até o bimestre X" (o invest% é YTD e sobe ao longo do ano).
- Termômetros (decisão 07/07/2026): base YTD em todas as esferas (o federal
  é o espelho mensal da fórmula bimestral).
- Guarda de sanidade: entes com invest% fora de [0, 100] são EXCLUÍDOS dos
  rankings/mapas (no rolling antigo, janelas descasadas geravam municípios
  com "investimento de 256% do total" no topo da tabela do site).

Rodar:
    python pipelines/exportar_web.py [--out data/web]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))              # p/ config.settings
sys.path.insert(0, str(BASE / "dashboard"))  # p/ components.theme

from components.theme import (  # noqa: E402
    COLUNA_FLUXO,
    calcular_categorias_projetadas,
    calcular_scatter_correntes_invest,
    composicao_investimentos_federal,
    composicao_obrigatorias_federal,
    ratio_federal,
    ratio_ytd_subnacional,
    serie_12m_pct_total,
    serie_resultado_primario,
)

DATA_DIR = BASE / "data"
SCHEMA_VERSION = 3  # v3 = base unificada 02/07 + termômetro YTD 07/07

# Paleta por categoria — herdada do frontend atual da TI (o site não muda de cara)
CORES_CATEGORIA = {
    "PessoalEEncargosSociais": "#EF4444",
    "JurosEEncargosDaDivida":  "#F97316",
    "OutrasDespesasCorrentes": "#FB923C",
    "Investimentos":           "#22C55E",
    "InversoesFinanceiras":    "#16A34A",
    "AmortizacaoDaDivida":     "#F59E0B",
}

COLUNA_ACUM = "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)"
CONTA_TOTAL = "DespesasExcetoIntraOrcamentarias"
CONTAS_INVEST = {"Investimentos", "InversoesFinanceiras"}


# ── Utilidades ──────────────────────────────────────────────────────────────

def _agora_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _gerado_em() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _salvar(out_dir: Path, nome: str, payload: dict) -> None:
    """Grava JSON em UTF-8 (sem \\uXXXX) com floats redondos p/ web."""
    caminho = out_dir / nome
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False,
                   default=_serializar),
        encoding="utf-8",
    )
    print(f"  data/web/{nome}  ({caminho.stat().st_size / 1024:,.0f} KB)")


def _serializar(x):
    """json.dumps não conhece tipos numpy/pandas — converte na saída."""
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.strftime("%Y-%m-%d")
    if hasattr(x, "item"):          # numpy int64/float64
        return x.item()
    raise TypeError(f"não serializável: {type(x)}")


def _bloco_web(bloco: dict, fonte: str = "contador_fiscal") -> dict:
    """Traduz um bloco do contador_fiscal.json p/ o schema da camada web.

    O frontend anima: valor = acc_base_rs + (agora − start_ms)/1000 × taxa.
    Mantemos os campos originais e acrescentamos valor_atual_rs (posição no
    momento da geração, útil p/ depuração e p/ montar o total consolidado).
    """
    agora = _agora_ms()
    acc   = float(bloco.get("acc_base_rs", 0))
    taxa  = float(bloco.get("taxa_por_segundo_rs", 0))
    start = int(bloco.get("start_ms", agora))
    return {
        "fonte": fonte,
        "acc_base_rs": round(acc, 2),
        "taxa_por_segundo_rs": round(taxa, 4),
        "start_ms": start,
        "valor_atual_rs": round(acc + taxa * max(0, (agora - start) / 1000), 2),
        "ultimo_dado": bloco.get("ultimo_dado"),
        "referencia": bloco.get("mes_referencia") or bloco.get("bim_referencia"),
        "referencia_fim": (bloco.get("mes_referencia_fim")
                           or bloco.get("bim_referencia_fim")),
        "ratio_rolling": bloco.get("ratio_rolling"),
        "previsao_total_rs": round(float(bloco.get("previsao_total_rs", 0)), 2),
    }


def _termometro_web(r: tuple | None, tipo_ref: str = "B") -> dict | None:
    """Converte a tupla (pct, inv_mi, tot_mi, (ano, per)) de theme.py p/ dict web."""
    if r is None:
        return None
    ano, per = r[3]
    ref = f"{ano}-B{per}" if tipo_ref == "B" else f"{ano}-{per:02d}"
    return {
        "invest_ratio": round(r[0], 2),
        "investimento_milhoes": round(r[1], 2),
        "total_milhoes": round(r[2], 2),
        "correntes_obrig_milhoes": round(r[2] - r[1], 2),
        "referencia": ref,
        "base": "no ano, projetado até o período corrente",
    }


def _acumulado_real(df: pd.DataFrame) -> dict:
    """Realizado (sem projeção): p/ cada ente, o acumulado EMPENHADO do seu
    último bimestre real no ano mais recente; soma das UFs/municípios."""
    acum = df[df["coluna"] == COLUNA_ACUM]
    ano = int(acum["ano"].max())
    acum = acum[acum["ano"] == ano]
    tot = inv = 0.0
    cobertura = 0
    per_max = 0
    for _, g in acum.groupby("cod_ibge"):
        per = int(g["periodo"].max())
        per_max = max(per_max, per)
        ult = g[g["periodo"] == per]
        t = ult[ult["cod_conta"] == CONTA_TOTAL]["valor_milhoes"].sum()
        i = ult[ult["cod_conta"].isin(CONTAS_INVEST)]["valor_milhoes"].sum()
        if t > 0:
            tot += t
            inv += i
            cobertura += 1
    return {
        "ano": ano,
        "periodo": per_max,
        "referencia": f"{ano}-B{per_max}",
        "cobertura_entes": cobertura,
        "total_rs": round(tot * 1e6, 2),
        "investimento_rs": round(inv * 1e6, 2),
        "invest_ratio": round(inv / tot * 100, 2) if tot else None,
        "observacao": "Somente valores reportados (último bimestre real de "
                      "cada ente, sem projeção).",
    }


def _linha_ente(row: pd.Series, digitos: int,
                 blocos_por_cod: dict | None = None) -> dict:
    """Uma linha de mapa/ranking a partir do scatter de theme.py.

    Até aqui esta linha só carregava totais estáticos (total_milhoes etc.) —
    por isso o contador do site ficava PARADO quando alguém selecionava um
    estado/município individual (só o bloco "_consolidado" ia para a camada
    web com acc_base_rs/taxa_por_segundo_rs/start_ms, o trio que o front-end
    anima com setInterval). Quando `blocos_por_cod` é passado, anexamos o
    mesmo bloco (via _bloco_web) TAMBÉM por ente, para o site poder animar a
    seleção individual do mesmo jeito que já anima o Consolidado.
    """
    cod = int(row["cod_ibge"])
    linha = {
        "cod_ibge": cod,
        "cod_ibge_str": str(cod).zfill(digitos),
        "uf": row["uf"],
        "ente": row["ente"],
        "invest_ratio": round(float(row["invest_ratio"]), 2),
        "invest_milhoes": round(float(row["invest_milhoes"]), 2),
        "total_milhoes": round(float(row["total_milhoes"]), 2),
        "correntes_obrig_milhoes": round(float(row["correntes_obrig_milhoes"]), 2),
        "ano": int(row["ano"]),
        "periodo": int(row["periodo"]),
    }
    if blocos_por_cod and cod in blocos_por_cod:
        linha["contador"] = _bloco_web(blocos_por_cod[cod])
    return linha


def _sanear_ratios(scatter: pd.DataFrame, contexto: str) -> pd.DataFrame:
    """Exclui entes com invest% impossível (fora de [0, 100]) e avisa.

    Com a base unificada isso não deve ocorrer (invest e total saem do mesmo
    plano de projeção); o guarda protege contra dados degenerados na origem.
    """
    ruins = scatter[(scatter["invest_ratio"] < 0) | (scatter["invest_ratio"] > 100)]
    if not ruins.empty:
        print(f"  AVISO [{contexto}]: {len(ruins)} ente(s) excluído(s) por "
              f"invest% fora de [0,100]: {ruins['ente'].head(5).tolist()}")
    return scatter.drop(ruins.index)


def _composicao_ente(df: pd.DataFrame, cods: list | None, bloco: dict | None,
                     titulo: str, uf: str | None) -> dict:
    """Bloco de composição por categoria (schema do estados_composicao_gastos)."""
    cats = calcular_categorias_projetadas(df, cods, bloco)
    if cats.empty:
        return {}
    total = float(cats["valor_projetado"].sum())
    itens = [
        {
            "cod_conta": r["cod_conta"],
            "nome": r["nome"],
            "cor": CORES_CATEGORIA.get(r["cod_conta"], "#94A3B8"),
            "valor_projetado_milhoes": round(float(r["valor_projetado"]), 4),
            "pct_composicao": round(float(r["valor_projetado"]) / total * 100, 4)
                              if total else None,
        }
        for _, r in cats.sort_values("valor_projetado", ascending=False).iterrows()
    ]
    ano, per = int(cats["ano"].iloc[0]), int(cats["periodo"].iloc[0])
    return {
        "titulo": titulo,
        "uf": uf,
        "ano": ano,
        "periodo": per,
        "referencia": f"{ano}-B{per}",
        "ratio_rolling": (bloco or {}).get("ratio_rolling"),
        "total_projetado_milhoes": round(total, 4),
        "itens": itens,
    }


# ── Exportadores por aba ────────────────────────────────────────────────────

def exportar_federal(out: Path, df_rtn: pd.DataFrame, cont: dict) -> tuple | None:
    bloco = cont.get("federal", {})
    ano = int(df_rtn["ano"].max())
    mes = int(df_rtn[df_rtn["ano"] == ano]["mes"].max())
    r_fed = ratio_federal(df_rtn, bloco)

    _salvar(out, "federal_resumo.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "contador": _bloco_web(bloco),
        "termometro": _termometro_web(r_fed, tipo_ref="M"),
        "linhas_rtn": len(df_rtn),
        "ultimo_ano": ano,
        "ultimo_mes": mes,
    })

    # Elemento 2 — séries % da despesa total (12m, R$ constantes), 2000+
    series = {}
    chaves = [
        (("4.1 ", "4.2 ", "4.3 ", "4.4.1 "), "despesas_obrigatorias_pct_total"),
        (("4.4.2 ",),                        "despesas_discricionarias_pct_total"),
        (("Investimento",),                  "investimentos_pct_total"),
    ]
    for prefixos, chave in chaves:
        s = serie_12m_pct_total(df_rtn, prefixos)
        for _, r in s.iterrows():
            d = r["data"].strftime("%Y-%m-%d")
            series.setdefault(d, {"data": d, "ano": int(r["ano"])})[chave] = \
                round(float(r["pct"]), 4)
    _salvar(out, "federal_series.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "observacao": "Séries em % da Despesa Total, acumulado 12m em R$ "
                      "constantes (IPCA). Exibir de 2010 em diante.",
        "series": sorted(series.values(), key=lambda x: x["data"]),
    })

    # Elementos 4 e 5 — composições (12m, R$ constantes)
    ref = f"{ano}-{mes:02d}"
    for nome_arq, itens, obs in [
        ("federal_composicao_obrigatorias.json",
         composicao_obrigatorias_federal(df_rtn, ano, mes),
         "Rubricas RTN: 4.1, 4.2, 4.4.1 e maiores aberturas de 4.3."),
        ("federal_composicao_investimentos.json",
         composicao_investimentos_federal(df_rtn, ano, mes),
         "Rubricas da aba 1.3 da RTN — investimentos GND 4 por natureza."),
    ]:
        _salvar(out, nome_arq, {
            "schema_version": SCHEMA_VERSION,
            "gerado_em": _gerado_em(),
            "referencia": ref,
            "observacao": obs,
            "dados": [{"nome": i["nome"],
                       "valor_milhoes": round(i["valor_mi"], 4),
                       "pct": round(i["pct"], 4)} for i in itens],
        })

    # Elemento 6 — resultado primário 12m
    traj = serie_resultado_primario(df_rtn, ano, mes)
    _salvar(out, "federal_resultado_primario.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "observacao": "Resultado primário acumulado 12 meses, R$ bilhões "
                      "(nominal).",
        "dados": [{"data": r["data"].strftime("%Y-%m-%d"),
                   "valor": round(float(r["valor"]), 4)}
                  for _, r in traj.iterrows()],
    })
    return r_fed


def exportar_estados(out: Path, df_est: pd.DataFrame, cont: dict) -> tuple | None:
    est = cont.get("estados", {})
    bloco_cons = est.get("_consolidado", {})

    # {cod_ibge: bloco} — o contador estadual é chaveado por UF
    blocos_por_cod: dict[int, dict] = {}
    for uf, blk in est.items():
        if uf == "_consolidado":
            continue
        linha = df_est[df_est["uf"] == uf]
        if not linha.empty:
            blocos_por_cod[int(linha["cod_ibge"].iloc[0])] = blk

    scatter = calcular_scatter_correntes_invest(df_est, blocos_por_cod)
    scatter = _sanear_ratios(scatter, "estados")
    r_est = ratio_ytd_subnacional(df_est, bloco_cons)
    referencia = (f"{int(scatter['ano'].iloc[0])}-B{int(scatter['periodo'].iloc[0])}"
                  if not scatter.empty else None)

    _salvar(out, "estados_resumo.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "contador": _bloco_web(bloco_cons),
        "acumulado_real": _acumulado_real(df_est),
        "termometro": _termometro_web(r_est),
        "total_ufs": int(df_est["cod_ibge"].nunique()),
        "referencia": referencia,
    })

    dados = [_linha_ente(r, 2, blocos_por_cod) for _, r in scatter.iterrows()]
    _salvar(out, "estados_mapa.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "referencia": referencia,
        "observacao": "Projeção 'no ano, até o bimestre corrente' (base "
                      "unificada 02/07/2026) — mesma conta do contador.",
        "dados": dados,
    })

    por_total = sorted(dados, key=lambda x: x["total_milhoes"], reverse=True)
    _salvar(out, "estados_ranking.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "referencia": referencia,
        "ranking_total": [
            {**d,
             "total_rs": round(d["total_milhoes"] * 1e6, 2),
             "investimento_rs": round(d["invest_milhoes"] * 1e6, 2)}
            for d in por_total
        ],
        "ranking_investimento_top":
            sorted(dados, key=lambda x: x["invest_ratio"], reverse=True),
        "ranking_investimento_bottom":
            sorted(dados, key=lambda x: x["invest_ratio"]),
    })

    composicoes = {"_consolidado": _composicao_ente(
        df_est, None, bloco_cons, "Todos os Estados + DF", None)}
    for uf, blk in est.items():
        if uf == "_consolidado":
            continue
        linha = df_est[df_est["uf"] == uf]
        if linha.empty:
            continue
        cod = int(linha["cod_ibge"].iloc[0])
        composicoes[uf] = _composicao_ente(
            df_est, [cod], blk, linha["ente"].iloc[0], uf)
    _salvar(out, "estados_composicao_gastos.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "referencia": referencia,
        "cobertura_entes": len(composicoes) - 1,
        "observacao": "Composição na base unificada 02/07/2026: realizado no "
                      "ano + projeção sazonal até o bimestre corrente (mesma "
                      "fórmula do contador; os elementos batem por construção).",
        "categorias": [{"cod_conta": c, "cor": cor}
                       for c, cor in CORES_CATEGORIA.items()],
        "dados": composicoes,
    })
    return r_est


def exportar_municipios(out: Path, df_mun: pd.DataFrame, cont: dict) -> tuple | None:
    mun = cont.get("municipios", {})
    bloco_cons = mun.get("_consolidado", {})
    blocos_por_cod = {
        int(k): v for k, v in mun.items() if k != "_consolidado"
    }

    # Guarda: consórcios públicos reportam RREO ao SICONFI sob o cod_ibge do
    # município-SEDE. Sem este filtro, o gasto do consórcio se mistura ao da
    # prefeitura (mesma chave) e o ente aparece duplicado no ranking.
    # A correção definitiva é na extração; aqui protegemos a camada web.
    eh_consorcio = df_mun["ente"].str.upper().str.contains(
        "CONSORCIO|CONSÓRCIO", regex=True, na=False)
    if eh_consorcio.any():
        n = df_mun[eh_consorcio]["ente"].nunique()
        print(f"  AVISO [municipios]: {n} consórcio(s) público(s) excluído(s) "
              f"da camada web (reportam sob o cod_ibge do município-sede).")
        df_mun = df_mun[~eh_consorcio]

    scatter = calcular_scatter_correntes_invest(df_mun, blocos_por_cod)
    # Nomes de ente podem variar entre bimestres p/ o mesmo código (grafia do
    # SICONFI) — mantém uma linha por município.
    scatter = scatter.drop_duplicates(subset="cod_ibge", keep="first")
    scatter = _sanear_ratios(scatter, "municipios")
    r_mun = ratio_ytd_subnacional(df_mun, bloco_cons)
    referencia = (f"{int(scatter['ano'].iloc[0])}-B{int(scatter['periodo'].iloc[0])}"
                  if not scatter.empty else None)

    _salvar(out, "municipios_resumo.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "contador": _bloco_web(bloco_cons),
        "acumulado_real": _acumulado_real(df_mun),
        "termometro": _termometro_web(r_mun),
        "total_municipios": int(df_mun["cod_ibge"].nunique()),
        "total_ufs": int(df_mun["uf"].nunique()),
        "referencia": referencia,
        "observacao": "Cobertura: municípios que publicam o RREO bimestral no "
                      "SICONFI. Municípios <50 mil hab. podem publicar "
                      "semestralmente (LRF art. 63) e ficam de fora até a "
                      "consulta semestral complementar.",
    })

    # Mapa nacional: municípios agregados por UF (arquivo leve)
    agg = (scatter.groupby("uf", as_index=False)
           .agg(invest_milhoes=("invest_milhoes", "sum"),
                total_milhoes=("total_milhoes", "sum"),
                n_municipios=("cod_ibge", "count"),
                ano=("ano", "max"), periodo=("periodo", "max")))
    agg["invest_ratio"] = (agg["invest_milhoes"] / agg["total_milhoes"] * 100)
    # cod_ibge da UF = 2 primeiros dígitos do cod municipal (p/ featureidkey do mapa)
    cod_uf = (scatter.assign(cod_uf=scatter["cod_ibge"].astype(str).str[:2])
              .groupby("uf")["cod_uf"].first())
    _salvar(out, "municipios_mapa_ufs.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "referencia": referencia,
        "observacao": "Municípios agregados por UF para o mapa nacional leve.",
        "dados": [
            {
                "cod_ibge": int(cod_uf[r["uf"]]),
                "cod_ibge_str": cod_uf[r["uf"]],
                "uf": r["uf"],
                "ente": r["uf"],
                "n_municipios": int(r["n_municipios"]),
                "invest_ratio": round(float(r["invest_ratio"]), 2),
                "invest_milhoes": round(float(r["invest_milhoes"]), 2),
                "total_milhoes": round(float(r["total_milhoes"]), 2),
                "correntes_obrig_milhoes": round(
                    float(r["total_milhoes"] - r["invest_milhoes"]), 2),
                "ano": int(r["ano"]), "periodo": int(r["periodo"]),
            }
            for _, r in agg.iterrows()
        ],
    })

    # Ranking nacional + um arquivo de mapa por UF (o site carrega sob demanda)
    dados = [_linha_ente(r, 7, blocos_por_cod) for _, r in scatter.iterrows()]
    por_total = sorted(dados, key=lambda x: x["total_milhoes"], reverse=True)
    por_ratio = sorted(dados, key=lambda x: x["invest_ratio"], reverse=True)
    _salvar(out, "municipios_ranking.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "referencia": referencia,
        "ranking_total": [
            {**d,
             "total_rs": round(d["total_milhoes"] * 1e6, 2),
             "investimento_rs": round(d["invest_milhoes"] * 1e6, 2)}
            for d in por_total
        ],
        "ranking_investimento_top": por_ratio[:100],
        "ranking_investimento_bottom": por_ratio[-100:][::-1],
    })
    for uf in sorted(scatter["uf"].unique()):
        _salvar(out, f"municipios_mapa_uf_{uf}.json", {
            "schema_version": SCHEMA_VERSION,
            "gerado_em": _gerado_em(),
            "referencia": referencia,
            "uf": uf,
            "dados": [d for d in dados if d["uf"] == uf],
        })

    # Composição por conta (NOVO — o Elemento 5 municipal faltava na camada web):
    # _consolidado + agregado por UF. Por município fica p/ evolução futura.
    composicoes = {"_consolidado": _composicao_ente(
        df_mun, None, bloco_cons, "Todos os municípios", None)}
    for uf in sorted(df_mun["uf"].unique()):
        cods = df_mun[df_mun["uf"] == uf]["cod_ibge"].unique().tolist()
        composicoes[uf] = _composicao_ente(
            df_mun, cods, None, f"Municípios de {uf}", uf)
    _salvar(out, "municipios_composicao_gastos.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "referencia": referencia,
        "observacao": "Composição na base unificada 02/07/2026. Agregados por "
                      "UF usam ratio neutro quando o ente não está no contador.",
        "categorias": [{"cod_conta": c, "cor": cor}
                       for c, cor in CORES_CATEGORIA.items()],
        "dados": composicoes,
    })
    return r_mun


def exportar_geral(out: Path, cont: dict,
                   r_fed: tuple | None, r_est: tuple | None,
                   r_mun: tuple | None) -> None:
    fed = _bloco_web(cont.get("federal", {}))
    est = _bloco_web(cont.get("estados", {}).get("_consolidado", {}))
    mun = _bloco_web(cont.get("municipios", {}).get("_consolidado", {}))

    # Total consolidado ancorado no instante da geração: as esferas têm
    # start_ms distintos, então o total soma as POSIÇÕES atuais e anda com a
    # soma das taxas — em qualquer instante, total == federal+estados+municípios.
    agora = _agora_ms()
    total = {
        "fonte": "web_snapshot",
        "acc_base_rs": round(sum(b["valor_atual_rs"] for b in (fed, est, mun)), 2),
        "taxa_por_segundo_rs": round(
            sum(b["taxa_por_segundo_rs"] for b in (fed, est, mun)), 4),
        "start_ms": agora,
        "observacao": "Total iniciado no momento de geração do JSON web para "
                      "manter a identidade total == soma das esferas.",
    }

    # Termômetro consolidado = média ponderada das esferas disponíveis
    disponiveis = [r for r in (r_fed, r_est, r_mun) if r is not None]
    consolidado = None
    if disponiveis:
        inv = sum(r[1] for r in disponiveis)
        tot = sum(r[2] for r in disponiveis)
        if tot > 0:
            consolidado = {
                "invest_ratio": round(inv / tot * 100, 2),
                "investimento_milhoes": round(inv, 2),
                "total_milhoes": round(tot, 2),
                "correntes_obrig_milhoes": round(tot - inv, 2),
                "base": "no ano, projetado até o período corrente",
            }

    _salvar(out, "geral.json", {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _gerado_em(),
        "ano_referencia": datetime.now().year,
        "contador": {"total": total, "federal": fed,
                     "estados": est, "municipios": mun},
        "cards": [
            {"id": "federal", "label": "Governo Federal",
             "valor_atual_rs": fed["valor_atual_rs"],
             "ultimo_dado": fed["ultimo_dado"]},
            {"id": "estados", "label": "Estados + DF",
             "valor_atual_rs": est["valor_atual_rs"],
             "ultimo_dado": est["ultimo_dado"]},
            {"id": "municipios", "label": "Municípios",
             "valor_atual_rs": mun["valor_atual_rs"],
             "ultimo_dado": mun["ultimo_dado"]},
        ],
        "termometro": {
            "consolidado": consolidado,
            "federal": _termometro_web(r_fed, tipo_ref="M"),
            "estados": _termometro_web(r_est),
            "municipios": _termometro_web(r_mun),
        },
    })


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera a camada web (JSONs) do Gastômetro em data/web/")
    parser.add_argument("--out", default=str(DATA_DIR / "web"),
                        help="diretório de saída (padrão: data/web)")
    args = parser.parse_args()
    out = Path(args.out)

    print(f"Exportando camada web para {out} ...")
    cont = json.loads(
        (DATA_DIR / "contador_fiscal.json").read_text(encoding="utf-8"))

    r_fed = r_est = r_mun = None

    rtn_path = DATA_DIR / "rtn" / "rtn_mensal.parquet"
    if rtn_path.exists():
        r_fed = exportar_federal(out, pd.read_parquet(rtn_path), cont)
    else:
        print("  AVISO: RTN ausente — arquivos federais não gerados.")

    est_path = DATA_DIR / "estados" / "gastos_estados.parquet"
    if est_path.exists():
        r_est = exportar_estados(out, pd.read_parquet(est_path), cont)
    else:
        print("  AVISO: parquet de estados ausente.")

    mun_path = DATA_DIR / "municipios" / "gastos_municipios.parquet"
    if mun_path.exists():
        r_mun = exportar_municipios(out, pd.read_parquet(mun_path), cont)
    else:
        print("  AVISO: parquet de municípios ausente.")

    exportar_geral(out, cont, r_fed, r_est, r_mun)
    print("Concluído.")


if __name__ == "__main__":
    main()
