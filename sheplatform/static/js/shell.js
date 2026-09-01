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

  const darkOnlyPage = window.location.pathname === "/login";

  function applyTheme(theme) {
    const selected = darkOnlyPage ? "dark" : theme;
    document.documentElement.setAttribute("data-theme", selected);
    document.documentElement.style.colorScheme = selected;
    document.body.setAttribute("data-theme", selected);
  }
  const bootTheme = document.documentElement.getAttribute("data-theme");
  const initial = bootTheme === "light" ? "light" : "dark";
  applyTheme(initial);
  const themeButton = document.getElementById("theme-toggle");
  if (themeButton) themeButton.addEventListener("click", function () {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem("she-theme", next);
  });

  const navToggle = document.getElementById("nav-toggle");
  if (navToggle) navToggle.addEventListener("click", function () {
    const sidebar = document.getElementById("sidebar");
    const open = sidebar?.classList.toggle("open") || false;
    navToggle.setAttribute("aria-expanded", String(open));
    navToggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
  });

  const path = window.location.pathname;
  const navLinks = Array.from(document.querySelectorAll(".side-nav a[data-nav]"));
  const activeLink = navLinks
    .filter(function (link) {
      const nav = link.getAttribute("data-nav");
      return nav === "/" ? path === "/" : path === nav || path.indexOf(nav + "/") === 0;
    })
    .sort(function (left, right) {
      return right.getAttribute("data-nav").length - left.getAttribute("data-nav").length;
    })[0];
  activeLink?.classList.add("active");

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/js/sw.js")
      .then(function (registration) { console.info("SW registered", registration.scope); })
      .catch(function (error) { console.warn("SW registration failed", error); });
  }
})();
