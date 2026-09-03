"""
Estruturas de dados do domínio: o que é um cupom, um item e um preço observado.

Estas classes são o contrato entre os módulos. O extrator da SEFAZ produz um
Cupom; a normalização classifica cada ItemCupom; o repositório guarda
PrecoObservado; a estatística e o cálculo da cesta consomem esses preços.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Estabelecimento:
    """O supermercado que emitiu o cupom. É sempre pessoa jurídica."""

    cnpj: str
    nome: str
    endereco: str = ""
    # Preenchidas depois, geocodificando o endereço no Nominatim.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Dados oficiais do registro do CNPJ, quando consultados.
    cep: str = ""
    bairro: str = ""
    # Quão exato é o ponto: "endereco" (porta), "cep" (quadra do CEP),
    # "rua" (centro do logradouro) ou "" (não localizado).
    precisao: str = ""

    @property
    def localizado(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def precisao_legivel(self) -> str:
        return {
            "endereco": "endereço exato",
            "cep": "aproximado pelo CEP",
            "rua": "aproximado pela rua",
        }.get(self.precisao, "não localizado")

    @property
    def ponto_aproximado(self) -> bool:
        """Indica que o pino não marca a porta da loja."""
        return self.precisao in ("cep", "rua")

    @property
    def cnpj_formatado(self) -> str:
        c = self.cnpj
        if len(c) != 14:
            return c
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


@dataclass
class ItemCupom:
    """Uma linha do cupom fiscal."""

    descricao: str
    quantidade: float
    unidade: str
    valor_unitario: float
    valor_total: float
    codigo: str = ""
    # Preenchido pela normalização; None quando o produto não pertence à cesta.
    item_cesta: Optional[str] = None

    @property
    def preco_por_unidade(self) -> float:
        """
        Preço unitário efetivo.

        Usa o valor total dividido pela quantidade, que é mais confiável que o
        valor unitário impresso quando há desconto aplicado na linha.
        """
        if self.quantidade > 0:
            return round(self.valor_total / self.quantidade, 4)
        return self.valor_unitario


@dataclass
class Cupom:
    """Um cupom fiscal completo, já extraído da página da SEFAZ."""

    chave: str
    estabelecimento: Estabelecimento
    emitido_em: datetime
    itens: list[ItemCupom] = field(default_factory=list)
    valor_total: float = 0.0

    @property
    def total_calculado(self) -> float:
        return round(sum(i.valor_total for i in self.itens), 2)

    @property
    def itens_da_cesta(self) -> list[ItemCupom]:
        return [i for i in self.itens if i.item_cesta]

    def resumo(self) -> str:
        return (
            f"{self.estabelecimento.nome} | {self.emitido_em:%d/%m/%Y %H:%M} | "
            f"{len(self.itens)} itens | R$ {self.total_calculado:.2f}"
        )

    # ---- serialização ----
    #
    # Necessária porque em hospedagem serverless cada requisição pode cair em
    # uma instância diferente do processo. Guardar o cupom em memória entre a
    # leitura e a confirmação simplesmente não funciona: a tela de confirmação
    # cairia em outra instância, que não teria o cupom. Serializado e assinado,
    # ele viaja com o formulário.

    def para_dicionario(self) -> dict:
        return {
            "chave": self.chave,
            "estabelecimento": asdict(self.estabelecimento),
            "emitido_em": self.emitido_em.isoformat(),
            "valor_total": self.valor_total,
            "itens": [asdict(i) for i in self.itens],
        }

    @classmethod
    def de_dicionario(cls, dados: dict) -> "Cupom":
        return cls(
            chave=dados["chave"],
            estabelecimento=Estabelecimento(**dados["estabelecimento"]),
            emitido_em=datetime.fromisoformat(dados["emitido_em"]),
            itens=[ItemCupom(**i) for i in dados.get("itens", [])],
            valor_total=dados.get("valor_total", 0.0),
        )


@dataclass
class PrecoObservado:
    """
    Um preço de um produto em um estabelecimento, em uma data.

    É o registro que alimenta a base colaborativa. Nunca é sobrescrito: cada
    cupom acrescenta uma nova observação, preservando o histórico.

    Registra TODOS os produtos do cupom, não apenas os da cesta básica:
    `categoria` sempre tem valor, enquanto `item_cesta` é None para os produtos
    que estão fora da cesta oficial.
    """

    descricao_original: str
    cnpj: str
    nome_estabelecimento: str
    preco: float
    unidade: str
    observado_em: datetime
    chave_cupom: str
    categoria: str = "outros"
    item_cesta: Optional[str] = None
    codigo: str = ""

    @property
    def da_cesta(self) -> bool:
        return self.item_cesta is not None

    def para_dicionario(self) -> dict:
        dados = asdict(self)
        dados["observado_em"] = self.observado_em.isoformat()
        return dados

    @classmethod
    def de_dicionario(cls, dados: dict) -> "PrecoObservado":
        dados = dict(dados)
        dados["observado_em"] = datetime.fromisoformat(dados["observado_em"])
        return cls(**dados)
