"""
Consulta e extração da nota no portal público da SEFAZ.

Responsabilidades separadas de propósito:

    buscar_html(url)  -> faz a requisição HTTP (precisa de rede)
    extrair(html)     -> converte o HTML em um Cupom (função pura, testável)

Essa separação é o que torna o projeto testável sem internet e é também a
mitigação do principal risco do MVP: se a SEFAZ mudar o layout da página,
apenas `extrair` precisa mudar.

IMPORTANTE SOBRE PRIVACIDADE (LGPD): o extrator lê apenas itens, preços e a
identificação do estabelecimento. O CPF do consumidor, quando presente na
nota, é ignorado e nunca chega a ser armazenado.
"""

import re
import unicodedata
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .modelos import Cupom, Estabelecimento, ItemCupom

TEMPO_LIMITE_SEGUNDOS = 20

# Identificar-se é boa prática e evita ser confundido com um robô abusivo.
CABECALHOS = {
    "User-Agent": (
        "TaBao/0.1 (projeto academico; monitoramento colaborativo de precos "
        "de cesta basica)"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}


class ConsultaSEFAZError(RuntimeError):
    """Falha ao consultar ou interpretar a página da SEFAZ."""


def buscar_html(url: str, tempo_limite: int = TEMPO_LIMITE_SEGUNDOS) -> str:
    """
    Baixa a página pública da nota fiscal.

    Requisições são feitas em ritmo compatível com uso humano e com
    identificação no User-Agent, conforme previsto na análise de riscos.
    """
    try:
        import requests
    except ImportError as erro:  # pragma: no cover
        raise ConsultaSEFAZError(
            "requests não está instalado. Rode: pip install requests"
        ) from erro

    try:
        resposta = requests.get(url, headers=CABECALHOS, timeout=tempo_limite)
    except Exception as erro:
        raise ConsultaSEFAZError(f"Falha de rede ao consultar a SEFAZ: {erro}") from erro

    if resposta.status_code != 200:
        raise ConsultaSEFAZError(
            f"A SEFAZ respondeu com status {resposta.status_code}. "
            "A nota pode não existir, ou o portal pode estar indisponível."
        )

    resposta.encoding = resposta.apparent_encoding or "utf-8"
    return resposta.text


# --------------------------------------------------------------------------
# Extração (função pura: recebe HTML, devolve Cupom)
# --------------------------------------------------------------------------

def _texto(no) -> str:
    """Texto limpo de um nó, com espaços colapsados."""
    if no is None:
        return ""
    return re.sub(r"\s+", " ", no.get_text(" ", strip=True)).strip()


def _numero(texto: str) -> float:
    """
    Converte um número em formato brasileiro para float.

    Aceita "1.234,56", "1234,56", "12,500" e "2". Devolve 0.0 quando não há
    número reconhecível.
    """
    if not texto:
        return 0.0
    achado = re.search(r"-?[\d.]*\d(?:,\d+)?", texto.replace(" ", ""))
    if not achado:
        return 0.0
    bruto = achado.group(0)
    # Pontos são separadores de milhar; a vírgula é o separador decimal.
    bruto = bruto.replace(".", "").replace(",", ".")
    try:
        return float(bruto)
    except ValueError:
        return 0.0


def _sem_acento(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _primeiro_valor(no, *classes: str) -> str:
    """Devolve o texto do primeiro descendente que casar com uma das classes."""
    for classe in classes:
        achado = no.find(class_=classe)
        if achado is not None:
            return _texto(achado)
    return ""


def _extrair_itens(sopa: BeautifulSoup) -> list[ItemCupom]:
    """
    Lê a tabela de produtos da página.

    O portal da NFC-e usa a tabela de id "tabResult", em que cada linha traz
    spans com classes conhecidas: txtTit (descrição), Rqtd (quantidade),
    RUN (unidade), RvlUnit (valor unitário) e valor (total da linha).
    """
    itens: list[ItemCupom] = []

    tabela = sopa.find(id="tabResult")
    linhas = tabela.find_all("tr") if tabela else []

    if not linhas:
        # Alternativa: algumas variações do portal não usam o id "tabResult".
        linhas = [tr for tr in sopa.find_all("tr") if tr.find(class_="txtTit")]

    for linha in linhas:
        descricao = _primeiro_valor(linha, "txtTit", "txtTit2")
        if not descricao:
            continue

        quantidade = _numero(_primeiro_valor(linha, "Rqtd", "qtd"))
        unidade = _primeiro_valor(linha, "RUN", "un")
        valor_unitario = _numero(_primeiro_valor(linha, "RvlUnit", "vlUnit"))
        valor_total = _numero(_primeiro_valor(linha, "valor", "vlTotal"))
        codigo = _primeiro_valor(linha, "RCod", "cod")

        # Limpa os rótulos que vêm colados ao valor no HTML do portal.
        unidade = re.sub(r"(?i)^un\.?:?\s*", "", unidade).strip() or "UN"
        codigo_digitos = re.sub(r"\D", "", codigo)

        if quantidade <= 0:
            quantidade = 1.0
        if valor_total <= 0:
            valor_total = round(valor_unitario * quantidade, 2)
        if valor_unitario <= 0 and quantidade:
            valor_unitario = round(valor_total / quantidade, 4)

        if valor_total <= 0:
            continue  # linha sem preço utilizável

        itens.append(
            ItemCupom(
                descricao=descricao,
                quantidade=quantidade,
                unidade=unidade,
                valor_unitario=valor_unitario,
                valor_total=valor_total,
                codigo=codigo_digitos,
            )
        )

    return itens


def _extrair_estabelecimento(sopa: BeautifulSoup) -> Estabelecimento:
    """Lê nome, CNPJ e endereço do emitente no cabeçalho da página."""
    nome = _primeiro_valor(sopa, "txtTopo") or "Estabelecimento não identificado"

    texto_completo = _texto(sopa)
    achado_cnpj = re.search(
        r"CNPJ[:\s]*([\d]{2}\.?[\d]{3}\.?[\d]{3}/?[\d]{4}-?[\d]{2})",
        texto_completo,
        re.IGNORECASE,
    )
    cnpj = re.sub(r"\D", "", achado_cnpj.group(1)) if achado_cnpj else ""

    # O cabeçalho traz várias linhas com class="text": a primeira é o CNPJ e a
    # segunda o endereço. Pegar a primeira devolveria "CNPJ: 03.083.231/0041-91".
    endereco = ""
    for candidato in sopa.find_all(class_=("text", "endereco")):
        linha = _texto(candidato)
        if not linha or re.match(r"(?i)^\s*(CNPJ|CPF)", linha):
            continue
        # O endereço é a linha com vírgulas separando logradouro, número e bairro.
        if linha.count(",") >= 2:
            endereco = re.sub(r"\s+,", ",", linha)
            break

    return Estabelecimento(cnpj=cnpj, nome=nome, endereco=endereco)


def _extrair_emissao(sopa: BeautifulSoup) -> datetime:
    """
    Procura a data e hora de emissão no rodapé da página.

    Quando a página não traz a data em formato reconhecível, usa o momento da
    consulta — o dado continua utilizável, apenas menos preciso.
    """
    texto = _sem_acento(_texto(sopa))

    padrao = re.search(
        r"(\d{2}/\d{2}/\d{4})\s*(?:as|às)?\s*(\d{2}:\d{2}(?::\d{2})?)?",
        texto,
        re.IGNORECASE,
    )
    if padrao:
        data = padrao.group(1)
        hora = padrao.group(2) or "00:00"
        formato = "%d/%m/%Y %H:%M:%S" if hora.count(":") == 2 else "%d/%m/%Y %H:%M"
        try:
            return datetime.strptime(f"{data} {hora}", formato)
        except ValueError:
            pass

    return datetime.now()


def _extrair_chave(sopa: BeautifulSoup, chave_esperada: Optional[str]) -> str:
    if chave_esperada:
        return chave_esperada
    achado = re.search(r"\b(\d{44})\b", re.sub(r"\D", lambda m: m.group(0), _texto(sopa)))
    if achado:
        return achado.group(1)
    achado = re.search(r"(?:\d[\s.]*){44}", _texto(sopa))
    return re.sub(r"\D", "", achado.group(0)) if achado else ""


def extrair(html: str, chave: Optional[str] = None) -> Cupom:
    """
    Converte o HTML da página da SEFAZ em um Cupom.

    Função pura: não acessa a rede. É o único ponto que conhece o layout do
    portal, e portanto o único que precisa mudar se a SEFAZ alterar a página.
    """
    if not html or not html.strip():
        raise ConsultaSEFAZError("HTML vazio: não há o que extrair.")

    sopa = BeautifulSoup(html, "html.parser")

    itens = _extrair_itens(sopa)
    if not itens:
        raise ConsultaSEFAZError(
            "Nenhum item foi encontrado na página. O layout do portal pode ter "
            "mudado, ou a consulta pode ter retornado uma página de erro."
        )

    cupom = Cupom(
        chave=_extrair_chave(sopa, chave),
        estabelecimento=_extrair_estabelecimento(sopa),
        emitido_em=_extrair_emissao(sopa),
        itens=itens,
    )
    cupom.valor_total = cupom.total_calculado
    return cupom


# --------------------------------------------------------------------------
# Fluxo real do portal da SEFAZ-GO
# --------------------------------------------------------------------------
#
# A página aberta pelo QR Code é apenas um invólucro: os produtos vêm de um
# endpoint interno que devolve XML com o HTML do DANFE escapado dentro.
#
#   1. GET na URL do QR Code            -> cria a sessão (cookie jsessionid)
#   2. GET em RENDER_HTML?chNFe=<chave> -> <Map><PARAMS><DANFE_NFCE_HTML>...
#   3. desembrulhar o HTML e extrair
#
# Sem o passo 1 o portal responde "Sessão Expirada".

RENDER_HTML = "/nfeweb/sites/nfce/render/html/danfeNFCe"


def desembrulhar_resposta(xml: str) -> str:
    """
    Extrai o HTML do DANFE de dentro do envelope <Map> devolvido pelo portal.

    O envelope tem a forma:
        <Map><STATUS>SUCCESS</STATUS><PARAMS><DANFE_NFCE_HTML>&lt;div...
    """
    sopa = BeautifulSoup(xml, "xml")

    status = sopa.find("STATUS")
    if status is not None and _texto(status).upper() != "SUCCESS":
        mensagem = _texto(sopa.find("MESSAGE")) or "sem detalhes"
        raise ConsultaSEFAZError(f"A SEFAZ recusou a consulta: {mensagem}")

    conteudo = sopa.find("DANFE_NFCE_HTML")
    if conteudo is None:
        raise ConsultaSEFAZError(
            "A resposta da SEFAZ não trouxe o HTML do DANFE. O portal pode ter "
            "mudado o formato do endpoint interno."
        )

    return conteudo.get_text()


def consultar_por_qrcode(url_qrcode: str, chave: str,
                         tempo_limite: int = TEMPO_LIMITE_SEGUNDOS) -> Cupom:
    """
    Percorre o fluxo completo do portal e devolve o cupom extraído.

    Este é o caminho usado em produção: a URL do QR Code estabelece a sessão e
    o endpoint interno entrega os itens. Diferente da consulta por chave
    digitada, ele não é protegido por captcha.
    """
    try:
        import requests
    except ImportError as erro:  # pragma: no cover
        raise ConsultaSEFAZError(
            "requests não está instalado. Rode: pip install requests"
        ) from erro

    origem = urlparse(url_qrcode)
    base = f"{origem.scheme}://{origem.netloc}"

    sessao = requests.Session()
    sessao.headers.update(CABECALHOS)

    try:
        # Passo 1: abre a página do QR Code para receber o cookie de sessão.
        sessao.get(url_qrcode, timeout=tempo_limite)

        # Passo 2: pede o HTML do DANFE ao endpoint interno.
        resposta = sessao.get(
            f"{base}{RENDER_HTML}", params={"chNFe": chave}, timeout=tempo_limite
        )
    except Exception as erro:
        raise ConsultaSEFAZError(f"Falha de rede ao consultar a SEFAZ: {erro}") from erro

    if resposta.status_code != 200:
        raise ConsultaSEFAZError(
            f"A SEFAZ respondeu com status {resposta.status_code}."
        )

    resposta.encoding = "utf-8"
    return extrair(desembrulhar_resposta(resposta.text), chave=chave)


def consultar(url: str, chave: Optional[str] = None) -> Cupom:
    """
    Busca a nota e devolve o cupom extraído.

    Quando a chave é conhecida, usa o fluxo completo do portal; caso contrário,
    tenta interpretar a própria página como DANFE (útil para páginas salvas).
    """
    if chave:
        return consultar_por_qrcode(url, chave)
    return extrair(buscar_html(url))
