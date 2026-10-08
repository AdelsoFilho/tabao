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
import time
from collections import Counter
from datetime import datetime
from typing import Iterator, Optional

from .categorias import categorizar
from .contas import ContaError, Usuario
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

create table if not exists usuarios (
    id                text primary key,
    email             text not null unique,
    nome              text not null,
    senha_hash        text not null,
    combustivel       text not null default 'gasolina',
    preco_combustivel numeric(6,3),
    consumo_km_l      numeric(5,2),
    criado_em         timestamptz not null default now()
);
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


# Cache de leitura compartilhado pelas requisições do mesmo processo.
#
# Cada ida ao banco custa uma viagem de rede, e abrir a conexão custa várias.
# A base muda pouco (alguns cupons por dia), então guardar a lista de preços
# por um minuto deixa as telas instantâneas numa instância já aquecida. Quem
# grava limpa o cache da própria instância; as outras enxergam o cupom novo
# em no máximo CACHE_SEGUNDOS.
CACHE_SEGUNDOS = 60
_cache: dict[str, tuple[float, object]] = {}


def limpar_cache() -> None:
    _cache.clear()


def _em_cache(nome: str, carregar):
    agora = time.monotonic()
    guardado = _cache.get(nome)
    if guardado and agora - guardado[0] < CACHE_SEGUNDOS:
        return guardado[1]
    valor = carregar()
    _cache[nome] = (agora, valor)
    return valor


class _UsuariosPostgres:
    """Contas de usuário no Postgres. Base do RepositorioPostgres."""

    _SELECAO_USUARIO = """
        select id, email, nome, senha_hash, combustivel, preco_combustivel,
               consumo_km_l, criado_em
        from usuarios
    """

    def _usuario_da_linha(self, linha) -> Optional[Usuario]:
        if linha is None:
            return None
        return Usuario(
            id=linha[0], email=linha[1], nome=linha[2], senha_hash=linha[3],
            combustivel=linha[4],
            preco_combustivel=float(linha[5]) if linha[5] is not None else None,
            consumo_km_l=float(linha[6]) if linha[6] is not None else None,
            criado_em=linha[7],
        )

    def usuario_por_email(self, email: str) -> Optional[Usuario]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO_USUARIO + " where email = %s", (email,))
            return self._usuario_da_linha(cursor.fetchone())

    def usuario_por_id(self, id_: str) -> Optional[Usuario]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO_USUARIO + " where id = %s", (id_,))
            return self._usuario_da_linha(cursor.fetchone())

    def criar_usuario(self, usuario: Usuario) -> None:
        try:
            with self._conexao.cursor() as cursor:
                cursor.execute(
                    """
                    insert into usuarios (id, email, nome, senha_hash, combustivel,
                                          preco_combustivel, consumo_km_l, criado_em)
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (usuario.id, usuario.email, usuario.nome, usuario.senha_hash,
                     usuario.combustivel, usuario.preco_combustivel,
                     usuario.consumo_km_l, usuario.criado_em),
                )
        except Exception as erro:
            if getattr(erro, "sqlstate", None) == "23505":
                raise ContaError("Já existe uma conta com este e-mail.") from erro
            raise

    def atualizar_usuario(self, usuario: Usuario) -> None:
        with self._conexao.cursor() as cursor:
            cursor.execute(
                """
                update usuarios
                   set nome = %s, combustivel = %s, preco_combustivel = %s,
                       consumo_km_l = %s
                 where id = %s
                """,
                (usuario.nome, usuario.combustivel, usuario.preco_combustivel,
                 usuario.consumo_km_l, usuario.id),
            )


class RepositorioPostgres(_UsuariosPostgres):
    """Mesma interface de `Repositorio`, com Postgres por trás."""

    def __init__(self, url: Optional[str] = None) -> None:
        self.url = url or os.environ.get("DATABASE_URL", "")
        if not self.url:
            raise BancoError("DATABASE_URL não está definida.")
        self._con = None

    @property
    def _conexao(self):
        """Conecta só na primeira consulta: tela servida do cache nem abre conexão."""
        if self._con is None:
            self._con = _conectar(self.url)
        return self._con

    # ---- estrutura ----

    def criar_esquema(self) -> None:
        """Cria as tabelas se ainda não existirem. Seguro rodar várias vezes."""
        with self._conexao.cursor() as cursor:
            cursor.execute(ESQUEMA)

    def fechar(self) -> None:
        if self._con is None:
            return
        try:
            self._con.close()
        except Exception:
            pass
        self._con = None

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

        try:
            return self._gravar_cupom(cupom)
        except Exception as erro:
            limpar_cache()
            # Duas pessoas confirmando o mesmo cupom ao mesmo tempo: ambas
            # passam pelo ja_processado, a segunda esbarra na chave primária.
            # A transação já foi desfeita; o cupom está na base, nada a fazer.
            if getattr(erro, "sqlstate", None) == "23505":
                return 0
            raise

    def _gravar_cupom(self, cupom: Cupom) -> int:
        limpar_cache()
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

    def _carregar_precos(self) -> list[PrecoObservado]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO + " order by observado_em desc")
            return [self._preco_da_linha(l) for l in cursor.fetchall()]

    # Tudo abaixo deriva de uma única consulta (em cache). Com o volume da
    # base, filtrar em Python é mais rápido que uma viagem extra ao banco.

    @property
    def precos(self) -> list[PrecoObservado]:
        return list(_em_cache("precos", self._carregar_precos))

    @property
    def precos_da_cesta(self) -> list[PrecoObservado]:
        return [p for p in self.precos if p.item_cesta]

    def por_item(self, item: str) -> list[PrecoObservado]:
        return [p for p in self.precos if p.item_cesta == item]

    def por_categoria(self, categoria: str) -> list[PrecoObservado]:
        return [p for p in self.precos if p.categoria == categoria]

    def por_estabelecimento(self, cnpj: str) -> list[PrecoObservado]:
        return [p for p in self.precos if p.cnpj == cnpj]

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
        # Do mais antigo ao mais novo: o nome mais recente de cada CNPJ prevalece.
        return {p.cnpj: p.nome_estabelecimento for p in reversed(self.precos)}

    def itens_cobertos(self) -> set[str]:
        return {p.item_cesta for p in self.precos if p.item_cesta}

    def categorias_cobertas(self) -> dict[str, int]:
        contagem = Counter(p.categoria for p in self.precos)
        return dict(contagem.most_common())

    def __len__(self) -> int:
        return len(self.precos)

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

    def _carregar_estabelecimentos(self) -> dict[str, Estabelecimento]:
        with self._conexao.cursor() as cursor:
            cursor.execute(self._SELECAO_ESTAB)
            return {l[0]: self._estabelecimento_da_linha(l) for l in cursor.fetchall()}

    @property
    def registro_estabelecimentos(self) -> dict[str, Estabelecimento]:
        return dict(_em_cache("estabelecimentos", self._carregar_estabelecimentos))

    def estabelecimentos_localizados(self) -> list[Estabelecimento]:
        return [e for e in self.registro_estabelecimentos.values() if e.latitude is not None]

    def estabelecimentos_sem_local(self) -> list[Estabelecimento]:
        return [e for e in self.registro_estabelecimentos.values()
                if e.latitude is None and e.endereco]

    def atualizar_estabelecimento(self, estabelecimento: Estabelecimento) -> None:
        """
        Grava as coordenadas e o endereço enriquecido de um mercado.

        Chamado pela geocodificação, que trabalha sobre objetos em memória e
        precisa devolver o resultado ao armazenamento.
        """
        limpar_cache()
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

# Se o esquema já foi conferido neste processo.
_esquema_garantido = False


def criar_repositorio(caminho_json=None):
    """
    Devolve o repositório adequado ao ambiente.

    Com DATABASE_URL definida (produção, Supabase), usa Postgres. Sem ela
    (desenvolvimento, linha de comando), usa o arquivo JSON. Nenhum outro
    módulo precisa saber qual dos dois está ativo.
    """
    global _esquema_garantido

    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        repositorio = RepositorioPostgres(url)
        # O DDL é idempotente, mas rodá-lo a cada requisição custa uma ida ao
        # banco à toa. Uma vez por processo (por partida a frio, na Vercel) basta.
        if not _esquema_garantido:
            repositorio.criar_esquema()
            _esquema_garantido = True
        return repositorio

    from .repositorio import Repositorio
    return Repositorio(caminho_json or "dados/precos.json")
