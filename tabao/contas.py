"""
Contas de usuário: cadastro, login e as preferências do veículo.

A conta é opcional. Sem ela o app funciona igual, com as preferências
guardadas só no aparelho; com ela, preço do combustível e consumo do carro
acompanham o usuário em qualquer aparelho.

Senhas nunca são guardadas: só o hash (scrypt, via werkzeug), que não pode
ser revertido. O e-mail é o único dado pessoal coletado.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

# Consumo urbano médio (km/l) dos carros mais vendidos no Brasil, por
# combustível. Referência: Programa Brasileiro de Etiquetagem Veicular
# (PBEV/Inmetro), modelos de entrada e SUVs compactos que lideram as vendas
# (Onix, HB20, Argo, Polo, Strada, Tracker, T-Cross, Creta...). O etanol rende
# cerca de 70% da gasolina. Usado quando o usuário marca "Não sei".
CONSUMO_MEDIO_KM_L = {
    "gasolina": 11.5,
    "etanol": 8.0,
}
COMBUSTIVEIS = {"gasolina": "Gasolina", "etanol": "Etanol"}

SENHA_MINIMA = 8
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContaError(ValueError):
    """Dados de cadastro ou de perfil inválidos. A mensagem vai para a tela."""


@dataclass
class Usuario:
    id: str
    email: str
    nome: str
    senha_hash: str
    combustivel: str = "gasolina"
    preco_combustivel: Optional[float] = None
    # None significa "não sei": vale a média do combustível escolhido.
    consumo_km_l: Optional[float] = None
    criado_em: datetime = field(default_factory=datetime.now)

    @property
    def consumo_efetivo(self) -> float:
        return self.consumo_km_l or CONSUMO_MEDIO_KM_L.get(self.combustivel, 11.5)

    @property
    def consumo_estimado(self) -> bool:
        return self.consumo_km_l is None

    def preferencias(self, preco_padrao: float) -> dict:
        """O que o mapa precisa para fazer a conta do combustível."""
        return {
            "consumo": self.consumo_efetivo,
            "preco": self.preco_combustivel or preco_padrao,
            "consumo_estimado": self.consumo_estimado,
            "combustivel": self.combustivel,
        }

    def para_dicionario(self) -> dict:
        dados = asdict(self)
        dados["criado_em"] = self.criado_em.isoformat()
        return dados

    @classmethod
    def de_dicionario(cls, dados: dict) -> "Usuario":
        dados = dict(dados)
        dados["criado_em"] = datetime.fromisoformat(dados["criado_em"])
        return cls(**dados)


def normalizar_email(email: str) -> str:
    return (email or "").strip().lower()


def validar_cadastro(email: str, nome: str, senha: str, confirmacao: str) -> None:
    if not _EMAIL.match(normalizar_email(email)):
        raise ContaError("Informe um e-mail válido.")
    if not (nome or "").strip():
        raise ContaError("Informe seu nome.")
    if len(senha or "") < SENHA_MINIMA:
        raise ContaError(f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.")
    if senha != confirmacao:
        raise ContaError("As senhas não conferem.")


def gerar_hash(senha: str) -> str:
    return generate_password_hash(senha)


def senha_confere(usuario: Optional[Usuario], senha: str) -> bool:
    if usuario is None:
        # Mesmo custo de quando o usuário existe: não revela pelo tempo de
        # resposta quais e-mails têm conta.
        check_password_hash(_HASH_FALSO, senha or "")
        return False
    return check_password_hash(usuario.senha_hash, senha or "")


_HASH_FALSO = generate_password_hash("senha-que-ninguem-usa")


def _numero(texto: str, campo: str, minimo: float, maximo: float) -> Optional[float]:
    texto = (texto or "").strip().replace("R$", "").replace(" ", "")
    if not texto:
        return None
    # Aceita "6,49" e "6.49".
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        valor = float(texto)
    except ValueError:
        raise ContaError(f"{campo}: informe um número.")
    if not minimo <= valor <= maximo:
        raise ContaError(f"{campo}: valor fora do esperado.")
    return round(valor, 3)


def ler_veiculo(combustivel: str, preco: str, consumo: str, nao_sei: bool) -> dict:
    """Converte o formulário do perfil em valores validados."""
    if combustivel not in COMBUSTIVEIS:
        raise ContaError("Escolha o combustível.")
    return {
        "combustivel": combustivel,
        "preco_combustivel": _numero(preco, "Preço do combustível", 1, 20),
        "consumo_km_l": None if nao_sei else _numero(consumo, "Consumo", 2, 40),
    }
