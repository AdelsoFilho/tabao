"""
Distância, rota e viabilidade: vale a pena ir até lá?

O mercado mais barato nem sempre compensa. Se a economia na compra é de R$ 8 e
o deslocamento de ida e volta gasta R$ 12 de combustível, o "mais barato" saiu
mais caro. Este módulo faz essa conta.

    custo real = custo da compra + combustível do trajeto de ida e volta

Duas formas de medir a distância:

    haversine   linha reta, instantânea, sem rede
    OSRM        rota real por ruas, gratuita e sem chave de API

A linha reta subestima o percurso urbano, então quando o OSRM não está
disponível aplica-se um fator de desvio antes de calcular o custo.
"""

import math
from dataclasses import dataclass
from typing import Iterable, Optional

OSRM = "https://router.project-osrm.org/route/v1/driving/{coordenadas}"

CABECALHOS = {
    "User-Agent": (
        "TaBao/0.1 (projeto academico SENAI Fatesg; monitoramento colaborativo "
        "de precos de alimentos)"
    ),
}

RAIO_TERRA_M = 6371000.0

# Em malha urbana o trajeto real costuma ser 30% a 40% maior que a linha reta.
# Usado apenas quando o roteamento por ruas não está disponível.
FATOR_DESVIO_URBANO = 1.35

# Valores padrão quando o usuário ainda não informou os do carro dele.
CONSUMO_PADRAO_KM_L = 10.0
PRECO_COMBUSTIVEL_PADRAO = 6.00


class RotaError(RuntimeError):
    """Falha ao calcular a rota."""


@dataclass
class Trajeto:
    """Distância, tempo e, opcionalmente, o desenho da rota."""

    distancia_km: float
    duracao_min: Optional[float] = None
    # "ruas" quando veio do roteamento real, "reta" quando é linha reta ajustada.
    metodo: str = "reta"
    # Pontos (latitude, longitude) para desenhar o caminho no mapa.
    # Vazio quando só se pediu a distância, para não trafegar dado à toa.
    geometria: list[tuple[float, float]] = None

    @property
    def ida_e_volta_km(self) -> float:
        return round(self.distancia_km * 2, 2)

    @property
    def aproximado(self) -> bool:
        return self.metodo == "reta"


@dataclass
class Viabilidade:
    """Resultado da conta 'vale a pena ir até lá?'."""

    nome: str
    cnpj: str
    custo_compra: float
    trajeto: Trajeto
    custo_combustivel: float

    @property
    def custo_total(self) -> float:
        return round(self.custo_compra + self.custo_combustivel, 2)

    @property
    def distancia_km(self) -> float:
        return self.trajeto.distancia_km


def distancia_haversine(origem: tuple[float, float],
                        destino: tuple[float, float]) -> float:
    """
    Distância em linha reta entre dois pontos, em quilômetros.

    Fórmula de haversine: considera a curvatura da Terra, o que importa pouco
    dentro de uma cidade, mas mantém o cálculo correto em qualquer escala.
    """
    lat1, lon1 = math.radians(origem[0]), math.radians(origem[1])
    lat2, lon2 = math.radians(destino[0]), math.radians(destino[1])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return round(2 * RAIO_TERRA_M * math.asin(math.sqrt(a)) / 1000, 3)


def trajeto_em_linha_reta(origem: tuple[float, float],
                          destino: tuple[float, float]) -> Trajeto:
    """Distância estimada aplicando o fator de desvio urbano."""
    reta = distancia_haversine(origem, destino)
    return Trajeto(distancia_km=round(reta * FATOR_DESVIO_URBANO, 2), metodo="reta")


def trajeto_por_ruas(origem: tuple[float, float], destino: tuple[float, float],
                     tempo_limite: int = 20, com_desenho: bool = False) -> Trajeto:
    """
    Calcula a rota real de carro pelo OSRM.

    Com `com_desenho`, traz também a lista de pontos do caminho, para
    desenhá-lo no mapa. Sem ele, pede só os números — a resposta fica muito
    menor, o que importa quando se calcula a rota até vários mercados.

    O serviço é gratuito e não exige chave. Em caso de falha, levanta
    RotaError para que o chamador use a estimativa em linha reta.
    """
    try:
        import requests
    except ImportError as erro:  # pragma: no cover
        raise RotaError("requests não está instalado.") from erro

    # O OSRM espera longitude,latitude — o inverso do habitual.
    coordenadas = f"{origem[1]},{origem[0]};{destino[1]},{destino[0]}"

    try:
        resposta = requests.get(
            OSRM.format(coordenadas=coordenadas),
            params=(
                {"overview": "full", "geometries": "geojson"}
                if com_desenho else {"overview": "false"}
            ),
            headers=CABECALHOS,
            timeout=tempo_limite,
        )
    except Exception as erro:
        raise RotaError(f"Falha de rede ao calcular a rota: {erro}") from erro

    if resposta.status_code != 200:
        raise RotaError(f"O serviço de rotas respondeu {resposta.status_code}.")

    try:
        dados = resposta.json()
    except ValueError as erro:
        raise RotaError("Resposta do serviço de rotas não é JSON.") from erro

    if dados.get("code") != "Ok" or not dados.get("routes"):
        raise RotaError("Nenhuma rota encontrada entre os dois pontos.")

    rota = dados["routes"][0]

    # O GeoJSON traz [longitude, latitude]; o Leaflet espera o inverso.
    desenho = None
    if com_desenho:
        coordenadas_rota = (rota.get("geometry") or {}).get("coordinates") or []
        desenho = [(ponto[1], ponto[0]) for ponto in coordenadas_rota]

    return Trajeto(
        distancia_km=round(rota["distance"] / 1000, 2),
        duracao_min=round(rota["duration"] / 60, 1),
        metodo="ruas",
        geometria=desenho,
    )


def calcular_trajeto(origem: tuple[float, float], destino: tuple[float, float],
                     usar_ruas: bool = True, com_desenho: bool = False) -> Trajeto:
    """
    Melhor estimativa disponível: rota por ruas, com linha reta como reserva.

    Nunca levanta exceção — sempre devolve alguma distância, porque a tela
    precisa mostrar um número mesmo quando o serviço de rotas está fora.
    """
    if usar_ruas:
        try:
            return trajeto_por_ruas(origem, destino, com_desenho=com_desenho)
        except RotaError:
            pass

    trajeto = trajeto_em_linha_reta(origem, destino)
    if com_desenho:
        # Sem roteamento, o desenho possível é o segmento entre os dois pontos.
        trajeto.geometria = [origem, destino]
    return trajeto


def custo_do_trajeto(distancia_km: float, consumo_km_l: float = CONSUMO_PADRAO_KM_L,
                     preco_combustivel: float = PRECO_COMBUSTIVEL_PADRAO,
                     ida_e_volta: bool = True) -> float:
    """
    Quanto custa em combustível percorrer essa distância.

    Considera ida e volta por padrão: quem vai ao mercado precisa voltar, e
    ignorar isso subestimaria o custo pela metade.
    """
    if consumo_km_l <= 0:
        raise ValueError("O consumo em km/l deve ser maior que zero.")

    percorrido = distancia_km * (2 if ida_e_volta else 1)
    litros = percorrido / consumo_km_l
    return round(litros * preco_combustivel, 2)


def avaliar(origem: tuple[float, float],
            candidatos: Iterable[tuple[str, str, float, tuple[float, float]]],
            consumo_km_l: float = CONSUMO_PADRAO_KM_L,
            preco_combustivel: float = PRECO_COMBUSTIVEL_PADRAO,
            usar_ruas: bool = True) -> list[Viabilidade]:
    """
    Ordena os mercados pelo custo REAL: compra mais deslocamento.

    Cada candidato é (nome, cnpj, custo_da_compra, (lat, lon)). O resultado sai
    ordenado do menor custo total para o maior — que pode ser uma ordem
    diferente da do preço de prateleira, e é justamente esse o ponto.
    """
    resultado: list[Viabilidade] = []

    for nome, cnpj, custo_compra, destino in candidatos:
        trajeto = calcular_trajeto(origem, destino, usar_ruas=usar_ruas)
        combustivel = custo_do_trajeto(
            trajeto.distancia_km, consumo_km_l, preco_combustivel
        )
        resultado.append(Viabilidade(
            nome=nome, cnpj=cnpj, custo_compra=round(custo_compra, 2),
            trajeto=trajeto, custo_combustivel=combustivel,
        ))

    resultado.sort(key=lambda v: v.custo_total)
    return resultado


def compensa_ir(mais_perto: Viabilidade, alternativa: Viabilidade) -> dict:
    """
    Compara ir ao mercado mais próximo com ir a outro mais distante.

    Devolve a economia na compra, o custo extra do deslocamento e o saldo.
    Saldo positivo significa que vale a pena pegar a estrada.
    """
    economia = round(mais_perto.custo_compra - alternativa.custo_compra, 2)
    custo_extra = round(alternativa.custo_combustivel - mais_perto.custo_combustivel, 2)
    saldo = round(economia - custo_extra, 2)

    return {
        "economia_na_compra": economia,
        "custo_extra_do_trajeto": custo_extra,
        "saldo": saldo,
        "compensa": saldo > 0,
        "km_a_mais": round(alternativa.distancia_km - mais_perto.distancia_km, 2),
    }
