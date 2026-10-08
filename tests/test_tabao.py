"""
Testes do TáBão.

Os testes de chave e de extração usam dados de um cupom fiscal real
(CEMA CENTRAL MINEIRA ATACADISTA, 15/05/2026), o que garante que o pipeline
funciona com o formato que a SEFAZ realmente emite, e não com um exemplo
inventado.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tabao import chave as mod_chave
from tabao import estatistica as est
from tabao import categorias as cat
from tabao import produtos as prod
from tabao import sefaz
from tabao.cesta import montar_ranking
from tabao.modelos import PrecoObservado
from tabao.qrcode_nfce import QRCodeInvalidoError, interpretar_url
from tabao.repositorio import FilaProcessamento, Repositorio, matriz_precos

# Cupom real usado como referência em todo o arquivo.
CHAVE_REAL = "52260503083231004191652170000092211671742645"
FIXTURE = Path(__file__).parent / "fixtures" / "cupom_cema_15-05-2026.html"
# Página realmente devolvida pelo portal da SEFAZ-GO para esta nota.
FIXTURE_REAL = Path(__file__).parent / "fixtures" / "danfe_real.html"
ENVELOPE_REAL = Path(__file__).parent / "fixtures" / "real_html.html"


# --------------------------------------------------------------------------
# Chave de acesso
# --------------------------------------------------------------------------

def test_chave_real_e_valida():
    assert mod_chave.chave_e_valida(CHAVE_REAL)


def test_chave_real_aceita_formatacao_com_espacos():
    formatada = "5226 0503 0832 3100 4191 6521 7000 0092 2116 7174 2645"
    assert mod_chave.chave_e_valida(formatada)


def test_campos_da_chave_batem_com_o_cupom_impresso():
    dados = mod_chave.interpretar(CHAVE_REAL)
    assert dados.uf == "GO"
    assert dados.ano == 2026 and dados.mes == 5
    assert dados.cnpj_formatado == "03.083.231/0041-91"
    assert dados.e_nfce
    assert dados.serie == "217"
    assert dados.numero == "000009221"


def test_chave_com_digito_trocado_e_rejeitada():
    adulterada = CHAVE_REAL[:43] + ("4" if CHAVE_REAL[43] != "4" else "3")
    assert not mod_chave.chave_e_valida(adulterada)


def test_chave_curta_levanta_erro():
    with pytest.raises(mod_chave.ChaveInvalidaError):
        mod_chave.interpretar("123")


# --------------------------------------------------------------------------
# QR Code
# --------------------------------------------------------------------------

def test_interpreta_url_de_qrcode_em_producao():
    url = (
        "https://nfeweb.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe"
        f"?p={CHAVE_REAL}|2|1|000001|A1B2C3"
    )
    qr = interpretar_url(url)
    assert qr.chave == CHAVE_REAL
    assert qr.uf == "GO"
    assert qr.e_producao
    assert not qr.em_contingencia


def test_qrcode_em_contingencia_e_reconhecido():
    url = (
        "https://nfeweb.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe"
        f"?p={CHAVE_REAL}|2|1|15|365.31|abc|000001|A1B2C3"
    )
    assert interpretar_url(url).em_contingencia


def test_url_sem_parametro_p_e_rejeitada():
    with pytest.raises(QRCodeInvalidoError):
        interpretar_url("https://nfeweb.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe")


# --------------------------------------------------------------------------
# Origem do QR Code
#
# A URL vem de quem envia o cupom. Se qualquer endereço fosse aceito, bastaria
# hospedar um DANFE forjado para injetar preços inventados na base com o selo
# de "conferido na SEFAZ" — o que anularia a própria premissa do projeto.
# --------------------------------------------------------------------------

def test_qrcode_apontando_para_dominio_qualquer_e_rejeitado():
    url = f"https://sefaz-goias.exemplo.com/nfeweb/sites/nfce/danfeNFCe?p={CHAVE_REAL}|2|1"
    with pytest.raises(QRCodeInvalidoError, match="não é o portal da SEFAZ"):
        interpretar_url(url)


def test_dominio_que_apenas_termina_parecido_e_rejeitado():
    """nfeweb.sefaz.go.gov.br.exemplo.com pertence a exemplo.com, não à SEFAZ."""
    url = f"https://nfeweb.sefaz.go.gov.br.exemplo.com/?p={CHAVE_REAL}|2|1"
    with pytest.raises(QRCodeInvalidoError, match="não é o portal da SEFAZ"):
        interpretar_url(url)


def test_mesmo_dominio_em_http_e_rejeitado():
    """Rebaixar para HTTP permitiria interceptar a consulta no caminho."""
    url = f"http://nfeweb.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe?p={CHAVE_REAL}|2|1"
    with pytest.raises(QRCodeInvalidoError, match="não é o portal da SEFAZ"):
        interpretar_url(url)


def test_portal_de_homologacao_continua_aceito():
    url = (
        "https://nfewebhomolog.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe"
        f"?p={CHAVE_REAL}|2|2"
    )
    assert interpretar_url(url).chave == CHAVE_REAL


def test_portal_legado_http_continua_aceito():
    """
    Os QR Codes impressos apontam para o portal legado nfe.sefaz.go.gov.br,
    que segue no ar e só atende por HTTP. Ele é oficial da SEFAZ-GO, então
    precisa ser aceito — do contrário nenhum cupom real de Goiás é lido.
    """
    url = (
        "http://nfe.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe"
        f"?p={CHAVE_REAL}|2|1"
    )
    assert interpretar_url(url).chave == CHAVE_REAL


def test_consulta_recusa_origem_nao_oficial_sem_tocar_na_rede(monkeypatch):
    """A trava precisa agir antes da requisição, não depois."""
    import requests

    def nao_deveria_chamar(*args, **kwargs):
        raise AssertionError("a consulta não pode sair para a rede")

    monkeypatch.setattr(requests, "Session", nao_deveria_chamar)

    url = f"https://servidor-do-atacante.exemplo/?p={CHAVE_REAL}|2|1"
    with pytest.raises(sefaz.ConsultaSEFAZError, match="não é o portal da SEFAZ"):
        sefaz.consultar_por_qrcode(url, CHAVE_REAL)


# --------------------------------------------------------------------------
# Chave de assinatura do cupom
#
# A rota /confirmar grava o que vier dentro do token assinado, sem reconsultar
# a SEFAZ. Uma chave fixa e publicada junto do código deixaria qualquer pessoa
# forjar um cupom e gravá-lo sem passar por um QR Code.
# --------------------------------------------------------------------------

def _limpar_hospedagem(monkeypatch):
    import app as aplicacao

    for nome in aplicacao.MARCAS_DE_HOSPEDAGEM:
        monkeypatch.delenv(nome, raising=False)
    return aplicacao


def test_chave_do_ambiente_e_usada_como_esta(monkeypatch):
    aplicacao = _limpar_hospedagem(monkeypatch)
    monkeypatch.setenv("SECRET_KEY", "chave-vinda-do-painel")
    assert aplicacao._chave_secreta() == "chave-vinda-do-painel"


def test_publicado_sem_chave_o_app_se_recusa_a_subir(monkeypatch):
    aplicacao = _limpar_hospedagem(monkeypatch)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        aplicacao._chave_secreta()


def test_fora_de_hospedagem_sorteia_chave_diferente_a_cada_execucao(monkeypatch):
    aplicacao = _limpar_hospedagem(monkeypatch)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    primeira = aplicacao._chave_secreta()
    segunda = aplicacao._chave_secreta()

    assert primeira != segunda
    assert len(primeira) >= 32
    assert "tabao-desenvolvimento" not in (primeira, segunda)


# --------------------------------------------------------------------------
# Extração da página da SEFAZ
# --------------------------------------------------------------------------

@pytest.fixture
def cupom():
    return sefaz.extrair(FIXTURE.read_text(encoding="utf-8"), chave=CHAVE_REAL)


def test_extrai_os_treze_itens_do_cupom(cupom):
    assert len(cupom.itens) == 13


def test_total_extraido_bate_com_o_impresso(cupom):
    # O cupom impresso traz "Valor a Pagar R$ 365,31".
    assert cupom.total_calculado == pytest.approx(365.31, abs=0.02)


def test_extrai_estabelecimento(cupom):
    assert cupom.estabelecimento.cnpj == "03083231004191"
    assert "CEMA" in cupom.estabelecimento.nome


def test_extrai_item_com_quantidade_fracionada(cupom):
    tomate = next(i for i in cupom.itens if "TOMATE" in i.descricao)
    assert tomate.quantidade == pytest.approx(2.370)
    assert tomate.valor_total == pytest.approx(28.18)
    assert tomate.preco_por_unidade == pytest.approx(11.89, abs=0.01)


def test_html_vazio_levanta_erro():
    with pytest.raises(sefaz.ConsultaSEFAZError):
        sefaz.extrair("")


def test_pagina_sem_itens_levanta_erro():
    with pytest.raises(sefaz.ConsultaSEFAZError):
        sefaz.extrair("<html><body><p>Sessão expirada</p></body></html>")


# --------------------------------------------------------------------------
# Normalização e classificação
# --------------------------------------------------------------------------

def test_normalizar_remove_acento_e_padroniza():
    assert prod.normalizar("Açúcar Cristal 1kg") == "ACUCAR CRISTAL 1KG"


@pytest.mark.parametrize("descricao,esperado", [
    ("ARROZ TP1 TIO JOAO 5KG", "arroz"),
    ("FEIJAO CARIOCA KICALDO 1KG", "feijao"),
    ("TOMATE ANDREA kg", "tomate"),
    ("ACUCAR CRISTAL UNIAO 1KG", "acucar"),
    ("OLEO DE SOJA LIZA 900ML", "oleo"),
])
def test_classifica_itens_da_cesta(descricao, esperado):
    assert prod.classificar(descricao).item == esperado


@pytest.mark.parametrize("descricao", [
    # Casos reais tirados do cupom da CEMA: nenhum pertence à cesta básica.
    "MEIO ASA FGO RESF kg",
    "COXA S COXA FGO CONG QUALITTI kg",
    "LING CHUAR FGO SUP FGO 800G QUEIJO",
    "KETCHUP HEMMER FR 320G TRAD",
    "CARVAO VEGETAL FIAT LUX PC 4kg",
    "MANDIOCA CONG SO MANDIOCA PC 800G VACUO",
    # Armadilhas clássicas de normalização.
    "LEITE DE COCO SOCOCO 200ML",
    "BATATA PALHA ELMA CHIPS 140G",
    "EXTRATO DE TOMATE QUERO 340G",
    "FARINHA DE MANDIOCA TORRADA 1KG",
])
def test_nao_classifica_o_que_nao_e_da_cesta(descricao):
    assert prod.classificar(descricao).item is None


def test_batata_pre_frita_nao_conta_como_batata():
    # Descrição exata da página da SEFAZ: "CONG" = congelada, R$ 25,99 por 2 kg.
    assert prod.classificar("BATATA CONG UAI BATATA PC 2kg TRAD").item is None


def test_batata_in_natura_continua_na_cesta():
    assert prod.classificar("BATATA INGLESA LAVADA KG").item == "batata"


def test_extrai_tamanho_da_embalagem():
    assert prod.extrair_embalagem("ARROZ TP1 5KG") == (5.0, "kg")
    assert prod.extrair_embalagem("OLEO LIZA 900ML") == (0.9, "L")
    assert prod.extrair_embalagem("TOMATE ANDREA kg") is None


def test_preco_por_quilo_torna_embalagens_comparaveis():
    pacote_grande = prod.preco_por_unidade_padrao("ARROZ TP1 5KG", 25.00)
    pacote_pequeno = prod.preco_por_unidade_padrao("ARROZ TP1 1KG", 6.00)
    assert pacote_grande == pytest.approx(5.00)
    assert pacote_pequeno == pytest.approx(6.00)
    assert pacote_grande < pacote_pequeno


# --------------------------------------------------------------------------
# Classificação: casos reais da base em produção
#
# Descrições exatas coletadas em produção (rede VMS Supermercados e outras,
# diferentes da rede do cupom de teste). Antes da correção de fronteira de
# palavra, todas as cinco primeiras eram classificadas erradas e infladas o
# custo da cesta publicado no app.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("descricao", [
    "PIPOCA MICROONDAS SINHA 90GR MANTEIGA CINE",  # sabor manteiga, é pipoca
    "PAO ALH CUISI 300g UN",                       # pão de alho, não francês
    "PAO ALHO ZINHO 300 UN",
    "OLEO ESSEN CIT 120ML",                        # óleo essencial, não de soja
    "OLEO ESSEN EUC 120ML",
    "NESCAFE DOLCE GUSTO MOCHA 216GR",             # CAFE no meio de NESCAFE
    "CAFE COM LEITE GRAO",                         # café com leite, não leite
    "MAC ARROZ ESPAGUETE",                         # macarrão, não arroz
    "PAO DE QJO FORNEART TRAD 400G",               # pão de queijo
    "PAO DE FORMA TRADICI",                        # pão de forma
])
def test_nao_confunde_sabor_e_qualificador_com_produto(descricao):
    assert prod.classificar(descricao).item is None


@pytest.mark.parametrize("descricao,esperado", [
    # Abreviações que o cupom fiscal grava e que antes ficavam invisíveis.
    ("MANT S SAL CARREFOUR", "manteiga"),
    ("FEIJ CAR KICALDO 1kg", "feijao"),
    ("ARR TIO JORGE T1 5kg", "arroz"),
    ("ACUC UNIAO REFINADO", "acucar"),
])
def test_reconhece_abreviacoes_reais_do_cupom(descricao, esperado):
    assert prod.classificar(descricao).item == esperado


def test_abreviacao_curta_so_vale_como_palavra_inteira():
    # "MANT" é manteiga, mas "MANTA" (corte de bacon) não pode virar manteiga.
    assert prod.classificar("MANT S SAL").item == "manteiga"
    assert prod.classificar("BACON MANTA AURORA PED kg").item is None


def test_chave_mais_a_esquerda_vence():
    # "PAO LEITE": as duas chaves aparecem, mas a cabeça é pão.
    assert prod.classificar("PAO LEITE SEVEN BOYS").item == "pao"


def test_codigo_de_oferta_nao_e_lido_como_peso():
    # "OF3" é código de oferta; sem a correção, virava embalagem de 3 kg e o
    # preço do tomate saía dividido por três.
    assert prod.extrair_embalagem("TOMATE SALADET OF3 KG") is None
    # Pesos de verdade continuam sendo lidos.
    assert prod.extrair_embalagem("ARROZ CRISTAL 5KG") == (5.0, "kg")
    assert prod.extrair_embalagem("MANDIOCA PC 800G VACUO") == (0.8, "kg")


# --------------------------------------------------------------------------
# Unidade de medida
#
# O HTML de alguns emissores (Atacadão Costa, cupom real) traz a unidade com um
# número colado — "KG1", "UN1" — que aparecia grudado na tela.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bruto,esperado", [
    ("KG1", "KG"),
    ("UN1", "UN"),
    ("KG", "KG"),
    ("UN", "UN"),
    ("VD", "VD"),
    ("UN: KG", "KG"),      # rótulo colado
    ("UN: KG1", "KG"),     # rótulo e código, juntos
    ("", "UN"),            # vazio assume unidade
])
def test_normaliza_unidade_sem_lixo(bruto, esperado):
    assert sefaz._normalizar_unidade(bruto) == esperado


def test_extracao_do_cupom_real_nao_traz_unidade_com_numero():
    cupom = sefaz.extrair(FIXTURE_REAL.read_text(encoding="utf-8"), chave=CHAVE_REAL)
    assert all(not u.unidade[-1:].isdigit() for u in cupom.itens)


@pytest.mark.parametrize("valor,esperado", [
    (1, "1"),            # uma unidade não pode virar "1,000" (mil)
    (4, "4"),
    (1.406, "1,406"),    # peso em quilos, vírgula decimal
    (0.988, "0,988"),
    (1.036, "1,036"),
])
def test_quantidade_no_formato_brasileiro(valor, esperado):
    import app
    assert app.filtro_qtd(valor) == esperado


# --------------------------------------------------------------------------
# Busca de produto
# --------------------------------------------------------------------------

def _repo_com(descricoes, tmp_path):
    """Repositório em memória com uma observação por descrição, já classificada."""
    repo = Repositorio(tmp_path / "vazio.json")
    for i, d in enumerate(descricoes):
        repo._precos.append(PrecoObservado(
            descricao_original=d, cnpj=f"{i:014d}", nome_estabelecimento="Loja",
            preco=9.9, unidade="un", observado_em=datetime(2026, 9, 1),
            chave_cupom=f"k{i}", item_cesta=prod.classificar(d).item,
        ))
    return repo


def test_busca_por_item_da_cesta_ignora_intrusos(tmp_path):
    repo = _repo_com([
        "LEITE UHT ITALAC INT 1L",
        "PAO LEITE SEVEN BOYS",       # pão de leite: não pode entrar
        "CAFE COM LEITE GRAO",        # café: não pode entrar
    ], tmp_path)
    achados = {p.descricao_original for p in repo.buscar_produto("leite")}
    assert achados == {"LEITE UHT ITALAC INT 1L"}


def test_busca_livre_respeita_limite_de_palavra(tmp_path):
    repo = _repo_com([
        "CAFE MOINHO FINO EXTRA FORTE 250G",
        "NESCAFE DOLCE GUSTO MOCHA 216GR",   # CAFE no meio: não pode entrar
    ], tmp_path)
    achados = {p.descricao_original for p in repo.buscar_produto("cafe")}
    assert achados == {"CAFE MOINHO FINO EXTRA FORTE 250G"}


# --------------------------------------------------------------------------
# Estatística
# --------------------------------------------------------------------------

def test_medidas_de_posicao():
    valores = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    assert est.media(valores) == pytest.approx(5.0)
    assert est.mediana(valores) == pytest.approx(4.5)
    assert est.desvio_padrao(valores) == pytest.approx(2.1381, abs=1e-3)


def test_mediana_com_numero_impar_de_valores():
    assert est.mediana([3.0, 1.0, 2.0]) == 2.0


def test_quartis():
    q1, q2, q3 = est.quartis([1, 2, 3, 4, 5])
    assert (q1, q2, q3) == (2.0, 3.0, 4.0)


def test_detecta_preco_absurdo_como_atipico():
    # Preços de tomate plausíveis, mais um erro grosseiro.
    precos = [8.90, 9.50, 9.90, 10.20, 8.75, 9.10, 119.00]
    assert est.e_atipico(119.00, precos)
    assert not est.e_atipico(9.50, precos)


def test_remover_atipicos_preserva_os_normais():
    precos = [8.90, 9.50, 9.90, 10.20, 8.75, 119.00]
    limpos = est.remover_atipicos(precos)
    assert 119.00 not in limpos
    assert len(limpos) == 5


def test_poucos_dados_nao_geram_falso_atipico():
    assert not est.e_atipico(50.0, [10.0, 11.0])


def test_confianca_sobe_com_confirmacoes():
    sozinho = est.confianca_bayesiana(0, 0)
    confirmado = est.confianca_bayesiana(3, 0)
    contestado = est.confianca_bayesiana(0, 3)
    assert confirmado > sozinho > contestado


def test_confianca_cai_com_o_tempo():
    assert est.penalidade_por_idade(0) == 1.0
    assert est.penalidade_por_idade(7) == pytest.approx(0.5)
    assert est.confianca_final(2, 0, 14) < est.confianca_final(2, 0, 0)


# --------------------------------------------------------------------------
# Fila, repositório e ranking
# --------------------------------------------------------------------------

def test_fila_e_fifo_e_deduplica():
    fila = FilaProcessamento()
    assert fila.enfileirar("url1", "chave1")
    assert fila.enfileirar("url2", "chave2")
    assert not fila.enfileirar("url1", "chave1")  # já está na fila
    assert len(fila) == 2
    assert fila.desenfileirar().chave == "chave1"
    assert fila.desenfileirar().chave == "chave2"
    assert fila.vazia()


def _preco(item, cnpj, nome, valor, dias_atras=0):
    return PrecoObservado(
        item_cesta=item, descricao_original=item.upper(), cnpj=cnpj,
        nome_estabelecimento=nome, preco=valor, unidade="kg",
        observado_em=datetime.now() - timedelta(days=dias_atras),
        chave_cupom=f"{cnpj}-{item}",
    )


def test_repositorio_grava_e_recarrega(tmp_path, cupom):
    caminho = tmp_path / "precos.json"
    repo = Repositorio(caminho)
    gravados = repo.registrar_cupom(cupom)
    repo.salvar()

    # Todos os itens do cupom entram na base; só o tomate é da cesta básica.
    assert gravados == len(cupom.itens)
    recarregado = Repositorio(caminho)
    assert len(recarregado) == len(cupom.itens)
    assert recarregado.itens_cobertos() == {"tomate"}


def test_repositorio_ignora_cupom_repetido(tmp_path, cupom):
    repo = Repositorio(tmp_path / "precos.json")
    assert repo.registrar_cupom(cupom) == len(cupom.itens)
    assert repo.registrar_cupom(cupom) == 0


def test_matriz_produto_por_estabelecimento():
    precos = [
        _preco("arroz", "1", "Mercado A", 6.00),
        _preco("arroz", "2", "Mercado B", 7.50),
        _preco("feijao", "1", "Mercado A", 8.00),
    ]
    matriz = matriz_precos(precos)
    assert matriz["arroz"]["1"] == 6.00
    assert matriz["arroz"]["2"] == 7.50
    assert "2" not in matriz["feijao"]


def test_matriz_mantem_observacao_mais_recente():
    precos = [
        _preco("arroz", "1", "Mercado A", 6.00, dias_atras=10),
        _preco("arroz", "1", "Mercado A", 7.00, dias_atras=1),
    ]
    assert matriz_precos(precos)["arroz"]["1"] == 7.00


def test_ranking_ordena_do_mais_barato_ao_mais_caro():
    itens = ["arroz", "feijao", "acucar", "oleo", "leite", "cafe", "tomate"]
    precos = []
    for item in itens:
        precos.append(_preco(item, "1", "Mercado Barato", 5.00))
        precos.append(_preco(item, "2", "Mercado Caro", 10.00))

    ranking = montar_ranking(precos, cobertura_minima=0.5)
    assert ranking.mais_barato.nome == "Mercado Barato"
    assert ranking.mais_caro.nome == "Mercado Caro"
    assert ranking.economia_possivel > 0


def test_ranking_exclui_mercado_com_cobertura_baixa():
    precos = [
        _preco(i, "1", "Completo", 5.00)
        for i in ["arroz", "feijao", "acucar", "oleo", "leite", "cafe", "tomate"]
    ]
    precos.append(_preco("arroz", "2", "So Arroz", 1.00))

    ranking = montar_ranking(precos, cobertura_minima=0.5)
    nomes = [e.nome for e in ranking.estabelecimentos]
    assert "Completo" in nomes
    assert "So Arroz" not in nomes  # seria "o mais barato" sem ter a cesta


def test_dias_desde_atualizacao_com_data_com_fuso():
    # O Postgres devolve observado_em com fuso (timestamptz); subtrair de um
    # datetime.now() ingênuo levantava TypeError e derrubava a página de preços.
    from tabao.modelos import PrecoObservado

    com_fuso = datetime.now().astimezone() - timedelta(days=3)
    precos = [
        PrecoObservado(descricao_original=i.upper(), cnpj="1", nome_estabelecimento="Loja",
                       preco=5.0, unidade="kg", observado_em=com_fuso,
                       chave_cupom="k" + i, item_cesta=i)
        for i in ["arroz", "feijao", "acucar", "oleo", "leite", "cafe", "tomate"]
    ]
    ranking = montar_ranking(precos, cobertura_minima=0.5)
    assert ranking.mais_barato.dias_desde_atualizacao == 3


# --------------------------------------------------------------------------
# Página real da SEFAZ-GO
# --------------------------------------------------------------------------

@pytest.fixture
def cupom_real():
    """Cupom extraído da página que a SEFAZ realmente devolveu."""
    return sefaz.extrair(FIXTURE_REAL.read_text(encoding="utf-8"), chave=CHAVE_REAL)


def test_pagina_real_tem_dezessete_itens(cupom_real):
    # A foto do cupom estava dobrada e mostrava 13 linhas; a nota tem 17.
    assert len(cupom_real.itens) == 17


def test_total_da_pagina_real_bate_exatamente(cupom_real):
    assert cupom_real.total_calculado == pytest.approx(365.31, abs=0.01)


def test_estabelecimento_da_pagina_real(cupom_real):
    assert cupom_real.estabelecimento.cnpj == "03083231004191"
    assert "CEMA CENTRAL MINEIRA" in cupom_real.estabelecimento.nome


def test_desembrulha_envelope_map_da_sefaz():
    html = sefaz.desembrulhar_resposta(ENVELOPE_REAL.read_text(encoding="utf-8"))
    assert "tabResult" in html
    assert "AZEITONA" in html


def test_envelope_com_erro_levanta_excecao():
    xml = "<Map><STATUS>ERROR</STATUS><MESSAGE>Nota nao encontrada</MESSAGE></Map>"
    with pytest.raises(sefaz.ConsultaSEFAZError):
        sefaz.desembrulhar_resposta(xml)


def test_batata_congelada_da_pagina_real_nao_entra_na_cesta(cupom_real):
    # Achado real: "BATATA CONG UAI BATATA PC 2kg" é pré-frita congelada.
    batata = next(i for i in cupom_real.itens if "BATATA" in i.descricao)
    assert prod.classificar(batata.descricao).item is None


def test_tomate_da_pagina_real_entra_na_cesta(cupom_real):
    tomates = [i for i in cupom_real.itens if "TOMATE" in i.descricao]
    assert len(tomates) == 2  # duas pesagens na mesma compra
    assert all(prod.classificar(i.descricao).item == "tomate" for i in tomates)


# --------------------------------------------------------------------------
# Categorias: todos os produtos, não só a cesta
# --------------------------------------------------------------------------

@pytest.mark.parametrize("descricao,esperado", [
    ("TOMATE ANDREA kg", "hortifruti"),
    ("BACON MANTA AURORA PED kg", "carnes"),
    ("QUEIJO MUSS VIDALAC PED kg", "laticinios"),
    ("KETCHUP HEMMER FR 320G TRAD", "mercearia"),
    ("CARVAO VEGETAL FIAT LUX PC 4kg", "casa"),
    ("MEIO ASA FGO RESF kg", "carnes"),
])
def test_categoriza_produtos_do_cupom_real(descricao, esperado):
    assert cat.categorizar(descricao) == esperado


def test_produto_desconhecido_cai_em_outros():
    assert cat.categorizar("XYZ PRODUTO INEXISTENTE 123") == "outros"


def test_repositorio_grava_todos_os_itens(tmp_path, cupom_real):
    repo = Repositorio(tmp_path / "precos.json")
    gravados = repo.registrar_cupom(cupom_real)

    # Todos os 17 itens entram na base, não apenas os 2 da cesta.
    assert gravados == 17
    assert len(repo.precos_da_cesta) == 2
    assert repo.itens_cobertos() == {"tomate"}
    assert "carnes" in repo.categorias_cobertas()


def test_busca_livre_de_produto(tmp_path, cupom_real):
    repo = Repositorio(tmp_path / "precos.json")
    repo.registrar_cupom(cupom_real)
    assert len(repo.buscar_produto("bacon")) == 1
    assert len(repo.buscar_produto("tomate")) == 2
    assert repo.buscar_produto("inexistente") == []


def test_ranking_ignora_itens_fora_da_cesta(tmp_path, cupom_real):
    """Produtos fora da cesta não podem inflar o custo do indicador oficial."""
    repo = Repositorio(tmp_path / "precos.json")
    repo.registrar_cupom(cupom_real)
    custos = montar_ranking(repo.precos, cobertura_minima=0.0).estabelecimentos
    assert len(custos) == 1
    # Só o tomate foi classificado; o custo deve refletir apenas ele.
    assert set(custos[0].detalhe) == {"tomate"}


def test_mad_zero_nao_descarta_preco_plausivel():
    """
    Regressão: quando a maioria dos preços é idêntica o MAD vira zero.

    Antes da correção, qualquer valor diferente virava "infinitamente atípico"
    e o mercado mais barato sumia do ranking.
    """
    precos = [8.90, 9.90, 11.89, 11.89, 11.89]
    assert not est.e_atipico(8.90, precos)
    assert not est.e_atipico(9.90, precos)


def test_mad_quase_zero_nao_descarta_preco_plausivel():
    """
    Regressão do caso real: preços quase idênticos por arredondamento.

    Tomate a 11,8889 / 11,89 / 11,8917 (mesmo preço por quilo, quantidades
    diferentes) deixava o MAD em 0,001. O tomate de R$ 8,90 do mercado mais
    barato era descartado com z = -720 e sumia do ranking.
    """
    precos = [8.90, 9.90, 11.8889, 11.89, 11.8917]
    assert not est.e_atipico(8.90, precos)
    assert abs(est.escore_z_robusto(8.90, precos)) < 3.5


def test_ranking_mantem_o_mercado_mais_barato_com_precos_repetidos():
    """O mercado mais barato não pode perder um item por causa do MAD."""
    from datetime import datetime as _dt
    itens = ["arroz", "feijao", "acucar", "oleo", "leite", "cafe", "tomate"]
    precos = []
    for item in itens:
        precos.append(_preco(item, "1", "Barato", 5.00))
        precos.append(_preco(item, "2", "Medio", 9.00))
        precos.append(_preco(item, "3", "Caro", 9.00))
        precos.append(_preco(item, "4", "Caro2", 9.00))

    ranking = montar_ranking(precos, cobertura_minima=0.5)
    barato = next(e for e in ranking.estabelecimentos if e.nome == "Barato")
    assert barato.itens_encontrados == len(itens)


def test_mad_zero_ainda_detecta_absurdo():
    assert est.e_atipico(200.0, [11.89, 11.89, 11.89, 12.00])


def test_valores_todos_identicos_sinalizam_diferente():
    assert est.e_atipico(9.00, [10.0, 10.0, 10.0])
    assert not est.e_atipico(10.0, [10.0, 10.0, 10.0])


# --------------------------------------------------------------------------
# Fluxo sem estado (necessário em hospedagem serverless)
# --------------------------------------------------------------------------

def test_cupom_sobrevive_a_ida_e_volta_em_dicionario(cupom_real):
    """O cupom precisa ser serializável para viajar no formulário."""
    from tabao.modelos import Cupom

    volta = Cupom.de_dicionario(cupom_real.para_dicionario())
    assert len(volta.itens) == len(cupom_real.itens)
    assert volta.total_calculado == cupom_real.total_calculado
    assert volta.estabelecimento.cnpj == cupom_real.estabelecimento.cnpj
    assert volta.emitido_em == cupom_real.emitido_em


def test_token_assinado_devolve_o_mesmo_cupom(cupom_real):
    import app as aplicacao

    token = aplicacao.empacotar_cupom(cupom_real)
    volta = aplicacao.desempacotar_cupom(token)
    assert volta is not None
    assert volta.chave == cupom_real.chave
    assert len(volta.itens) == len(cupom_real.itens)


def test_token_adulterado_e_recusado(cupom_real):
    """
    Sem a assinatura, alguém poderia editar os preços entre a conferência e a
    confirmação e envenenar a base colaborativa.
    """
    import app as aplicacao

    token = aplicacao.empacotar_cupom(cupom_real)
    assert aplicacao.desempacotar_cupom(token[:-6] + "AAAAAA") is None
    assert aplicacao.desempacotar_cupom("qualquer-coisa") is None
    assert aplicacao.desempacotar_cupom("") is None


def test_mapa_usa_os_mercados_embarcados(tmp_path):
    """
    Em produção o disco é efêmero: sem o arquivo versionado, o mapa nasceria
    vazio e o app voltaria a pedir a cidade.
    """
    from tabao.mapa import CacheMapa

    cache = CacheMapa(tmp_path / "nao-existe.json")
    assert len(cache.locais) > 100
    assert cache.area == "Goiânia"
    assert cache.centro is not None


# --------------------------------------------------------------------------
# Robustez: falhas externas, dados corrompidos e erros inesperados
# --------------------------------------------------------------------------

class _RespostaFalsa:
    def __init__(self, status, texto=""):
        self.status_code = status
        self.text = texto
        self.encoding = None


class _ClienteFalso:
    """Devolve, em ordem, respostas ou exceções pré-programadas."""

    def __init__(self, *roteiro):
        self.roteiro = list(roteiro)
        self.chamadas = 0

    def get(self, url, **kwargs):
        self.chamadas += 1
        proximo = self.roteiro.pop(0)
        if isinstance(proximo, Exception):
            raise proximo
        return proximo


@pytest.fixture
def sem_pausa(monkeypatch):
    monkeypatch.setattr(sefaz, "PAUSA_ENTRE_TENTATIVAS", 0)


def test_retentativa_recupera_queda_de_conexao(sem_pausa):
    cliente = _ClienteFalso(ConnectionError("caiu"), _RespostaFalsa(200))
    assert sefaz._get_com_retentativa(cliente, "u").status_code == 200
    assert cliente.chamadas == 2


def test_retentativa_recupera_erro_5xx(sem_pausa):
    cliente = _ClienteFalso(_RespostaFalsa(503), _RespostaFalsa(200))
    assert sefaz._get_com_retentativa(cliente, "u").status_code == 200


def test_retentativa_nao_repete_erro_4xx(sem_pausa):
    cliente = _ClienteFalso(_RespostaFalsa(404))
    assert sefaz._get_com_retentativa(cliente, "u").status_code == 404
    assert cliente.chamadas == 1


def test_retentativa_desiste_e_propaga_a_falha(sem_pausa):
    cliente = _ClienteFalso(ConnectionError("1"), ConnectionError("2"))
    with pytest.raises(ConnectionError):
        sefaz._get_com_retentativa(cliente, "u")


def test_base_json_corrompida_e_guardada_e_nao_sobrescrita(tmp_path):
    base = tmp_path / "precos.json"
    base.write_text("{ isto não é json", encoding="utf-8")

    repo = Repositorio(base)
    assert len(repo) == 0
    guardados = list(tmp_path.glob("precos.json.corrompido-*"))
    assert len(guardados) == 1
    assert guardados[0].read_text(encoding="utf-8") == "{ isto não é json"

    repo.salvar()
    assert guardados[0].exists()


def test_salvar_nao_deixa_temporario(tmp_path, cupom_real):
    repo = Repositorio(tmp_path / "precos.json")
    repo.registrar_cupom(cupom_real)
    repo.salvar()
    assert [p.name for p in tmp_path.iterdir()] == ["precos.json"]
    assert len(Repositorio(tmp_path / "precos.json")) == len(cupom_real.itens)


def test_postgres_trata_corrida_de_cupom_duplicado(cupom_real):
    from tabao.banco import RepositorioPostgres

    class Duplicado(Exception):
        sqlstate = "23505"

    repo = RepositorioPostgres.__new__(RepositorioPostgres)
    repo.ja_processado = lambda chave: False

    def gravar(_cupom):
        raise Duplicado()

    repo._gravar_cupom = gravar
    assert repo.registrar_cupom(cupom_real) == 0


@pytest.fixture
def cliente_web(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import app as modulo_app

    monkeypatch.setattr(modulo_app, "BASE", tmp_path / "precos.json")
    modulo_app.app.config["TESTING"] = False
    modulo_app.app.config["PROPAGATE_EXCEPTIONS"] = False
    return modulo_app


def test_banco_fora_do_ar_mostra_pagina_503(cliente_web, monkeypatch):
    from tabao.banco import BancoError

    def falha(_caminho):
        raise BancoError("sem conexão")

    monkeypatch.setattr(cliente_web, "criar_repositorio", falha)
    resposta = cliente_web.app.test_client().get("/precos")
    assert resposta.status_code == 503
    assert "indisponível" in resposta.get_data(as_text=True)


def test_erro_inesperado_mostra_pagina_amigavel(cliente_web, monkeypatch):
    def falha(_caminho):
        raise KeyError("bug")

    monkeypatch.setattr(cliente_web, "criar_repositorio", falha)
    resposta = cliente_web.app.test_client().get("/precos")
    assert resposta.status_code == 500
    assert "Algo deu errado" in resposta.get_data(as_text=True)


def test_repositorio_e_fechado_ao_fim_da_requisicao(cliente_web, monkeypatch):
    fechados = []

    class RepoFalso(Repositorio):
        def fechar(self):
            fechados.append(True)

    monkeypatch.setattr(cliente_web, "criar_repositorio", RepoFalso)
    cliente_web.app.test_client().get("/precos")
    assert fechados == [True]


def test_layout_inesperado_da_sefaz_vira_mensagem(monkeypatch, sem_pausa):
    import requests

    class SessaoFalsa:
        headers = {}

        def get(self, url, **kwargs):
            return _RespostaFalsa(200, "<Map/>")

    monkeypatch.setattr(requests, "Session", SessaoFalsa)
    monkeypatch.setattr(sefaz, "desembrulhar_resposta",
                        lambda _x: (_ for _ in ()).throw(AttributeError("x")))
    url = ("https://nfeweb.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe?p="
           f"{CHAVE_REAL}|2|1|1|ABC")
    with pytest.raises(sefaz.ConsultaSEFAZError, match="layout"):
        sefaz.consultar_por_qrcode(url, CHAVE_REAL)


def test_pagina_inexistente_tem_404_amigavel(cliente_web):
    resposta = cliente_web.app.test_client().get("/nao-existe")
    assert resposta.status_code == 404
    assert "não encontrada" in resposta.get_data(as_text=True)


def test_esquema_do_postgres_e_criado_uma_vez_por_processo(monkeypatch):
    from tabao import banco

    criacoes = []

    class RepoFalso:
        def __init__(self, url):
            pass

        def criar_esquema(self):
            criacoes.append(True)

    monkeypatch.setenv("DATABASE_URL", "postgresql://falso")
    monkeypatch.setattr(banco, "RepositorioPostgres", RepoFalso)
    monkeypatch.setattr(banco, "_esquema_garantido", False)

    banco.criar_repositorio()
    banco.criar_repositorio()
    assert criacoes == [True]


# --------------------------------------------------------------------------
# Validação do cupom, limite de envios e prazo da consulta
# --------------------------------------------------------------------------

from tabao.limite import LimitadorDeTaxa
from tabao.validacao import CupomInvalidoError, problemas_do_cupom, validar_cupom


def test_cupom_real_passa_na_validacao(cupom_real):
    assert problemas_do_cupom(cupom_real) == []


def test_validacao_recusa_preco_zerado(cupom_real):
    cupom_real.itens[0].valor_total = 0
    with pytest.raises(CupomInvalidoError, match="zerado"):
        validar_cupom(cupom_real)


def test_validacao_recusa_quantidade_absurda(cupom_real):
    cupom_real.itens[0].quantidade = 50_000
    assert any("quantidade" in p for p in problemas_do_cupom(cupom_real))


def test_validacao_recusa_data_no_futuro(cupom_real):
    agora = cupom_real.emitido_em.replace(tzinfo=None) - timedelta(days=10)
    cupom_real.emitido_em = cupom_real.emitido_em.replace(tzinfo=None)
    assert any("futuro" in p for p in problemas_do_cupom(cupom_real, agora))


def test_validacao_recusa_total_que_nao_bate(cupom_real):
    cupom_real.valor_total = cupom_real.total_calculado + 100
    assert any("soma" in p for p in problemas_do_cupom(cupom_real))


def test_validacao_recusa_linha_incoerente(cupom_real):
    item = cupom_real.itens[0]
    item.valor_total = item.quantidade * item.valor_unitario * 3
    assert any("não bate" in p for p in problemas_do_cupom(cupom_real))


def test_limitador_barra_rajada_e_libera_depois_da_janela():
    limite = LimitadorDeTaxa(limite=2, janela=60)
    assert limite.permitir("ip", agora=0)
    assert limite.permitir("ip", agora=1)
    assert not limite.permitir("ip", agora=2)
    assert limite.permitir("outro-ip", agora=2)
    assert limite.permitir("ip", agora=61)


def test_prazo_esgotado_nao_faz_nova_requisicao(sem_pausa):
    import time

    cliente = _ClienteFalso(_RespostaFalsa(200))
    with pytest.raises(TimeoutError):
        sefaz._get_com_retentativa(cliente, "u", prazo=time.monotonic())
    assert cliente.chamadas == 0


def test_prazo_encolhe_o_timeout_da_requisicao(sem_pausa):
    import time

    recebido = {}

    class Cliente:
        def get(self, url, **kwargs):
            recebido.update(kwargs)
            return _RespostaFalsa(200)

    sefaz._get_com_retentativa(Cliente(), "u", prazo=time.monotonic() + 8, timeout=15)
    assert recebido["timeout"] <= 8


URL_QR_REAL = ("https://nfeweb.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe?p="
               f"{CHAVE_REAL}|2|1|1|ABC")


@pytest.fixture
def web_com_sefaz_falsa(cliente_web, monkeypatch, cupom_real):
    cliente_web.LIMITE_ENVIOS.limpar()
    cliente_web.LIMITE_CONFIRMACOES.limpar()
    monkeypatch.setattr(cliente_web.sefaz, "consultar_por_qrcode",
                        lambda url, chave: cupom_real)
    return cliente_web


def test_fluxo_completo_envio_conferencia_e_gravacao(web_com_sefaz_falsa, cupom_real):
    import re

    cliente = web_com_sefaz_falsa.app.test_client()

    conferencia = cliente.post("/enviar", data={"url": URL_QR_REAL})
    assert conferencia.status_code == 200
    html = conferencia.get_data(as_text=True)
    token = re.search(r'name="cupom" value="([^"]+)"', html).group(1)

    gravacao = cliente.post("/confirmar", data={"cupom": token})
    assert gravacao.status_code == 302

    base = Repositorio(web_com_sefaz_falsa.BASE)
    assert len(base) == len(cupom_real.itens)
    assert base.ja_processado(cupom_real.chave)

    # Reenviar o mesmo cupom não duplica nada.
    de_novo = cliente.post("/enviar", data={"url": URL_QR_REAL})
    assert de_novo.status_code == 302
    assert len(Repositorio(web_com_sefaz_falsa.BASE)) == len(cupom_real.itens)


def test_envio_de_cupom_incoerente_e_recusado(web_com_sefaz_falsa, cupom_real):
    cupom_real.itens[0].valor_total = 0
    resposta = web_com_sefaz_falsa.app.test_client().post("/enviar", data={"url": URL_QR_REAL})
    assert resposta.status_code == 302
    assert not Path(web_com_sefaz_falsa.BASE).exists()


def test_rajada_de_envios_recebe_429(web_com_sefaz_falsa):
    cliente = web_com_sefaz_falsa.app.test_client()
    codigos = [cliente.post("/enviar", data={"url": URL_QR_REAL}).status_code
               for _ in range(11)]
    assert codigos[-1] == 429
    assert 429 not in codigos[:10]


# --------------------------------------------------------------------------
# Desempenho: conexão sob demanda e cache de leitura do Postgres
# --------------------------------------------------------------------------

class _CursorFalso:
    def __init__(self, conexao):
        self.conexao = conexao

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conexao.consultas.append(sql)

    def fetchall(self):
        return self.conexao.linhas


class _ConexaoFalsa:
    def __init__(self, linhas):
        self.linhas = linhas
        self.consultas = []

    def cursor(self):
        return _CursorFalso(self)

    def close(self):
        pass


@pytest.fixture
def postgres_falso(monkeypatch):
    from tabao import banco

    banco.limpar_cache()
    agora = datetime(2026, 5, 15, 10, 0)
    linhas = [
        ("ARROZ TIPO 1 5KG", "111", "Mercado A", 25.9, "un", agora, "c1", "graos", "arroz", ""),
        ("SABAO EM PO", "222", "Mercado B", 12.0, "un", agora, "c2", "limpeza", None, ""),
    ]
    conexao = _ConexaoFalsa(linhas)
    conexoes = []

    def conectar(_url):
        conexoes.append(conexao)
        return conexao

    monkeypatch.setattr(banco, "_conectar", conectar)
    yield banco, conexao, conexoes
    banco.limpar_cache()


def test_postgres_nao_conecta_sem_consulta(postgres_falso):
    banco, _, conexoes = postgres_falso
    repo = banco.RepositorioPostgres("postgresql://falso")
    repo.fechar()
    assert conexoes == []


def test_painel_inteiro_faz_uma_unica_consulta(postgres_falso):
    banco, conexao, _ = postgres_falso
    repo = banco.RepositorioPostgres("postgresql://falso")

    assert len(repo) == 2
    assert len(repo.precos_da_cesta) == 1
    assert repo.estabelecimentos() == {"111": "Mercado A", "222": "Mercado B"}
    assert repo.itens_cobertos() == {"arroz"}
    assert repo.categorias_cobertas() == {"graos": 1, "limpeza": 1}
    assert [p.descricao_original for p in repo.buscar_produto("arroz")] == ["ARROZ TIPO 1 5KG"]
    assert len(conexao.consultas) == 1

    # Outra requisição no mesmo processo: servida do cache, sem conexão nova.
    outro = banco.RepositorioPostgres("postgresql://falso")
    assert len(outro.precos) == 2
    assert len(conexao.consultas) == 1


def test_cache_expira(postgres_falso, monkeypatch):
    banco, conexao, _ = postgres_falso
    repo = banco.RepositorioPostgres("postgresql://falso")
    repo.precos
    monkeypatch.setattr(banco, "CACHE_SEGUNDOS", 0)
    repo.precos
    assert len(conexao.consultas) == 2


def test_mapa_mostra_so_mercados_com_precos(cliente_web, monkeypatch, cupom_real):
    import json as _json
    import re

    repo = Repositorio(cliente_web.BASE)
    repo.registrar_cupom(cupom_real)
    mercado = repo.registro_estabelecimentos[cupom_real.estabelecimento.cnpj]
    mercado.latitude, mercado.longitude = -16.65, -49.25
    repo.atualizar_estabelecimento(mercado)
    repo.salvar()

    html = cliente_web.app.test_client().get("/mapa").get_data(as_text=True)
    linha = next(l for l in html.splitlines() if "const LOCAIS = " in l)
    locais = _json.loads(linha.split("const LOCAIS = ", 1)[1].rstrip().rstrip(";"))
    assert locais, "o mercado do cupom deveria aparecer"
    assert all(local["cnpj"] for local in locais)
    assert all("tipo" in local for local in locais)
    assert "#C1440E\"></i>sem preços" not in html


# --------------------------------------------------------------------------
# Comparação justa entre mercados com cestas diferentes
# --------------------------------------------------------------------------

from tabao.cesta import CustoEstabelecimento, cesta_comparavel


def _custo(cnpj, detalhe):
    return CustoEstabelecimento(cnpj=cnpj, nome=cnpj, custo_total=sum(detalhe.values()),
                                itens_encontrados=len(detalhe), detalhe=detalhe)


def test_comparacao_usa_so_itens_em_comum():
    # B parece mais barato no total só porque tem menos itens registrados.
    a = _custo("A", {"arroz": 20, "feijao": 8, "leite": 5, "cafe": 15, "carne": 40})
    b = _custo("B", {"arroz": 22, "feijao": 9, "leite": 6})
    assert b.custo_total < a.custo_total

    comparavel = cesta_comparavel([a, b])
    assert comparavel.metodo == "comum"
    assert comparavel.itens == ["arroz", "feijao", "leite"]
    assert comparavel.custos == {"A": 33, "B": 37}


def test_poucos_itens_em_comum_preenche_pela_mediana():
    a = _custo("A", {"arroz": 20, "feijao": 8})
    b = _custo("B", {"arroz": 22, "leite": 6})
    comparavel = cesta_comparavel([a, b])
    assert comparavel.metodo == "mediana"
    assert comparavel.itens == ["arroz", "feijao", "leite"]
    assert comparavel.custos == {"A": 34, "B": 36}
    assert comparavel.estimados == {"A": 1, "B": 1}


def test_rotas_da_viabilidade_sao_calculadas_em_paralelo(monkeypatch):
    import threading
    import time as _time
    from tabao import rota

    ativos, pico = [0], [0]
    trava = threading.Lock()

    def trajeto_lento(origem, destino, usar_ruas=True, com_desenho=False):
        with trava:
            ativos[0] += 1
            pico[0] = max(pico[0], ativos[0])
        _time.sleep(0.05)
        with trava:
            ativos[0] -= 1
        return rota.trajeto_em_linha_reta(origem, destino)

    monkeypatch.setattr(rota, "calcular_trajeto", trajeto_lento)
    candidatos = [(f"M{i}", str(i), 100.0 + i, (-16.6 - i / 100, -49.2)) for i in range(4)]
    resultado = rota.avaliar((-16.6, -49.2), candidatos)
    assert len(resultado) == 4
    assert pico[0] > 1


# --------------------------------------------------------------------------
# Contas de usuário
# --------------------------------------------------------------------------

from tabao.contas import CONSUMO_MEDIO_KM_L, ContaError, ler_veiculo


def _csrf(cliente, caminho):
    import re
    html = cliente.get(caminho).get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


@pytest.fixture
def web_contas(cliente_web):
    for limite in (cliente_web.LIMITE_LOGIN, cliente_web.LIMITE_CADASTRO):
        limite.limpar()
    return cliente_web


def _cadastrar(cliente, email="ana@exemplo.com", senha="senha-forte-1"):
    return cliente.post("/cadastro", data={
        "csrf": _csrf(cliente, "/cadastro"), "nome": "Ana Souza", "email": email,
        "senha": senha, "confirmacao": senha,
    })


def test_cadastro_login_e_sair(web_contas):
    cliente = web_contas.app.test_client()
    resposta = _cadastrar(cliente)
    assert resposta.status_code == 302 and resposta.location.endswith("/perfil")
    assert "Ana" in cliente.get("/perfil").get_data(as_text=True)

    cliente.post("/sair", data={"csrf": _csrf(cliente, "/perfil")})
    assert cliente.get("/perfil").status_code == 302

    errada = cliente.post("/entrar", data={
        "csrf": _csrf(cliente, "/entrar"), "email": "ana@exemplo.com", "senha": "outra-senha"})
    assert errada.status_code == 401

    certa = cliente.post("/entrar", data={
        "csrf": _csrf(cliente, "/entrar"), "email": "ANA@exemplo.com ", "senha": "senha-forte-1"})
    assert certa.status_code == 302
    assert cliente.get("/perfil").status_code == 200


def test_senha_nao_e_guardada_em_texto(web_contas):
    _cadastrar(web_contas.app.test_client())
    conteudo = Path(web_contas.BASE).read_text(encoding="utf-8")
    assert "senha-forte-1" not in conteudo
    assert "ana@exemplo.com" in conteudo


def test_email_repetido_e_recusado(web_contas):
    _cadastrar(web_contas.app.test_client())
    resposta = _cadastrar(web_contas.app.test_client())
    assert resposta.status_code == 400
    assert "Já existe" in resposta.get_data(as_text=True)


def test_senha_curta_e_recusada(web_contas):
    resposta = _cadastrar(web_contas.app.test_client(), senha="curta")
    assert resposta.status_code == 400


def test_formulario_sem_csrf_e_recusado(web_contas):
    resposta = web_contas.app.test_client().post("/entrar", data={
        "email": "ana@exemplo.com", "senha": "senha-forte-1"})
    assert resposta.status_code == 400


def test_login_nao_redireciona_para_outro_site(web_contas):
    cliente = web_contas.app.test_client()
    _cadastrar(cliente)
    cliente.post("/sair", data={"csrf": _csrf(cliente, "/perfil")})
    resposta = cliente.post("/entrar", data={
        "csrf": _csrf(cliente, "/entrar"), "email": "ana@exemplo.com",
        "senha": "senha-forte-1", "proximo": "//golpe.com/x"})
    assert "golpe.com" not in resposta.location
    assert resposta.location.endswith("/mapa")


def test_rajada_de_senhas_e_barrada(web_contas):
    cliente = web_contas.app.test_client()
    token = _csrf(cliente, "/entrar")
    codigos = [cliente.post("/entrar", data={"csrf": token, "email": "x@y.com",
                                             "senha": "errada-123"}).status_code
               for _ in range(9)]
    assert codigos[-1] == 429


def test_perfil_salva_veiculo_e_nao_sei_usa_media(web_contas):
    cliente = web_contas.app.test_client()
    _cadastrar(cliente)
    cliente.post("/perfil", data={
        "csrf": _csrf(cliente, "/perfil"), "nome": "Ana", "combustivel": "etanol",
        "preco_combustivel": "4,59", "consumo_km_l": "", "nao_sei": "1"})

    usuario = Repositorio(web_contas.BASE).usuario_por_email("ana@exemplo.com")
    assert usuario.preco_combustivel == 4.59
    assert usuario.consumo_km_l is None
    assert usuario.consumo_efetivo == CONSUMO_MEDIO_KM_L["etanol"]

    assert usuario.preferencias(6.0) == {
        "consumo": CONSUMO_MEDIO_KM_L["etanol"], "preco": 4.59,
        "consumo_estimado": True, "combustivel": "etanol"}


def test_ler_veiculo_valida_numeros():
    assert ler_veiculo("gasolina", "6,49", "12,5", False) == {
        "combustivel": "gasolina", "preco_combustivel": 6.49, "consumo_km_l": 12.5}
    with pytest.raises(ContaError):
        ler_veiculo("gasolina", "abc", "", True)
    with pytest.raises(ContaError):
        ler_veiculo("gasolina", "6", "300", False)
    with pytest.raises(ContaError):
        ler_veiculo("diesel-de-foguete", "6", "", True)
