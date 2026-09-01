/* Apply the saved theme before styles render to prevent a light-frame flash. */
(function () {
  "use strict";
  const root = document.documentElement;
  const loginOnly = window.location.pathname === "/login";
  let revealFallback = 0;

  if (!loginOnly) {
    root.classList.add("ui-booting");
    revealFallback = window.setTimeout(function () {
      root.classList.remove("ui-booting");
      root.classList.add("ui-ready");
    }, 2500);
  }

  window.__ecoaegisRevealUI = function () {
    if (revealFallback) window.clearTimeout(revealFallback);
    root.classList.remove("ui-booting");
    root.classList.add("ui-ready");
  };

  let theme = "dark";
  if (!loginOnly) {
    try {
      const stored = localStorage.getItem("she-theme");
      if (stored === "light" || stored === "dark") theme = stored;
    } catch (error) {
      theme = "dark";
    }
  }
  root.setAttribute("data-theme", theme);
  root.style.colorScheme = theme;
  root.style.backgroundColor = theme === "dark" ? "#090a0b" : "#e9edf4";
})();
