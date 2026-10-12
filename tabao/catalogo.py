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
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Iterable, Optional

from .categorias import categorizar
from .modelos import PrecoObservado
from .padronizacao import padronizar
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
    chave: str                     # identifica o produto (ver _chaves)
    descricao: str                 # nome padronizado, ou o do cupom
    categoria: str
    item_cesta: Optional[str]
    ofertas: list[Oferta] = field(default_factory=list)   # da mais barata à mais cara
    no_cupom: list[str] = field(default_factory=list)     # como cada mercado escreve

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
    """Chave de um produto isolado (sem olhar os outros); ver _chaves."""
    padrao = padronizar(descricao)
    if padrao.familia:
        return f"{padrao.familia}|{padrao.tamanho or '?'}"
    return normalizar(descricao)


def _comparavel(data: datetime) -> datetime:
    # JSON guarda datas sem fuso e o Postgres com fuso: compara pelo relógio.
    return data.replace(tzinfo=None)


def _chaves(precos: list[PrecoObservado]) -> list[str]:
    """
    A chave de produto de cada observação, olhando o conjunto.

    1. Produto identificado (tipo + marca + variante, ver padronizacao): a
       chave é essa família mais o tamanho.
    2. Tamanho que o cupom cortou ("PAO FORMA SEVEN BOYS", sem os 450 g):
       herda o tamanho quando a família só aparece com UM tamanho conhecido.
       Com dois ou mais (Coca 1 L e 2 L) não dá para saber, e fica à parte.
    3. Dentro do mesmo mercado, o código interno do produto manda: se o
       mercado mudou a descrição, o código continua o mesmo.
    """
    padroes = [padronizar(p.descricao_original) for p in precos]

    tamanhos: dict[str, set[str]] = defaultdict(set)
    for padrao in padroes:
        if padrao.familia and padrao.tamanho:
            tamanhos[padrao.familia].add(padrao.tamanho)

    chaves = []
    for p, padrao in zip(precos, padroes):
        if padrao.familia:
            tamanho = padrao.tamanho
            if tamanho is None and len(tamanhos[padrao.familia]) == 1:
                tamanho = next(iter(tamanhos[padrao.familia]))
            # Só junta preços na mesma unidade: com o peso na descrição o preço
            # é gravado por kg/l ("un. padrão"); sem ele, por embalagem. Juntar
            # os dois compararia R$ 1,49 a bala com R$ 50,55 o quilo da bala.
            unidade = "padrao" if p.unidade == "un. padrão" else (p.unidade or "").upper()
            chaves.append(f"{padrao.familia}|{unidade}|{tamanho or '?'}")
        else:
            chaves.append(normalizar(p.descricao_original))

    por_codigo: dict[tuple[str, str], str] = {}
    for i, p in enumerate(precos):
        if p.codigo:
            chaves[i] = por_codigo.setdefault((p.cnpj, p.codigo), chaves[i])
    return chaves


def _nome_do_grupo(original: str, chave: str) -> str:
    """Nome padronizado com o tamanho do grupo (que pode ter sido herdado)."""
    padrao = padronizar(original)
    if not padrao.familia:
        # Não junta com outros mercados, mas o nome legível já ajuda
        # ("Linguiça 800 g" em vez de "LING CHURR FGO SUP FGO 800G").
        return padrao.nome
    tamanho = chave.rsplit("|", 1)[-1]
    if tamanho != "?" and tamanho != padrao.tamanho:
        padrao = replace(padrao, tamanho=tamanho)
    return padrao.nome


def agrupar_produtos(precos: Iterable[PrecoObservado],
                     nomes: Optional[dict[str, str]] = None) -> list[Produto]:
    """
    Agrupa as observações em produtos, cada um com uma oferta por mercado.

    `nomes` traduz CNPJ em nome de exibição (o do mapa, quando há). Sai
    ordenado por nome.
    """
    nomes = nomes or {}
    precos = list(precos)
    por_produto: dict[str, list[PrecoObservado]] = defaultdict(list)
    for p, chave in zip(precos, _chaves(precos)):
        por_produto[chave].append(p)

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

        originais = [d for d, _ in Counter(o.descricao_original for o in observacoes).most_common()]
        produtos.append(Produto(
            chave=chave, descricao=_nome_do_grupo(originais[0], chave),
            categoria=categorizar(originais[0]), item_cesta=observacoes[0].item_cesta,
            ofertas=ofertas, no_cupom=originais,
        ))

    produtos.sort(key=lambda p: p.descricao)
    return produtos


def produto_por_chave(precos: Iterable[PrecoObservado], chave: str,
                      nomes: Optional[dict[str, str]] = None) -> Optional[Produto]:
    # Agrupa tudo (o tamanho herdado depende do conjunto) e escolhe o produto.
    return next((p for p in agrupar_produtos(precos, nomes) if p.chave == chave), None)
