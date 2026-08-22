/* Shared shell behavior kept external so sensitive pages can enforce script CSP. */
(function () {
  "use strict";

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)she_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  const originalFetch = window.fetch;
  window.fetch = function (url, options) {
    const opts = options || {};
    const method = (opts.method || "GET").toUpperCase();
    if (!(["GET", "HEAD", "OPTIONS"].includes(method))) {
      const token = csrfToken();
      const headers = new Headers(opts.headers || {});
      if (token && !headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", token);
      opts.headers = headers;
    }
    return originalFetch(url, opts);
  };

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document.body.setAttribute("data-theme", theme);
  }
  const stored = localStorage.getItem("she-theme");
  const initial = stored === "dark" ||
    (!stored && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
  applyTheme(initial);
  const themeButton = document.getElementById("theme-toggle");
  if (themeButton) themeButton.addEventListener("click", function () {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem("she-theme", next);
  });

  const navToggle = document.getElementById("nav-toggle");
  if (navToggle) navToggle.addEventListener("click", function () {
    document.getElementById("sidebar")?.classList.toggle("open");
  });

  const path = window.location.pathname;
  document.querySelectorAll(".side-nav a[data-nav]").forEach(function (link) {
    const nav = link.getAttribute("data-nav");
    if ((nav !== "/" && path.indexOf(nav) === 0) || (nav === "/" && path === "/")) {
      link.classList.add("active");
    }
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/js/sw.js")
      .then(function (registration) { console.info("SW registered", registration.scope); })
      .catch(function (error) { console.warn("SW registration failed", error); });
  }
})();
