"""
Catálogo de produtos: transforma a lista bruta de observações em algo que dá
para navegar.

A base guarda uma linha por produto por cupom. Mostrar isso direto repete o
mesmo produto a cada compra ("COXA S COXA FGO CONG" quatro vezes, mesmo
mercado). Aqui as observações viram produtos, e cada produto tem uma oferta
por mercado: o preço mais recente que alguém pagou lá.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from .modelos import PrecoObservado
from .produtos import normalizar

# Pedaços da razão social que não ajudam a reconhecer o mercado: o código
# entre parênteses, o tipo societário e o "comércio e indústria" de cartório.
_RUIDO_RAZAO = re.compile(
    r"^\(\d+\)\s*|"
    r"\b(ltda|ltd|s\s*/\s*a|s\.\s*a\.?|epp|eireli)(?=\W|$)\.?|"
    r"\b(me|sa)$|"                             # "ME"/"SA" só no fim: "MEGA ME" é nome
    r"\bcom(ercio)?\.?\s+e\s+ind(ustria)?\b\.?",
    re.IGNORECASE,
)
_PALAVRAS_MINUSCULAS = {"de", "da", "do", "das", "dos", "e"}


def nome_amigavel(razao_social: str) -> str:
    """
    "(38)CEMA CENTRAL MINEIRA ATACADISTA LTDA" -> "Cema Central Mineira Atacadista".

    Usado quando o mapa não tem um nome melhor para o mercado. Tira o código
    entre parênteses, o tipo societário e o "comércio e indústria", e acerta
    as maiúsculas.
    """
    texto = _RUIDO_RAZAO.sub(" ", razao_social or "")
    texto = re.sub(r"\s+", " ", texto).strip(" -.,")
    if not texto:
        return (razao_social or "").strip()
    palavras = texto.lower().split()
    return " ".join(
        p if (i and p in _PALAVRAS_MINUSCULAS) else p.capitalize()
        for i, p in enumerate(palavras)
    )


@dataclass
class Oferta:
    """O preço de um produto em um mercado: a observação mais recente."""

    cnpj: str
    mercado: str
    preco: float
    unidade: str
    observado_em: datetime
    vezes: int = 1        # quantas vezes o preço foi visto neste mercado


@dataclass
class Produto:
    chave: str                     # descrição normalizada: identifica o produto
    descricao: str                 # como aparece no cupom
    categoria: str
    item_cesta: Optional[str]
    ofertas: list[Oferta] = field(default_factory=list)   # da mais barata à mais cara

    @property
    def menor(self) -> float:
        return self.ofertas[0].preco

    @property
    def maior(self) -> float:
        return self.ofertas[-1].preco

    @property
    def mercados(self) -> int:
        return len(self.ofertas)

    @property
    def mais_recente(self) -> datetime:
        return max(o.observado_em for o in self.ofertas)


def chave_do_produto(descricao: str) -> str:
    return normalizar(descricao)


def _comparavel(data: datetime) -> datetime:
    # JSON guarda datas sem fuso e o Postgres com fuso: compara pelo relógio.
    return data.replace(tzinfo=None)


def agrupar_produtos(precos: Iterable[PrecoObservado],
                     nomes: Optional[dict[str, str]] = None) -> list[Produto]:
    """
    Agrupa as observações em produtos, cada um com uma oferta por mercado.

    `nomes` traduz CNPJ em nome de exibição (o do mapa, quando há). Sai
    ordenado por descrição.
    """
    nomes = nomes or {}
    por_produto: dict[str, list[PrecoObservado]] = defaultdict(list)
    for p in precos:
        por_produto[chave_do_produto(p.descricao_original)].append(p)

    produtos = []
    for chave, observacoes in por_produto.items():
        por_mercado: dict[str, list[PrecoObservado]] = defaultdict(list)
        for o in observacoes:
            por_mercado[o.cnpj].append(o)

        ofertas = []
        for cnpj, lista in por_mercado.items():
            recente = max(lista, key=lambda o: _comparavel(o.observado_em))
            ofertas.append(Oferta(
                cnpj=cnpj,
                mercado=nomes.get(cnpj) or nome_amigavel(recente.nome_estabelecimento),
                preco=recente.preco, unidade=recente.unidade,
                observado_em=recente.observado_em, vezes=len(lista),
            ))
        ofertas.sort(key=lambda o: o.preco)

        descricao = Counter(o.descricao_original for o in observacoes).most_common(1)[0][0]
        produtos.append(Produto(
            chave=chave, descricao=descricao,
            categoria=observacoes[0].categoria, item_cesta=observacoes[0].item_cesta,
            ofertas=ofertas,
        ))

    produtos.sort(key=lambda p: p.descricao)
    return produtos


def produto_por_chave(precos: Iterable[PrecoObservado], chave: str,
                      nomes: Optional[dict[str, str]] = None) -> Optional[Produto]:
    alvo = [p for p in precos if chave_do_produto(p.descricao_original) == chave]
    grupos = agrupar_produtos(alvo, nomes)
    return grupos[0] if grupos else None
