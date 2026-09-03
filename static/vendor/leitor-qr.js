/*
 * Leitura do QR Code do cupom, no próprio aparelho.
 *
 * A primeira versão só reduzia a foto para 700-1600px e passava ao jsQR. Isso
 * funciona quando o cupom preenche o quadro, mas falha quando a foto é de 12
 * megapixels e o cupom ocupa um pedaço dela: ao reduzir, o QR vira um borrão.
 *
 * Medido com uma foto real em quatro enquadramentos:
 *
 *   estratégia                      cupom grande  médio  pequeno  bem pequeno
 *   reduzir para 700-1600px              lê       falha   falha     falha
 *   escalas até a resolução cheia        lê        lê      lê        lê
 *   ladrilhos 3x3 em resolução cheia     lê        lê      lê        lê
 *   contraste e nitidez                  lê       falha   falha     falha
 *
 * Daí a ordem adotada: detector nativo (quando existe), escalas crescentes até
 * a resolução original e, por fim, varredura por ladrilhos.
 */

(function (escopo) {
  "use strict";

  // Acima disso o canvas estoura a memória de celulares mais simples.
  var LADO_MAXIMO = 3000;

  function temNativo() {
    return typeof BarcodeDetector !== "undefined";
  }

  // 1) Detector nativo do navegador (Chrome e Android). É o mais forte e o
  //    mais rápido: trabalha na resolução original sem passar pelo canvas.
  function tentarNativo(bitmap) {
    if (!temNativo()) return Promise.resolve(null);

    return BarcodeDetector.getSupportedFormats()
      .then(function (formatos) {
        if (formatos.indexOf("qr_code") === -1) return null;
        var detector = new BarcodeDetector({ formats: ["qr_code"] });
        return detector.detect(bitmap).then(function (achados) {
          return achados && achados.length ? achados[0].rawValue : null;
        });
      })
      .catch(function () { return null; });
  }

  function paraCanvas(bitmap, largura, altura, recorte) {
    var canvas = document.createElement("canvas");
    canvas.width = largura;
    canvas.height = altura;
    var ctx = canvas.getContext("2d", { willReadFrequently: true });

    if (recorte) {
      ctx.drawImage(bitmap, recorte.x, recorte.y, recorte.w, recorte.h,
                    0, 0, largura, altura);
    } else {
      ctx.drawImage(bitmap, 0, 0, largura, altura);
    }
    return ctx.getImageData(0, 0, largura, altura);
  }

  function lerImagem(dados) {
    try {
      var achado = jsQR(dados.data, dados.width, dados.height,
                        { inversionAttempts: "attemptBoth" });
      return achado && achado.data ? achado.data : null;
    } catch (e) {
      return null; // imagem grande demais para o binarizador
    }
  }

  // 2) Escalas crescentes. Reduzir demais destrói o código; por isso a lista
  //    termina na resolução original em vez de parar em 1600.
  function porEscalas(bitmap, aoProgredir) {
    var nativa = Math.min(bitmap.width, LADO_MAXIMO);
    var escalas = [1600, 2400, nativa, 1000].filter(function (l, i, lista) {
      return l <= nativa && lista.indexOf(l) === i;
    });

    for (var i = 0; i < escalas.length; i++) {
      var largura = escalas[i];
      var altura = Math.round(bitmap.height * (largura / bitmap.width));
      if (aoProgredir) aoProgredir(i + 1, escalas.length + 1);

      var achado = lerImagem(paraCanvas(bitmap, largura, altura));
      if (achado) return achado;
    }
    return null;
  }

  // 3) Ladrilhos sobrepostos em resolução original. É o que salva a foto tirada
  //    de longe: cada pedaço é pequeno o bastante para o canvas e o QR aparece
  //    nele em tamanho natural.
  function porLadrilhos(bitmap, colunas, linhas) {
    colunas = colunas || 3;
    linhas = linhas || 3;

    var sobreposicao = 0.25;
    var lw = Math.floor(bitmap.width / colunas * (1 + sobreposicao));
    var lh = Math.floor(bitmap.height / linhas * (1 + sobreposicao));

    // Percorre do centro para as bordas: o cupom quase sempre está no meio.
    var ordem = [];
    for (var c = 0; c < colunas; c++) {
      for (var l = 0; l < linhas; l++) {
        var distancia = Math.abs(c - (colunas - 1) / 2) + Math.abs(l - (linhas - 1) / 2);
        ordem.push({ c: c, l: l, distancia: distancia });
      }
    }
    ordem.sort(function (a, b) { return a.distancia - b.distancia; });

    for (var i = 0; i < ordem.length; i++) {
      var x = Math.max(0, Math.min(Math.floor(ordem[i].c * bitmap.width / colunas),
                                   bitmap.width - lw));
      var y = Math.max(0, Math.min(Math.floor(ordem[i].l * bitmap.height / linhas),
                                   bitmap.height - lh));

      var achado = lerImagem(paraCanvas(bitmap, lw, lh, { x: x, y: y, w: lw, h: lh }));
      if (achado) return achado;
    }
    return null;
  }

  /**
   * Lê o QR Code de um arquivo de imagem.
   *
   * `aoProgredir(etapa, total)` é chamado entre as tentativas, para que a tela
   * possa mostrar que ainda está trabalhando: numa foto grande a varredura
   * completa leva alguns segundos.
   *
   * Devolve uma Promise com o conteúdo lido, ou null.
   */
  function ler(arquivo, aoProgredir) {
    // imageOrientation resolve a rotação registrada no EXIF pelo celular.
    return createImageBitmap(arquivo, { imageOrientation: "from-image" })
      .catch(function () { return createImageBitmap(arquivo); })
      .then(function (bitmap) {
        return tentarNativo(bitmap).then(function (nativo) {
          if (nativo) return nativo;

          var porEscala = porEscalas(bitmap, aoProgredir);
          if (porEscala) return porEscala;

          if (aoProgredir) aoProgredir(0, 0); // sinaliza a varredura fina
          return porLadrilhos(bitmap);
        }).then(function (resultado) {
          if (bitmap.close) bitmap.close();
          return resultado;
        });
      });
  }

  escopo.LeitorQR = { ler: ler, temNativo: temNativo };
})(window);
