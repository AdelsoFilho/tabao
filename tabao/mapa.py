"""
Localização dos estabelecimentos no mapa, com dados do OpenStreetMap.

Duas fontes, ambas gratuitas e sem chave de API:

    Overpass API   lista supermercados, atacadões e mercearias de uma região
    Nominatim      converte o endereço impresso no cupom em coordenadas

O resultado é a resposta visual para "onde eu compro?": o mapa mostra todos os
mercados da região, destacando aqueles que já têm preço na base.

Uso responsável: as duas APIs são mantidas por doação e pedem no máximo uma
requisição por segundo, além de um User-Agent que identifique a aplicação.
Este módulo respeita as duas regras e guarda o resultado em cache local para
não repetir consultas.
"""

import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

OVERPASS = "https://overpass-api.de/api/interpreter"
NOMINATIM_BUSCA = "https://nominatim.openstreetmap.org/search"

CABECALHOS = {
    "User-Agent": (
        "TaBao/0.1 (projeto academico SENAI Fatesg; monitoramento colaborativo "
        "de precos de alimentos)"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Tipos de loja do OpenStreetMap que vendem alimentos.
TIPOS_OSM = {
    "supermarket": "Supermercado",
    "wholesale": "Atacadão",
    "convenience": "Mercearia",
    "greengrocer": "Hortifrúti",
    "butcher": "Açougue",
    "bakery": "Padaria",
}

INTERVALO_ENTRE_CHAMADAS = 1.1  # segundos, exigência de uso das APIs


class MapaError(RuntimeError):
    """Falha ao consultar o OpenStreetMap."""


@dataclass
class Local:
    """Um estabelecimento localizado no mapa."""

    nome: str
    latitude: float
    longitude: float
    tipo: str = "supermarket"
    endereco: str = ""
    marca: str = ""
    osm_id: str = ""
    cnpj: str = ""          # preenchido quando casa com um cupom da base
    fonte: str = "osm"      # "osm" ou "cupom"
    precisao: str = ""      # ver Estabelecimento.precisao

    @property
    def tipo_legivel(self) -> str:
        return TIPOS_OSM.get(self.tipo, "Mercado")

    @property
    def tem_precos(self) -> bool:
        return bool(self.cnpj)


def _requisitar(url: str, params: dict, metodo: str = "get", tempo_limite: int = 90):
    try:
        import requests
    except ImportError as erro:  # pragma: no cover
        raise MapaError("requests não está instalado. Rode: pip install requests") from erro

    try:
        if metodo == "post":
            resposta = requests.post(url, data=params, headers=CABECALHOS, timeout=tempo_limite)
        else:
            resposta = requests.get(url, params=params, headers=CABECALHOS, timeout=tempo_limite)
    except Exception as erro:
        raise MapaError(f"Falha de rede ao consultar o OpenStreetMap: {erro}") from erro

    if resposta.status_code != 200:
        raise MapaError(
            f"O OpenStreetMap respondeu com status {resposta.status_code}. "
            "O serviço é gratuito e limita requisições; tente de novo em alguns minutos."
        )

    try:
        return resposta.json()
    except ValueError as erro:
        raise MapaError("Resposta do OpenStreetMap não é JSON válido.") from erro


# --------------------------------------------------------------------------
# Geocodificação (endereço -> coordenadas)
# --------------------------------------------------------------------------

# Trechos que só existem no cadastro fiscal e atrapalham a geocodificação.
_RUIDO_ENDERECO = re.compile(
    r"(?i)\b(QUADRA|QD|LOTE|LT|SALA|LOJA|BOX|GALPAO|ANDAR|BLOCO|BL|CONJ|CONJUNTO)\b[^,]*"
)


def variantes_de_endereco(endereco: str) -> list[str]:
    """
    Gera versões progressivamente mais simples de um endereço.

    Endereços de cupom fiscal trazem informação de cadastro que o Nominatim não
    reconhece. "ENGENHEIRO FUAD RASSI, 49, QUADRA R LOTE 01/21, SETOR NOVA
    VILA, GOIANIA, GO" só é encontrado depois de remover o trecho de quadra e
    lote — foi exatamente o que aconteceu no cupom de teste.

    A busca tenta da versão mais específica para a mais genérica e para na
    primeira que o serviço reconhecer.
    """
    if not endereco:
        return []

    partes = [p.strip() for p in endereco.split(",") if p.strip()]
    sem_ruido = [p for p in partes if not _RUIDO_ENDERECO.fullmatch(p)]

    variantes = [", ".join(sem_ruido)]

    # Sem o número: alguns cadastros trazem numeração que não existe no mapa.
    sem_numero = [p for p in sem_ruido if not p.isdigit()]
    if sem_numero != sem_ruido:
        variantes.append(", ".join(sem_numero))

    # Só logradouro + as duas últimas partes (cidade e UF).
    if len(sem_numero) >= 3:
        variantes.append(", ".join([sem_numero[0]] + sem_numero[-2:]))

    # Último recurso: logradouro e cidade.
    if len(sem_numero) >= 2:
        variantes.append(f"{sem_numero[0]}, {sem_numero[-2]}")

    vistas: list[str] = []
    for v in variantes:
        if v and v not in vistas:
            vistas.append(v)
    return vistas


# Como o Nominatim classifica o que encontrou, do mais exato ao mais vago.
_PRECISAO_POR_TIPO = {
    "building": "endereco", "house": "endereco", "residential": "endereco",
    "amenity": "endereco", "shop": "endereco", "place": "endereco",
    "postcode": "cep",
    "road": "rua", "street": "rua", "highway": "rua",
}


def _consultar_nominatim(parametros: dict) -> Optional[tuple[float, float, str]]:
    """
    Consulta o Nominatim e devolve (lat, lon, precisao).

    A precisão importa: um resultado do tipo "road" é o centro do logradouro,
    e numa avenida longa isso erra centenas de metros. Guardar essa informação
    permite avisar o usuário em vez de fingir exatidão.
    """
    parametros = dict(parametros)
    parametros.setdefault("format", "json")
    parametros.setdefault("limit", 1)
    parametros.setdefault("countrycodes", "br")
    parametros["addressdetails"] = 1

    dados = _requisitar(NOMINATIM_BUSCA, parametros, tempo_limite=30)
    if not dados:
        return None

    try:
        primeiro = dados[0]
        tipo = primeiro.get("addresstype") or primeiro.get("type") or ""
        precisao = _PRECISAO_POR_TIPO.get(tipo, "rua")
        return (float(primeiro["lat"]), float(primeiro["lon"]), precisao)
    except (KeyError, ValueError, IndexError):
        return None


def geocodificar_por_cep(cep: str, logradouro: str = "", numero: str = "",
                         cidade: str = "") -> Optional[tuple[float, float, str]]:
    """
    Geocodifica usando o CEP, que delimita muito melhor que o nome da rua.

    Tenta primeiro a consulta estruturada com rua e número; se falhar, usa só
    o CEP, cujo centroide fica dentro da quadra correta.
    """
    cep_limpo = "".join(c for c in (cep or "") if c.isdigit())
    if len(cep_limpo) != 8:
        return None

    cep_formatado = f"{cep_limpo[:5]}-{cep_limpo[5:]}"

    if logradouro:
        rua = f"{numero} {logradouro}".strip()
        achado = _consultar_nominatim({
            "street": rua, "postalcode": cep_formatado, "country": "Brasil",
            **({"city": cidade} if cidade else {}),
        })
        if achado and achado[2] == "endereco":
            return achado
        time.sleep(INTERVALO_ENTRE_CHAMADAS)

    return _consultar_nominatim({"postalcode": cep_formatado, "country": "Brasil"})


def geocodificar(endereco: str, cidade: str = "",
                 uf: str = "") -> Optional[tuple[float, float, str]]:
    """
    Converte um endereço em coordenadas usando o Nominatim.

    Tenta variantes cada vez mais simples do endereço e devolve
    (lat, lon, precisao). Devolve None quando nenhuma é reconhecida.
    """
    tentativas = variantes_de_endereco(endereco) or ([endereco] if endereco else [])

    for indice, variante in enumerate(tentativas):
        consulta = ", ".join(p for p in [variante, cidade, uf, "Brasil"] if p)
        achado = _consultar_nominatim({"q": consulta})
        if achado:
            return achado
        if indice < len(tentativas) - 1:
            time.sleep(INTERVALO_ENTRE_CHAMADAS)

    return None


def caixa_delimitadora(area: str) -> tuple[float, float, float, float]:
    """
    Descobre a caixa (sul, oeste, norte, leste) de uma cidade ou bairro.

    É o retângulo dentro do qual os mercados serão procurados.
    """
    dados = _requisitar(NOMINATIM_BUSCA, {
        "q": area, "format": "json", "limit": 1, "countrycodes": "br",
    }, tempo_limite=30)

    if not dados:
        raise MapaError(f"Não encontrei a região '{area}' no OpenStreetMap.")

    caixa = dados[0].get("boundingbox")
    if not caixa or len(caixa) != 4:
        raise MapaError(f"A região '{area}' não trouxe uma área delimitada.")

    sul, norte, oeste, leste = (float(v) for v in caixa)
    return (sul, oeste, norte, leste)


# --------------------------------------------------------------------------
# Busca de mercados (Overpass)
# --------------------------------------------------------------------------

def buscar_mercados(caixa: tuple[float, float, float, float]) -> list[Local]:
    """
    Lista todos os estabelecimentos de alimentos dentro da caixa informada.

    Consulta nós e áreas (way/relation), pegando o centro geométrico das áreas
    para que todo mercado vire um ponto único no mapa.
    """
    sul, oeste, norte, leste = caixa
    area = f"{sul},{oeste},{norte},{leste}"
    tipos = "|".join(TIPOS_OSM)

    consulta = f"""
    [out:json][timeout:90];
    (
      node["shop"~"^({tipos})$"]({area});
      way["shop"~"^({tipos})$"]({area});
      relation["shop"~"^({tipos})$"]({area});
    );
    out center tags;
    """

    dados = _requisitar(OVERPASS, {"data": consulta}, metodo="post")

    locais: list[Local] = []
    for elemento in dados.get("elements", []):
        etiquetas = elemento.get("tags", {})

        # Nós têm lat/lon direto; áreas trazem o centro em "center".
        if "lat" in elemento and "lon" in elemento:
            lat, lon = elemento["lat"], elemento["lon"]
        elif "center" in elemento:
            lat, lon = elemento["center"]["lat"], elemento["center"]["lon"]
        else:
            continue

        nome = etiquetas.get("name") or etiquetas.get("brand") or ""
        if not nome:
            continue  # sem nome não ajuda o usuário

        partes_endereco = [
            etiquetas.get("addr:street", ""),
            etiquetas.get("addr:housenumber", ""),
            etiquetas.get("addr:suburb", ""),
        ]
        endereco = ", ".join(p for p in partes_endereco if p)

        locais.append(Local(
            nome=nome,
            latitude=float(lat),
            longitude=float(lon),
            tipo=etiquetas.get("shop", "supermarket"),
            endereco=endereco,
            marca=etiquetas.get("brand", ""),
            osm_id=f"{elemento.get('type', 'node')}/{elemento.get('id', '')}",
        ))

    return locais


# --------------------------------------------------------------------------
# Casamento com os estabelecimentos da base
# --------------------------------------------------------------------------

def _simplificar(texto: str) -> str:
    """
    Reduz um nome a letras e dígitos maiúsculos, sem acento.

    Separa dígitos de letras porque a razão social do cupom costuma trazer o
    número da loja colado ao nome: "(38)CEMA ..." viraria "38CEMA" e nunca
    casaria com "CEMA" no mapa.
    """
    import re as _re

    sem_acento = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    limpo = "".join(c if (c.isalnum() or c == " ") else " " for c in sem_acento.upper())
    limpo = _re.sub(r"(?<=\d)(?=[A-Z])|(?<=[A-Z])(?=\d)", " ", limpo)
    return _re.sub(r"\s+", " ", limpo).strip()


def _palavras_significativas(nome: str) -> set[str]:
    """Palavras do nome que servem para comparação, ignorando ruído."""
    ruido = {
        "LTDA", "SA", "ME", "EPP", "EIRELI", "COMERCIO", "COMERCIAL", "DE", "DA",
        "DO", "DOS", "DAS", "E", "SUPERMERCADO", "SUPERMERCADOS", "MERCADO",
        "ATACADISTA", "ATACADO", "ATACADAO", "REDE", "LOJA", "FILIAL",
    }
    return {p for p in _simplificar(nome).split() if len(p) >= 3 and p not in ruido}


def casar_com_estabelecimentos(locais: list[Local],
                               estabelecimentos: dict[str, str]) -> int:
    """
    Marca no mapa os locais que já têm preços na base.

    O casamento é por nome: compara as palavras significativas da razão social
    do cupom com as do nome no OpenStreetMap. É uma heurística — o cupom traz
    "(38)CEMA CENTRAL MINEIRA ATACADISTA LTDA" e o mapa costuma trazer só
    "CEMA" — por isso basta uma palavra forte em comum.

    Devolve quantos locais foram casados.
    """
    casados = 0

    for cnpj, razao_social in estabelecimentos.items():
        palavras_cupom = _palavras_significativas(razao_social)
        if not palavras_cupom:
            continue

        for local in locais:
            if local.cnpj:
                continue
            if palavras_cupom & _palavras_significativas(local.nome):
                local.cnpj = cnpj
                local.fonte = "osm+cupom"
                casados += 1
                break

    return casados


# --------------------------------------------------------------------------
# Cache em disco
# --------------------------------------------------------------------------

# Mercados de Goiânia versionados junto com o código.
#
# São dados de referência, não dados de usuário: mudam raramente e podem ser
# regerados com "cli.py mapear". Ficam no repositório porque a hospedagem
# gratuita apaga o disco a cada reinício — sem isso, o mapa nasceria vazio em
# produção e o app pediria a cidade a cada visita.
DADOS_EMBARCADOS = Path(__file__).resolve().parent.parent / "dados_iniciais" / "mercados_goiania.json"


class CacheMapa:
    """Guarda os mercados já encontrados, para não repetir consultas."""

    def __init__(self, caminho: str | Path = "dados/mapa.json") -> None:
        self.caminho = Path(caminho)
        self.area = ""
        self.centro: tuple[float, float] | None = None
        self.locais: list[Local] = []
        self.carregar()

    def carregar(self) -> None:
        # Uma importação local mais recente tem prioridade sobre o embarcado.
        origem = self.caminho if self.caminho.exists() else DADOS_EMBARCADOS
        if not origem.exists():
            return
        self._ler(origem)

    def _ler(self, origem: Path) -> None:
        self.caminho_lido = origem
        try:
            dados = json.loads(origem.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        self.area = dados.get("area", "")
        centro = dados.get("centro")
        self.centro = tuple(centro) if centro else None
        self.locais = [Local(**registro) for registro in dados.get("locais", [])]

    def salvar(self) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        conteudo = {
            "area": self.area,
            "centro": list(self.centro) if self.centro else None,
            "locais": [asdict(local) for local in self.locais],
            "fonte": "OpenStreetMap (ODbL)",
        }
        self.caminho.write_text(
            json.dumps(conteudo, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def __len__(self) -> int:
        return len(self.locais)

    def por_tipo(self) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for local in self.locais:
            contagem[local.tipo] = contagem.get(local.tipo, 0) + 1
        return dict(sorted(contagem.items(), key=lambda kv: -kv[1]))


def localizar_estabelecimentos(repositorio, cidade: str = "", uf: str = "") -> int:
    """
    Geocodifica os mercados que têm preços na base mas ainda não têm coordenadas.

    É o complemento necessário ao Overpass: o OpenStreetMap não conhece todos
    os supermercados brasileiros. No cupom de teste, o estabelecimento não
    existia no mapa — mas o endereço impresso na nota permite localizá-lo.

    Devolve quantos foram localizados. Respeita o limite de uma requisição por
    segundo do Nominatim.
    """
    from .cnpj import ConsultaCNPJError, consultar as consultar_cnpj

    pendentes = repositorio.estabelecimentos_sem_local()
    localizados = 0

    for estabelecimento in pendentes:
        achado = None

        # 1) Registro oficial do CNPJ: traz o CEP, que o cupom não tem.
        try:
            registro = consultar_cnpj(estabelecimento.cnpj)
        except ConsultaCNPJError:
            registro = None

        if registro:
            estabelecimento.cep = registro.cep
            estabelecimento.bairro = registro.bairro
            if registro.nome_fantasia:
                estabelecimento.nome = registro.nome_fantasia
            estabelecimento.endereco = registro.endereco_completo
            time.sleep(INTERVALO_ENTRE_CHAMADAS)

            achado = geocodificar_por_cep(
                registro.cep, registro.logradouro, registro.numero, registro.municipio
            )
            time.sleep(INTERVALO_ENTRE_CHAMADAS)

        # 2) Sem CNPJ ou sem CEP, cai no endereço impresso na nota.
        if achado is None:
            achado = geocodificar(estabelecimento.endereco, cidade, uf)
            time.sleep(INTERVALO_ENTRE_CHAMADAS)

        if achado is None:
            continue

        estabelecimento.latitude, estabelecimento.longitude, estabelecimento.precisao = achado
        repositorio.atualizar_estabelecimento(estabelecimento)
        localizados += 1

    if localizados:
        repositorio.salvar()
    return localizados


def locais_dos_estabelecimentos(repositorio) -> list[Local]:
    """Converte os mercados já localizados da base em pontos do mapa."""
    return [
        Local(
            nome=e.nome,
            latitude=e.latitude,
            longitude=e.longitude,
            tipo="supermarket",
            endereco=e.endereco,
            cnpj=e.cnpj,
            fonte="cupom",
            precisao=e.precisao,
        )
        for e in repositorio.estabelecimentos_localizados()
    ]


# Dois pontos mais próximos que isto são tratados como a MESMA loja. Cobre o
# caso em que o casamento por nome falha (o cupom diz "CARREFOUR COMERCIO E
# INDUSTRIA LTDA" e o OSM diz "Carrefour Flamboyant"), mas os dois pontos caem
# no mesmo prédio. Supermercados diferentes raramente ficam a menos de 120 m um
# do outro, então o valor é seguro. Ajuste aqui para afrouxar ou apertar.
RAIO_MESMA_LOJA_METROS = 120


def _distancia_metros(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância aproximada entre dois pontos (fórmula de Haversine)."""
    from math import asin, cos, radians, sin, sqrt

    raio = 6_371_000  # raio médio da Terra, em metros
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * raio * asin(sqrt(a))


def mesclar(locais_osm: list[Local], locais_base: list[Local]) -> list[Local]:
    """
    Junta os mercados do OpenStreetMap com os que vieram dos cupons.

    Um mercado da base que já casou com um ponto do OSM (por nome) não é
    duplicado. Além disso, quando um ponto COM preços (verde) coincide no espaço
    com um ponto SEM preços (laranja) do OSM, o laranja é descartado: é a mesma
    loja vista por duas fontes, e mostrar os dois polui o mapa.
    """
    cnpjs_no_mapa = {local.cnpj for local in locais_osm if local.cnpj}
    novos = [local for local in locais_base if local.cnpj not in cnpjs_no_mapa]
    combinado = locais_osm + novos

    verdes = [local for local in combinado if local.cnpj]

    resultado: list[Local] = []
    for local in combinado:
        if local.cnpj:
            resultado.append(local)
            continue
        # Laranja (sem preços): só entra se não estiver colado a um verde.
        coincide = any(
            _distancia_metros(local.latitude, local.longitude, v.latitude, v.longitude)
            <= RAIO_MESMA_LOJA_METROS
            for v in verdes
        )
        if not coincide:
            resultado.append(local)

    return resultado


def importar_area(area: str, caminho_cache: str | Path = "dados/mapa.json",
                  estabelecimentos: dict[str, str] | None = None) -> CacheMapa:
    """
    Baixa os mercados de uma região e grava no cache.

    `area` é o nome como aparece no OpenStreetMap, por exemplo "Goiânia, GO"
    ou "Setor Bueno, Goiânia".
    """
    caixa = caixa_delimitadora(area)
    time.sleep(INTERVALO_ENTRE_CHAMADAS)  # respeita o limite das APIs públicas

    locais = buscar_mercados(caixa)

    if estabelecimentos:
        casar_com_estabelecimentos(locais, estabelecimentos)

    cache = CacheMapa(caminho_cache)
    cache.area = area
    cache.centro = ((caixa[0] + caixa[2]) / 2, (caixa[1] + caixa[3]) / 2)
    cache.locais = locais
    cache.salvar()
    return cache
