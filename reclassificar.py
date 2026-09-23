"""
Reclassifica os preços já gravados com as regras de classificação atuais.

A classificação (item da cesta e categoria) é calculada quando o cupom entra e
fica gravada em cada preço. Quando as regras melhoram — como na correção de
fronteira de palavra, que deixou de ler pipoca como manteiga —, as linhas
antigas continuam com a classificação velha até serem reprocessadas. Este
script faz isso, relendo apenas a descrição, que nunca muda.

Só toca em `item_cesta` e `categoria`, que são função pura da descrição. Preço,
descrição e data não são alterados.

Rode na SUA máquina, com a DATABASE_URL no ambiente. A senha nunca sai daqui.

    Windows (PowerShell):
        $env:DATABASE_URL = "postgresql://..."
        python reclassificar.py            # só mostra o que mudaria
        python reclassificar.py --aplicar  # grava as mudanças

    Sem DATABASE_URL, reclassifica o arquivo local dados/precos.json.
"""

import os
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tabao.categorias import categorizar, nome_da_categoria
from tabao.produtos import classificar, extrair_embalagem, nome_do_item, normalizar

# Uma unidade de peso/volume aparece na descrição (KG, 500G, 1L...).
_TEM_UNIDADE = re.compile(r"\b\d*\s*(KG|G|GR|GRAMAS?|L|LT|LITROS?|ML)\b")


def _mascarar(url: str) -> str:
    if "@" in url and "://" in url:
        inicio, resto = url.split("://", 1)
        credenciais, servidor = resto.split("@", 1)
        usuario = credenciais.split(":", 1)[0]
        return f"{inicio}://{usuario}:*****@{servidor}"
    return url


def _rotulo_cesta(item):
    return nome_do_item(item) if item else "— (fora da cesta)"


def _preco_suspeito(descricao: str, unidade: str) -> bool:
    """
    Preço que foi calculado por quilo/litro mas cuja embalagem não é mais
    reconhecida — sinal do bug antigo (ex.: "OF3 KG" lido como 3 kg). O valor
    gravado não é recuperável, porque a nota original não guardou o preço pago.

    Exige que a descrição traga uma unidade (KG, ML...): é o rastro do código
    lido como peso, e evita marcar linhas que nunca tiveram embalagem.
    """
    if unidade != "un. padrão":
        return False
    texto = normalizar(descricao)
    return bool(_TEM_UNIDADE.search(texto)) and extrair_embalagem(descricao) is None


def _resumo(linhas):
    """
    Recebe uma lista de (descricao, item_antigo, categoria_antiga) e devolve as
    mudanças. `linhas` já vem da base; aqui só se compara com a regra atual.
    """
    mudou_item = []
    mudou_categoria = 0
    suspeitos = []
    de_para = Counter()

    for id_linha, descricao, item_antigo, cat_antiga, unidade in linhas:
        item_novo = classificar(descricao).item
        cat_nova = categorizar(descricao)

        if item_novo != item_antigo:
            mudou_item.append((id_linha, descricao, item_antigo, item_novo))
            de_para[(item_antigo, item_novo)] += 1
        if cat_nova != cat_antiga:
            mudou_categoria += 1
        if _preco_suspeito(descricao, unidade):
            suspeitos.append((descricao, unidade))

    return mudou_item, mudou_categoria, suspeitos, de_para


def _imprimir_relatorio(total, mudou_item, mudou_categoria, suspeitos, de_para):
    print(f"\n{total} preços na base.\n")

    print(f"ITEM DA CESTA: {len(mudou_item)} linha(s) mudam de classificação.")
    if mudou_item:
        print("  (a mudança mais importante: o que entra e sai da cesta básica)")
        for _id, descricao, antigo, novo in mudou_item:
            print(f"    {descricao[:44]:46}  {_rotulo_cesta(antigo)}  ->  {_rotulo_cesta(novo)}")

    print(f"\nCATEGORIA: {mudou_categoria} linha(s) mudam de categoria.")

    if suspeitos:
        print(f"\nPREÇOS SUSPEITOS: {len(suspeitos)} linha(s) com preço por quilo/litro")
        print("  que a embalagem não sustenta mais (provável leitura de código como")
        print("  peso). O valor não é recuperável daqui; considere reenviar o cupom.")
        for descricao, unidade in suspeitos:
            print(f"    {descricao[:52]:54}  ({unidade})")


def reclassificar_postgres(url: str, aplicar: bool) -> int:
    print(f"Conectando em: {_mascarar(url)}")
    from tabao.banco import BancoError, RepositorioPostgres

    try:
        repo = RepositorioPostgres(url)
    except BancoError as erro:
        print(f"ERRO: {erro}")
        return 1

    try:
        with repo._conexao.cursor() as cursor:  # noqa: SLF001 - script de manutenção
            cursor.execute(
                "select id, descricao_original, item_cesta, categoria, unidade from precos"
            )
            linhas = cursor.fetchall()

        mudou_item, mudou_categoria, suspeitos, de_para = _resumo(linhas)
        _imprimir_relatorio(len(linhas), mudou_item, mudou_categoria, suspeitos, de_para)

        if not aplicar:
            print("\nModo de conferência. Nada foi gravado.")
            print("Para aplicar: python reclassificar.py --aplicar")
            return 0

        # Regrava item_cesta e categoria de cada linha, em uma transação.
        with repo._conexao.transaction():  # noqa: SLF001
            with repo._conexao.cursor() as cursor:  # noqa: SLF001
                for id_linha, descricao, _item, _cat, _un in linhas:
                    cursor.execute(
                        "update precos set item_cesta = %s, categoria = %s where id = %s",
                        (classificar(descricao).item, categorizar(descricao), id_linha),
                    )
        print(f"\nAplicado: {len(linhas)} linha(s) reprocessadas em uma transação.")
        return 0
    finally:
        repo.fechar()


def reclassificar_json(aplicar: bool) -> int:
    from tabao.repositorio import Repositorio

    repo = Repositorio()
    linhas = [
        (i, p.descricao_original, p.item_cesta, p.categoria, p.unidade)
        for i, p in enumerate(repo.precos)
    ]
    if not linhas:
        print("Base local vazia (dados/precos.json). Nada a fazer.")
        return 0

    print("DATABASE_URL não definida — reclassificando a base local dados/precos.json.")
    mudou_item, mudou_categoria, suspeitos, de_para = _resumo(linhas)
    _imprimir_relatorio(len(linhas), mudou_item, mudou_categoria, suspeitos, de_para)

    if not aplicar:
        print("\nModo de conferência. Nada foi gravado.")
        print("Para aplicar: python reclassificar.py --aplicar")
        return 0

    for p in repo._precos:  # noqa: SLF001 - script de manutenção
        p.item_cesta = classificar(p.descricao_original).item
        p.categoria = categorizar(p.descricao_original)
    repo.salvar()
    print(f"\nAplicado: {len(linhas)} linha(s) reprocessadas em dados/precos.json.")
    return 0


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return reclassificar_postgres(url, aplicar)
    return reclassificar_json(aplicar)


if __name__ == "__main__":
    sys.exit(main())
