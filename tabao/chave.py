"""
Chave de acesso da NFC-e (44 dígitos).

A chave identifica unicamente um documento fiscal eletrônico em todo o país.
Este módulo valida a chave e a decompõe em seus campos, o que permite recusar
cupons inválidos antes de qualquer acesso à rede.

Composição (conforme Manual de Orientação do Contribuinte):

    posições  campo      significado
    ---------------------------------------------------------------
     0 -  1   cUF        código da unidade da federação (IBGE)
     2 -  5   AAMM       ano e mês de emissão
     6 - 19   CNPJ       CNPJ do emitente
    20 - 21   mod        modelo do documento (65 = NFC-e, 55 = NF-e)
    22 - 24   serie      série do documento
    25 - 33   nNF        número do documento
    34 - 34   tpEmis     forma de emissão
    35 - 42   cNF        código numérico aleatório
    43 - 43   cDV        dígito verificador (módulo 11)
"""

from dataclasses import dataclass

# Código IBGE de cada unidade da federação, usado no início da chave.
UF_POR_CODIGO = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP",
    "17": "TO", "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB",
    "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
    "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
    "51": "MT", "52": "GO", "53": "DF",
}

MODELO_NFCE = "65"


class ChaveInvalidaError(ValueError):
    """Levantada quando a chave de acesso não passa na validação."""


@dataclass(frozen=True)
class DadosChave:
    """Campos extraídos de uma chave de acesso válida."""

    chave: str
    uf: str
    ano: int
    mes: int
    cnpj_emitente: str
    modelo: str
    serie: str
    numero: str
    digito_verificador: str

    @property
    def e_nfce(self) -> bool:
        """Indica se o documento é uma NFC-e (modelo 65)."""
        return self.modelo == MODELO_NFCE

    @property
    def cnpj_formatado(self) -> str:
        c = self.cnpj_emitente
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


def apenas_digitos(texto: str) -> str:
    """Remove qualquer caractere que não seja dígito."""
    return "".join(c for c in texto if c.isdigit())


def calcular_digito_verificador(chave43: str) -> str:
    """
    Calcula o dígito verificador pelo módulo 11.

    Os 43 primeiros dígitos são percorridos da direita para a esquerda,
    multiplicados por pesos que ciclam de 2 a 9. O dígito é 0 quando o resto
    da divisão da soma por 11 é 0 ou 1; caso contrário, é 11 menos o resto.
    """
    if len(chave43) != 43 or not chave43.isdigit():
        raise ChaveInvalidaError("O cálculo do dígito exige exatamente 43 dígitos.")

    soma = 0
    peso = 2
    for digito in reversed(chave43):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1

    resto = soma % 11
    return "0" if resto in (0, 1) else str(11 - resto)


def chave_e_valida(chave: str) -> bool:
    """Retorna True quando a chave tem 44 dígitos e o dígito verificador confere."""
    chave = apenas_digitos(chave)
    if len(chave) != 44:
        return False
    return calcular_digito_verificador(chave[:43]) == chave[43]


def interpretar(chave: str) -> DadosChave:
    """
    Valida a chave e devolve seus campos.

    Levanta ChaveInvalidaError quando a chave não tem 44 dígitos, quando o
    dígito verificador não confere ou quando a UF não é reconhecida.
    """
    chave = apenas_digitos(chave)

    if len(chave) != 44:
        raise ChaveInvalidaError(
            f"A chave deve ter 44 dígitos; foram informados {len(chave)}."
        )

    esperado = calcular_digito_verificador(chave[:43])
    if esperado != chave[43]:
        raise ChaveInvalidaError(
            f"Dígito verificador inválido: esperado {esperado}, encontrado {chave[43]}."
        )

    codigo_uf = chave[0:2]
    if codigo_uf not in UF_POR_CODIGO:
        raise ChaveInvalidaError(f"Código de UF desconhecido: {codigo_uf}.")

    return DadosChave(
        chave=chave,
        uf=UF_POR_CODIGO[codigo_uf],
        ano=2000 + int(chave[2:4]),
        mes=int(chave[4:6]),
        cnpj_emitente=chave[6:20],
        modelo=chave[20:22],
        serie=chave[22:25],
        numero=chave[25:34],
        digito_verificador=chave[43],
    )
