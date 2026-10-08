"""
Limite de envios por endereço de origem.

Cada cupom enviado vira duas consultas ao portal da SEFAZ. Sem limite, um
script (ou um usuário impaciente) pode fazer o portal bloquear o IP do
servidor, e aí ninguém mais consegue enviar.

A contagem fica na memória do processo. Em hospedagem serverless cada
instância conta separado, então o limite é aproximado; ainda assim barra o
abuso mais comum, que é uma rajada de requisições caindo na mesma instância
aquecida. Um limite exato exigiria um armazenamento compartilhado (Redis).
"""

import threading
import time
from collections import defaultdict, deque


class LimitadorDeTaxa:
    """Janela deslizante: no máximo `limite` eventos em `janela` segundos."""

    def __init__(self, limite: int, janela: float) -> None:
        self.limite = limite
        self.janela = janela
        self._eventos: dict[str, deque] = defaultdict(deque)
        self._trava = threading.Lock()

    def permitir(self, chave: str, agora: float | None = None) -> bool:
        """Registra o evento e diz se ele cabe no limite."""
        agora = time.monotonic() if agora is None else agora
        with self._trava:
            eventos = self._eventos[chave]
            while eventos and agora - eventos[0] >= self.janela:
                eventos.popleft()
            if len(eventos) >= self.limite:
                return False
            eventos.append(agora)
            # Evita que o dicionário cresça sem fim com IPs que não voltam.
            if len(self._eventos) > 10_000:
                self._eventos = defaultdict(deque, {chave: eventos})
            return True

    def limpar(self) -> None:
        with self._trava:
            self._eventos.clear()
