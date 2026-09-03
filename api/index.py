"""
Ponto de entrada da Vercel.

A Vercel executa funções serverless a partir da pasta `api/`. Este arquivo só
importa a aplicação Flask da raiz do projeto e a expõe como `app`, que é o que
o runtime Python da Vercel procura.

Nenhuma lógica vive aqui de propósito: rodar local (`python app.py`), no Render
(gunicorn) ou na Vercel (serverless) usa exatamente o mesmo código.
"""

import sys
from pathlib import Path

# A função roda com a raiz do projeto fora do sys.path.
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import app  # noqa: E402  (o ajuste de path precisa vir antes)

# A Vercel procura por uma variável chamada `app` ou `handler`.
handler = app
