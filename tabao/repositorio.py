"""
Armazenamento da base colaborativa e estruturas de dados de trabalho.

Três estruturas do módulo aparecem aqui:

    FilaProcessamento    fila (FIFO) dos cupons aguardando extração
    Repositorio          histórico imutável de preços observados
    matriz_precos        matriz produto × estabelecimento

O histórico é imutável por decisão de projeto: um preço novo nunca sobrescreve
um antigo. Isso preserva a série temporal que dá valor à base.
"""

import json
from collections import deque
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .modelos import Cupom, Estabelecimento, PrecoObservado
from .categorias import categorizar, nome_da_categoria
from .produtos import classificar, nome_do_item, preco_por_unidade_padrao


# --------------------------------------------------------------------------
# Fila de processamento
# --------------------------------------------------------------------------

@dataclass
class CupomPendente:
    """Um cupom lido pelo usuário e ainda não processado."""

    url: str
    chave: str
    recebido_em: datetime


class FilaProcessamento:
    """
    Fila FIFO dos cupons aguardando consulta à SEFAZ.

    Usar fila (e não pilha) é intencional: o primeiro cupom enviado deve ser o
    primeiro processado, para que ninguém fique esperando indefinidamente.
    """

    def __init__(self) -> None:
        self._fila: deque[CupomPendente] = deque()
        self._chaves_na_fila: set[str] = set()

    def enfileirar(self, url: str, chave: str) -> bool:
        """
        Coloca um cupom no fim da fila.

        Devolve False quando a chave já está na fila, evitando trabalho
        duplicado — é o conjunto (set) fazendo a deduplicação em tempo O(1).
        """
        if chave in self._chaves_na_fila:
            return False

        self._fila.append(CupomPendente(url=url, chave=chave, recebido_em=datetime.now()))
        self._chaves_na_fila.add(chave)
        return True

    def desenfileirar(self) -> Optional[CupomPendente]:
        """Retira e devolve o cupom mais antigo, ou None se a fila estiver vazia."""
        if not self._fila:
            return None

        pendente = self._fila.popleft()
        self._chaves_na_fila.discard(pendente.chave)
        return pendente

    def __len__(self) -> int:
        return len(self._fila)

    def vazia(self) -> bool:
        return not self._fila


# --------------------------------------------------------------------------
# Repositório de preços
# --------------------------------------------------------------------------

class Repositorio:
    """
    Guarda os preços observados em um arquivo JSON.

    Para o MVP, JSON é suficiente e tem a vantagem de ser legível — dá para
    abrir o arquivo e conferir o que foi gravado. A migração para um banco de
    dados é uma troca localizada nesta classe.
    """

    def __init__(self, caminho: str | Path = "dados/precos.json") -> None:
        self.caminho = Path(caminho)
        self._precos: list[PrecoObservado] = []
        self._chaves_processadas: set[str] = set()
        # Registro dos mercados: nome, endereço e coordenadas, por CNPJ.
        self._estabelecimentos: dict[str, Estabelecimento] = {}
        self.carregar()

    # ---- persistência ----

    def carregar(self) -> None:
        """Lê o arquivo do disco. Base vazia quando o arquivo não existe."""
        if not self.caminho.exists():
            return

        try:
            dados = json.loads(self.caminho.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Base corrompida ou ilegível: começa vazia em vez de quebrar.
            return

        self._precos = [PrecoObservado.de_dicionario(d) for d in dados.get("precos", [])]
        self._chaves_processadas = set(dados.get("cupons_processados", []))
        self._estabelecimentos = {
            cnpj: Estabelecimento(**registro)
            for cnpj, registro in dados.get("estabelecimentos", {}).items()
        }

    def salvar(self) -> None:
        """Grava a base no disco, criando o diretório se necessário."""
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        conteudo = {
            "precos": [p.para_dicionario() for p in self._precos],
            "cupons_processados": sorted(self._chaves_processadas),
            "estabelecimentos": {
                cnpj: asdict(e) for cnpj, e in self._estabelecimentos.items()
            },
        }
        self.caminho.write_text(
            json.dumps(conteudo, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- escrita ----

    def ja_processado(self, chave: str) -> bool:
        """Evita que o mesmo cupom entre duas vezes na base."""
        return chave in self._chaves_processadas

    def registrar_cupom(self, cupom: Cupom) -> int:
        """
        Grava TODOS os itens do cupom na base, com categoria.

        Os que pertencem à cesta básica recebem também `item_cesta`, o que
        alimenta o indicador oficial. Os demais entram categorizados e ficam
        disponíveis para comparação de preço entre mercados.

        Devolve quantos preços foram registrados. Cupons já processados são
        ignorados, o que torna a operação idempotente.
        """
        if self.ja_processado(cupom.chave):
            return 0

        # Registra o mercado, preservando coordenadas já obtidas antes.
        cnpj = cupom.estabelecimento.cnpj
        anterior = self._estabelecimentos.get(cnpj)
        if anterior is None:
            self._estabelecimentos[cnpj] = cupom.estabelecimento
        else:
            anterior.nome = cupom.estabelecimento.nome or anterior.nome
            anterior.endereco = cupom.estabelecimento.endereco or anterior.endereco

        registrados = 0
        for item in cupom.itens:
            classificacao = classificar(item.descricao)
            item.item_cesta = classificacao.item

            # Quando a descrição informa a embalagem, o preço é convertido para
            # reais por quilo ou por litro, tornando a comparação justa entre
            # embalagens de tamanhos diferentes.
            preco_padrao = preco_por_unidade_padrao(
                item.descricao, item.valor_total, item.quantidade
            )
            preco = preco_padrao if preco_padrao is not None else item.preco_por_unidade
            unidade = "un. padrão" if preco_padrao is not None else item.unidade

            self._precos.append(
                PrecoObservado(
                    descricao_original=item.descricao,
                    cnpj=cupom.estabelecimento.cnpj,
                    nome_estabelecimento=cupom.estabelecimento.nome,
                    preco=round(preco, 4),
                    unidade=unidade,
                    observado_em=cupom.emitido_em,
                    chave_cupom=cupom.chave,
                    categoria=categorizar(item.descricao),
                    item_cesta=classificacao.item,
                    codigo=item.codigo,
                )
            )
            registrados += 1

        self._chaves_processadas.add(cupom.chave)
        return registrados

    # ---- leitura ----

    def __len__(self) -> int:
        return len(self._precos)

    def __iter__(self) -> Iterator[PrecoObservado]:
        return iter(self._precos)

    @property
    def precos(self) -> list[PrecoObservado]:
        return list(self._precos)

    def por_item(self, item: str) -> list[PrecoObservado]:
        return [p for p in self._precos if p.item_cesta == item]

    def por_estabelecimento(self, cnpj: str) -> list[PrecoObservado]:
        return [p for p in self._precos if p.cnpj == cnpj]

    def estabelecimentos(self) -> dict[str, str]:
        """Mapa CNPJ -> nome de todos os estabelecimentos da base."""
        return {p.cnpj: p.nome_estabelecimento for p in self._precos}

    @property
    def registro_estabelecimentos(self) -> dict[str, Estabelecimento]:
        """Registro completo, com endereço e coordenadas quando conhecidas."""
        return self._estabelecimentos

    def estabelecimentos_localizados(self) -> list[Estabelecimento]:
        return [e for e in self._estabelecimentos.values() if e.localizado]

    def estabelecimentos_sem_local(self) -> list[Estabelecimento]:
        return [e for e in self._estabelecimentos.values()
                if not e.localizado and e.endereco]

    def atualizar_estabelecimento(self, estabelecimento: Estabelecimento) -> None:
        """
        Grava as alterações de um mercado.

        No JSON os objetos já são os mesmos que estão em memória, então basta
        garantir que ele esteja no registro. O método existe para que o
        repositório em Postgres possa ser trocado sem mudar quem o chama.
        """
        self._estabelecimentos[estabelecimento.cnpj] = estabelecimento

    def itens_cobertos(self) -> set[str]:
        """Itens da cesta básica presentes na base."""
        return {p.item_cesta for p in self._precos if p.item_cesta}

    @property
    def precos_da_cesta(self) -> list[PrecoObservado]:
        """Somente os preços que compõem o indicador oficial da cesta."""
        return [p for p in self._precos if p.da_cesta]

    def por_categoria(self, categoria: str) -> list[PrecoObservado]:
        return [p for p in self._precos if p.categoria == categoria]

    def categorias_cobertas(self) -> dict[str, int]:
        """Quantas observações há em cada categoria, da maior para a menor."""
        contagem: dict[str, int] = {}
        for p in self._precos:
            contagem[p.categoria] = contagem.get(p.categoria, 0) + 1
        return dict(sorted(contagem.items(), key=lambda kv: -kv[1]))

    def buscar_produto(self, termo: str) -> list[PrecoObservado]:
        """Busca livre na descrição dos produtos, sem acento e sem caixa."""
        from .produtos import normalizar
        alvo = normalizar(termo)
        if not alvo:
            return []
        return [p for p in self._precos if alvo in normalizar(p.descricao_original)]


# --------------------------------------------------------------------------
# Matriz produto × estabelecimento
# --------------------------------------------------------------------------

def matriz_precos(precos: Iterable[PrecoObservado],
                  usar_mais_recente: bool = True) -> dict[str, dict[str, float]]:
    """
    Monta a matriz produto × estabelecimento.

    Cada célula guarda o preço vigente daquele item naquele mercado. Quando há
    várias observações, mantém a mais recente (comportamento padrão), que é o
    que interessa para decidir onde comprar hoje.

    Estrutura devolvida:

        { "arroz": { "CNPJ1": 6.49, "CNPJ2": 7.20 }, ... }
    """
    matriz: dict[str, dict[str, float]] = {}
    datas: dict[tuple[str, str], datetime] = {}

    for p in precos:
        celula = matriz.setdefault(p.item_cesta, {})
        chave = (p.item_cesta, p.cnpj)

        if p.cnpj not in celula:
            celula[p.cnpj] = p.preco
            datas[chave] = p.observado_em
            continue

        mais_novo = p.observado_em > datas[chave]
        if (usar_mais_recente and mais_novo) or (not usar_mais_recente and p.preco < celula[p.cnpj]):
            celula[p.cnpj] = p.preco
            datas[chave] = p.observado_em

    return matriz


def imprimir_matriz(matriz: dict[str, dict[str, float]],
                    nomes: dict[str, str],
                    largura_nome: int = 18) -> str:
    """Renderiza a matriz como texto alinhado, para exibição no terminal."""
    if not matriz:
        return "(base vazia)"

    cnpjs = sorted({c for linha in matriz.values() for c in linha})
    cabecalho = "Item".ljust(largura_nome)
    for cnpj in cnpjs:
        cabecalho += nomes.get(cnpj, cnpj)[:12].rjust(14)

    linhas = [cabecalho, "-" * len(cabecalho)]

    for item in sorted(matriz):
        linha = nome_do_item(item).ljust(largura_nome)
        for cnpj in cnpjs:
            preco = matriz[item].get(cnpj)
            linha += (f"{preco:.2f}" if preco is not None else "—").rjust(14)
        linhas.append(linha)

    return "\n".join(linhas)
