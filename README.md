# TáBão — MVP

Monitoramento colaborativo do custo da cesta básica a partir da leitura
automatizada de cupons fiscais eletrônicos (NFC-e).

Projeto de Extensão Integrador — Fundamentos Computacionais
Tecnologia em Inteligência Artificial — Faculdade SENAI Fatesg

---

## O que já funciona

Pipeline completo, validado com um cupom fiscal **real** (CEMA Central Mineira
Atacadista, 15/05/2026, 17 itens, R$ 365,31):

| Etapa | Módulo | Estado |
|---|---|---|
| Validar a chave de acesso (44 dígitos, módulo 11) | `chave.py` | funcionando |
| Interpretar a URL do QR Code da NFC-e | `qrcode_nfce.py` | funcionando |
| Ler o QR Code de uma foto | `qrcode_nfce.py` | **validado com foto real** |
| Consultar a página da SEFAZ | `sefaz.py` | **validado contra o portal real** |
| Extrair itens, quantidades e preços do HTML | `sefaz.py` | **validado: 17 itens, R$ 365,31 exato** |
| Classificar produtos nos itens da cesta | `produtos.py` | funcionando |
| Categorizar TODOS os produtos | `categorias.py` | funcionando |
| Guardar o histórico de preços | `repositorio.py` | funcionando |
| Estatística e detecção de atípicos | `estatistica.py` | funcionando |
| Ranking de custo da cesta | `cesta.py` | funcionando |
| Interface de terminal | `cli.py` | funcionando |
| **Interface web (PWA)** | `app.py` + `templates/` | **instalável, offline parcial** |
| **Mapa de mercados** | `mapa.py` | **satélite + OpenStreetMap** |
| **Rota e viabilidade** | `rota.py` | **rota real por ruas (OSRM)** |
| **Endereço oficial pelo CNPJ** | `cnpj.py` | **funcionando (BrasilAPI)** |
| **Armazenamento em Postgres** | `banco.py` | escrito; falta testar com o Supabase |

71 testes automatizados, todos passando.

---

## Como rodar

### Interface web (recomendado para demonstrar)

```bash
pip install -r requirements.txt
python app.py          # abra http://localhost:5000
```

Telas: painel com o ranking, **mapa dos mercados**, envio de cupom (foto ou URL
do QR), conferência dos itens lidos, busca de produto, navegação por categoria
e detalhe da cesta.

Para popular o mapa e localizar os mercados que já têm preços:

```bash
python cli.py mapear "Goiânia, Goiás"   # importa mercados do OpenStreetMap
python cli.py localizar                  # geocodifica os mercados da base
```

**A base começa vazia.** Nenhum dado fictício é carregado por padrão; o comando
`python cli.py demo` existe, mas só deve ser usado quando se quiser demonstrar
o cálculo sem cupons reais suficientes.

### PWA: instalável no celular e no computador

O app declara `manifest.json`, service worker e ícones, então o navegador
oferece "Instalar" e ele abre em janela própria, sem barra de endereço.

- **Estáticos** (ícones, Leaflet) ficam em cache: abrem instantaneamente.
- **Páginas** usam rede primeiro e cache como reserva — preço é dado que
  envelhece, então a rede tem prioridade; sem conexão, aparece o último estado
  conhecido em vez de erro.
- O envio de cupom **exige rede**, porque a nota é buscada na SEFAZ.

Para instalar de outro aparelho na mesma rede, rode
`app.run(host="0.0.0.0")` e acesse pelo IP da máquina. Navegadores só oferecem
a instalação em HTTPS ou em `localhost`; num IP simples o app funciona, mas o
convite de instalação não aparece.

### Linha de comando

```bash
python -m pytest tests/ -q          # roda os testes
python cli.py demo                  # carrega dados de exemplo
python cli.py ranking               # onde a cesta sai mais barata
```

Processar um cupom:

```bash
python cli.py foto foto_do_cupom.jpg                    # a partir da foto
python cli.py cupom "https://nfeweb.sefaz.go.gov.br/..." # a partir da URL do QR
python cli.py arquivo pagina_salva.html                 # a partir de HTML salvo
python cli.py chave 52260503083231004191652170000092211671742645
```

Consultar a base:

```bash
python cli.py base            # resumo do que foi coletado
python cli.py matriz          # matriz produto x estabelecimento
python cli.py item tomate     # estatísticas de um item da cesta
python cli.py produto bacon   # compara qualquer produto entre mercados
python cli.py categorias      # o que já foi coletado, por categoria
```

---

## Mapa: OpenStreetMap em duas fontes

Nenhuma chave de API, nenhum custo:

| Fonte | Para quê |
|---|---|
| **Overpass API** | lista supermercados, atacadões, mercearias, açougues e padarias de uma região |
| **Nominatim** | converte o endereço impresso no cupom em coordenadas |

As duas são mantidas por doação e pedem no máximo uma requisição por segundo,
com User-Agent identificando a aplicação. `mapa.py` respeita as duas regras e
guarda o resultado em `dados/mapa.json` para não repetir consultas.

**Por que duas fontes:** o OpenStreetMap não conhece todo supermercado
brasileiro. Na importação de Goiânia vieram 193 estabelecimentos, e o mercado
do cupom de teste **não estava entre eles**. O endereço da nota resolveu: o
Nominatim localizou a loja e ela entrou no mapa mesmo sem existir no OSM.

No mapa, verde = já tem preços na base; laranja = ainda sem preços. É também um
convite: "envie um cupom deste mercado".

O botão "Onde estou" usa a geolocalização do navegador. A posição fica no
aparelho e **nunca é enviada ao servidor**.

---

## Vale a pena ir até lá?

O mercado mais barato nem sempre compensa. Se a economia é de R$ 8 e o
deslocamento gasta R$ 12 de combustível, o "mais barato" saiu mais caro.
`rota.py` faz essa conta:

```
custo real = custo da compra + combustível da ida e volta
```

A distância vem do **OSRM** (rota real por ruas, gratuita e sem chave de API).
Quando o serviço não responde, cai para linha reta com fator de desvio urbano
de 1,35, e a tela marca o número como estimado.

No mapa, o botão de localização calcula tudo e mostra o ranking pelo custo
TOTAL, que costuma ter ordem diferente do preço de prateleira. A posição do
usuário é usada só durante o cálculo e não é armazenada.

---

## O endereço estava errado — e por quê

O ponto do mercado no mapa estava **716 metros fora do lugar**.

O cupom fiscal traz o endereço abreviado e **sem CEP**, e ainda por cima com o
bairro divergente: a nota diz "SETOR NOVA VILA" enquanto o registro da Receita
Federal diz "VILA JARAGUÁ". Sem CEP, o Nominatim só consegue devolver o centro
da avenida — e a Avenida Engenheiro Fuad Rassi é longa o bastante para que isso
custe centenas de metros. Testando variações da mesma consulta, os resultados
ficaram até 606 m distantes entre si.

A correção foi buscar o endereço oficial pelo CNPJ na **BrasilAPI** (dados
públicos da Receita Federal, gratuita e sem chave), que devolve o CEP. Com o
CEP, o ponto cai na quadra certa.

Mesmo assim o resultado continua sendo uma aproximação, e o sistema agora diz
isso: cada estabelecimento guarda o campo `precisao` (`endereco`, `cep` ou
`rua`) e a tela mostra "posição aproximada" quando é o caso. O número 49 dessa
avenida não está mapeado no OpenStreetMap, então precisão de porta é impossível
com fontes gratuitas.

---

## Achado técnico importante: o QR Code não é conveniência, é necessidade

Testando o portal da SEFAZ-GO com a chave do cupom real, descobrimos que os
dois caminhos de consulta se comportam de maneira **diferente**:

| Caminho | Endpoint | Resultado |
|---|---|---|
| Consulta por chave digitada | `/nfeweb/sites/nfe/consulta-completa` | protegido por **Cloudflare Turnstile** (captcha) |
| Consulta pelo QR Code | `/nfeweb/sites/nfce/danfeNFCe?p=...` | responde 200, **sem captcha**, HTML renderizado no servidor |

Isso reforça a decisão de arquitetura do projeto: ler o QR Code não é apenas
mais cômodo para o usuário, é o único caminho automatizável. A consulta por
chave digitada permanece como alternativa **manual**, para quando o QR estiver
danificado.

A página do QR é renderizada no servidor (não depende de JavaScript), o que
significa que `requests` + `BeautifulSoup` bastam — não é preciso navegador
headless.

**Atenção:** a URL de Goiás mudou em 2025 (Informe Técnico 2025.003). A antiga
`http://nfe.sefaz.go.gov.br/...` foi desativada em 30/08/2025. A atual é
`https://nfeweb.sefaz.go.gov.br/nfeweb/sites/nfce/danfeNFCe`.

---

## Fluxo real do portal (descoberto em 03/09/2026)

A página aberta pelo QR Code é apenas um invólucro. Os produtos vêm de um
endpoint interno que devolve XML com o HTML do DANFE escapado dentro:

```
1. GET  <url do QR Code>                          -> cria a sessão (jsessionid)
2. GET  /nfeweb/sites/nfce/render/html/danfeNFCe?chNFe=<chave>
        -> <Map><STATUS>SUCCESS</STATUS><PARAMS><DANFE_NFCE_HTML>&lt;div...
3. desembrulhar o HTML e extrair
```

Sem o passo 1 o portal responde "Sessão Expirada". Está implementado em
`sefaz.consultar_por_qrcode`.

## O que falta validar

1. **Outros estabelecimentos.** Todo o teste foi feito com uma rede
   atacadista. Redes diferentes podem descrever produtos de outro jeito.
2. **Ampliar o dicionário de produtos.** Cada cupom novo tende a revelar
   abreviações inéditas — foi assim que "FGO" e "CONG" apareceram.
3. **Outras UFs.** Só a URL de Goiás está cadastrada em
   `qrcode_nfce.URL_CONSULTA_POR_UF`.

---

## Escopo: todos os produtos, não só a cesta

A base guarda **todos** os itens do cupom, cada um com sua categoria
(`categorias.py`). A cesta básica continua sendo o indicador oficial, calculado
com as quantidades do Decreto-Lei nº 399/1938, mas os demais produtos ficam
disponíveis para comparação entre mercados:

```bash
python cli.py produto bacon      # compara um produto qualquer
python cli.py categorias         # o que já foi coletado
python cli.py categoria carnes   # tudo de uma categoria
```

No cupom testado, só 2 das 17 linhas eram da cesta básica. Guardar apenas essas
descartaria 88% do dado já coletado.

## Três bugs encontrados por dado real

Nenhum deles apareceria com dados inventados:

- **`MEIO ASA FGO RESF`** — `FGO` é abreviação de frango. A cesta considera
  carne bovina, então `FGO` e `FRG` entraram nas exclusões.
- **`BATATA CONG UAI BATATA PC 2kg`** a R$ 25,99 — `CONG` é congelada, e
  R$ 13,00/kg é o dobro da batata in natura. Sem excluir, inflaria a cesta.
- **`AZEITONA ... IMPERADOR`** e **`MOSTARDA CEPERA`** caíam em *Hortifrúti*,
  porque a palavra-chave `PERA` casava dentro de "im**pera**dor" e "ce**pera**".
  A correção foi exigir limite de palavra (``) no casamento.
- **O tomate mais barato sumia do ranking.** Com preços de 11,8889 / 11,89 /
  11,8917 (mesmo preço por quilo, quantidades diferentes), o MAD caiu para
  0,001 e o escore z robusto marcou o tomate de R$ 8,90 como atípico
  (z = -720). A correção foi um piso relativo: quando o MAD fica abaixo de 1%
  da mediana, a dispersão passa a vir do desvio absoluto médio.

---

## Onde hospedar e armazenar, de graça

Levantamento de setembro de 2026. Camadas gratuitas mudam com frequência —
confira antes de decidir.

### O que o projeto exige do servidor

A raspagem da SEFAZ precisa de **saída HTTP com cookie de sessão**. Isso elimina
opções populares: o plano gratuito do **PythonAnywhere** só permite acessar
sites de uma lista branca, e a SEFAZ não está nela. **Fly.io** e **Koyeb**
encerraram os planos gratuitos de computação em 2026.

### Publicação na Vercel

| Arquivo | Para quê |
|---|---|
| `api/index.py` | ponto de entrada serverless (importa o mesmo `app.py`) |
| `vercel.json` | rewrites e `maxDuration` de 60 s para a consulta à SEFAZ |
| `.vercelignore` | mantém dados e testes fora da função |

**Consequência de projeto:** a Vercel limita o tamanho da função, e o OpenCV
sozinho passa de 100 MB. Por isso a leitura do QR Code passou para o navegador
(`static/vendor/jsQR.js`): a foto é decodificada no aparelho e o que vai para o
servidor é só a URL contida no código. Isso é melhor em três frentes — função
pequena, resposta imediata e a foto nunca sai do celular.

O `requirements.txt` de produção não traz OpenCV. Para usar `cli.py foto`
localmente, instale `requirements-dev.txt`.

### Banco de dados (Supabase)

`banco.py` implementa `RepositorioPostgres` com **a mesma interface** do
repositório JSON, e `criar_repositorio()` escolhe um ou outro conforme a
variável `DATABASE_URL` exista ou não. Nenhum outro módulo sabe qual está ativo.

O esquema (`banco.ESQUEMA`) tem três tabelas: `estabelecimentos`, `cupons` e
`precos`, e é criado sozinho na primeira execução. O histórico continua
imutável: cada cupom acrescenta linhas e nada é sobrescrito.

Para ligar ao Supabase, copie a *connection string* em Project Settings →
Database e defina `DATABASE_URL` no painel da Vercel.

> **Ainda não testado contra um banco real.** O código está escrito e as
> consultas revisadas, mas sem uma instância do Supabase não dá para garantir
> que rode de primeira. É o primeiro item a validar.

### Arquivos de publicação já prontos

| Arquivo | Para quê |
|---|---|
| `Procfile` | comando de execução com gunicorn |
| `render.yaml` | blueprint do Render: cria o serviço a partir do repositório |
| `runtime.txt` | versão do Python |
| `.github/workflows/manter-acordado.yml` | ping periódico contra a hibernação |
| `.gitignore` | mantém `dados/` e fotos fora do repositório |

Passos: subir o repositório para o GitHub, criar um **Blueprint** no Render
apontando para ele e definir a variável `APP_URL` nas configurações do
repositório para o ping funcionar.

O app lê do ambiente: `SECRET_KEY`, `PORT`, `FLASK_DEBUG` e `DADOS_DIR`.

**Limitação importante do plano gratuito:** o disco do Render é efêmero. A
pasta `dados/` é apagada a cada reinício, então os preços coletados se perdem.
Para a apresentação é aceitável — basta reenviar os cupons. Para uso real, é
preciso migrar o `Repositorio` para Postgres.

### Recomendação

| Camada | Serviço | Limite gratuito | Ressalva |
|---|---|---|---|
| Aplicação | **Render** (web service) | 512 MB RAM, HTTPS e domínio | hiberna após inatividade; a primeira visita demora ~50 s |
| Banco | **Supabase** (Postgres) | 500 MB, API REST e autenticação | pausa após 7 dias sem consultas |
| Código | **GitHub** | repositório e Actions | Actions serve para acordar os dois acima |

As duas ressalvas se resolvem com o mesmo truque: uma tarefa agendada no GitHub
Actions que faz uma requisição ao app a cada poucas horas. Isso mantém o Render
acordado e reseta o contador de inatividade do Supabase.

### Alternativa sem servidor sempre ligado

Se a hibernação do Render incomodar na apresentação:

- **Frontend PWA** em GitHub Pages ou Netlify — estático, sem hibernação.
- **Raspagem** numa função serverless (Vercel aceita Python no plano gratuito).
- **Banco** no Supabase, acessado direto do navegador com RLS.

É mais rápido para o usuário, mas exige reescrever a interface em JavaScript.
Para a entrega acadêmica, Flask no Render é bem menos trabalho.

### Migração do JSON para o banco

Hoje tudo vive em `dados/precos.json`, o que basta para um usuário. Para vários,
a troca é localizada: só a classe `Repositorio` conhece o armazenamento. Trocar
o corpo de `carregar`/`salvar`/`registrar_cupom` por consultas SQL não afeta
nenhum outro módulo — foi por isso que o repositório ficou isolado desde o
começo.

---

## Interface: aplicativo, não site

A navegação segue o padrão de aplicativo móvel, não de página informativa:

- **O mapa é a tela inicial.** Abrir o app já mostra os mercados em volta.
- **Barra de abas fixa embaixo** (Mapa · Preços · Cupom · Buscar), com o envio
  de cupom em botão circular destacado, porque é a ação principal do produto.
- **Busca e filtros flutuam sobre o mapa**, em pílula, com chips de categoria
  que filtram os marcadores sem recarregar a página.
- **Toque em um marcador abre um cartão inferior**, não um balão — é o padrão
  de app de navegação.
- **Listas no lugar de tabelas**: linha com título, detalhe e valor à direita,
  em vez de grade com cabeçalho.

---

## Articulação com as disciplinas do módulo

| Disciplina | Onde aparece |
|---|---|
| Algoritmos e Python | pipeline modular, tratamento de exceções, `cli.py` |
| Estrutura de Dados | fila FIFO (`FilaProcessamento`), conjuntos na deduplicação e nas exclusões, dicionários na normalização, matriz produto × estabelecimento |
| Probabilidade e Estatística | média, mediana, variância, quartis, MAD, escore z robusto, Teorema de Bayes (`estatistica.py`) |
| Lógica Matemática | validação da chave (módulo 11), regra proposicional da classificação: `pertence(item) := tem_palavra_chave ∧ ¬tem_exclusao` |

---

## Estrutura

```
tabao/
├── app.py                 interface web (Flask)
├── templates/             telas da interface web
├── static/
│   ├── manifest.json      declaração do PWA
│   ├── sw.js              service worker (cache e offline)
│   ├── icones/            ícones do aplicativo
│   └── vendor/            Leaflet (local, sem CDN)
├── cli.py                 interface de terminal
├── requirements.txt
├── tabao/
│   ├── chave.py           chave de acesso: validação e decomposição
│   ├── qrcode_nfce.py     leitura e interpretação do QR Code
│   ├── sefaz.py           consulta HTTP + extração do HTML
│   ├── mapa.py            OpenStreetMap: Overpass e Nominatim
│   ├── produtos.py        normalização e classificação na cesta
│   ├── categorias.py      categorização de todos os produtos
│   ├── modelos.py         Cupom, ItemCupom, PrecoObservado
│   ├── repositorio.py     fila, base JSON, matriz
│   ├── estatistica.py     medidas, atípicos, confiança bayesiana
│   └── cesta.py           custo da cesta e ranking
├── tests/
│   ├── test_tabao.py      66 testes
│   └── fixtures/          página real da SEFAZ + cupom transcrito
└── dados/precos.json      base coletada (gerada em tempo de execução)
```

---

## Privacidade (LGPD)

O extrator lê apenas **itens, preços e a identificação do estabelecimento**,
que é pessoa jurídica. O CPF do consumidor, quando presente na nota, é
ignorado e nunca chega a ser gravado. O cupom usado nos testes está registrado
como "CONSUMIDOR NÃO IDENTIFICADO".
