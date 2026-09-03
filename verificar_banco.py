"""
Verifica se o banco do Supabase está pronto para receber o TáBão.

Rode na SUA máquina, com a DATABASE_URL no ambiente. A senha nunca sai daqui.

    Windows (PowerShell):
        $env:DATABASE_URL = "postgresql://..."
        python verificar_banco.py

    Linux / macOS:
        export DATABASE_URL="postgresql://..."
        python verificar_banco.py

O script conecta, cria o esquema, grava um cupom de teste, confere a leitura e
APAGA tudo o que criou. O banco fica exatamente como estava.
"""

import os
import sys
from datetime import datetime

# Saída em UTF-8 mesmo no terminal do Windows.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHAVE_TESTE = "52260503083231004191652170000092211671742645"


def passo(numero: int, texto: str) -> None:
    print(f"\n[{numero}] {texto}")


def ok(texto: str) -> None:
    print(f"    OK  {texto}")


def falha(texto: str) -> None:
    print(f"    ERRO {texto}")


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL não está definida. Veja as instruções no topo deste arquivo.")
        return 1

    # Mostra a URL sem a senha, para conferência sem vazar segredo.
    visivel = url
    if "@" in url and "://" in url:
        inicio, resto = url.split("://", 1)
        credenciais, servidor = resto.split("@", 1)
        usuario = credenciais.split(":", 1)[0]
        visivel = f"{inicio}://{usuario}:*****@{servidor}"
    print(f"Conectando em: {visivel}")

    from tabao.banco import BancoError, RepositorioPostgres, usa_pooler

    if usa_pooler(url):
        ok("usando o transaction pooler (correto para Vercel)")
    else:
        print("    AVISO: esta não parece ser a string do 'Transaction pooler'.")
        print("    A conexão direta do Supabase é IPv6 e a Vercel não alcança.")
        print("    Funciona para testar daqui, mas troque antes de publicar.")

    # ---- 1. conexão ----
    passo(1, "Conectando ao banco")
    try:
        repo = RepositorioPostgres(url)
    except BancoError as erro:
        falha(str(erro))
        return 1
    ok("conexão estabelecida")

    # ---- 2. esquema ----
    passo(2, "Criando o esquema (seguro rodar de novo)")
    try:
        repo.criar_esquema()
    except Exception as erro:
        falha(f"não consegui criar as tabelas: {erro}")
        return 1
    ok("tabelas estabelecimentos, cupons e precos prontas")

    # ---- 3. leitura ----
    passo(3, "Lendo a base")
    try:
        total = len(repo)
        categorias = repo.categorias_cobertas()
    except Exception as erro:
        falha(f"falha ao consultar: {erro}")
        return 1
    ok(f"{total} preço(s) na base, {len(categorias)} categoria(s)")

    # ---- 4. gravação de teste ----
    passo(4, "Gravando um cupom de teste")
    if repo.ja_processado(CHAVE_TESTE):
        ok("o cupom de teste já está na base; pulando a gravação")
        gravou = False
    else:
        from pathlib import Path
        from tabao import sefaz

        fixture = Path(__file__).parent / "tests" / "fixtures" / "danfe_real.html"
        if not fixture.exists():
            falha("fixture de teste não encontrada; pulei esta etapa")
            return 0

        cupom = sefaz.extrair(fixture.read_text(encoding="utf-8"), chave=CHAVE_TESTE)
        try:
            gravados = repo.registrar_cupom(cupom)
        except Exception as erro:
            falha(f"falha ao gravar: {erro}")
            return 1
        ok(f"{gravados} produtos gravados em uma transação")
        gravou = True

        # ---- 5. conferência ----
        passo(5, "Conferindo o que foi gravado")
        lidos = repo.por_estabelecimento(cupom.estabelecimento.cnpj)
        if len(lidos) != gravados:
            falha(f"gravei {gravados} mas li {len(lidos)}")
            return 1
        ok(f"{len(lidos)} preços lidos de volta")

        da_cesta = repo.itens_cobertos()
        ok(f"itens da cesta reconhecidos: {', '.join(sorted(da_cesta)) or 'nenhum'}")

        busca = repo.buscar_produto("tomate")
        ok(f"busca por 'tomate' devolveu {len(busca)} resultado(s)")

        # ---- 6. limpeza ----
        passo(6, "Removendo o cupom de teste")
        try:
            with repo._conexao.cursor() as cursor:  # noqa: SLF001 - script de manutenção
                cursor.execute("delete from precos where chave_cupom = %s", (CHAVE_TESTE,))
                cursor.execute("delete from cupons where chave = %s", (CHAVE_TESTE,))
                cursor.execute(
                    """
                    delete from estabelecimentos e
                     where e.cnpj = %s
                       and not exists (select 1 from precos p where p.cnpj = e.cnpj)
                    """,
                    (cupom.estabelecimento.cnpj,),
                )
        except Exception as erro:
            falha(f"não consegui limpar: {erro}")
            return 1
        ok("banco de volta ao estado original")

    repo.fechar()

    print("\n" + "=" * 62)
    print("TUDO CERTO. O banco está pronto.")
    print("Agora defina no painel da Vercel:")
    print("  DATABASE_URL  = a mesma string (do Transaction pooler)")
    print("  SECRET_KEY    = qualquer texto aleatório e longo")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
