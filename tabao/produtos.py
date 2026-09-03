"""
Normalização de descrições comerciais e classificação nos itens da cesta básica.

O supermercado escreve "ARROZ TP1 TIO JOAO 5KG"; a cesta básica fala apenas em
"arroz". Este módulo faz a ponte entre os dois, usando conjuntos de palavras-
chave e regras de exclusão — aplicação direta de operações sobre conjuntos
(Estrutura de Dados) e de lógica proposicional (Lógica Matemática).

A cesta adotada é a do Decreto-Lei nº 399/1938, acompanhada mensalmente pelo
DIEESE, composta por treze produtos na maior parte das unidades da federação.
"""

import re
import unicodedata
from dataclasses import dataclass

# Os treze itens canônicos e a quantidade mensal de referência por adulto,
# conforme o Decreto-Lei nº 399/1938.
CESTA_BASICA = {
    "arroz": ("Arroz", 3.6, "kg"),
    "feijao": ("Feijão", 4.5, "kg"),
    "carne": ("Carne bovina", 6.6, "kg"),
    "leite": ("Leite", 7.5, "L"),
    "pao": ("Pão francês", 6.0, "kg"),
    "cafe": ("Café em pó", 0.6, "kg"),
    "acucar": ("Açúcar", 3.0, "kg"),
    "oleo": ("Óleo", 0.9, "L"),
    "banana": ("Banana", 7.5, "kg"),
    "tomate": ("Tomate", 12.0, "kg"),
    "batata": ("Batata", 6.0, "kg"),
    "farinha": ("Farinha de trigo", 1.5, "kg"),
    "manteiga": ("Manteiga", 0.75, "kg"),
}

# Palavras que identificam cada item. Basta uma para classificar.
PALAVRAS_CHAVE = {
    "arroz": {"ARROZ"},
    "feijao": {"FEIJAO"},
    "carne": {"CARNE", "ACEM", "PATINHO", "COXAO", "MUSCULO", "ALCATRA", "PEITO"},
    "leite": {"LEITE"},
    "pao": {"PAO"},
    "cafe": {"CAFE"},
    "acucar": {"ACUCAR"},
    "oleo": {"OLEO"},
    "banana": {"BANANA"},
    "tomate": {"TOMATE"},
    "batata": {"BATATA"},
    "farinha": {"FARINHA"},
    "manteiga": {"MANTEIGA"},
}

# Palavras que, se presentes, impedem a classificação — evitam que
# "LEITE DE COCO" vire leite ou que "BATATA PALHA" vire batata.
EXCLUSOES = {
    "arroz": {"DOCE", "SABONETE"},
    "feijao": {"TROPEIRO", "ENLATADO", "CONSERVA"},
    # "FGO" e "FRG" sao abreviacoes de frango usadas nos cupons reais; a cesta
    # do Decreto-Lei 399/1938 considera carne bovina.
    "carne": {"SECA", "SOJA", "FRANGO", "FGO", "FRG", "AVE", "SUINA", "PORCO",
              "PEIXE", "LINGUICA", "SALSICHA", "HAMBURGUER", "EMPANAD"},
    "leite": {"COCO", "CONDENSADO", "PO", "FERMENTADO", "DE MAGNESIA"},
    "pao": {"DOCE", "QUEIJO", "FORMA", "MEL", "RALADO", "BISNAGUINHA"},
    "cafe": {"CAPSULA", "SOLUVEL", "FILTRO", "CAFETEIRA"},
    "acucar": {"ADOCANTE", "CONFEITEIRO"},
    "oleo": {"MOTOR", "CORPORAL", "COZINHA SPRAY", "DIESEL"},
    "banana": {"DOCE", "CHIPS", "PASSA"},
    "tomate": {"MOLHO", "EXTRATO", "SECO", "PELADO", "SACHE", "POLPA", "KETCHUP"},
    # "CONG" (congelada), "PALITO" e "SMILE" identificam batata processada, que
    # custa cerca do dobro da in natura e distorceria o custo da cesta.
    # A abreviacao "CONG" foi encontrada em cupom real: "BATATA CONG UAI ... 2kg".
    "batata": {"PALHA", "FRITA", "CHIPS", "DOCE", "PURE", "CONGELADA", "CONG",
               "PALITO", "SMILE", "NOISETTE", "PRE FRITA"},
    "farinha": {"MANDIOCA", "ROSCA", "MILHO", "AVEIA", "LACTEA"},
    "manteiga": {"CACAU", "GARRAFA", "AMENDOIM"},
}

# Unidades reconhecidas nas descrições, para extrair o peso/volume da embalagem.
_PADRAO_EMBALAGEM = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(KG|G|GR|GRAMAS?|L|LT|LITROS?|ML)\b"
)


@dataclass(frozen=True)
class Classificacao:
    """Resultado da tentativa de encaixar uma descrição na cesta básica."""

    item: str | None
    nome_exibicao: str | None
    descricao_normalizada: str

    @property
    def pertence_a_cesta(self) -> bool:
        return self.item is not None


def normalizar(descricao: str) -> str:
    """
    Deixa a descrição em uma forma comparável.

    Remove acentos, coloca em maiúsculas, troca pontuação por espaço e colapsa
    espaços repetidos. "Açúcar Cristal 1kg" vira "ACUCAR CRISTAL 1KG".
    """
    if not descricao:
        return ""

    texto = unicodedata.normalize("NFKD", descricao)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.upper()
    texto = re.sub(r"[^A-Z0-9,.]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def extrair_embalagem(descricao: str) -> tuple[float, str] | None:
    """
    Extrai quantidade e unidade da embalagem a partir da descrição.

    "ARROZ TIPO 1 5KG" devolve (5.0, "kg"); "LEITE INTEGRAL 1L" devolve
    (1.0, "L"). Devolve None quando não há indicação reconhecível.

    Isso permite comparar preço por quilo entre embalagens diferentes, em vez
    de comparar o preço do pacote.
    """
    texto = normalizar(descricao)
    achado = _PADRAO_EMBALAGEM.search(texto)
    if not achado:
        return None

    valor = float(achado.group(1).replace(",", "."))
    unidade = achado.group(2)

    if unidade in ("G", "GR", "GRAMA", "GRAMAS"):
        return (valor / 1000.0, "kg")
    if unidade == "KG":
        return (valor, "kg")
    if unidade == "ML":
        return (valor / 1000.0, "L")
    if unidade in ("L", "LT", "LITRO", "LITROS"):
        return (valor, "L")
    return None


def classificar(descricao: str) -> Classificacao:
    """
    Decide a qual item da cesta básica uma descrição pertence.

    A regra é uma proposição simples, avaliada para cada item:

        pertence(item) := existe_palavra_chave(item) E NAO existe_exclusao(item)

    Devolve item None quando o produto não faz parte da cesta, o que é o caso
    da maioria das linhas de um cupom real.
    """
    texto = normalizar(descricao)
    if not texto:
        return Classificacao(None, None, texto)

    palavras = set(texto.split())

    for item, chaves in PALAVRAS_CHAVE.items():
        # A palavra-chave pode aparecer isolada ou dentro do texto
        # (ex.: "ACUCAR" em "ACUCAR CRISTAL").
        tem_chave = bool(palavras & chaves) or any(c in texto for c in chaves)
        if not tem_chave:
            continue

        exclusoes = EXCLUSOES.get(item, set())
        if any(e in texto for e in exclusoes):
            continue

        return Classificacao(item, CESTA_BASICA[item][0], texto)

    return Classificacao(None, None, texto)


def preco_por_unidade_padrao(descricao: str, preco_pago: float,
                             quantidade: float = 1.0) -> float | None:
    """
    Converte o preço da embalagem para preço por quilo ou por litro.

    Sem isso, um pacote de arroz de 5 kg por R$ 25 pareceria mais caro que um
    de 1 kg por R$ 6, quando na verdade é mais barato por quilo.

    Devolve None quando a descrição não informa o tamanho da embalagem.
    """
    embalagem = extrair_embalagem(descricao)
    if not embalagem or quantidade <= 0:
        return None

    tamanho, _unidade = embalagem
    total_na_unidade = tamanho * quantidade
    if total_na_unidade <= 0:
        return None

    return round(preco_pago / total_na_unidade, 4)


def itens_da_cesta() -> list[str]:
    """Lista as chaves dos itens da cesta, em ordem estável."""
    return list(CESTA_BASICA.keys())


def nome_do_item(item: str) -> str:
    """Nome de exibição de um item da cesta."""
    return CESTA_BASICA[item][0] if item in CESTA_BASICA else item
