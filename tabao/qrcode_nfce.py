"""
Leitura e interpretação do QR Code impresso no DANFE-NFC-e.

O QR Code não guarda os produtos: ele guarda a URL de consulta da nota no
portal da SEFAZ. É por isso que o TáBão não precisa de OCR — basta ler o
código, montar a URL e buscar os dados já estruturados na fonte oficial.

Formato do parâmetro "p" da URL (Manual de Padrões do DANFE-NFC-e):

    QR Code 2.00, emissão normal (5 campos):
        chNFe | nVersao | tpAmb | cIdToken | cHashQRCode

    QR Code 2.00, contingência offline (8 campos):
        chNFe | nVersao | tpAmb | dia | vNF | digVal | cIdToken | cHashQRCode
"""

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .chave import ChaveInvalidaError, DadosChave, interpretar

# URLs de consulta da NFC-e por unidade da federação (ambiente de produção).
#
# Goiás: a URL foi alterada pelo Informe Técnico 2025.003. A antiga
# (http://nfe.sefaz.go.gov.br/...) deixou de ser aceita em 30/08/2025.
URL_CONSULTA_POR_UF = {
    "GO": "https://nfeweb.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe",
}

URL_CONSULTA_HOMOLOGACAO = {
    "GO": "https://nfewebhomolog.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe",
}

AMBIENTE_PRODUCAO = "1"
AMBIENTE_HOMOLOGACAO = "2"


def _origem(url: str) -> str:
    """Esquema e domínio de uma URL, em minúsculas: "https://host"."""
    partes = urlparse(url)
    return f"{(partes.scheme or '').lower()}://{(partes.hostname or '').lower()}"


def _origens_oficiais() -> dict:
    """Deriva as origens aceitas das tabelas de URL, para não haver duas listas."""
    tabela: dict = {}
    for fonte in (URL_CONSULTA_POR_UF, URL_CONSULTA_HOMOLOGACAO):
        for uf, url in fonte.items():
            tabela.setdefault(uf, set()).add(_origem(url))
    return tabela


# Origens oficiais aceitas, por unidade da federação.
#
# A URL do QR Code é escolhida por quem envia o cupom, e não é fonte confiável
# de endereço. Sem esta trava bastaria apontar o parâmetro "p" para um servidor
# próprio, devolver um DANFE forjado e o preço inventado entraria na base
# carregando o selo de "conferido na fonte oficial" — que é exatamente o que o
# projeto promete e o que o torna diferente dos aplicativos de digitação.
ORIGENS_POR_UF = _origens_oficiais()


class QRCodeInvalidoError(ValueError):
    """Levantada quando o conteúdo lido não é um QR Code de NFC-e válido."""


@dataclass(frozen=True)
class QRCodeNFCe:
    """Conteúdo útil extraído do QR Code de um cupom fiscal."""

    url_original: str
    chave: str
    versao: str
    tipo_ambiente: str
    dados_chave: DadosChave
    em_contingencia: bool

    @property
    def uf(self) -> str:
        return self.dados_chave.uf

    @property
    def e_producao(self) -> bool:
        return self.tipo_ambiente == AMBIENTE_PRODUCAO

    @property
    def url_consulta(self) -> str:
        """URL da página da SEFAZ que exibe esta nota."""
        return self.url_original


def conferir_origem_oficial(url: str, uf: str) -> None:
    """
    Recusa a URL que não apontar para o portal da SEFAZ daquela unidade da
    federação.

    Confere esquema e domínio juntos: exigir HTTPS impede rebaixar a consulta
    para HTTP no mesmo domínio e interceptá-la no caminho. A UF vem da chave de
    acesso, não da URL, de modo que não basta estar na lista — precisa ser o
    portal do estado que emitiu aquela nota.
    """
    permitidas = ORIGENS_POR_UF.get(uf)
    if not permitidas:
        raise QRCodeInvalidoError(
            f"Ainda não há portal da SEFAZ cadastrado para a UF {uf}."
        )

    origem = _origem(url)
    if origem not in permitidas:
        esperadas = ", ".join(sorted(permitidas))
        raise QRCodeInvalidoError(
            f"O QR Code aponta para {origem}, que não é o portal da SEFAZ de "
            f"{uf}. Esperado: {esperadas}."
        )


def interpretar_url(url: str) -> QRCodeNFCe:
    """
    Interpreta a URL lida do QR Code e valida a chave de acesso nela contida.

    Aceita tanto o formato de emissão normal quanto o de contingência offline.
    """
    url = (url or "").strip()
    if not url:
        raise QRCodeInvalidoError("Conteúdo do QR Code vazio.")

    partes = urlparse(url)
    if partes.scheme not in ("http", "https"):
        raise QRCodeInvalidoError(f"URL sem protocolo http/https: {url!r}")

    # O parâmetro pode vir na query (?p=...) ou no fragmento (#p=...).
    consulta = parse_qs(partes.query)
    if "p" not in consulta and partes.fragment:
        consulta = parse_qs(partes.fragment)

    if "p" not in consulta or not consulta["p"]:
        raise QRCodeInvalidoError("A URL não contém o parâmetro 'p' do QR Code.")

    campos = consulta["p"][0].split("|")
    if len(campos) < 3:
        raise QRCodeInvalidoError(
            f"O parâmetro 'p' deveria ter ao menos 3 campos; tem {len(campos)}."
        )

    chave, versao, tipo_ambiente = campos[0], campos[1], campos[2]

    try:
        dados = interpretar(chave)
    except ChaveInvalidaError as erro:
        raise QRCodeInvalidoError(f"Chave de acesso inválida no QR Code: {erro}") from erro

    if not dados.e_nfce:
        raise QRCodeInvalidoError(
            f"O documento não é uma NFC-e (modelo {dados.modelo}, esperado 65)."
        )

    # Só agora, com a UF lida da chave, dá para saber qual portal é o legítimo.
    conferir_origem_oficial(url, dados.uf)

    return QRCodeNFCe(
        url_original=url,
        chave=dados.chave,
        versao=versao,
        tipo_ambiente=tipo_ambiente,
        dados_chave=dados,
        # 8 campos indicam emissão em contingência offline.
        em_contingencia=len(campos) >= 8,
    )


def montar_url_por_chave(chave: str, producao: bool = True) -> str:
    """
    Monta a URL de consulta a partir apenas da chave de acesso.

    Serve de alternativa quando o QR Code está danificado ou ilegível e o
    usuário digita os 44 dígitos impressos no rodapé do cupom.
    """
    dados = interpretar(chave)
    tabela = URL_CONSULTA_POR_UF if producao else URL_CONSULTA_HOMOLOGACAO

    if dados.uf not in tabela:
        raise QRCodeInvalidoError(
            f"Ainda não há URL de consulta cadastrada para a UF {dados.uf}."
        )

    ambiente = AMBIENTE_PRODUCAO if producao else AMBIENTE_HOMOLOGACAO
    return f"{tabela[dados.uf]}?p={dados.chave}|2|{ambiente}"


def ler_de_imagem(caminho: str) -> str:
    """
    Lê o QR Code de uma foto do cupom e devolve a URL contida nele.

    Usa o detector do OpenCV, que não depende de bibliotecas externas do
    sistema operacional. Levanta QRCodeInvalidoError quando nenhum código é
    encontrado na imagem.
    """
    try:
        import cv2
    except ImportError as erro:  # pragma: no cover - depende do ambiente
        raise QRCodeInvalidoError(
            "OpenCV não está instalado. Rode: pip install opencv-python"
        ) from erro

    imagem = cv2.imread(caminho)
    if imagem is None:
        raise QRCodeInvalidoError(f"Não foi possível abrir a imagem: {caminho}")

    detector = cv2.QRCodeDetector()
    conteudo, _pontos, _ = detector.detectAndDecode(imagem)

    if not conteudo:
        # Segunda tentativa em escala de cinza, que costuma ajudar em fotos
        # de cupom com pouca luz ou papel amassado.
        cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
        conteudo, _pontos, _ = detector.detectAndDecode(cinza)

    if not conteudo:
        raise QRCodeInvalidoError(
            "Nenhum QR Code foi encontrado na imagem. "
            "Tente uma foto mais nítida e com o código inteiro visível."
        )

    return conteudo


def ler_cupom(caminho_imagem: str) -> QRCodeNFCe:
    """Atalho: lê o QR Code de uma imagem e já interpreta a URL."""
    return interpretar_url(ler_de_imagem(caminho_imagem))
