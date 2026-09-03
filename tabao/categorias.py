"""
Categorização de todos os produtos do cupom, não apenas os da cesta básica.

A cesta básica é o indicador oficial e continua sendo o centro do produto, mas
um cupom real tem muito mais coisa: no cupom de teste, apenas 2 das 17 linhas
pertenciam à cesta. Jogar as outras 15 fora seria desperdiçar dado já coletado.

Com a categorização, o TáBão passa a responder também "onde o bacon está mais
barato?", e o histórico de preços da cidade fica muito mais rico.
"""

import re

from .produtos import normalizar

# A ORDEM IMPORTA: a primeira categoria que casar vence.
#   - carnes antes de congelados, porque frango congelado ainda é carne;
#   - congelados antes de hortifrúti, porque batata pré-frita não é hortifrúti.
CATEGORIAS: dict[str, tuple[str, set[str]]] = {
    "carnes": ("Carnes e aves", {
        "CARNE", "ACEM", "PATINHO", "COXAO", "MUSCULO", "ALCATRA", "PICANHA",
        "CONTRA FILE", "COSTELA", "FRALDINHA", "MAMINHA", "FGO", "FRG",
        "FRANGO", "ASA", "COXA", "SOBRECOXA", "PEITO", "LINGUICA", "LING",
        "SALSICHA", "BACON", "PERNIL", "LOMBO", "SUINA", "PORCO", "PEIXE",
        "TILAPIA", "SALMAO", "CAMARAO", "MOELA", "FIGADO", "CHURR",
    }),
    "laticinios": ("Laticínios e frios", {
        "LEITE", "QUEIJO", "MUSS", "MUSSARELA", "PRATO", "REQUEIJAO",
        "IOGURTE", "MANTEIGA", "MARGARINA", "NATA", "PRESUNTO", "MORTADELA",
        "SALAME", "CATUPIRY", "COALHO", "RICOTA",
    }),
    "padaria": ("Padaria e confeitaria", {
        "PAO", "BISNAGUINHA", "BOLO", "TORTA", "ROSCA", "BROA", "CROISSANT",
        "SONHO", "BISCOITO", "BOLACHA", "TORRADA",
    }),
    "bebidas": ("Bebidas", {
        "REFRIGERANTE", "REFRI", "COCA", "GUARANA", "SUCO", "AGUA", "CERVEJA",
        "VINHO", "ENERGETICO", "ISOTONICO", "CHA", "CAFE", "ACHOCOLATADO",
        "NECTAR", "WHISKY", "VODKA", "CACHACA", "GIN",
    }),
    "congelados": ("Congelados", {
        "CONG", "CONGELAD", "NUGGET", "HAMBURGUER", "PIZZA", "LASANHA",
        "SORVETE", "ACAI", "EMPANAD", "POLPA",
    }),
    "hortifruti": ("Hortifrúti", {
        "TOMATE", "BATATA", "CEBOLA", "ALHO", "CENOURA", "ALFACE", "COUVE",
        "BANANA", "MACA", "LARANJA", "LIMAO", "MAMAO", "MELANCIA", "ABACAXI",
        "MANGA", "UVA", "PERA", "ABOBRINHA", "PIMENTAO", "BETERRABA", "CHUCHU",
        "REPOLHO", "MANDIOCA", "INHAME", "QUIABO", "BERINJELA", "PEPINO",
        "GOIABA", "MARACUJA", "MELAO", "MORANGO", "SALSA", "CHEIRO VERDE",
    }),
    "mercearia": ("Mercearia", {
        "ARROZ", "FEIJAO", "MACARRAO", "MASSA", "FARINHA", "FUBA", "AMIDO",
        "ACUCAR", "SAL", "OLEO", "AZEITE", "AZEITONA", "VINAGRE", "MOLHO",
        "EXTRATO", "KETCHUP", "MOSTARDA", "MAIONESE", "TEMPERO", "COLORAU",
        "OREGANO", "MILHO", "ERVILHA", "SARDINHA", "ATUM", "GELATINA",
        "CEREAL", "AVEIA", "GRANOLA", "AMENDOIM", "CASTANHA",
    }),
    "limpeza": ("Limpeza", {
        "DETERGENTE", "SABAO", "AMACIANTE", "DESINFETANTE", "SANITARIA",
        "CLORO", "ALVEJANTE", "ESPONJA", "VASSOURA", "RODO", "LUSTRA",
        "MULTIUSO", "LIMPADOR",
    }),
    "higiene": ("Higiene e beleza", {
        "SABONETE", "SHAMPOO", "XAMPU", "CONDICIONADOR", "CREME DENTAL",
        "ESCOVA DENTAL", "DESODORANTE", "PAPEL HIGIENICO", "ABSORVENTE",
        "FRALDA", "HIDRATANTE", "BARBEAR", "COTONETE",
    }),
    "casa": ("Utilidades e casa", {
        "CARVAO", "FOSFORO", "ISQUEIRO", "PILHA", "LAMPADA", "GUARDANAPO",
        "ALUMINIO", "FILME PVC", "DESCARTAVEL", "PALITO", "VELA",
    }),
}

OUTROS = "outros"
NOME_OUTROS = "Outros"

# Cache dos padrões compilados, montado sob demanda.
_PADROES: dict[str, re.Pattern] = {}


def _padrao_da_categoria(chave: str) -> re.Pattern:
    r"""
    Compila as palavras-chave de uma categoria em um único padrão.

    O casamento exige limite de palavra à esquerda (\b). Sem isso, "PERA"
    casaria dentro de "IMPERADOR" e de "CEPERA" — erro real observado ao
    processar o cupom de teste, que classificava azeitona e mostarda como
    hortifrúti. O fim fica livre de propósito, para que "CONG" alcance
    "CONGELADA" e "MUSS" alcance "MUSSARELA".
    """
    if chave not in _PADROES:
        palavras = CATEGORIAS[chave][1]
        alternativas = "|".join(
            re.escape(p) for p in sorted(palavras, key=len, reverse=True)
        )
        _PADROES[chave] = re.compile(r"\b(?:" + alternativas + r")")
    return _PADROES[chave]


def categorizar(descricao: str) -> str:
    """
    Devolve a chave da categoria de um produto.

    Sempre devolve alguma categoria: o que não casa com nenhuma regra cai em
    "outros", de modo que nenhum item do cupom é descartado.
    """
    texto = normalizar(descricao)
    if not texto:
        return OUTROS

    for chave in CATEGORIAS:
        if _padrao_da_categoria(chave).search(texto):
            return chave

    return OUTROS


def nome_da_categoria(chave: str) -> str:
    """Nome de exibição de uma categoria."""
    if chave in CATEGORIAS:
        return CATEGORIAS[chave][0]
    return NOME_OUTROS


def todas_as_categorias() -> list[str]:
    """Lista as chaves de categoria, incluindo 'outros'."""
    return list(CATEGORIAS.keys()) + [OUTROS]
