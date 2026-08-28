/* Progressive shell refinements. Security-sensitive fetch and auth behavior remain in shell.js. */
(function () {
  "use strict";

  const root = document.documentElement;
  const body = document.body;
  const themeButton = document.getElementById("theme-toggle");
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  const sidebar = document.getElementById("sidebar");
  const navToggle = document.getElementById("nav-toggle");

  function currentTheme() {
    return root.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function syncChartTheme() {
    if (!window.Chart || !window.Chart.instances) return;
    const styles = getComputedStyle(body);
    const text = styles.getPropertyValue("--text-mid").trim();
    const muted = styles.getPropertyValue("--muted").trim();
    const border = styles.getPropertyValue("--border").trim();
    Object.values(window.Chart.instances).forEach(function (chart) {
      chart.options.color = text;
      Object.values(chart.options.scales || {}).forEach(function (scale) {
        scale.ticks = { ...(scale.ticks || {}), color: muted };
        scale.title = { ...(scale.title || {}), color: text };
        scale.grid = { ...(scale.grid || {}), color: border };
      });
      chart.update("none");
    });
  }

  function syncThemePresentation() {
    const dark = currentTheme() === "dark";
    if (themeButton) {
      themeButton.setAttribute("aria-pressed", String(dark));
      themeButton.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
      themeButton.title = dark ? "Switch to light theme" : "Switch to dark theme";
    }
    if (themeMeta) themeMeta.content = dark ? "#0b0d10" : "#e9edf4";
    window.requestAnimationFrame(syncChartTheme);
  }

  themeButton?.addEventListener("click", syncThemePresentation);
  syncThemePresentation();

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && sidebar?.classList.contains("open")) {
      sidebar.classList.remove("open");
      navToggle?.focus();
    }
  });

  document.addEventListener("click", function (event) {
    if (!sidebar?.classList.contains("open") || window.innerWidth > 768) return;
    if (sidebar.contains(event.target) || navToggle?.contains(event.target)) return;
    sidebar.classList.remove("open");
  });

  sidebar?.querySelectorAll("a[data-nav]").forEach(function (link) {
    link.addEventListener("click", function () {
      if (window.innerWidth <= 768) sidebar.classList.remove("open");
    });
  });

  const backgroundVideo = document.querySelector(".login-background-video");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (backgroundVideo && reducedMotion.matches) backgroundVideo.pause();

  body.classList.add("foundation-ready");
})();
