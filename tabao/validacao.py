"""
Checagem de sanidade do cupom antes de ele entrar na base colaborativa.

O token assinado garante que ninguém alterou os preços entre a conferência e
a gravação, mas não garante que a SEFAZ (ou o nosso extrator) devolveu algo
coerente. Um preço zerado ou uma quantidade de 50 mil unidades contaminaria
as médias da cesta para todo mundo; melhor recusar o cupom inteiro e avisar.
"""

from datetime import datetime, timedelta

from .modelos import Cupom

QUANTIDADE_MAXIMA = 1000
PRECO_UNITARIO_MAXIMO = 50_000.0
# Arredondamento e descontos por item fazem quantidade × unitário divergir um
# pouco do total da linha; acima disto já não é arredondamento.
TOLERANCIA_LINHA = 0.10
TOLERANCIA_TOTAL = 0.05


class CupomInvalidoError(ValueError):
    """O cupom tem dados incoerentes e não deve ser gravado."""


def problemas_do_cupom(cupom: Cupom, agora: datetime | None = None) -> list[str]:
    """Lista os problemas encontrados. Lista vazia: cupom aceitável."""
    problemas = []

    if not cupom.itens:
        problemas.append("o cupom não tem itens")

    if cupom.emitido_em.tzinfo is not None:
        agora = agora or datetime.now(cupom.emitido_em.tzinfo)
    else:
        agora = agora or datetime.now()
    if cupom.emitido_em > agora + timedelta(days=1):
        problemas.append("a data de emissão está no futuro")

    for item in cupom.itens:
        nome = item.descricao or "item sem descrição"
        if item.quantidade <= 0 or item.quantidade > QUANTIDADE_MAXIMA:
            problemas.append(f"quantidade fora do esperado em “{nome}”")
            continue
        if item.valor_total <= 0 or item.valor_unitario <= 0:
            problemas.append(f"preço zerado ou negativo em “{nome}”")
            continue
        if item.valor_unitario > PRECO_UNITARIO_MAXIMO:
            problemas.append(f"preço fora do esperado em “{nome}”")
            continue
        esperado = item.quantidade * item.valor_unitario
        if abs(esperado - item.valor_total) > max(0.05, esperado * TOLERANCIA_LINHA):
            problemas.append(f"quantidade × preço não bate com o total em “{nome}”")

    if cupom.valor_total and abs(cupom.valor_total - cupom.total_calculado) > TOLERANCIA_TOTAL:
        problemas.append("a soma dos itens não bate com o total do cupom")

    return problemas


def validar_cupom(cupom: Cupom, agora: datetime | None = None) -> None:
    """Levanta CupomInvalidoError com todos os problemas, se houver."""
    problemas = problemas_do_cupom(cupom, agora)
    if problemas:
        raise CupomInvalidoError("; ".join(problemas))
