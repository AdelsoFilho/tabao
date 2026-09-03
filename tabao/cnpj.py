"""
Enriquecimento do estabelecimento pelo registro oficial do CNPJ.

O endereço impresso no cupom fiscal é o do cadastro do emitente, mas vem
abreviado e sem CEP. Pior: pode trazer o bairro desatualizado. No cupom de
teste a nota diz "SETOR NOVA VILA" enquanto o registro da Receita Federal diz
"VILA JARAGUÁ" — bairros vizinhos, com CEPs diferentes.

Sem o CEP, a geocodificação só encontra o centro da avenida, e avenidas longas
produzem erro de centenas de metros. Com o CEP, o ponto cai dentro do trecho
certo.

Fonte: BrasilAPI, que expõe os dados públicos da Receita Federal. Gratuita,
sem chave de API e sem cadastro.
"""

import re
from dataclasses import dataclass
from typing import Optional

BRASILAPI_CNPJ = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"

CABECALHOS = {
    "User-Agent": (
        "TaBao/0.1 (projeto academico SENAI Fatesg; monitoramento colaborativo "
        "de precos de alimentos)"
    ),
}

# Abreviações que a Receita usa no cadastro e que atrapalham a geocodificação.
_ABREVIACOES = {
    r"\bVI\b": "VILA",
    r"\bJD\b": "JARDIM",
    r"\bST\b": "SETOR",
    r"\bPQ\b": "PARQUE",
    r"\bCJ\b": "CONJUNTO",
    r"\bRES\b": "RESIDENCIAL",
    r"\bAV\b": "AVENIDA",
    r"\bR\b": "RUA",
}


class ConsultaCNPJError(RuntimeError):
    """Falha ao consultar o registro do CNPJ."""


@dataclass
class RegistroCNPJ:
    """Endereço oficial de um estabelecimento, vindo da Receita Federal."""

    cnpj: str
    razao_social: str
    nome_fantasia: str
    logradouro: str
    numero: str
    bairro: str
    cep: str
    municipio: str
    uf: str
    situacao: str = ""

    @property
    def ativo(self) -> bool:
        return self.situacao.upper() == "ATIVA"

    @property
    def nome_exibicao(self) -> str:
        """Prefere o nome fantasia, que é como o cliente conhece a loja."""
        return self.nome_fantasia or self.razao_social

    @property
    def cep_formatado(self) -> str:
        c = re.sub(r"\D", "", self.cep)
        return f"{c[:5]}-{c[5:]}" if len(c) == 8 else self.cep

    @property
    def endereco_completo(self) -> str:
        partes = [
            f"{self.logradouro}, {self.numero}".strip(", "),
            self.bairro,
            self.municipio,
            self.uf,
        ]
        return ", ".join(p for p in partes if p)


def expandir_abreviacoes(texto: str) -> str:
    """
    Expande as abreviações do cadastro da Receita.

    "VI JARAGUA" vira "VILA JARAGUA"; sem isso o Nominatim não reconhece o
    bairro.
    """
    resultado = (texto or "").upper().strip()
    for padrao, completo in _ABREVIACOES.items():
        resultado = re.sub(padrao, completo, resultado)
    return resultado


def consultar(cnpj: str, tempo_limite: int = 30) -> Optional[RegistroCNPJ]:
    """
    Busca o registro oficial de um CNPJ.

    Devolve None quando o CNPJ não é encontrado. Levanta ConsultaCNPJError
    apenas em falha de rede ou de serviço, para que o chamador decida se
    interrompe ou segue com o endereço do cupom.
    """
    try:
        import requests
    except ImportError as erro:  # pragma: no cover
        raise ConsultaCNPJError("requests não está instalado.") from erro

    limpo = re.sub(r"\D", "", cnpj or "")
    if len(limpo) != 14:
        return None

    try:
        resposta = requests.get(
            BRASILAPI_CNPJ.format(cnpj=limpo), headers=CABECALHOS, timeout=tempo_limite
        )
    except Exception as erro:
        raise ConsultaCNPJError(f"Falha de rede ao consultar o CNPJ: {erro}") from erro

    if resposta.status_code == 404:
        return None
    if resposta.status_code != 200:
        raise ConsultaCNPJError(
            f"O serviço de CNPJ respondeu {resposta.status_code}. "
            "É gratuito e limita requisições; tente de novo em alguns minutos."
        )

    try:
        dados = resposta.json()
    except ValueError as erro:
        raise ConsultaCNPJError("Resposta do serviço de CNPJ não é JSON.") from erro

    return RegistroCNPJ(
        cnpj=limpo,
        razao_social=(dados.get("razao_social") or "").strip(),
        nome_fantasia=(dados.get("nome_fantasia") or "").strip(),
        logradouro=expandir_abreviacoes(dados.get("logradouro") or ""),
        numero=str(dados.get("numero") or "").strip(),
        bairro=expandir_abreviacoes(dados.get("bairro") or ""),
        cep=re.sub(r"\D", "", str(dados.get("cep") or "")),
        municipio=(dados.get("municipio") or "").strip(),
        uf=(dados.get("uf") or "").strip(),
        situacao=(dados.get("descricao_situacao_cadastral") or "").strip(),
    )
