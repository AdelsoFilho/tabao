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

# Palavras que identificam cada item. Inclui as abreviações que o cupom fiscal
# realmente grava — "MANT" para manteiga, "FEIJ" para feijão, "ARR" para arroz —
# porque sem elas o produto certo fica invisível na base.
PALAVRAS_CHAVE = {
    "arroz": {"ARROZ", "ARR"},
    "feijao": {"FEIJAO", "FEIJ"},
    "carne": {"CARNE", "ACEM", "PATINHO", "COXAO", "MUSCULO", "ALCATRA", "PEITO"},
    "leite": {"LEITE"},
    "pao": {"PAO"},
    "cafe": {"CAFE"},
    "acucar": {"ACUCAR", "ACUC"},
    "oleo": {"OLEO"},
    "banana": {"BANANA"},
    "tomate": {"TOMATE"},
    "batata": {"BATATA"},
    "farinha": {"FARINHA"},
    "manteiga": {"MANTEIGA", "MANT"},
}

# Abreviações curtas e ambíguas que só valem quando são a palavra INTEIRA. Sem
# isso, "MANT" casaria dentro de "MANTA" (bacon manta) e "ARR" dentro de nomes
# de marca. As demais chaves casam como prefixo ("ACUCAR" acha "ACUCAR CRISTAL").
CHAVE_SO_PALAVRA_INTEIRA = {"ARR", "FEIJ", "ACUC", "MANT", "PEITO"}

# A palavra-chave precisa estar entre as primeiras palavras da descrição. É o
# que distingue o produto do sabor: em "PIPOCA ... MANTEIGA CINE", a manteiga é
# só o sabor — o produto é pipoca, e por isso não entra na cesta.
JANELA_CABECA = 3

# Palavras que, se presentes, impedem a classificação — evitam que
# "PAO DE ALHO" vire pão francês ou que "OLEO ESSENCIAL" vire óleo de cozinha.
# Só são conferidas depois que a palavra-chave já casou, então cada lista só
# afeta o seu próprio item.
EXCLUSOES = {
    # "MAC"/"ESPAGUETE": "MAC ARROZ ESPAGUETE" é macarrão, não arroz.
    "arroz": {"DOCE", "SABONETE", "MAC", "MACARRAO", "ESPAGUETE"},
    "feijao": {"TROPEIRO", "ENLATADO", "CONSERVA"},
    # "FGO" e "FRG" sao abreviacoes de frango usadas nos cupons reais; a cesta
    # do Decreto-Lei 399/1938 considera carne bovina.
    "carne": {"SECA", "SOJA", "FRANGO", "FGO", "FRG", "AVE", "SUINA", "PORCO",
              "PEIXE", "LINGUICA", "SALSICHA", "HAMBURGUER", "EMPANAD"},
    "leite": {"COCO", "CONDENSADO", "PO", "FERMENTADO", "MAGNESIA"},
    # Pão de alho, de queijo e de forma são pães, mas não o pão francês da cesta.
    # "QJO"/"ALH" são as abreviações que aparecem no cupom.
    "pao": {"DOCE", "QUEIJO", "QJO", "QIJO", "FORMA", "FOR", "MEL", "RALADO",
            "BISNAGUINHA", "ALHO", "ALH", "SUPREME"},
    # "CAPP" (cappuccino), "DOLCE GUSTO" (cápsula) e "COM LEITE" não são café em
    # pó; entram como categoria bebidas, mas não no indicador da cesta.
    "cafe": {"CAPSULA", "CAPPUCCINO", "CAPP", "SOLUVEL", "FILTRO", "CAFETEIRA",
             "DOLCE", "GUSTO", "3EM1", "MOCHA", "COM LEITE"},
    "acucar": {"ADOCANTE", "CONFEITEIRO"},
    # "ESSEN": óleo essencial (aromaterapia), não óleo de cozinha.
    "oleo": {"MOTOR", "CORPORAL", "SPRAY", "DIESEL", "ESSENCIAL", "ESSEN"},
    "banana": {"DOCE", "CHIPS", "PASSA"},
    "tomate": {"MOLHO", "EXTRATO", "SECO", "PELADO", "SACHE", "POLPA", "KETCHUP"},
    # "CONG" (congelada), "PALITO" e "SMILE" identificam batata processada, que
    # custa cerca do dobro da in natura e distorceria o custo da cesta.
    # A abreviacao "CONG" foi encontrada em cupom real: "BATATA CONG UAI ... 2kg".
    "batata": {"PALHA", "FRITA", "CHIPS", "DOCE", "PURE", "CONGELADA", "CONG",
               "PALITO", "SMILE", "NOISETTE"},
    "farinha": {"MANDIOCA", "ROSCA", "MILHO", "AVEIA", "LACTEA"},
    # "MANTA" é corte de bacon; a chave "MANT" já é palavra inteira, mas a
    # exclusão reforça que "BACON MANTA" nunca conte como manteiga.
    "manteiga": {"CACAU", "GARRAFA", "AMENDOIM", "MANTA"},
}

# Unidades reconhecidas nas descrições, para extrair o peso/volume da embalagem.
#
# O limite de palavra (\b) antes do número é essencial: sem ele, o "3" de "OF3"
# (código de oferta em "TOMATE SALADET OF3 KG") era lido como 3 kg, e o preço
# do tomate saía dividido por três.
_PADRAO_EMBALAGEM = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(KG|G|GR|GRAMAS?|L|LT|LITROS?|ML)\b"
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


def _chave_casa(palavra: str, chave: str) -> bool:
    """
    Diz se uma palavra da descrição corresponde a uma palavra-chave.

    Abreviações ambíguas ("MANT", "ARR") só valem como palavra inteira; as
    demais valem como prefixo, para que "ACUCAR" alcance "ACUCARADO" e "PAO"
    alcance "PAOZINHO". Como o casamento é palavra a palavra, "CAFE" nunca
    alcança "NESCAFE" e "MANTEIGA" nunca alcança o meio de outra palavra.
    """
    if chave in CHAVE_SO_PALAVRA_INTEIRA:
        return palavra == chave
    return palavra == chave or palavra.startswith(chave)


def _tem_exclusao(palavras: list[str], texto: str, exclusoes: set[str]) -> bool:
    """Verifica as exclusões: palavras inteiras, ou expressões com espaço."""
    conjunto = set(palavras)
    for e in exclusoes:
        if " " in e:
            if e in texto:
                return True
        elif e in conjunto:
            return True
    return False


def classificar(descricao: str) -> Classificacao:
    """
    Decide a qual item da cesta básica uma descrição pertence.

    A decisão tem três partes, e cada uma corrige um erro real observado na
    base em produção:

    1. A palavra-chave casa palavra a palavra, com limite de palavra, e não como
       pedaço de texto. Sem isso, "NESCAFE" virava café e "MANTEIGA" dentro de
       outra descrição virava manteiga.
    2. A palavra-chave precisa estar entre as primeiras palavras (a cabeça da
       descrição). É o que distingue o produto do seu sabor: em "PIPOCA ...
       MANTEIGA CINE" a cabeça é pipoca, então não é manteiga. Quando duas
       chaves aparecem na cabeça, vence a mais à esquerda: "PAO LEITE" é pão.
    3. Só então as exclusões entram, para separar "PAO DE ALHO" do pão francês.

    Devolve item None quando o produto não faz parte da cesta, o que é o caso
    da maioria das linhas de um cupom real.
    """
    texto = normalizar(descricao)
    if not texto:
        return Classificacao(None, None, texto)

    palavras = texto.split()
    cabeca = palavras[:JANELA_CABECA]

    melhor_posicao = len(cabeca)
    melhor_item: str | None = None

    for item, chaves in PALAVRAS_CHAVE.items():
        for posicao, palavra in enumerate(cabeca):
            if posicao >= melhor_posicao:
                break
            if any(_chave_casa(palavra, c) for c in chaves):
                melhor_posicao, melhor_item = posicao, item
                break

    if melhor_item is None:
        return Classificacao(None, None, texto)

    if _tem_exclusao(palavras, texto, EXCLUSOES.get(melhor_item, set())):
        return Classificacao(None, None, texto)

    return Classificacao(melhor_item, CESTA_BASICA[melhor_item][0], texto)


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
