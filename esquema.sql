-- Esquema do TáBão
-- Cole no Supabase: menu lateral > SQL Editor > New query > Run.
-- Pode rodar mais de uma vez sem problema (tudo usa IF NOT EXISTS).
--
-- O app cria estas tabelas sozinho na primeira execucao; este arquivo
-- serve para criar antes e conferir no painel que deu certo.

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
