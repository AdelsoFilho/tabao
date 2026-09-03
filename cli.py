"""
TáBão — interface de linha de comando do MVP.

Uso:

    python cli.py cupom <url-do-qrcode>     processa um cupom pela URL do QR
    python cli.py foto <caminho-da-imagem>  lê o QR de uma foto e processa
    python cli.py arquivo <caminho.html>    processa uma página já baixada
    python cli.py chave <44-digitos>        valida e explica uma chave
    python cli.py ranking                   onde a cesta sai mais barata
    python cli.py matriz                    matriz produto x estabelecimento
    python cli.py item <nome>               estatísticas de um item da cesta
    python cli.py produto <texto>           compara qualquer produto entre mercados
    python cli.py categorias                o que já foi coletado, por categoria
    python cli.py categoria <nome>          produtos de uma categoria
    python cli.py base                      resumo da base coletada
    python cli.py mapear <regiao>           importa mercados do OpenStreetMap
    python cli.py localizar                 geocodifica os mercados da base
    python cli.py demo                      carrega dados de exemplo (fictícios)
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

from tabao import chave as mod_chave
from tabao import sefaz
from tabao.cesta import estatisticas_do_item, formatar_ranking, montar_ranking
from tabao.modelos import PrecoObservado
from tabao.categorias import (CATEGORIAS, categorizar, nome_da_categoria,
                              todas_as_categorias)
from tabao.estatistica import DadosInsuficientesError, resumir
from tabao.produtos import CESTA_BASICA, classificar, nome_do_item
from tabao.qrcode_nfce import (
    QRCodeInvalidoError,
    interpretar_url,
    ler_de_imagem,
)
from tabao.mapa import (CacheMapa, MapaError, TIPOS_OSM, importar_area,
                        localizar_estabelecimentos)
from tabao.banco import criar_repositorio
from tabao.repositorio import Repositorio, imprimir_matriz, matriz_precos

BASE = Path("dados/precos.json")
MAPA = Path("dados/mapa.json")


def _titulo(texto: str) -> None:
    print()
    print(texto)
    print("=" * len(texto))


def _mostrar_cupom(cupom, registrados: int) -> None:
    _titulo("Cupom processado")
    print(f"Estabelecimento : {cupom.estabelecimento.nome}")
    print(f"CNPJ            : {cupom.estabelecimento.cnpj_formatado}")
    print(f"Emissão         : {cupom.emitido_em:%d/%m/%Y %H:%M}")
    print(f"Itens no cupom  : {len(cupom.itens)}")
    print(f"Total           : R$ {cupom.total_calculado:.2f}")

    print()
    print("Confira os itens lidos:")
    print(f"  {'Descrição':<42}{'Qtd':>8}{'Total':>10}  {'Categoria':<20}Cesta")
    print("  " + "-" * 90)
    for item in cupom.itens:
        c = classificar(item.descricao)
        marca = nome_do_item(c.item) if c.pertence_a_cesta else ""
        categoria = nome_da_categoria(categorizar(item.descricao))
        print(
            f"  {item.descricao[:41]:<42}{item.quantidade:>8.3f}"
            f"{item.valor_total:>10.2f}  {categoria:<20}{marca}"
        )

    print()
    da_cesta = sum(1 for i in cupom.itens if classificar(i.descricao).pertence_a_cesta)
    print(f"{registrados} produto(s) registrado(s) na base — {da_cesta} da cesta básica.")


def comando_cupom(url: str) -> int:
    try:
        qr = interpretar_url(url)
    except QRCodeInvalidoError as erro:
        print(f"QR Code inválido: {erro}")
        return 1

    print(f"Chave {qr.chave} ({qr.uf}) validada. Consultando a SEFAZ...")

    repo = criar_repositorio(BASE)
    if repo.ja_processado(qr.chave):
        print("Este cupom já está na base. Nada a fazer.")
        return 0

    try:
        cupom = sefaz.consultar(qr.url_consulta, chave=qr.chave)
    except sefaz.ConsultaSEFAZError as erro:
        print(f"Não foi possível obter a nota: {erro}")
        return 1

    registrados = repo.registrar_cupom(cupom)
    repo.salvar()
    _mostrar_cupom(cupom, registrados)
    return 0


def comando_foto(caminho: str) -> int:
    try:
        url = ler_de_imagem(caminho)
    except QRCodeInvalidoError as erro:
        print(f"Falha ao ler o QR Code: {erro}")
        return 1

    print(f"QR Code lido: {url}")
    return comando_cupom(url)


def comando_arquivo(caminho: str) -> int:
    """Processa uma página da SEFAZ salva em disco. Útil para testes offline."""
    arquivo = Path(caminho)
    if not arquivo.exists():
        print(f"Arquivo não encontrado: {caminho}")
        return 1

    try:
        cupom = sefaz.extrair(arquivo.read_text(encoding="utf-8"))
    except sefaz.ConsultaSEFAZError as erro:
        print(f"Não foi possível extrair a nota: {erro}")
        return 1

    repo = criar_repositorio(BASE)
    registrados = repo.registrar_cupom(cupom)
    repo.salvar()
    _mostrar_cupom(cupom, registrados)
    return 0


def comando_chave(valor: str) -> int:
    try:
        dados = mod_chave.interpretar(valor)
    except mod_chave.ChaveInvalidaError as erro:
        print(f"Chave inválida: {erro}")
        return 1

    _titulo("Chave de acesso válida")
    print(f"Chave     : {dados.chave}")
    print(f"UF        : {dados.uf}")
    print(f"Emissão   : {dados.mes:02d}/{dados.ano}")
    print(f"CNPJ      : {dados.cnpj_formatado}")
    print(f"Modelo    : {dados.modelo} ({'NFC-e' if dados.e_nfce else 'outro'})")
    print(f"Série     : {dados.serie}")
    print(f"Número    : {dados.numero}")
    return 0


def comando_ranking() -> int:
    repo = criar_repositorio(BASE)
    if not len(repo):
        print("A base está vazia. Rode: python cli.py demo")
        return 1

    _titulo("Onde a cesta básica sai mais barata")
    print(formatar_ranking(montar_ranking(repo.precos)))
    return 0


def comando_matriz() -> int:
    repo = criar_repositorio(BASE)
    if not len(repo):
        print("A base está vazia. Rode: python cli.py demo")
        return 1

    _titulo("Matriz produto x estabelecimento (R$ por unidade padrão)")
    print(imprimir_matriz(matriz_precos(repo.precos), repo.estabelecimentos()))
    return 0


def comando_item(nome: str) -> int:
    repo = criar_repositorio(BASE)
    chave_item = nome.strip().lower()

    if chave_item not in CESTA_BASICA:
        print(f"Item desconhecido: {nome}")
        print("Itens válidos: " + ", ".join(CESTA_BASICA))
        return 1

    dados = estatisticas_do_item(repo.precos, chave_item)
    if dados["n"] == 0:
        print(f"Ainda não há preços de {nome_do_item(chave_item)} na base.")
        return 1

    _titulo(f"{dados['nome']} — {dados['n']} observação(ões)")
    print(f"Média          : R$ {dados['media']:.2f}")
    print(f"Mediana        : R$ {dados['mediana']:.2f}")
    print(f"Mínimo         : R$ {dados['minimo']:.2f}  ({dados['onde_mais_barato']})")
    print(f"Máximo         : R$ {dados['maximo']:.2f}  ({dados['onde_mais_caro']})")
    print(f"Desvio padrão  : R$ {dados['desvio_padrao']:.2f}")
    print(f"Coef. variação : {dados['coeficiente_variacao']:.1f}%")
    print(f"Variação       : {dados['variacao_percentual']:.1f}% entre o menor e o maior")
    return 0


def comando_produto(termo: str) -> int:
    """Compara o preço de qualquer produto entre os estabelecimentos da base."""
    repo = criar_repositorio(BASE)
    achados = repo.buscar_produto(termo)

    if not achados:
        print(f"Nenhum produto encontrado com '{termo}'.")
        print("Dica: use parte do nome, como 'bacon' ou 'queijo'.")
        return 1

    _titulo(f"'{termo}' — {len(achados)} observação(ões)")
    print(f"  {'Produto':<40}{'Estabelecimento':<26}{'Preço':>10}  Quando")
    print("  " + "-" * 88)
    for p in sorted(achados, key=lambda o: o.preco):
        print(
            f"  {p.descricao_original[:39]:<40}{p.nome_estabelecimento[:25]:<26}"
            f"{p.preco:>10.2f}  {p.observado_em:%d/%m/%Y}"
        )

    valores = [p.preco for p in achados]
    if len(valores) >= 2:
        try:
            r = resumir(valores)
        except DadosInsuficientesError:
            return 0
        print()
        print(f"  Menor R$ {r.minimo:.2f}   Mediana R$ {r.mediana:.2f}   Maior R$ {r.maximo:.2f}")
        if r.minimo > 0:
            variacao = 100 * (r.maximo - r.minimo) / r.minimo
            print(f"  Variação de {variacao:.1f}% entre o menor e o maior preço.")
    return 0


def comando_categorias() -> int:
    repo = criar_repositorio(BASE)
    contagem = repo.categorias_cobertas()

    if not contagem:
        print("A base está vazia. Rode: python cli.py demo")
        return 1

    _titulo("Produtos coletados por categoria")
    for chave, quantidade in contagem.items():
        print(f"  {nome_da_categoria(chave):<24}{quantidade:>5} observação(ões)")
    print()
    print(f"  {'TOTAL':<24}{len(repo):>5}")
    return 0


def comando_categoria(nome: str) -> int:
    repo = criar_repositorio(BASE)
    chave = nome.strip().lower()

    if chave not in todas_as_categorias():
        print(f"Categoria desconhecida: {nome}")
        print("Categorias: " + ", ".join(todas_as_categorias()))
        return 1

    achados = repo.por_categoria(chave)
    if not achados:
        print(f"Ainda não há produtos em {nome_da_categoria(chave)}.")
        return 1

    _titulo(f"{nome_da_categoria(chave)} — {len(achados)} observação(ões)")
    print(f"  {'Produto':<44}{'Estabelecimento':<24}{'Preço':>10}")
    print("  " + "-" * 80)
    for p in sorted(achados, key=lambda o: o.descricao_original):
        print(
            f"  {p.descricao_original[:43]:<44}{p.nome_estabelecimento[:23]:<24}"
            f"{p.preco:>10.2f}"
        )
    return 0


def comando_base() -> int:
    repo = criar_repositorio(BASE)
    _titulo("Base coletada")
    print(f"Preços registrados : {len(repo)}  (todos os produtos)")
    print(f"  da cesta básica  : {len(repo.precos_da_cesta)}")
    print(f"Estabelecimentos   : {len(repo.estabelecimentos())}")
    print(f"Categorias         : {len(repo.categorias_cobertas())}")
    print(f"Itens da cesta     : {len(repo.itens_cobertos())} de {len(CESTA_BASICA)}")

    if repo.itens_cobertos():
        print()
        for item in sorted(repo.itens_cobertos()):
            print(f"  {nome_do_item(item):<18} {len(repo.por_item(item))} observação(ões)")
    return 0


def comando_mapear(area: str) -> int:
    """Importa do OpenStreetMap os mercados de uma região."""
    print(f"Consultando o OpenStreetMap para '{area}'...")
    print("(as APIs são gratuitas e limitam requisições; pode levar alguns segundos)")

    try:
        cache = importar_area(area, MAPA, estabelecimentos=criar_repositorio(BASE).estabelecimentos())
    except MapaError as erro:
        print(f"Falhou: {erro}")
        return 1

    casados = sum(1 for local in cache.locais if local.cnpj)

    _titulo(f"{len(cache.locais)} mercados importados de {area}")
    for tipo, quantidade in cache.por_tipo().items():
        print(f"  {TIPOS_OSM.get(tipo, tipo):<16}{quantidade:>5}")
    print()
    print(f"  {casados} já têm preços na base.")
    print(f"  Salvo em {MAPA}")
    return 0


def comando_localizar() -> int:
    """Geocodifica os endereços dos mercados que têm preços na base."""
    repo = criar_repositorio(BASE)
    pendentes = repo.estabelecimentos_sem_local()

    if not pendentes:
        print("Todos os mercados com preços já estão localizados.")
        return 0

    print(f"Geocodificando {len(pendentes)} endereço(s) no Nominatim...")
    for e in pendentes:
        print(f"  {e.nome[:40]:<42} {e.endereco[:44]}")

    try:
        localizados = localizar_estabelecimentos(repo)
    except MapaError as erro:
        print(f"Falhou: {erro}")
        return 1

    print()
    print(f"{localizados} de {len(pendentes)} localizados.")
    for e in repo.estabelecimentos_localizados():
        print(f"  {e.nome[:40]:<42} {e.latitude:.5f}, {e.longitude:.5f}")
    return 0


def comando_demo() -> int:
    """
    Carrega preços de exemplo para demonstrar o ranking.

    ATENÇÃO: dados fictícios, gerados apenas para a demonstração. Servem para
    exercitar o cálculo enquanto a base real ainda está sendo formada.
    """
    mercados = [
        ("11111111000101", "Supermercado Economia"),
        ("22222222000102", "Atacadão do Bairro"),
        ("33333333000103", "Mercado Central"),
    ]
    # Preço por unidade padrão de cada item em cada mercado.
    tabela = {
        "arroz":    [5.49, 5.99, 7.20],
        "feijao":   [7.90, 8.49, 9.80],
        "carne":    [32.90, 35.50, 41.90],
        "leite":    [4.29, 4.59, 5.49],
        "pao":      [12.90, 13.50, 15.90],
        "cafe":     [29.90, 32.90, 38.50],
        "acucar":   [4.19, 4.49, 5.29],
        "oleo":     [7.49, 7.99, 9.20],
        "banana":   [4.99, 5.49, 6.90],
        "tomate":   [8.90, 9.90, 11.89],
        "batata":   [5.49, 5.99, 7.49],
        "farinha":  [4.99, 5.49, 6.49],
        "manteiga": [38.90, 42.50, 49.90],
    }

    repo = criar_repositorio(BASE)
    agora = datetime.now()
    novos = 0

    for item, precos in tabela.items():
        for posicao, (cnpj, nome) in enumerate(mercados):
            repo._precos.append(  # noqa: SLF001 - carga direta de demonstração
                PrecoObservado(
                    descricao_original=f"{nome_do_item(item).upper()} (demonstração)",
                    item_cesta=item,
                    categoria=categorizar(nome_do_item(item)),
                    cnpj=cnpj,
                    nome_estabelecimento=nome,
                    preco=precos[posicao],
                    unidade="un. padrão",
                    observado_em=agora - timedelta(days=posicao),
                    chave_cupom=f"DEMO-{item}-{cnpj}",
                )
            )
            novos += 1

    repo.salvar()
    print(f"{novos} preços de demonstração carregados em {BASE}.")
    print("Agora rode: python cli.py ranking")
    return 0


COMANDOS_COM_ARGUMENTO = {
    "cupom": comando_cupom,
    "foto": comando_foto,
    "arquivo": comando_arquivo,
    "chave": comando_chave,
    "item": comando_item,
    "produto": comando_produto,
    "categoria": comando_categoria,
    "mapear": comando_mapear,
}
COMANDOS_SIMPLES = {
    "ranking": comando_ranking,
    "matriz": comando_matriz,
    "base": comando_base,
    "demo": comando_demo,
    "categorias": comando_categorias,
    "localizar": comando_localizar,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help", "ajuda"):
        print(__doc__)
        return 0

    comando = argv[1]

    if comando in COMANDOS_SIMPLES:
        return COMANDOS_SIMPLES[comando]()

    if comando in COMANDOS_COM_ARGUMENTO:
        if len(argv) < 3:
            print(f"O comando '{comando}' precisa de um argumento.")
            print(__doc__)
            return 1
        return COMANDOS_COM_ARGUMENTO[comando](argv[2])

    print(f"Comando desconhecido: {comando}")
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
