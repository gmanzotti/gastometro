"""
pipelines/simplificar_geojson.py  —  Aligeira as malhas GeoJSON dos mapas interativos
─────────────────────────────────────────────────────────────────────────────
POR QUE ISSO EXISTE:
  Os mapas (aba Estadual e Municipal) recebem o GeoJSON CRU dentro da figura
  Plotly, que serializa a malha inteira e a envia ao navegador a cada load.
  A malha municipal do IBGE tem ~5.570 polígonos e ~56 MB — é a causa direta da
  lentidão. Reduzir o nº de vértices de cada fronteira (mantendo o formato
  reconhecível) corta drasticamente o tamanho do arquivo e o tráfego pro browser.

COMO FUNCIONA (o "simplify"):
  Usamos o algoritmo de Douglas-Peucker via shapely. shapely é exatamente o
  motor que o geopandas.simplify() chama por baixo — mesmo resultado, sem
  arrastar a stack pesada do GDAL/pyproj. Para cada polígono, vértices que se
  desviam menos que `tolerance` da linha são descartados.

  CRS = EPSG:4326 (graus de lat/lon), então `tolerance` está em GRAUS:
    0.01° ≈ 1,1 km   →  imperceptível no zoom nacional do painel
  Quanto MAIOR a tolerância, mais "quadrado" e leve fica o mapa.

  preserve_topology=True evita que um polígono colapse ou se auto-cruze. ATENÇÃO:
  isso preserva a topologia DENTRO de cada polígono, mas NÃO as fronteiras
  COMPARTILHADAS entre entes vizinhos (cada um é simplificado em separado). No
  zoom nacional, com o fundo escuro do mapa, eventuais "fendas" finas entre
  estados/municípios são invisíveis. Se algum dia incomodar, a evolução é trocar
  por simplificação topológica (biblioteca `topojson`).

ENTRADA  (malhas cruas, baixadas do IBGE pelos loaders do dashboard):
  data/estados_geojson.json       (~1,1 MB,  27 features)
  data/municipios_geojson.json    (~56  MB,  ~5.570 features)

SAÍDA  (malhas leves, consumidas preferencialmente pelo dashboard):
  data/estados_geojson_simplificado.json
  data/municipios_geojson_simplificado.json

  Os arquivos CRUS são preservados como fonte-de-verdade: dá para reprocessar
  com outra tolerância quando quiser, sem precisar rebaixar do IBGE de novo.

COMO RODAR:
  python pipelines/simplificar_geojson.py
  # tolerâncias custom (graus):
  python pipelines/simplificar_geojson.py --tol-estados 0.01 --tol-municipios 0.02

DEPENDÊNCIA:
  shapely>=2.0  — leve (wheel puro, sem GDAL). É dependência só de
  DESENVOLVIMENTO: o dashboard em produção lê apenas o JSON já simplificado,
  então a TI NÃO precisa instalar shapely no servidor.
"""

import argparse
import json
import sys
from pathlib import Path

from shapely.geometry import shape, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import DATA_DIR

# Tolerâncias-padrão (em graus, pois o CRS é EPSG:4326).
# Estados: poucos polígonos e fronteiras mais visíveis → tolerância modesta.
# Municípios: o arquivo gigante e sem linhas de borda no mapa → pode ser maior.
TOL_ESTADOS_PADRAO = 0.01
TOL_MUNICIPIOS_PADRAO = 0.01


def _contar_vertices(geometria: dict) -> int:
    """Conta vértices de uma geometria GeoJSON (Polygon ou MultiPolygon).

    Serve só para o relatório de antes/depois — mostra o quanto a malha encolheu
    em nº de pontos, que é o que de fato pesa no arquivo e no navegador.
    """
    tipo = geometria.get("type")
    coords = geometria.get("coordinates", [])
    if tipo == "Polygon":
        # [ anel_externo, buraco1, ... ]; cada anel é uma lista de [lon, lat]
        return sum(len(anel) for anel in coords)
    if tipo == "MultiPolygon":
        # [ poligono, ... ]; cada polígono é [ anel_externo, buraco1, ... ]
        return sum(len(anel) for poligono in coords for anel in poligono)
    return 0


def simplificar_geojson(caminho_entrada: Path, caminho_saida: Path, tolerancia: float) -> None:
    """Lê um GeoJSON, simplifica a geometria de cada feature e grava a versão leve.

    A lógica é deliberadamente simples e sem dependência de geopandas:
      1. json.load → dicionário GeoJSON
      2. para cada feature: shape() monta o objeto shapely a partir das coords,
         .simplify() descarta vértices redundantes, mapping() volta pra coords
      3. json.dump compacto (sem espaços) → arquivo de saída
    As propriedades (ex.: 'codarea', usado pelo featureidkey do Plotly) são
    preservadas intactas; só a geometria muda.
    """
    if not caminho_entrada.exists():
        print(f"  [pulado] {caminho_entrada.name} nao encontrado.")
        return

    dados = json.loads(caminho_entrada.read_text(encoding="utf-8"))
    features = dados.get("features", [])

    vertices_antes = 0
    vertices_depois = 0
    for feature in features:
        geom = feature.get("geometry")
        if not geom:
            continue
        vertices_antes += _contar_vertices(geom)

        # Coração da operação: Douglas-Peucker via shapely.
        geom_simplificada = shape(geom).simplify(tolerancia, preserve_topology=True)
        feature["geometry"] = mapping(geom_simplificada)

        vertices_depois += _contar_vertices(feature["geometry"])

    # separators sem espaço deixa o JSON o mais compacto possível.
    caminho_saida.write_text(
        json.dumps(dados, separators=(",", ":")),
        encoding="utf-8",
    )

    mb_antes = caminho_entrada.stat().st_size / 1e6
    mb_depois = caminho_saida.stat().st_size / 1e6
    reducao = (1 - mb_depois / mb_antes) * 100 if mb_antes else 0
    print(
        f"  [ok] {caminho_entrada.name} -> {caminho_saida.name}  "
        f"(tol={tolerancia} graus)\n"
        f"      tamanho:  {mb_antes:7.2f} MB -> {mb_depois:7.2f} MB  "
        f"(-{reducao:.1f}%)\n"
        f"      vertices: {vertices_antes:>9,} -> {vertices_depois:>9,}  "
        f"(-{(1 - vertices_depois / vertices_antes) * 100 if vertices_antes else 0:.1f}%)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Simplifica as malhas GeoJSON dos mapas interativos.")
    parser.add_argument("--tol-estados", type=float, default=TOL_ESTADOS_PADRAO,
                        help=f"Tolerância em graus para os estados (padrão {TOL_ESTADOS_PADRAO}).")
    parser.add_argument("--tol-municipios", type=float, default=TOL_MUNICIPIOS_PADRAO,
                        help=f"Tolerância em graus para os municípios (padrão {TOL_MUNICIPIOS_PADRAO}).")
    args = parser.parse_args()

    print("Simplificando malhas GeoJSON…\n")
    simplificar_geojson(
        DATA_DIR / "estados_geojson.json",
        DATA_DIR / "estados_geojson_simplificado.json",
        args.tol_estados,
    )
    simplificar_geojson(
        DATA_DIR / "municipios_geojson.json",
        DATA_DIR / "municipios_geojson_simplificado.json",
        args.tol_municipios,
    )
    print("\nPronto. O dashboard usará as versões simplificadas automaticamente.")


if __name__ == "__main__":
    main()