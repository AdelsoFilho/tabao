"""
TáBão — monitoramento colaborativo do custo da cesta básica.

Pipeline do MVP:

    QR Code do cupom  ->  qrcode_nfce  ->  chave (validação)
                                        -> sefaz (consulta + extração)
                                        -> produtos (normalização)
                                        -> repositorio (base colaborativa)
                                        -> cesta / estatistica (análise)
"""

__version__ = "0.1.0"

from .chave import chave_e_valida, interpretar
from .qrcode_nfce import interpretar_url, ler_cupom, montar_url_por_chave
from .sefaz import consultar, extrair
from .produtos import classificar, normalizar
from .repositorio import FilaProcessamento, Repositorio, matriz_precos
from .cesta import estatisticas_do_item, formatar_ranking, montar_ranking

__all__ = [
    "chave_e_valida", "interpretar",
    "interpretar_url", "ler_cupom", "montar_url_por_chave",
    "consultar", "extrair",
    "classificar", "normalizar",
    "FilaProcessamento", "Repositorio", "matriz_precos",
    "montar_ranking", "formatar_ranking", "estatisticas_do_item",
]
