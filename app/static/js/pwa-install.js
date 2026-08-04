/* Bonos CDELU — instalación de la PWA.
 *
 * Por qué existe: el manifest ya declara display:standalone, pero eso solo
 * aplica cuando la app se abre desde el ícono INSTALADO. Si el ícono de la
 * pantalla de inicio se creó con "Agregar a pantalla de inicio" (acceso
 * directo / marcador), Android lo abre como una pestaña más del navegador,
 * con la barra de direcciones arriba o abajo.
 *
 * Este script muestra un botón "Instalar" que dispara el instalador real del
 * navegador (beforeinstallprompt → WebAPK). Ese camino SIEMPRE crea la app
 * de verdad, nunca un acceso directo.
 */
(function () {
  "use strict";

  var DISMISS_KEY = "bonos_pwa_dismissed";
  var DISMISS_DAYS = 7;
  var deferredPrompt = null;
  var banner = null;
  var promptLlego = false;

  function isStandalone() {
    try {
      if (window.matchMedia) {
        if (window.matchMedia("(display-mode: standalone)").matches) return true;
        if (window.matchMedia("(display-mode: fullscreen)").matches) return true;
        if (window.matchMedia("(display-mode: minimal-ui)").matches) return true;
      }
    } catch (_) {}
    if (navigator.standalone === true) return true; // iOS
    return false;
  }

  function isIos() {
    var ua = navigator.userAgent || "";
    if (/iPad|iPhone|iPod/i.test(ua)) return true;
    // iPadOS 13+ se hace pasar por Mac con pantalla táctil
    return /Macintosh/i.test(ua) && navigator.maxTouchPoints > 1;
  }

  function isMobile() {
    return isIos() || /Android|Mobile/i.test(navigator.userAgent || "");
  }

  function dismissedRecently() {
    try {
      var t = Number(localStorage.getItem(DISMISS_KEY) || 0);
      return t ? Date.now() - t < DISMISS_DAYS * 864e5 : false;
    } catch (_) {
      return false;
    }
  }

  function markDismissed() {
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch (_) {}
  }

  function injectStyles() {
    if (document.getElementById("pwa-install-style")) return;
    var css =
      ".pwa-install{position:fixed;left:12px;right:12px;bottom:12px;z-index:4000;" +
      "background:#1a2a4a;color:#fff;border-radius:14px;padding:12px 14px;" +
      "box-shadow:0 10px 30px rgba(0,0,0,.35);display:flex;align-items:center;gap:12px;" +
      "font-family:inherit;animation:pwaUp .25s ease-out}" +
      "@keyframes pwaUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}" +
      ".pwa-install[hidden]{display:none}" +
      ".pwa-install-ico{width:42px;height:42px;border-radius:10px;flex:0 0 42px;background:#fff;" +
      "object-fit:contain;padding:3px;box-sizing:border-box}" +
      ".pwa-install-txt{flex:1 1 auto;min-width:0;line-height:1.3}" +
      ".pwa-install-t{display:block;font-weight:700;font-size:14px}" +
      ".pwa-install-s{display:block;font-size:12px;opacity:.85;margin-top:2px}" +
      ".pwa-install-btn{flex:0 0 auto;background:#dc3545;color:#fff;border:0;border-radius:9px;" +
      "padding:10px 15px;font-size:14px;font-weight:700;cursor:pointer}" +
      ".pwa-install-btn:active{opacity:.85}" +
      ".pwa-install-x{flex:0 0 auto;background:transparent;border:0;color:#fff;opacity:.7;" +
      "font-size:22px;line-height:1;cursor:pointer;padding:4px 6px}" +
      "@media (min-width:720px){.pwa-install{left:auto;right:16px;bottom:16px;max-width:430px}}";
    var st = document.createElement("style");
    st.id = "pwa-install-style";
    st.textContent = css;
    document.head.appendChild(st);
  }

  function hideBanner() {
    if (banner) banner.hidden = true;
  }

  /* modo: "prompt" (instalador real) | "ios" | "manual" (menú del navegador) */
  function showBanner(modo) {
    if (banner || isStandalone() || dismissedRecently()) return;
    injectStyles();

    var texto;
    if (modo === "ios") {
      texto = "Tocá <b>Compartir</b> y elegí <b>Agregar a inicio</b>.";
    } else if (modo === "manual") {
      texto =
        "Menú <b>⋮</b> del navegador → <b>Instalar aplicación</b> " +
        "(no “Agregar a pantalla de inicio”).";
    } else {
      texto = "Se abre sin la barra del navegador, como una app.";
    }

    banner = document.createElement("div");
    banner.className = "pwa-install";
    banner.setAttribute("role", "dialog");
    banner.innerHTML =
      '<img class="pwa-install-ico" src="/static/icons/icon-192.png" alt="" />' +
      '<div class="pwa-install-txt">' +
      '<span class="pwa-install-t">Instalá la app</span>' +
      '<span class="pwa-install-s">' +
      texto +
      "</span></div>" +
      (modo === "prompt"
        ? '<button class="pwa-install-btn" type="button">Instalar</button>'
        : "") +
      '<button class="pwa-install-x" type="button" aria-label="Cerrar">&times;</button>';

    var btn = banner.querySelector(".pwa-install-btn");
    if (btn) {
      btn.addEventListener("click", function () {
        if (!deferredPrompt) {
          hideBanner();
          return;
        }
        btn.disabled = true;
        var p = deferredPrompt;
        deferredPrompt = null;
        try {
          p.prompt();
          if (p.userChoice && p.userChoice.then) {
            p.userChoice.then(function () {
              hideBanner();
            });
            return;
          }
        } catch (_) {}
        hideBanner();
      });
    }
    banner.querySelector(".pwa-install-x").addEventListener("click", function () {
      markDismissed();
      hideBanner();
    });

    document.body.appendChild(banner);
  }

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
    } else {
      fn();
    }
  }

  /* Android / Chrome / Brave / Edge: el navegador avisa que es instalable */
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredPrompt = e;
    promptLlego = true;
    ready(function () {
      showBanner("prompt");
    });
  });

  window.addEventListener("appinstalled", function () {
    deferredPrompt = null;
    markDismissed();
    hideBanner();
  });

  ready(function () {
    if (isStandalone()) {
      document.documentElement.classList.add("pwa-standalone");
      return;
    }
    if (!isMobile()) return;
    document.documentElement.classList.add("pwa-browser");

    if (isIos()) {
      setTimeout(function () {
        showBanner("ios");
      }, 1500);
      return;
    }
    // Android: si el navegador no dispara el evento (pasa en Brave con ciertas
    // configuraciones), igual explicamos cómo instalarla desde su menú.
    setTimeout(function () {
      if (!promptLlego) showBanner("manual");
    }, 3000);
  });

  /* Punto de entrada manual, por si querés un botón propio en alguna pantalla */
  window.BonosPWA = {
    puedeInstalar: function () {
      return !!deferredPrompt;
    },
    instalar: function () {
      if (deferredPrompt) {
        var p = deferredPrompt;
        deferredPrompt = null;
        try {
          p.prompt();
        } catch (_) {}
        return true;
      }
      try {
        localStorage.removeItem(DISMISS_KEY);
      } catch (_) {}
      showBanner(isIos() ? "ios" : "manual");
      return false;
    },
  };
})();
