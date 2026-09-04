/*
 * Service worker do TáBão.
 *
 * Estratégia em duas camadas:
 *
 *   estáticos (ícones, Leaflet)  -> cache primeiro, rede como reserva
 *   páginas e dados              -> rede primeiro, cache como reserva
 *
 * Assim o app abre instantaneamente e continua utilizável sem internet,
 * mostrando os últimos preços vistos em vez de uma tela de erro. Preços são
 * dado que envelhece: a rede sempre tem prioridade quando está disponível.
 */

const VERSAO = "tabao-v2";
const CACHE_ESTATICO = `${VERSAO}-estatico`;
const CACHE_PAGINAS = `${VERSAO}-paginas`;

const ESSENCIAIS = [
  "/",
  "/offline",
  "/static/manifest.json",
  "/static/vendor/leaflet.js",
  "/static/vendor/jsQR.js",
  "/static/vendor/leitor-qr.js",
  "/static/vendor/leaflet.css",
  "/static/vendor/marker-icon.png",
  "/static/vendor/marker-icon-2x.png",
  "/static/vendor/marker-shadow.png",
  "/static/icones/icone-192.png",
  "/static/icones/icone-512.png",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(
    caches.open(CACHE_ESTATICO)
      .then((cache) => cache.addAll(ESSENCIAIS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((chaves) => Promise.all(
        chaves
          .filter((chave) => !chave.startsWith(VERSAO))
          .map((chave) => caches.delete(chave))
      ))
      .then(() => self.clients.claim())
  );
});

function ehEstatico(url) {
  return url.pathname.startsWith("/static/");
}

self.addEventListener("fetch", (evento) => {
  const requisicao = evento.request;

  // Só interceptamos GET: envio de cupom precisa da rede de verdade.
  if (requisicao.method !== "GET") return;

  const url = new URL(requisicao.url);
  if (url.origin !== self.location.origin) return;   // tiles do mapa, etc.

  if (ehEstatico(url)) {
    evento.respondWith(
      caches.match(requisicao).then((guardado) =>
        guardado || fetch(requisicao).then((resposta) => {
          const copia = resposta.clone();
          caches.open(CACHE_ESTATICO).then((cache) => cache.put(requisicao, copia));
          return resposta;
        })
      )
    );
    return;
  }

  // Páginas: rede primeiro, para que os preços estejam sempre atualizados.
  evento.respondWith(
    fetch(requisicao)
      .then((resposta) => {
        const copia = resposta.clone();
        caches.open(CACHE_PAGINAS).then((cache) => cache.put(requisicao, copia));
        return resposta;
      })
      .catch(() =>
        caches.match(requisicao).then((guardado) => guardado || caches.match("/offline"))
      )
  );
});
