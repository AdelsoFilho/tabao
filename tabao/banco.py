"""
Armazenamento em Postgres (Supabase), com a mesma interface do repositório JSON.

O JSON de `repositorio.py` resolve para um usuário e uma máquina. Assim que o
app é publicado, ele deixa de servir por dois motivos: vários usuários gravando
ao mesmo tempo, e o disco efêmero das hospedagens gratuitas, que apaga o
arquivo a cada reinício.

Esta classe implementa os mesmos métodos que `Repositorio`, de modo que
`app.py` e `cli.py` não precisam saber qual das duas está em uso. Quem decide é
a variável de ambiente DATABASE_URL, lida em `criar_repositorio()`.

O histórico continua imutável: cada cupom acrescenta linhas em `precos` e nada
é sobrescrito.
"""

import os
from datetime import datetime
from typing import Iterator, Optional

from .categorias import categorizar
from .modelos import Cupom, Estabelecimento, PrecoObservado
from .produtos import classificar, normalizar, preco_por_unidade_padrao

ESQUEMA = """
create table if not exists estabelecimentos (
    cnpj        text primary key,
    nome        text not null,
    endereco    text not null default '',
    bairro      text not null default '',
    cep         text not null default '',
    latitude    double precision,
    longitude   double precision,
    precisao    text not null default ''
);

create table if not exists cupons (
    chave         text primary key,
    cnpj          text references estabelecimentos(cnpj),
    emitido_em    timestamptz,
    processado_em timestamptz not null default now()
);

create table if not exists precos (
    id                 bigserial primary key,
    chave_cupom        text references cupons(chave) on delete cascade,
    cnpj               text not null,
    nome_estabelecimento text not null default '',
    descricao_original text not null,
    codigo             text not null default '',
    categoria          text not null default 'outros',
    item_cesta         text,
    preco              numeric(12,4) not null,
    unidade            text not null default '',
    observado_em       timestamptz not null
);

create index if not exists precos_item_cesta_idx on precos (item_cesta);
create index if not exists precos_cnpj_idx       on precos (cnpj);
create index if not exists precos_categoria_idx  on precos (categoria);
create index if not exists precos_observado_idx  on precos (observado_em desc);
"""


class BancoError(RuntimeError):
    """Falha ao acessar o banco de dados."""


# Porta do "transaction pooler" do Supabase (Supavisor).
PORTA_POOLER = "6543"


def usa_pooler(url: str) -> bool:
    """
    Detecta se a conexão passa pelo pooler em modo transação.

    Importa porque nesse modo a conexão é devolvida ao pool a cada consulta, e
    prepared statements deixam de valer: o psycopg criaria um statement que a
    próxima consulta não encontraria, com erro do tipo
    "prepared statement ... does not exist".
    """
    return f":{PORTA_POOLER}" in url or "pooler.supabase.com" in url


def _conectar(url: str):
    try:
        import psycopg
    except ImportError as erro:  # pragma: no cover
        raise BancoError(
            "psycopg não está instalado. Rode: pip install 'psycopg[binary]'"
        ) from erro

    # O Supabase exige TLS; sem isto a conexão é recusada.
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"

    opcoes = {"autocommit": True}
    if usa_pooler(url):
        # None desliga os prepared statements, exigência do modo transação.
        opcoes["prepare_threshold"] = None

    try:
        return psycopg.connect(url, **opcoes)
    except Exception as erro:
        raise BancoError(
            f"Não consegui conectar ao banco: {erro}. "
            "Confira a DATABASE_URL. Para Vercel, use a string do "
            "'Transaction pooler' (porta 6543): a conexão direta do Supabase "
            "é IPv6 e não é alcançável de lá."
        ) from erro


class RepositorioPostgres:
    """Mesma interface de `Repositorio`, com Postgres por trás."""

    def __init__(self, url: Optional[str] = None) -> None:
        self.url = url or os.environ.get("DATABASE_URL", "")
        if not self.url:
            raise BancoError("DATABASE_URL não está definida.")
        self._conexao = _conectar(self.url)

    # ---- estrutura ----

    def criar_esquema(self) -> None:
        """Cria as tabelas se ainda não existirem. Seguro rodar várias vezes."""
        with self._conexao.cursor() as cursor:
            cursor.execute(ESQUEMA)

    def fechar(self) -> None:
        try:
            self._conexao.close()
        except Exception:
            pass

    # ---- escrita ----

    def ja_processado(self, chave: str) -> bool:
        with self._conexao.cursor() as cursor:
            cursor.execute("select 1 from cupons where chave = %s", (chave,))
            return cursor.fetchone() is not None

    def registrar_cupom(self, cupom: Cupom) -> int:
        """
        Grava o cupom inteiro em uma transação.

        Ou entram todos os itens, ou nenhum: um cupom pela metade produziria
        um custo de cesta silenciosamente errado.
        """
        if self.ja_processado(cupom.chave):
            return 0

        estabelecimento = cupom.estabelecimento

        with self._conexao.transaction():
            with self._conexao.cursor() as cursor:
                # O endereço só é sobrescrito quando o novo não vem vazio, para
                # não perder o que já foi enriquecido pelo registro do CNPJ.
                cursor.execute(
                    """
                    insert into estabelecimentos (cnpj, nome, endereco)
                    values (%s, %s, %s)
                    on conflict (cnpj) do update set
                        nome = excluded.nome,
                        endereco = coalesce(nullif(excluded.endereco, ''),
                                            estabelecimentos.endereco)
                    """,
                    (estabelecimento.cnpj, estabelecimento.nome, estabelecimento.endereco),
                )

                cursor.execute(
                    "insert into cupons (chave, cnpj, emitido_em) values (%s, %s, %s)",
                    (cupom.chave, estabelecimento.cnpj, cupom.emitido_em),
                )

                linhas = []
                for item in cupom.itens:
                    classificacao = classificar(item.descricao)
                    item.item_cesta = classificacao.item

                    padrao = preco_por_unidade_padrao(
                        item.descricao, item.valor_total, item.quantidade
                    )
                    preco = padrao if padrao is not None else item.preco_por_unidade
                    unidade = "un. padrão" if padrao is not None else item.unidade

                    linhas.append((
                        cupom.chave, estabelecimento.cnpj, estabelecimento.nome,
                        item.descricao, item.codigo, categorizar(item.descricao),
                        classificacao.item, round(preco, 4), unidade, cupom.emitido_em,
                    ))

                cursor.executemany(
                    """
                    insert into precos (chave_cupom, cnpj, nome_estabelecimento,
                                        descricao_original, codigo, categoria,
                                        item_cesta, preco, unidade, observado_em)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    linhas,
                )

        return len(cupom.itens)

    def salvar(self) -> None:
        """Existe só para manter a interface: o Postgres já grava na hora."""

    def carregar(self) -> None:
        """Idem: não há arquivo para recarregar."""

    # ---- leitura ----

    def _preco_da_linha(self, linha) -> PrecoObservado:
        return PrecoObservado(
            descricao_original=linha[0], cnpj=linha[1], nome_estabelecimento=linha[2],
            preco=float(linha[3]), unidade=linha[4], observado_em=linha[5],
            chave_cupom=linha[6], categoria=linha[7], item_cesta=linha[8],
            codigo=linha[9] or "",
        )

    _SELECAO = """
        select descricao_original, cnpj, nome_estabelecimento, preco, unidade,
               observado_em, chave_cupom, categoria, item_cesta, codigo
        from precos
    """

    @property
    def precos(self) -> list[PrecoObservado]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO + " order by observado_em desc")
            return [self._preco_da_linha(l) for l in cursor.fetchall()]

    @property
    def precos_da_cesta(self) -> list[PrecoObservado]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO + " where item_cesta is not null")
            return [self._preco_da_linha(l) for l in cursor.fetchall()]

    def por_item(self, item: str) -> list[PrecoObservado]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO + " where item_cesta = %s", (item,))
            return [self._preco_da_linha(l) for l in cursor.fetchall()]

    def por_categoria(self, categoria: str) -> list[PrecoObservado]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO + " where categoria = %s", (categoria,))
            return [self._preco_da_linha(l) for l in cursor.fetchall()]

    def por_estabelecimento(self, cnpj: str) -> list[PrecoObservado]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO + " where cnpj = %s", (cnpj,))
            return [self._preco_da_linha(l) for l in cursor.fetchall()]

    def buscar_produto(self, termo: str) -> list[PrecoObservado]:
        """
        Busca livre, ignorando acento e caixa.

        `unaccent` exigiria uma extensão do Postgres, que nem toda hospedagem
        habilita. Como o volume é pequeno, filtra-se em Python usando a mesma
        normalização do resto do sistema, o que garante resultado idêntico ao
        do repositório JSON.
        """
        alvo = normalizar(termo)
        if not alvo:
            return []
        return [p for p in self.precos if alvo in normalizar(p.descricao_original)]

    def estabelecimentos(self) -> dict[str, str]:
        with self._conexao.cursor() as cursor:
            cursor.execute("select distinct cnpj, nome_estabelecimento from precos")
            return {linha[0]: linha[1] for linha in cursor.fetchall()}

    def itens_cobertos(self) -> set[str]:
        with self._conexao.cursor() as cursor:
            cursor.execute("select distinct item_cesta from precos where item_cesta is not null")
            return {linha[0] for linha in cursor.fetchall()}

    def categorias_cobertas(self) -> dict[str, int]:
        with self._conexao.cursor() as cursor:
            cursor.execute(
                "select categoria, count(*) from precos group by categoria order by 2 desc"
            )
            return {linha[0]: linha[1] for linha in cursor.fetchall()}

    def __len__(self) -> int:
        with self._conexao.cursor() as cursor:
            cursor.execute("select count(*) from precos")
            return cursor.fetchone()[0]

    def __iter__(self) -> Iterator[PrecoObservado]:
        return iter(self.precos)

    # ---- estabelecimentos ----

    _SELECAO_ESTAB = """
        select cnpj, nome, endereco, latitude, longitude, cep, bairro, precisao
        from estabelecimentos
    """

    def _estabelecimento_da_linha(self, linha) -> Estabelecimento:
        return Estabelecimento(
            cnpj=linha[0], nome=linha[1], endereco=linha[2],
            latitude=linha[3], longitude=linha[4],
            cep=linha[5] or "", bairro=linha[6] or "", precisao=linha[7] or "",
        )

    @property
    def registro_estabelecimentos(self) -> dict[str, Estabelecimento]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO_ESTAB)
            return {l[0]: self._estabelecimento_da_linha(l) for l in cursor.fetchall()}

    def estabelecimentos_localizados(self) -> list[Estabelecimento]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO_ESTAB + " where latitude is not null")
            return [self._estabelecimento_da_linha(l) for l in cursor.fetchall()]

    def estabelecimentos_sem_local(self) -> list[Estabelecimento]:
        with self._conexao.cursor() as cursor:
            cursor.execute(
                self._SELECAO_ESTAB + " where latitude is null and endereco <> ''"
            )
            return [self._estabelecimento_da_linha(l) for l in cursor.fetchall()]

    def atualizar_estabelecimento(self, estabelecimento: Estabelecimento) -> None:
        """
        Grava as coordenadas e o endereço enriquecido de um mercado.

        Chamado pela geocodificação, que trabalha sobre objetos em memória e
        precisa devolver o resultado ao armazenamento.
        """
        with self._conexao.cursor() as cursor:
            cursor.execute(
                """
                update estabelecimentos
                   set nome = %s, endereco = %s, bairro = %s, cep = %s,
                       latitude = %s, longitude = %s, precisao = %s
                 where cnpj = %s
                """,
                (estabelecimento.nome, estabelecimento.endereco, estabelecimento.bairro,
                 estabelecimento.cep, estabelecimento.latitude, estabelecimento.longitude,
                 estabelecimento.precisao, estabelecimento.cnpj),
            )


# --------------------------------------------------------------------------
# Escolha do armazenamento
# --------------------------------------------------------------------------

def criar_repositorio(caminho_json=None):
    """
    Devolve o repositório adequado ao ambiente.

    Com DATABASE_URL definida (produção, Supabase), usa Postgres. Sem ela
    (desenvolvimento, linha de comando), usa o arquivo JSON. Nenhum outro
    módulo precisa saber qual dos dois está ativo.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        repositorio = RepositorioPostgres(url)
        repositorio.criar_esquema()
        return repositorio

    from .repositorio import Repositorio
    return Repositorio(caminho_json or "dados/precos.json")
