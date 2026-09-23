"""
TáBão — interface web do MVP.

Roda sobre exatamente o mesmo pipeline da linha de comando: nenhuma regra de
negócio vive aqui. Esta camada só recebe a foto, chama os módulos e apresenta
o resultado.

    python app.py     e abra http://localhost:5000
"""

import json
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path

from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)

# Permite rodar "python tabao/app.py" a partir do diretório acima.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tabao import sefaz
from tabao.categorias import nome_da_categoria, todas_as_categorias
from tabao.cesta import estatisticas_do_item, montar_ranking
from tabao.categorias import categorizar
from tabao.chave import ChaveInvalidaError, interpretar
from tabao.estatistica import DadosInsuficientesError, resumir
from tabao.produtos import CESTA_BASICA, classificar, nome_do_item
from tabao.qrcode_nfce import QRCodeInvalidoError, interpretar_url, ler_de_imagem
from tabao.mapa import (CacheMapa, MapaError, TIPOS_OSM, casar_com_estabelecimentos,
                        importar_area, locais_dos_estabelecimentos,
                        localizar_estabelecimentos, mesclar)
from tabao.rota import (CONSUMO_PADRAO_KM_L, PRECO_COMBUSTIVEL_PADRAO, avaliar,
                       calcular_trajeto, compensa_ir, custo_do_trajeto)
from tabao.banco import criar_repositorio
from tabao.repositorio import Repositorio, matriz_precos

app = Flask(__name__)

# Hospedagens que definem essa variável por conta própria. Serve só para
# distinguir "está publicado" de "está na máquina de alguém".
MARCAS_DE_HOSPEDAGEM = ("VERCEL", "RENDER", "RAILWAY_ENVIRONMENT", "DYNO")


def _chave_secreta() -> str:
    """
    Chave que assina o cupom entre a conferência e a gravação.

    Não existe valor padrão de propósito. A rota /confirmar grava o que vier
    dentro do token assinado, sem reconsultar a SEFAZ: com uma chave fixa e
    publicada junto do código, qualquer pessoa forjaria um cupom com os preços
    que quisesse e o gravaria na base, sem nunca passar por um QR Code.

    Publicado, a ausência da variável é erro de configuração e o aplicativo se
    recusa a subir. Fora de hospedagem, sorteia uma chave para a execução
    atual: o desenvolvimento segue funcionando e nada assinado sobrevive ao
    reinício.
    """
    chave = os.environ.get("SECRET_KEY", "").strip()
    if chave:
        return chave

    hospedagem = next((n for n in MARCAS_DE_HOSPEDAGEM if os.environ.get(n)), None)
    if hospedagem:
        raise RuntimeError(
            f"SECRET_KEY não está definida e {hospedagem} foi detectado. "
            'Gere uma chave com \'python -c "import secrets; '
            "print(secrets.token_urlsafe(48))\"' e cadastre nas variáveis de "
            "ambiente da hospedagem."
        )

    print(
        "AVISO: SECRET_KEY não definida. Usando chave sorteada para esta "
        "execução; cupons em conferência não sobrevivem ao reinício.",
        file=sys.stderr,
    )
    return secrets.token_urlsafe(48)


app.secret_key = _chave_secreta()
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB por foto

# DADOS_DIR permite apontar para um disco persistente na hospedagem.
# Sem ele, usa a pasta ao lado do código.
_PADRAO = Path(__file__).resolve().parent / "dados"
DADOS = Path(os.environ.get("DADOS_DIR", _PADRAO))
BASE = DADOS / "precos.json"
MAPA = DADOS / "mapa.json"

# Tempo de vida do cupom lido e ainda não confirmado.
VALIDADE_CUPOM_SEGUNDOS = 30 * 60


def _assinador():
    """
    Serializador assinado para o cupom que aguarda confirmação.

    Em hospedagem serverless não existe memória compartilhada entre
    requisições: cada uma pode cair em outra instância. Por isso o cupom viaja
    dentro do formulário, e não em um dicionário do processo.

    A assinatura (mesma técnica das sessões do Flask) impede que alguém edite
    os preços no caminho entre a conferência e a confirmação — o que
    contaminaria a base colaborativa.
    """
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(app.secret_key, salt="tabao-cupom")


def empacotar_cupom(cupom) -> str:
    return _assinador().dumps(cupom.para_dicionario())


def desempacotar_cupom(token: str):
    """Devolve o Cupom assinado, ou None se estiver adulterado ou vencido."""
    from itsdangerous import BadSignature, SignatureExpired

    from tabao.modelos import Cupom

    try:
        dados = _assinador().loads(token, max_age=VALIDADE_CUPOM_SEGUNDOS)
    except SignatureExpired:
        return None
    except BadSignature:
        return None

    try:
        return Cupom.de_dicionario(dados)
    except (KeyError, TypeError, ValueError):
        return None


def repositorio():
    """
    Repositório do ambiente: Postgres quando há DATABASE_URL, JSON quando não.

    Uma instância por requisição mantém o código simples e evita conexão
    compartilhada entre threads do servidor.
    """
    return criar_repositorio(BASE)


@app.template_filter("reais")
def filtro_reais(valor) -> str:
    """Formata um número no padrão brasileiro: 1.234,56."""
    try:
        return f"{float(valor):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    except (TypeError, ValueError):
        return "—"


@app.template_filter("data")
def filtro_data(valor) -> str:
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y")
    return "—"


@app.template_filter("qtd")
def filtro_qtd(valor) -> str:
    """
    Formata a quantidade no padrão brasileiro.

    Uma unidade inteira vira "1" (e não "1,000", que se leria como mil); um peso
    fracionado vira "1,406", com vírgula decimal e sem zeros sobrando.
    """
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "—"
    if numero == int(numero):
        return str(int(numero))
    return f"{numero:.3f}".rstrip("0").rstrip(".").replace(".", ",")


# --------------------------------------------------------------------------
# Páginas
# --------------------------------------------------------------------------

@app.context_processor
def contexto_global():
    """A região importada aparece no topo de todas as telas."""
    return {"area_atual": CacheMapa(MAPA).area or "Goiânia"}


@app.route("/precos")
def precos():
    """Painel: ranking da cesta e resumo da base."""
    repo = repositorio()
    ranking = montar_ranking(repo.precos) if len(repo) else None

    return render_template(
        "precos.html",
        ranking=ranking,
        total_precos=len(repo),
        total_cesta=len(repo.precos_da_cesta),
        estabelecimentos=len(repo.estabelecimentos()),
        categorias=repo.categorias_cobertas(),
        nome_categoria=nome_da_categoria,
        itens_cobertos=len(repo.itens_cobertos()),
        total_itens_cesta=len(CESTA_BASICA),
    )


@app.route("/enviar", methods=["GET", "POST"])
def enviar():
    """Recebe a foto do cupom (ou a URL do QR) e mostra os itens para conferência."""
    if request.method == "GET":
        return render_template("enviar.html")

    url_qrcode = (request.form.get("url") or "").strip()
    # Dois inputs (câmera e galeria) compartilham name="foto"; pega o preenchido.
    foto = next((f for f in request.files.getlist("foto") if f and f.filename), None)

    try:
        if not url_qrcode and foto and foto.filename:
            sufixo = Path(foto.filename).suffix or ".jpg"
            with tempfile.NamedTemporaryFile(suffix=sufixo, delete=False) as arquivo:
                foto.save(arquivo.name)
                caminho = arquivo.name
            try:
                url_qrcode = ler_de_imagem(caminho)
            finally:
                Path(caminho).unlink(missing_ok=True)

        if not url_qrcode:
            flash("Envie a foto do cupom ou cole a URL do QR Code.", "erro")
            return redirect(url_for("enviar"))

        qr = interpretar_url(url_qrcode)
    except QRCodeInvalidoError as erro:
        flash(f"Não consegui ler o cupom: {erro}", "erro")
        return redirect(url_for("enviar"))

    repo = repositorio()
    if repo.ja_processado(qr.chave):
        flash("Este cupom já está na base.", "aviso")
        return redirect(url_for("precos"))

    try:
        cupom = sefaz.consultar_por_qrcode(qr.url_consulta, qr.chave)
    except sefaz.ConsultaSEFAZError as erro:
        flash(f"A SEFAZ não devolveu a nota: {erro}", "erro")
        return redirect(url_for("enviar"))

    # Renderiza a conferência na mesma requisição: sem redirect, sem estado.
    return render_template("conferir.html", **contexto_conferencia(cupom))


def contexto_conferencia(cupom) -> dict:
    """Monta o que a tela de conferência precisa, incluindo o cupom assinado."""
    linhas = []
    for item in cupom.itens:
        classificacao = classificar(item.descricao)
        linhas.append({
            "item": item,
            "categoria": nome_da_categoria(categorizar(item.descricao)),
            "cesta": nome_do_item(classificacao.item) if classificacao.item else None,
        })

    return {
        "cupom": cupom,
        "linhas": linhas,
        "da_cesta": sum(1 for linha in linhas if linha["cesta"]),
        "token": empacotar_cupom(cupom),
    }


@app.route("/confirmar", methods=["POST"])
def confirmar():
    """Grava na base colaborativa o cupom que o usuário confirmou."""
    cupom = desempacotar_cupom(request.form.get("cupom", ""))
    if cupom is None:
        flash(
            "A conferência expirou ou os dados foram alterados. "
            "Envie o cupom de novo.",
            "aviso",
        )
        return redirect(url_for("enviar"))

    repo = repositorio()
    if repo.ja_processado(cupom.chave):
        flash("Este cupom já está na base.", "aviso")
        return redirect(url_for("precos"))
    gravados = repo.registrar_cupom(cupom)
    repo.salvar()

    flash(f"{gravados} produtos adicionados à base. Obrigado por contribuir!", "sucesso")
    return redirect(url_for("precos"))


@app.route("/produtos")
def produtos():
    """Busca livre e comparação de preços entre estabelecimentos."""
    termo = (request.args.get("q") or "").strip()
    repo = repositorio()

    achados = repo.buscar_produto(termo) if termo else []
    achados.sort(key=lambda p: p.preco)

    resumo = None
    if len(achados) >= 2:
        try:
            resumo = resumir([p.preco for p in achados])
        except DadosInsuficientesError:
            resumo = None

    return render_template("produtos.html", termo=termo, achados=achados, resumo=resumo)


@app.route("/categorias")
@app.route("/categorias/<categoria>")
def categorias(categoria: str | None = None):
    """Navegação por categoria: todos os produtos, não só a cesta."""
    repo = repositorio()
    contagem = repo.categorias_cobertas()

    achados = []
    if categoria and categoria in todas_as_categorias():
        achados = sorted(repo.por_categoria(categoria), key=lambda p: p.preco)

    return render_template(
        "categorias.html",
        contagem=contagem,
        nome_categoria=nome_da_categoria,
        categoria=categoria,
        nome_atual=nome_da_categoria(categoria) if categoria else None,
        achados=achados,
    )


@app.route("/cesta")
def cesta():
    """Detalhe do indicador oficial: matriz de preços e estatística por item."""
    repo = repositorio()
    matriz = matriz_precos(repo.precos_da_cesta)

    itens = []
    total_cesta = 0.0
    itens_com_preco = 0

    for chave_item in CESTA_BASICA:
        dados = estatisticas_do_item(repo.precos, chave_item)
        quantidade = CESTA_BASICA[chave_item][1]
        dados["quantidade"] = quantidade
        dados["unidade"] = CESTA_BASICA[chave_item][2]

        # Subtotal pelo preço da coleta mais recente: é o que o consumidor
        # pagaria hoje, e não uma média que arrasta valores antigos.
        if dados["n"] > 0:
            dados["subtotal"] = round(dados["preco_recente"] * quantidade, 2)
            dados["subtotal_minimo"] = round(dados["minimo"] * quantidade, 2)
            total_cesta += dados["subtotal"]
            itens_com_preco += 1
        else:
            dados["subtotal"] = None
            dados["subtotal_minimo"] = None

        itens.append(dados)

    total_minimo = sum(i["subtotal_minimo"] or 0 for i in itens)

    return render_template(
        "cesta.html",
        itens=itens,
        total_cesta=round(total_cesta, 2),
        total_minimo=round(total_minimo, 2),
        itens_com_preco=itens_com_preco,
        total_itens_cesta=len(CESTA_BASICA),
        matriz=matriz,
        estabelecimentos=repo.estabelecimentos(),
        nome_item=nome_do_item,
    )


# --------------------------------------------------------------------------
# Mapa (OpenStreetMap)
# --------------------------------------------------------------------------

@app.route("/")
@app.route("/mapa")
def inicio():
    """Mapa dos mercados da região, destacando os que já têm preços."""
    cache = CacheMapa(MAPA)
    repo = repositorio()

    # O casamento entre o nome do cupom e o do mapa é feito aqui, e não no
    # arquivo: assim o dado embarcado continua válido conforme a base cresce.
    casar_com_estabelecimentos(cache.locais, repo.estabelecimentos())

    # Duas fontes: os mercados do OpenStreetMap e os que vieram dos cupons.
    # O OSM não conhece todo supermercado brasileiro, então o endereço da nota
    # completa o mapa.
    todos = mesclar(cache.locais, locais_dos_estabelecimentos(repo))

    # Valores por mercado, para mostrar de forma discreta no mapa e no cartão.
    # cobertura_minima=0.0 para incluir mesmo quem tem poucos itens da cesta.
    custos = {c.cnpj: c for c in montar_ranking(repo.precos, cobertura_minima=0.0).estabelecimentos}
    precos_por_cnpj: dict[str, int] = {}
    for p in repo.precos:
        precos_por_cnpj[p.cnpj] = precos_por_cnpj.get(p.cnpj, 0) + 1

    def valores(cnpj: str) -> dict:
        c = custos.get(cnpj)
        return {
            "cesta": round(c.custo_total, 2) if c and c.custo_total > 0 else None,
            "itens_cesta": c.itens_encontrados if c else 0,
            "n_precos": precos_por_cnpj.get(cnpj, 0),
        }

    locais = [{
        "nome": local.nome,
        "latitude": local.latitude,
        "longitude": local.longitude,
        "tipo_legivel": local.tipo_legivel,
        "endereco": local.endereco,
        "cnpj": local.cnpj,
        "fonte": local.fonte,
        "precisao": local.precisao,
        **(valores(local.cnpj) if local.cnpj else {}),
    } for local in todos]

    centro = list(cache.centro) if cache.centro else None
    if centro is None and locais:
        centro = [locais[0]["latitude"], locais[0]["longitude"]]
    if centro is None:
        centro = [-15.79, -47.88]   # centro do Brasil, quando não há nada

    return render_template(
        "mapa.html",
        cache=cache,
        total_locais=len(todos),
        com_precos=sum(1 for local in todos if local.cnpj),
        tipos=TIPOS_OSM,
        locais_json=json.dumps(locais, ensure_ascii=False),
        centro_json=json.dumps(centro),
    )


@app.route("/api/viabilidade")
def api_viabilidade():
    """
    Responde "vale a pena ir até lá?" a partir da posição do usuário.

    Recebe a posição por parâmetro e não a armazena em lugar nenhum: ela existe
    apenas durante o cálculo desta requisição.
    """
    try:
        origem = (float(request.args["lat"]), float(request.args["lon"]))
    except (KeyError, ValueError):
        return jsonify({"erro": "Informe lat e lon."}), 400

    try:
        consumo = float(request.args.get("consumo", CONSUMO_PADRAO_KM_L))
        preco = float(request.args.get("preco", PRECO_COMBUSTIVEL_PADRAO))
    except ValueError:
        return jsonify({"erro": "Consumo e preço devem ser números."}), 400
    if consumo <= 0:
        return jsonify({"erro": "O consumo deve ser maior que zero."}), 400

    repo = repositorio()
    custos = {c.cnpj: c for c in montar_ranking(repo.precos, cobertura_minima=0.0).estabelecimentos}
    registro = repo.registro_estabelecimentos

    candidatos = [
        (e.nome, cnpj, custos[cnpj].custo_total, (e.latitude, e.longitude))
        for cnpj, e in registro.items()
        if e.localizado and cnpj in custos and custos[cnpj].custo_total > 0
    ]

    if not candidatos:
        return jsonify({
            "resultados": [],
            "aviso": "Ainda não há mercado com preços suficientes para comparar.",
        })

    # Roteamento real só até 8 mercados, para não abusar do serviço gratuito.
    avaliacoes = avaliar(origem, candidatos, consumo, preco, usar_ruas=len(candidatos) <= 8)

    resultados = [{
        "nome": v.nome,
        "cnpj": v.cnpj,
        "custo_compra": v.custo_compra,
        "distancia_km": v.distancia_km,
        "duracao_min": v.trajeto.duracao_min,
        "custo_combustivel": v.custo_combustivel,
        "custo_total": v.custo_total,
        "rota_real": v.trajeto.metodo == "ruas",
    } for v in avaliacoes]

    mais_perto = min(avaliacoes, key=lambda v: v.distancia_km)
    melhor = avaliacoes[0]
    comparacao = None
    if melhor.cnpj != mais_perto.cnpj:
        comparacao = compensa_ir(mais_perto, melhor)
        comparacao["mais_perto"] = mais_perto.nome
        comparacao["alternativa"] = melhor.nome

    return jsonify({"resultados": resultados, "comparacao": comparacao})


@app.route("/api/rota")
def api_rota():
    """
    Traça a rota de carro entre dois pontos.

    Devolve os pontos do caminho para desenhar no mapa, além de distância,
    tempo e custo em combustível. A origem vem do aparelho e não é guardada.
    """
    try:
        origem = (float(request.args["de_lat"]), float(request.args["de_lon"]))
        destino = (float(request.args["para_lat"]), float(request.args["para_lon"]))
    except (KeyError, ValueError):
        return jsonify({"erro": "Informe de_lat, de_lon, para_lat e para_lon."}), 400

    try:
        consumo = float(request.args.get("consumo", CONSUMO_PADRAO_KM_L))
        preco = float(request.args.get("preco", PRECO_COMBUSTIVEL_PADRAO))
    except ValueError:
        return jsonify({"erro": "Consumo e preço devem ser números."}), 400
    if consumo <= 0:
        return jsonify({"erro": "O consumo deve ser maior que zero."}), 400

    trajeto = calcular_trajeto(origem, destino, com_desenho=True)

    return jsonify({
        "distancia_km": trajeto.distancia_km,
        "duracao_min": trajeto.duracao_min,
        "ida_e_volta_km": trajeto.ida_e_volta_km,
        "custo_combustivel": custo_do_trajeto(trajeto.distancia_km, consumo, preco),
        "rota_real": trajeto.metodo == "ruas",
        "pontos": trajeto.geometria or [],
    })


@app.route("/mapa/importar", methods=["POST"])
def importar_mapa():
    """Baixa os mercados de uma região do OpenStreetMap."""
    area = (request.form.get("area") or "").strip()
    if not area:
        flash("Informe a região a importar.", "erro")
        return redirect(url_for("inicio"))

    try:
        cache = importar_area(area, MAPA, estabelecimentos=repositorio().estabelecimentos())
    except MapaError as erro:
        flash(str(erro), "erro")
        return redirect(url_for("inicio"))

    casados = sum(1 for local in cache.locais if local.cnpj)
    flash(
        f"{len(cache.locais)} mercados importados de {area}. "
        f"{casados} já têm preços na base.",
        "sucesso",
    )
    return redirect(url_for("inicio"))


# --------------------------------------------------------------------------
# PWA
# --------------------------------------------------------------------------

@app.route("/mapa/localizar", methods=["POST"])
def localizar_mercados():
    """Geocodifica os mercados da base que ainda não estão no mapa."""
    repo = repositorio()
    pendentes = len(repo.estabelecimentos_sem_local())

    if not pendentes:
        flash("Todos os mercados com preços já estão localizados.", "aviso")
        return redirect(url_for("inicio"))

    try:
        localizados = localizar_estabelecimentos(repo)
    except MapaError as erro:
        flash(str(erro), "erro")
        return redirect(url_for("inicio"))

    if localizados:
        flash(f"{localizados} de {pendentes} mercados localizados no mapa.", "sucesso")
    else:
        flash(
            "Nenhum endereço foi reconhecido pelo OpenStreetMap. "
            "Endereços de cupom costumam vir abreviados.",
            "aviso",
        )
    return redirect(url_for("inicio"))


@app.route("/sw.js")
def serviceworker():
    """
    Servido da raiz de propósito: um service worker só controla páginas que
    estejam no seu diretório ou abaixo dele. Em /static/sw.js ele controlaria
    apenas /static/.
    """
    resposta = send_from_directory(app.static_folder, "sw.js")
    resposta.headers["Content-Type"] = "application/javascript"
    resposta.headers["Service-Worker-Allowed"] = "/"
    resposta.headers["Cache-Control"] = "no-cache"
    return resposta


@app.route("/offline")
def offline():
    """Tela mostrada quando o aparelho está sem internet e a página não está em cache."""
    return render_template("offline.html")


@app.errorhandler(413)
def arquivo_grande(_erro):
    flash("A foto passou de 16 MB. Tente uma imagem menor.", "erro")
    return redirect(url_for("enviar"))


if __name__ == "__main__":
    # Em produção quem serve é o gunicorn (ver Procfile); isto é só o modo local.
    porta = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=porta, debug=debug)
