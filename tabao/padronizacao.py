"""
Padronização das descrições de cupom.

Cada mercado escreve o mesmo produto do seu jeito, e o cupom corta a descrição
em poucos caracteres:

    ENERG MONS 473ml UN                       (Bretas)
    BEB ENERG MONSTER LT 473ML Z/ACUC GREEN   (Costa Atacadão)
    P FORMA S BOYS 450G                       (Distribuidora)
    PAO FORMA SEVEN BOYS                      (Carrefour)

A página pública da SEFAZ não traz o código de barras (só o código interno de
cada mercado), então a única ponte entre mercados é a descrição. Aqui ela é
decomposta em:

    tipo      o que o produto é ("Detergente", "Pão de forma")
    marca     quando reconhecida ("Limpol", "Seven Boys")
    variante  o que muda o produto (sabor, "zero", "com gás", "semidesnatado")
    tamanho   500 ml, 1 kg, 30 un. ou "por kg"

Dois produtos só são considerados o mesmo quando tipo, marca e variante batem
e os tamanhos não se contradizem. Na dúvida, ficam separados: juntar errado
(Coca 1 L com Coca 2 L) é pior do que deixar de juntar.

O tipo também decide a CATEGORIA, olhando a palavra que diz o que o produto é
(em geral a primeira). Isso corrige a classe de erro em que qualquer palavra
da descrição decidia: "ÁGUA SANITÁRIA" virava bebida por causa de "ÁGUA",
"DETERGENTE LIMÃO" virava hortifrúti, "PICOLÉ PINTA LÍNGUA" virava linguiça.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from .produtos import normalizar

# --------------------------------------------------------------------------
# 1. Abreviações de cupom
# --------------------------------------------------------------------------
# Trocadas antes de tudo, palavra por palavra (ou expressão). Expressões mais
# longas primeiro. Só entram abreviações sem ambiguidade relevante; as que
# dependem de posição ficam em _ABREV_INICIO.

_ABREVIACOES = {
    # expressões
    "LEIT COND": "LEITE CONDENSADO", "L V": "LONGA VIDA", "LA DE ACO": "LA DE ACO",
    "CRE MILHO": "CREME DE MILHO", "CR HID": "CREME HIDRATANTE",
    "S BOYS": "SEVEN BOYS", "SEVEN BO": "SEVEN BOYS", "D BENTA": "DONA BENTA",
    "EN EX POW": "ENERGETICO EXTRA POWER", "EX POW": "EXTRA POWER",
    "PETI SUI": "PETIT SUISSE", "PETIT": "PETIT SUISSE", "REFRES EM PO": "REFRESCO EM PO",
    "AP BARBA": "APARELHO DE BARBEAR", "PROT DIARIO": "PROTETOR DIARIO",
    "PAPEL HIG": "PAPEL HIGIENICO", "PAP ALUM": "PAPEL ALUMINIO",
    "TOALHA UMED": "TOALHA UMEDECIDA", "TOALHA PAPEL": "PAPEL TOALHA",
    "AGUA SANIT": "AGUA SANITARIA", "AGUA M MIN": "AGUA MINERAL", "AGUA MIN": "AGUA MINERAL",
    "OL SJ": "OLEO DE SOJA", "OLEO SOJA": "OLEO DE SOJA", "OLEO ESSEN": "OLEO ESSENCIAL",
    "COCA COLA": "COCA COLA", "Z ACUC": "ZERO ACUCAR", "S SAL": "SEM SAL",
    "C SAL": "COM SAL", "C GAS": "COM GAS", "S GAS": "SEM GAS",
    "COXA C SOBRECOXA": "COXA E SOBRECOXA", "COXA S COXA": "COXA E SOBRECOXA",
    "PAO FOR": "PAO DE FORMA", "PAO FORMA": "PAO DE FORMA", "PAO DE QJO": "PAO DE QUEIJO",
    "PAO QUEIJO": "PAO DE QUEIJO", "PAO ALH": "PAO DE ALHO", "PAO ALHO": "PAO DE ALHO",
    "PAO LEITE": "PAO DE LEITE", "PAO QJO": "PAO DE QUEIJO", "DONA DE": "DONA DE CASA",
    "CARIOCA DONA": "CARIOCA DONA DE CASA", "OLEO SJ": "OLEO DE SOJA",
    "QUEIJO MUSS": "QUEIJO MUSSARELA", "SAND BAG SALAME": "SALAME FATIADO",
    # palavras
    "ACUC": "ACUCAR", "AMAC": "AMACIANTE", "ARR": "ARROZ", "BISC": "BISCOITO",
    "BOMB": "BOMBOM", "CAPS": "CAPSULA", "CERV": "CERVEJA", "CERE": "CEREJA",
    "CD": "CREME DENTAL", "COLG": "COLGATE", "CONDIM": "CONDIMENTO", "CREM": "CREMOSO",
    "DESINF": "DESINFETANTE", "DESOD": "DESODORANTE", "DET": "DETERGENTE",
    "DETERG": "DETERGENTE", "ENERG": "ENERGETICO", "FEIJ": "FEIJAO", "FERM": "FERMENTO",
    "FGO": "FRANGO", "FRG": "FRANGO", "GELAT": "GELATINA", "GLIC": "GLICERINA",
    "HEINEK": "HEINEKEN", "EISENB": "EISENBAHN", "EISENBA": "EISENBAHN",
    "HOEGARDEN": "HOEGAARDEN", "INTEGRA": "INTEGRAL", "IOG": "IOGURTE",
    "ITALA": "ITALAC", "LIM": "LIMAO", "LIMP": "LIMPADOR", "LING": "LINGUICA",
    "LOC": "LOCAO", "LV": "LONGA VIDA", "MAC": "MACARRAO", "MONS": "MONSTER",
    "MORT": "MORTADELA", "MUSS": "MUSSARELA", "PERD": "PERDIGAO", "PIR": "PIRACANJUBA",
    "POLP": "POLPA", "QJO": "QUEIJO", "REF": "REFRIGERANTE", "REFRIG": "REFRIGERANTE",
    "REFRES": "REFRESCO", "RESF": "RESFRIADO", "SBT": "SABONETE", "SH": "SHAMPOO",
    "SANIT": "SANITARIA", "TIROL": "TIROLEZ", "TR": "TRADICIONAL", "TRAD": "TRADICIONAL",
    "CONG": "CONGELADO", "COC": "COCO", "NAT": "NATURAL", "LIQ": "LIQUIDO", "CL": "CLEAR", "CAJ": "CAJU", "GOI": "GOIABA", "GUA": "GUARANA", "BCO": "BRANCO", "SEMI": "SEMIDESNATADO",
    "CRF": "CARREFOUR", "CRFO": "CARREFOUR", "CARREF": "CARREFOUR", "CARREFOU": "CARREFOUR",
    "MIRTILO": "MIRTILO", "BERINGELA": "BERINJELA", "PICOLE": "PICOLE",
}

# Só valem como PRIMEIRA palavra: no meio da descrição significam outra coisa
# ("SAB" é sabonete no início e "sabor" no fim; "P" é pão; "COND" é
# condicionador, mas "LEIT COND" já foi tratado acima).
_ABREV_INICIO = {
    "SAB": "SABONETE", "P": "PAO", "COND": "CONDICIONADOR", "AZ": "AZEITE",
    "MANT": "MANTEIGA", "SU": "SUCO", "MIN": "MINI", "EN": "ENERGETICO",
    "BEB": "BEBIDA", "B": "BEBIDA", "OL": "OLEO", "FLV": "",
}

# Primeiras palavras que só dizem "isto é uma bebida" ou são sigla interna do
# caixa: saem da frente para o tipo de verdade aparecer (BEB GATORADE ->
# GATORADE). "BEBIDA" ainda serve de pista de categoria se nada mais casar.
_PREFIXOS = {"BEBIDA", "QA"}

# Palavras que não ajudam a identificar o produto: embalagem, unidade de
# venda, siglas do sistema de caixa.
_RUIDO = {
    "UN", "UND", "UNID", "PET", "PC", "PCT", "PT", "FR", "TP", "CX", "LT", "LATA", "PQ",
    "VD", "BDJ", "PVC", "SC", "PED", "PEDACO", "PD", "ASSADO", "OF3", "QA", "U", "ORI",
    "ESSE", "VACUO", "IQF", "BEBIDA", "UHT", "LONGA", "VIDA", "DE",
    "DA", "DO", "COM", "C", "E", "S", "EM", "ESPECIAL", "NACIONAL", "IMPORTADA",
    "IMPORTADO", "MON", "FD", "MASC", "PERF", "LAV", "TEX", "GL",
}

# --------------------------------------------------------------------------
# 2. Tipos de produto (o que o produto É) e suas categorias
# --------------------------------------------------------------------------
# Procurados no INÍCIO da descrição já expandida; expressões mais longas
# vencem. A categoria sai daqui, e não de qualquer palavra solta.

_TIPOS: dict[str, tuple[str, str]] = {
    # limpeza
    "AGUA SANITARIA": ("Água sanitária", "limpeza"), "DETERGENTE": ("Detergente", "limpeza"),
    "SABAO": ("Sabão", "limpeza"), "AMACIANTE": ("Amaciante", "limpeza"),
    "DESINFETANTE": ("Desinfetante", "limpeza"), "LA DE ACO": ("Lã de aço", "limpeza"),
    "LIMPADOR": ("Limpador", "limpeza"), "FLANELA": ("Flanela", "limpeza"),
    "PANO": ("Pano de limpeza", "limpeza"), "OMO": ("Sabão", "limpeza"),
    "ESPONJA": ("Esponja", "limpeza"), "ALVEJANTE": ("Alvejante", "limpeza"),
    # higiene e beleza
    "SABONETE": ("Sabonete", "higiene"), "SHAMPOO": ("Shampoo", "higiene"),
    "CONDICIONADOR": ("Condicionador", "higiene"), "CREME DENTAL": ("Creme dental", "higiene"),
    "COLGATE": ("Creme dental", "higiene"), "DESODORANTE": ("Desodorante", "higiene"),
    "LOCAO": ("Loção hidratante", "higiene"), "CREME HIDRATANTE": ("Creme hidratante", "higiene"),
    "PAPEL HIGIENICO": ("Papel higiênico", "higiene"), "PROTETOR DIARIO": ("Protetor diário", "higiene"),
    "APARELHO DE BARBEAR": ("Aparelho de barbear", "higiene"),
    "TOALHA UMEDECIDA": ("Toalha umedecida", "higiene"), "KIT BANHO": ("Kit banho", "higiene"),
    "COLORACAO": ("Coloração", "higiene"), "ABSORVENTE": ("Absorvente", "higiene"),
    "FRALDA": ("Fralda", "higiene"),
    # utilidades e casa
    "PAPEL TOALHA": ("Papel toalha", "casa"), "PAPEL ALUMINIO": ("Papel alumínio", "casa"),
    "FILME PVC": ("Filme PVC", "casa"), "SACOLA": ("Sacola", "casa"), "SACO": ("Saco", "casa"),
    "GARRAFA": ("Garrafa", "casa"), "INSET": ("Inseticida", "casa"),
    "CARVAO": ("Carvão", "casa"), "FOSFORO": ("Fósforo", "casa"),
    "OLEO ESSENCIAL": ("Óleo essencial", "casa"), "KIT CONDOR": ("Kit escolar", "casa"),
    # roupas
    "CALCA": ("Calça", "vestuario"), "CAMISETA": ("Camiseta", "vestuario"),
    "REGATA": ("Regata", "vestuario"), "KIT C 2 CALCINHA": ("Calcinha", "vestuario"),
    "KIT DUO CALCINHA": ("Calcinha", "vestuario"), "CALCINHA": ("Calcinha", "vestuario"),
    # bebidas
    "AGUA MINERAL": ("Água mineral", "bebidas"), "AGUA": ("Água mineral", "bebidas"),
    "REFRIGERANTE": ("Refrigerante", "bebidas"), "COCA COLA": ("Refrigerante", "bebidas"),
    "CERVEJA": ("Cerveja", "bebidas"), "COLORADO": ("Cerveja", "bebidas"),
    "ENERGETICO": ("Energético", "bebidas"), "SUCO": ("Suco", "bebidas"),
    "GATORADE": ("Isotônico", "bebidas"), "REFRESCO": ("Refresco em pó", "bebidas"),
    "CHAMPAGNE": ("Espumante", "bebidas"), "VINHO": ("Vinho", "bebidas"),
    "CHA": ("Chá", "bebidas"), "CAFE": ("Café", "bebidas"), "CAPSULA": ("Café em cápsula", "bebidas"),
    "NESCAFE": ("Café em cápsula", "bebidas"), "ACHOCOLATADO": ("Achocolatado", "bebidas"),
    # laticínios e frios
    "LEITE CONDENSADO": ("Leite condensado", "laticinios"), "CREME LEITE": ("Creme de leite", "laticinios"),
    "LEITE": ("Leite", "laticinios"), "IOGURTE": ("Iogurte", "laticinios"),
    "QUEIJO MUSSARELA": ("Queijo muçarela", "laticinios"), "QUEIJO": ("Queijo", "laticinios"),
    "SALAME FATIADO": ("Salame fatiado", "laticinios"), "REQUEIJAO": ("Requeijão", "laticinios"),
    "MANTEIGA": ("Manteiga", "laticinios"), "MARGARINA": ("Margarina", "laticinios"),
    "PETIT SUISSE": ("Petit suisse", "laticinios"), "BATAVO": ("Bebida láctea", "laticinios"),
    "MORTADELA": ("Mortadela", "laticinios"), "PRESUNTO": ("Presunto", "laticinios"),
    "MINI SAND": ("Peito de peru fatiado", "laticinios"),
    # padaria
    "PAO DE FORMA": ("Pão de forma", "padaria"), "PAO DE QUEIJO": ("Pão de queijo", "padaria"),
    "PAO DE ALHO": ("Pão de alho", "padaria"), "PAO DE LEITE": ("Pão de leite", "padaria"),
    "PAO FRANCES": ("Pão francês", "padaria"), "PAO": ("Pão", "padaria"),
    "BISNAGUINHA": ("Bisnaguinha", "padaria"), "BISNAGUITO": ("Bisnaguinha", "padaria"),
    "BISCOITO": ("Biscoito", "padaria"), "EMPADAO": ("Empadão", "padaria"),
    "CAFE COM LEITE GRAO": ("Pão café com leite", "padaria"),
    # sorvetes e congelados
    "PICOLE": ("Picolé", "congelados"), "SORVETE": ("Sorvete", "congelados"),
    "ACAI": ("Açaí", "congelados"), "POLPA": ("Polpa de fruta", "congelados"),
    # carnes
    "COXA E SOBRECOXA": ("Coxa e sobrecoxa", "carnes"), "COXINHA DA ASA": ("Coxinha da asa", "carnes"),
    "COXINHA DE FRANGO": ("Coxinha da asa", "carnes"), "MEIO ASA": ("Meio da asa", "carnes"),
    "LINGUICA": ("Linguiça", "carnes"), "BACON": ("Bacon", "carnes"),
    "LOMBO SUINO": ("Lombo suíno", "carnes"), "FILE DE COSTELA": ("Costela", "carnes"),
    "CUPIM": ("Cupim", "carnes"), "FRANGO": ("Frango", "carnes"), "TIRAS DE TILAPIA": ("Tilápia", "carnes"),
    # hortifrúti
    "CEBOLA": ("Cebola", "hortifruti"), "TOMATE": ("Tomate", "hortifruti"),
    "ALHO": ("Alho", "hortifruti"), "BATATA INGLESA": ("Batata", "hortifruti"),
    "BANANA": ("Banana", "hortifruti"), "LARANJA": ("Laranja", "hortifruti"),
    "MELAO": ("Melão", "hortifruti"), "UVA": ("Uva", "hortifruti"),
    "PIMENTAO": ("Pimentão", "hortifruti"), "BERINJELA": ("Berinjela", "hortifruti"),
    "MIRTILO": ("Mirtilo", "hortifruti"), "AMEIXA": ("Ameixa", "hortifruti"),
    "OVO": ("Ovos", "hortifruti"),
    # congelados que não são carne
    "BATATA CONGELADO": ("Batata congelada", "congelados"),
    "MANDIOCA CONGELADO": ("Mandioca congelada", "congelados"),
    "MORANGO CONGELADO": ("Morango congelado", "congelados"),
    # mercearia
    "ARROZ": ("Arroz", "mercearia"), "FEIJAO": ("Feijão", "mercearia"),
    "MACARRAO": ("Macarrão", "mercearia"), "MASSA": ("Macarrão", "mercearia"),
    "ACUCAR": ("Açúcar", "mercearia"), "SAL": ("Sal", "mercearia"),
    "OLEO DE SOJA": ("Óleo de soja", "mercearia"), "AZEITE": ("Azeite", "mercearia"),
    "AZEITONA": ("Azeitona", "mercearia"), "VINAGRE": ("Vinagre", "mercearia"),
    "KETCHUP": ("Ketchup", "mercearia"), "MAIONESE": ("Maionese", "mercearia"),
    "MOSTARDA": ("Mostarda", "mercearia"), "TEMPERO": ("Tempero", "mercearia"),
    "SAZON": ("Tempero", "mercearia"), "CONDIMENTO": ("Condimento", "mercearia"),
    "PAPRIC": ("Páprica", "mercearia"), "CRAVO": ("Cravo", "mercearia"),
    "FERMENTO": ("Fermento", "mercearia"), "GELATINA": ("Gelatina", "mercearia"),
    "CREME DE MILHO": ("Creme de milho", "mercearia"), "MILHO PIPOCA": ("Milho de pipoca", "mercearia"),
    "PIPOCA": ("Pipoca de micro-ondas", "mercearia"), "FLOCAO": ("Flocão de milho", "mercearia"),
    "TAPIOCA": ("Tapioca", "mercearia"), "BALA": ("Bala", "mercearia"),
    "BOMBOM": ("Bombom", "mercearia"), "PACOCA": ("Paçoca", "mercearia"),
    "M GLACE": ("Mistura para glacê", "mercearia"),
}

# Tipos vendidos sem marca relevante (o preço varia pela loja, não pela
# marca): juntam entre mercados mesmo sem marca reconhecida.
_GENERICOS = {
    "Água mineral", "Cebola", "Tomate", "Alho", "Batata", "Banana", "Laranja",
    "Melão", "Pimentão", "Pão francês", "Lombo suíno", "Ovos", "Coxa e sobrecoxa",
    "Berinjela", "Uva",
}

# --------------------------------------------------------------------------
# 3. Marcas e variantes
# --------------------------------------------------------------------------

_MARCAS = {
    "ITALAC", "PIRACANJUBA", "TIROLEZ", "SEVEN BOYS", "MONSTER", "EXTRA POWER",
    "COCA COLA", "GATORADE", "YPE", "LIMPOL", "OESTE", "PHEBO", "HALLS", "SAZON",
    "NESCAFE", "DOLCE GUSTO", "NESTLE", "CARREFOUR", "DONA DE CASA", "HEINEKEN",
    "EISENBAHN", "STELLA", "TIGER", "HOEGAARDEN", "COLORADO", "AURORA", "SADIA",
    "PERDIGAO", "QUALITTI", "COPACOL", "VIDALAC", "DOVE", "NIVEA", "COLGATE", "OMO",
    "VEJA", "ROYAL", "DONA BENTA", "KICALDO", "TIO JORGE", "BORGES", "SOYA", "BATAVO",
    "MARILAN", "NEGRESCO", "PULLMAN", "ZUPP", "ITAMBE", "JOHNSONS", "CAREFREE", "RAID",
    "HELLMANNS", "HEMMER", "CEPERA", "IMPERADOR", "EMEGE", "CASTELO", "POMAR", "DICEL",
    "TRIGOST", "FORNEART", "TIROL", "SALTON", "LEAO", "TEEKAN", "MARIZA", "DO BEM",
    "BRILHANTE", "COPALIMPA", "LUMIPAM", "FIAT LUX", "SNOB", "SINHA", "CUISI",
    "UAI BATATA", "SO MANDIOCA", "SANTA C", "MIDI", "MID", "BRETAS", "YAN",
}

# Mudam o produto: entram na comparação. As que são o "padrão" do tipo
# (integral, tradicional, sem gás...) são descartadas, para "LEITE ITALAC" e
# "LEITE ITALAC INTEGRAL" serem o mesmo.
_VARIANTES = {
    "ZERO", "ZERO ACUCAR", "DIET", "LIGHT", "COM GAS", "SEMIDESNATADO", "DESNATADO",
    "SEM SAL", "LIMAO", "UVA", "MARACUJA", "MORANGO", "COCO", "CEREJA", "GOIABA",
    "CAJU", "MANGA", "MACA", "LARANJA", "CHOCOLATE", "BAUNILHA", "NEUTRO", "CLEAR",
    "ANTIB", "GREEN", "CAPPUCCINO", "ESPRESSO", "EXPRESSO", "MOCHA", "INTENSO",
    "VERMELHO", "VERMELHA", "VERDE", "AMARELA", "AMARELO", "CARIOCA", "PRETO",
    "PARBOILIZADO", "INTEGRAL", "GRANDE", "EXTRA", "GROSSO", "REFINADO", "CRISTAL",
    "NATURAL", "GREGO", "LIQUIDO", "SALADET", "SALADETE", "ANDREA", "ITALIANO", "CAQUI",
}
_VARIANTES_PADRAO = {"INTEGRAL", "TRADICIONAL", "ORIGINAL", "SEM GAS", "BRANCO", "BRANCA",
                     "CRISTAL", "CARIOCA", "CREMOSO", "COMUM", "DURA"}
# "INTEGRAL" é padrão no leite, mas variante no pão e no arroz.
_INTEGRAL_E_VARIANTE = {"Pão de forma", "Pão", "Arroz", "Macarrão", "Biscoito", "Iogurte"}

_TAMANHO = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(ML|M|LTS|LT|L|GRS|GR|G|KG|K)\b"
)
_UNIDADES = re.compile(r"\bC\s*(\d+)\s*(?:UN|UNID|SC|F)?\b")

_ACENTOS = {
    "LIMAO": "Limão", "MARACUJA": "Maracujá", "COCO": "Coco", "CEREJA": "Cereja",
    "COM GAS": "com gás", "ZERO ACUCAR": "zero açúcar", "SEMIDESNATADO": "semidesnatado",
    "DESNATADO": "desnatado", "SEM SAL": "sem sal", "MACA": "Maçã", "PARBOILIZADO": "parboilizado",
    "INTEGRAL": "integral", "GRANDE": "grande", "EXTRA": "extra",
    "NESTLE": "Nestlé", "YPE": "Ypê", "ITAMBE": "Itambé", "LEAO": "Leão", "SINHA": "Sinhá",
    "PERDIGAO": "Perdigão", "SAO": "São",
}


@dataclass
class Padrao:
    """A descrição de cupom decomposta."""

    tipo: Optional[str]
    categoria: Optional[str]
    marca: Optional[str] = None
    variantes: tuple[str, ...] = ()
    tamanho: Optional[str] = None          # "500 ml", "1 kg", "30 un.", "por kg"
    expandida: str = ""
    original: str = field(default="", repr=False)

    @property
    def generico(self) -> bool:
        return self.tipo in _GENERICOS

    @property
    def identificavel(self) -> bool:
        """Dá para casar com outro mercado? (tipo e marca, ou tipo genérico)"""
        return bool(self.tipo) and (bool(self.marca) or self.generico)

    @property
    def familia(self) -> Optional[str]:
        """Tipo + marca + variantes: o produto, sem o tamanho."""
        if not self.identificavel:
            return None
        partes = [self.tipo, self.marca if not self.generico else "", *self.variantes]
        return "|".join(normalizar(p) for p in partes if p is not None)

    @property
    def nome(self) -> str:
        """Nome padronizado para exibir: "Energético Monster 473 ml"."""
        if not self.tipo:
            return self.original
        partes = [self.tipo]
        partes += [_ACENTOS.get(v, v.capitalize()) for v in self.variantes]
        if self.marca and not self.generico:
            partes.append(_ACENTOS.get(self.marca, _titulo(self.marca)))
        if self.tamanho:
            partes.append(self.tamanho)
        return " ".join(partes)


def _titulo(texto: str) -> str:
    """"DONA DE CASA" -> "Dona de Casa"."""
    return " ".join(p.lower() if p in ("DE", "DA", "DO", "E") else p.capitalize()
                    for p in texto.split())


def _expandir(texto: str) -> str:
    palavras = texto.split()
    if palavras and palavras[0] in _ABREV_INICIO:
        palavras[0] = _ABREV_INICIO[palavras[0]]
    texto = " " + " ".join(p for p in palavras if p) + " "
    # Duas passadas: "PAO QJO" só vira "PAO DE QUEIJO" depois de "QJO" virar
    # "QUEIJO"... e vice-versa. A segunda passada pega o que a primeira abriu.
    for _ in range(2):
        for abrev in sorted(_ABREVIACOES, key=len, reverse=True):
            texto = re.sub(rf"(?<= ){re.escape(abrev)}(?= )", _ABREVIACOES[abrev], texto)
    return re.sub(r"\s+", " ", texto).strip()


def _formatar_tamanho(valor: str, unidade: str) -> Optional[str]:
    numero = float(valor.replace(",", "."))
    if unidade == "M" and numero < 100:
        return None          # "30M" no papel higiênico são metros
    if unidade in ("ML", "M"):
        return f"{numero:g} ml"
    if unidade in ("L", "LT", "LTS"):
        return f"{numero:g} l".replace(".", ",")
    if unidade in ("G", "GR", "GRS"):
        return f"{numero:g} g".replace(".", ",")
    if unidade in ("KG", "K"):
        return f"{numero:g} kg".replace(".", ",")
    return None


def _tamanho(texto: str) -> Optional[str]:
    for achado in _TAMANHO.finditer(texto):
        tamanho = _formatar_tamanho(*achado.groups())
        if tamanho:
            return tamanho
    medidas = re.search(r"\b(\d+)\s*X\s*(\d+)\s*CM\b", texto)      # flanela 30X50CM
    if medidas:
        return f"{medidas.group(1)}x{medidas.group(2)} cm"
    unidades = _UNIDADES.search(texto)
    if unidades:
        return f"{unidades.group(1)} un."
    if re.search(r"\bKG\b|\bK$", texto):
        return "por kg"
    return None


@lru_cache(maxsize=8192)
def padronizar(descricao: str) -> Padrao:
    """Decompõe uma descrição de cupom (ver o docstring do módulo)."""
    base = normalizar(descricao).replace("/", " ")
    base = re.sub(r"\bC\s*/\s*", "C ", base)
    expandida = _expandir(base)
    tamanho = _tamanho(expandida)

    palavras = expandida.split()
    pista = None
    while palavras and palavras[0] in _PREFIXOS:
        pista = "bebidas" if palavras[0] == "BEBIDA" else pista
        palavras.pop(0)
    inicio = " ".join(palavras)

    tipo = categoria = None
    for chave in sorted(_TIPOS, key=len, reverse=True):
        if inicio == chave or inicio.startswith(chave + " "):
            tipo, categoria = _TIPOS[chave]
            break
    if tipo is None and pista:
        categoria = pista

    marca = next(
        (m for m in sorted(_MARCAS, key=len, reverse=True)
         if re.search(rf"(?<![A-Z]){re.escape(m)}(?![A-Z])", expandida)),
        None,
    )
    if marca and tipo and normalizar(tipo) == marca:
        marca = None

    variantes = []
    resto = f" {expandida} "
    for v in sorted(_VARIANTES | _VARIANTES_PADRAO, key=len, reverse=True):
        if f" {v} " in resto:
            resto = resto.replace(f" {v} ", " ")
            padrao_do_tipo = v in _VARIANTES_PADRAO or (
                v == "INTEGRAL" and tipo not in _INTEGRAL_E_VARIANTE)
            if not padrao_do_tipo:
                variantes.append(v)
    # A fruta que dá nome ao tipo não é variante dele (uva no "Uva verde"), e
    # a palavra que faz parte da marca também não ("EXTRA" em "EXTRA POWER").
    if tipo:
        variantes = [v for v in variantes if v != normalizar(tipo)]
    if marca:
        variantes = [v for v in variantes if v not in marca.split()]

    return Padrao(tipo=tipo, categoria=categoria, marca=marca,
                  variantes=tuple(sorted(variantes)), tamanho=tamanho,
                  expandida=expandida, original=descricao)
