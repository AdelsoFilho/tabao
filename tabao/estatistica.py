"""
Estatística descritiva e detecção de preços atípicos.

As funções são implementadas à mão, sem numpy ou statistics, porque o objetivo
pedagógico do módulo é justamente exercitar as medidas de posição e dispersão.
Todas recebem uma lista de números e não dependem de nenhum outro módulo.
"""

from dataclasses import dataclass

# Constante que torna o desvio absoluto mediano comparável ao desvio padrão
# quando os dados seguem distribuição normal (1 / 0.6745).
FATOR_MAD = 1.4826

# Fator equivalente para o desvio absoluto MÉDIO, usado quando o MAD colapsa.
FATOR_DESVIO_MEDIO = 1.253314

# Abaixo deste percentual da mediana, o MAD é considerado degenerado e a
# dispersão passa a ser estimada pelo desvio absoluto médio.
PISO_RELATIVO_MAD = 0.01

# Acima deste escore, o preço é tratado como atípico e não entra nos cálculos.
LIMITE_ATIPICO = 3.5


class DadosInsuficientesError(ValueError):
    """Levantada quando não há observações suficientes para o cálculo."""


@dataclass(frozen=True)
class Resumo:
    """Resumo estatístico de um conjunto de preços."""

    n: int
    media: float
    mediana: float
    minimo: float
    maximo: float
    variancia: float
    desvio_padrao: float
    q1: float
    q3: float

    @property
    def amplitude(self) -> float:
        return round(self.maximo - self.minimo, 4)

    @property
    def amplitude_interquartil(self) -> float:
        return round(self.q3 - self.q1, 4)

    @property
    def coeficiente_variacao(self) -> float:
        """Dispersão relativa, em porcentagem. Permite comparar produtos."""
        if self.media == 0:
            return 0.0
        return round(100 * self.desvio_padrao / self.media, 2)


def media(valores: list[float]) -> float:
    """Média aritmética."""
    if not valores:
        raise DadosInsuficientesError("A média exige ao menos um valor.")
    return sum(valores) / len(valores)


def mediana(valores: list[float]) -> float:
    """
    Valor central da amostra ordenada.

    Preferida à média na comparação de preços porque não é distorcida por um
    único valor absurdo digitado ou promocional.
    """
    if not valores:
        raise DadosInsuficientesError("A mediana exige ao menos um valor.")

    ordenados = sorted(valores)
    n = len(ordenados)
    meio = n // 2

    if n % 2 == 1:
        return ordenados[meio]
    return (ordenados[meio - 1] + ordenados[meio]) / 2


def variancia(valores: list[float], amostral: bool = True) -> float:
    """
    Variância. Usa denominador n-1 (amostral) por padrão.

    Os preços coletados são uma amostra dos preços praticados na cidade, não a
    população inteira, por isso o padrão é o cálculo amostral.
    """
    n = len(valores)
    if n < 2:
        if n == 1 and not amostral:
            return 0.0
        raise DadosInsuficientesError("A variância amostral exige ao menos dois valores.")

    m = media(valores)
    soma_quadrados = sum((v - m) ** 2 for v in valores)
    return soma_quadrados / (n - 1 if amostral else n)


def desvio_padrao(valores: list[float], amostral: bool = True) -> float:
    """Raiz quadrada da variância."""
    return variancia(valores, amostral) ** 0.5


def percentil(valores: list[float], p: float) -> float:
    """
    Percentil por interpolação linear entre os valores vizinhos.

    p vai de 0 a 100. percentil(v, 50) coincide com a mediana.
    """
    if not valores:
        raise DadosInsuficientesError("O percentil exige ao menos um valor.")
    if not 0 <= p <= 100:
        raise ValueError("O percentil deve estar entre 0 e 100.")

    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return ordenados[0]

    posicao = (len(ordenados) - 1) * (p / 100)
    inferior = int(posicao)
    superior = min(inferior + 1, len(ordenados) - 1)
    fracao = posicao - inferior

    return ordenados[inferior] + fracao * (ordenados[superior] - ordenados[inferior])


def quartis(valores: list[float]) -> tuple[float, float, float]:
    """Devolve (Q1, Q2, Q3)."""
    return (percentil(valores, 25), percentil(valores, 50), percentil(valores, 75))


def desvio_absoluto_mediano(valores: list[float]) -> float:
    """
    MAD: mediana dos desvios absolutos em relação à mediana.

    É uma medida de dispersão robusta: diferente do desvio padrão, não é
    inflada por um preço absurdo, o que é exatamente o que precisamos para
    detectar o preço absurdo.
    """
    if not valores:
        raise DadosInsuficientesError("O MAD exige ao menos um valor.")

    centro = mediana(valores)
    return mediana([abs(v - centro) for v in valores])


def desvio_absoluto_medio(valores: list[float]) -> float:
    """Média dos desvios absolutos em relação à mediana."""
    if not valores:
        raise DadosInsuficientesError("O cálculo exige ao menos um valor.")
    centro = mediana(valores)
    return sum(abs(v - centro) for v in valores) / len(valores)


def escore_z_robusto(valor: float, valores: list[float]) -> float:
    """
    Quantos desvios robustos o valor está distante da mediana.

    Usa o MAD como medida de dispersão, porque ele não é inflado pelo próprio
    valor atípico que queremos encontrar.

    O MAD colapsa quando mais da metade das observações tem preço igual ou
    quase igual — situação comum aqui, já que o mesmo produto costuma custar o
    mesmo em várias notas. Nesse caso a dispersão fica perto de zero e
    QUALQUER outro preço, ainda que plausível, viraria atípico.

    Bug real observado: com tomate a 11,8889 / 11,89 / 11,8917 (diferenças de
    arredondamento na divisão por quilo), o MAD ficou em 0,001 e o tomate de
    R$ 8,90 do mercado mais barato foi descartado do ranking com z = -720.

    A correção é um piso relativo: se o MAD for menor que 1% da mediana, ele é
    tratado como degenerado e a dispersão passa a vir do desvio absoluto médio.
    """
    if len(valores) < 3:
        raise DadosInsuficientesError(
            "A detecção de atípicos exige ao menos três observações."
        )

    centro = mediana(valores)
    dispersao = FATOR_MAD * desvio_absoluto_mediano(valores)

    if dispersao < PISO_RELATIVO_MAD * abs(centro):
        dispersao = FATOR_DESVIO_MEDIO * desvio_absoluto_medio(valores)

    if dispersao == 0:
        # Todas as observações são exatamente iguais.
        return 0.0 if valor == centro else float("inf")

    return (valor - centro) / dispersao


def e_atipico(valor: float, valores: list[float],
              limite: float = LIMITE_ATIPICO) -> bool:
    """Indica se o valor deve ser tratado como fora do padrão."""
    try:
        return abs(escore_z_robusto(valor, valores)) > limite
    except DadosInsuficientesError:
        # Com poucos dados não há como afirmar que algo é atípico.
        return False


def remover_atipicos(valores: list[float],
                     limite: float = LIMITE_ATIPICO) -> list[float]:
    """Devolve a lista sem os valores considerados atípicos."""
    if len(valores) < 3:
        return list(valores)
    return [v for v in valores if not e_atipico(v, valores, limite)]


def resumir(valores: list[float]) -> Resumo:
    """Calcula todas as medidas descritivas de uma vez."""
    if not valores:
        raise DadosInsuficientesError("Não há valores para resumir.")

    q1, q2, q3 = quartis(valores)
    n = len(valores)

    return Resumo(
        n=n,
        media=round(media(valores), 4),
        mediana=round(q2, 4),
        minimo=round(min(valores), 4),
        maximo=round(max(valores), 4),
        variancia=round(variancia(valores) if n >= 2 else 0.0, 6),
        desvio_padrao=round(desvio_padrao(valores) if n >= 2 else 0.0, 4),
        q1=round(q1, 4),
        q3=round(q3, 4),
    )


# --------------------------------------------------------------------------
# Confiança do dado (Teorema de Bayes)
# --------------------------------------------------------------------------

def confianca_bayesiana(confirmacoes: int, contradicoes: int,
                        prior: float = 0.7,
                        acerto_confirmacao: float = 0.9,
                        acerto_contradicao: float = 0.8) -> float:
    """
    Atualiza a confiança de que um preço está correto.

    Parte de uma probabilidade inicial (prior) de que o preço lido do cupom
    está correto, e a atualiza a cada nova observação:

        P(correto | evidências) ∝ P(evidências | correto) · P(correto)

    Cada cupom de outro usuário que registra o mesmo preço é uma confirmação;
    cada um que registra preço diferente na mesma data é uma contradição.

    Como o dado do TáBão vem de documento fiscal, o prior é alto (0.7) — bem
    diferente do que seria razoável para um preço digitado à mão.
    """
    if not 0 < prior < 1:
        raise ValueError("O prior deve estar entre 0 e 1 (exclusivo).")
    if confirmacoes < 0 or contradicoes < 0:
        raise ValueError("As contagens não podem ser negativas.")

    # Verossimilhança das evidências sob cada hipótese.
    p_evidencia_se_correto = (
        acerto_confirmacao ** confirmacoes
        * (1 - acerto_contradicao) ** contradicoes
    )
    p_evidencia_se_errado = (
        (1 - acerto_confirmacao) ** confirmacoes
        * acerto_contradicao ** contradicoes
    )

    numerador = p_evidencia_se_correto * prior
    denominador = numerador + p_evidencia_se_errado * (1 - prior)

    if denominador == 0:
        return 0.0
    return round(numerador / denominador, 4)


def penalidade_por_idade(dias: int, meia_vida_dias: int = 7) -> float:
    """
    Fator de 0 a 1 que reduz a confiança conforme o preço envelhece.

    Um preço de hoje vale 1.0; com uma meia-vida de 7 dias, um preço de uma
    semana atrás vale 0.5, e de duas semanas, 0.25.
    """
    if dias < 0:
        dias = 0
    return round(0.5 ** (dias / meia_vida_dias), 4)


def confianca_final(confirmacoes: int, contradicoes: int, dias: int,
                    prior: float = 0.7) -> float:
    """Confiança bayesiana ajustada pela idade do dado."""
    base = confianca_bayesiana(confirmacoes, contradicoes, prior)
    return round(base * penalidade_por_idade(dias), 4)
