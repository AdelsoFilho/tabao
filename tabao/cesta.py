"""
Cálculo do custo da cesta básica por estabelecimento e do ranking de economia.

Este é o módulo que responde à pergunta central do produto: onde a minha
compra inteira sai mais barata? Ele não compara produtos isolados; soma o
conjunto de itens em cada mercado, que é o que o consumidor de fato paga.
"""

from dataclasses import dataclass, field
from datetime import datetime

from .estatistica import DadosInsuficientesError, mediana, resumir, e_atipico
from .modelos import PrecoObservado
from .produtos import CESTA_BASICA, nome_do_item


@dataclass
class CustoEstabelecimento:
    """Custo da cesta em um estabelecimento e a cobertura desse cálculo."""

    cnpj: str
    nome: str
    custo_total: float
    itens_encontrados: int
    itens_faltando: list[str] = field(default_factory=list)
    detalhe: dict[str, float] = field(default_factory=dict)
    observado_ate: datetime | None = None

    @property
    def cobertura(self) -> float:
        """Proporção dos itens da cesta com preço conhecido neste mercado."""
        return round(self.itens_encontrados / len(CESTA_BASICA), 4)

    @property
    def completo(self) -> bool:
        return self.itens_encontrados == len(CESTA_BASICA)

    @property
    def dias_desde_atualizacao(self) -> int | None:
        if self.observado_ate is None:
            return None
        return (datetime.now() - self.observado_ate).days


@dataclass
class Ranking:
    """Resultado da comparação entre estabelecimentos."""

    estabelecimentos: list[CustoEstabelecimento]
    itens_considerados: list[str]

    @property
    def mais_barato(self) -> CustoEstabelecimento | None:
        return self.estabelecimentos[0] if self.estabelecimentos else None

    @property
    def mais_caro(self) -> CustoEstabelecimento | None:
        return self.estabelecimentos[-1] if self.estabelecimentos else None

    @property
    def economia_possivel(self) -> float:
        """
        Diferença em reais entre o mercado mais caro e o mais barato.

        É o equivalente, na base do TáBão, ao número que o Procon divulga:
        quanto se deixa de gastar escolhendo onde comprar.
        """
        if len(self.estabelecimentos) < 2:
            return 0.0
        return round(self.mais_caro.custo_total - self.mais_barato.custo_total, 2)

    @property
    def economia_percentual(self) -> float:
        if not self.mais_caro or self.mais_caro.custo_total == 0:
            return 0.0
        return round(100 * self.economia_possivel / self.mais_caro.custo_total, 2)


def _preco_vigente_por_estabelecimento(
    precos: list[PrecoObservado],
    descartar_atipicos: bool = True,
) -> dict[str, dict[str, tuple[float, datetime]]]:
    """
    Reduz o histórico ao preço vigente de cada item em cada mercado.

    Mantém a observação mais recente. Antes disso, descarta preços atípicos
    comparando cada valor com os demais preços do MESMO item na cidade — é
    assim que um erro grosseiro deixa de contaminar o ranking.
    """
    # A base guarda todos os produtos do cupom; o indicador oficial da cesta
    # considera apenas os que foram classificados em um item canônico.
    por_item: dict[str, list[PrecoObservado]] = {}
    for p in precos:
        if not p.da_cesta:
            continue
        por_item.setdefault(p.item_cesta, []).append(p)

    vigentes: dict[str, dict[str, tuple[float, datetime]]] = {}

    for item, observacoes in por_item.items():
        valores = [o.preco for o in observacoes]

        if descartar_atipicos and len(valores) >= 3:
            observacoes = [o for o in observacoes if not e_atipico(o.preco, valores)]

        for o in observacoes:
            celula = vigentes.setdefault(o.cnpj, {})
            anterior = celula.get(item)
            if anterior is None or o.observado_em > anterior[1]:
                celula[item] = (o.preco, o.observado_em)

    return vigentes


def custo_por_estabelecimento(
    precos: list[PrecoObservado],
    itens: list[str] | None = None,
    descartar_atipicos: bool = True,
) -> list[CustoEstabelecimento]:
    """
    Calcula quanto custa a cesta em cada estabelecimento da base.

    O custo usa a quantidade mensal de referência de cada item (Decreto-Lei
    nº 399/1938), de modo que o total é comparável ao valor publicado pelo
    DIEESE, e não uma soma arbitrária de preços unitários.
    """
    itens = itens or list(CESTA_BASICA.keys())
    nomes = {p.cnpj: p.nome_estabelecimento for p in precos if p.da_cesta}
    vigentes = _preco_vigente_por_estabelecimento(precos, descartar_atipicos)

    resultado: list[CustoEstabelecimento] = []

    for cnpj, precos_item in vigentes.items():
        total = 0.0
        detalhe: dict[str, float] = {}
        faltando: list[str] = []
        mais_recente: datetime | None = None

        for item in itens:
            if item not in precos_item:
                faltando.append(item)
                continue

            preco, quando = precos_item[item]
            quantidade_referencia = CESTA_BASICA[item][1]
            custo_item = round(preco * quantidade_referencia, 2)

            total += custo_item
            detalhe[item] = custo_item
            if mais_recente is None or quando > mais_recente:
                mais_recente = quando

        resultado.append(
            CustoEstabelecimento(
                cnpj=cnpj,
                nome=nomes.get(cnpj, cnpj),
                custo_total=round(total, 2),
                itens_encontrados=len(detalhe),
                itens_faltando=faltando,
                detalhe=detalhe,
                observado_ate=mais_recente,
            )
        )

    return resultado


def montar_ranking(
    precos: list[PrecoObservado],
    cobertura_minima: float = 0.5,
    descartar_atipicos: bool = True,
) -> Ranking:
    """
    Ordena os estabelecimentos do mais barato ao mais caro.

    Só entram no ranking os mercados com cobertura suficiente: comparar um
    mercado que tem 2 dos 13 itens com outro que tem 13 produziria um "mais
    barato" falso. A cobertura mínima padrão é de metade da cesta.
    """
    custos = custo_por_estabelecimento(precos, descartar_atipicos=descartar_atipicos)
    elegiveis = [c for c in custos if c.cobertura >= cobertura_minima]
    elegiveis.sort(key=lambda c: c.custo_total)

    itens_considerados = sorted({i for c in elegiveis for i in c.detalhe})
    return Ranking(estabelecimentos=elegiveis, itens_considerados=itens_considerados)


def estatisticas_do_item(precos: list[PrecoObservado], item: str) -> dict:
    """
    Resumo estatístico dos preços de um item na base inteira.

    Serve tanto ao usuário ("está caro ou barato?") quanto à análise do artigo
    científico, medindo a dispersão de preços na cidade.
    """
    observacoes = [p for p in precos if p.item_cesta == item]
    if not observacoes:
        return {"item": item, "nome": nome_do_item(item), "n": 0}

    valores = [o.preco for o in observacoes]

    try:
        resumo = resumir(valores)
    except DadosInsuficientesError:
        return {"item": item, "nome": nome_do_item(item), "n": 0}

    mais_barato = min(observacoes, key=lambda o: o.preco)
    mais_caro = max(observacoes, key=lambda o: o.preco)
    mais_recente = max(observacoes, key=lambda o: o.observado_em)

    return {
        "item": item,
        "nome": nome_do_item(item),
        "n": resumo.n,
        "media": resumo.media,
        "mediana": resumo.mediana,
        "minimo": resumo.minimo,
        "maximo": resumo.maximo,
        "desvio_padrao": resumo.desvio_padrao,
        "coeficiente_variacao": resumo.coeficiente_variacao,
        "amplitude": resumo.amplitude,
        "variacao_percentual": (
            round(100 * (resumo.maximo - resumo.minimo) / resumo.minimo, 2)
            if resumo.minimo > 0 else 0.0
        ),
        "onde_mais_barato": mais_barato.nome_estabelecimento,
        "onde_mais_caro": mais_caro.nome_estabelecimento,
        # Preço da coleta mais recente: é o que o consumidor pagaria hoje.
        "preco_recente": mais_recente.preco,
        "onde_recente": mais_recente.nome_estabelecimento,
        "quando_recente": mais_recente.observado_em,
    }


def formatar_ranking(ranking: Ranking) -> str:
    """Renderiza o ranking como texto para o terminal."""
    if not ranking.estabelecimentos:
        return (
            "Ainda não há estabelecimentos com cobertura suficiente da cesta.\n"
            "Envie mais cupons para que a comparação fique confiável."
        )

    linhas = [
        f"{'#':<3}{'Estabelecimento':<34}{'Cesta (R$)':>12}{'Itens':>8}{'Idade':>8}",
        "-" * 65,
    ]

    for posicao, e in enumerate(ranking.estabelecimentos, start=1):
        idade = e.dias_desde_atualizacao
        idade_txt = f"{idade}d" if idade is not None else "—"
        linhas.append(
            f"{posicao:<3}{e.nome[:33]:<34}{e.custo_total:>12.2f}"
            f"{e.itens_encontrados:>5}/{len(CESTA_BASICA):<3}{idade_txt:>7}"
        )

    if ranking.economia_possivel > 0:
        linhas.append("-" * 65)
        linhas.append(
            f"Economia possível: R$ {ranking.economia_possivel:.2f} "
            f"({ranking.economia_percentual:.1f}%) entre o mais caro e o mais barato."
        )

    return "\n".join(linhas)
