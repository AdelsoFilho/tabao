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
