/* Progressive interaction layer for the shared EcoAegis shell. */
(function () {
  "use strict";

  const root = document.documentElement;
  const body = document.body;
  const themeButton = document.getElementById("theme-toggle");
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  const sidebar = document.getElementById("sidebar");
  const navToggle = document.getElementById("nav-toggle");
  const sidebarScroll = sidebar?.querySelector("[data-sidebar-scroll]");
  const groupKey = "ecoaegis.sidebar.groups.v1";
  const scrollKey = "ecoaegis.sidebar.scroll.v1";
  let activeTray = null;
  let trayOpener = null;
  let restoringSidebar = false;

  function revealUI() {
    if (typeof window.__ecoaegisRevealUI === "function") {
      window.__ecoaegisRevealUI();
      return;
    }
    root.classList.remove("ui-booting");
    root.classList.add("ui-ready");
  }

  function currentTheme() {
    return root.getAttribute("data-theme") === "light" ? "light" : "dark";
  }

  function syncChartTheme() {
    if (!window.Chart || !window.Chart.instances) return;
    const styles = getComputedStyle(body);
    const text = styles.getPropertyValue("--text-mid").trim();
    const muted = styles.getPropertyValue("--muted").trim();
    const border = styles.getPropertyValue("--border").trim();
    const charts = window.Chart.instances instanceof Map
      ? Array.from(window.Chart.instances.values())
      : Object.values(window.Chart.instances);
    charts.forEach(function (chart) {
      const options = chart.config.options || {};
      options.color = text;
      options.plugins = options.plugins || {};
      if (options.plugins.legend) {
        options.plugins.legend.labels = {
          ...(options.plugins.legend.labels || {}),
          color: muted,
          boxWidth: 9,
          boxHeight: 9,
        };
      }
      Object.keys(options.scales || {}).forEach(function (key) {
        const scale = options.scales[key];
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
    if (themeMeta) themeMeta.content = dark ? "#090a0b" : "#e9edf4";
    window.requestAnimationFrame(syncChartTheme);
  }

  function closeMobileSidebar() {
    if (!sidebar?.classList.contains("open")) return;
    sidebar.classList.remove("open");
    navToggle?.setAttribute("aria-expanded", "false");
    navToggle?.setAttribute("aria-label", "Open navigation");
  }

  function parseJSON(value, fallback) {
    if (value == null || value === "") return fallback;
    try {
      const parsed = JSON.parse(value);
      return parsed == null ? fallback : parsed;
    } catch (error) {
      return fallback;
    }
  }

  function saveSidebarState() {
    if (!sidebar || restoringSidebar) return;
    const state = {};
    sidebar.querySelectorAll("[data-sidebar-group]").forEach(function (group) {
      state[group.dataset.sidebarGroup] = group.open;
    });
    try {
      localStorage.setItem(groupKey, JSON.stringify(state));
      if (sidebarScroll) sessionStorage.setItem(scrollKey, String(sidebarScroll.scrollTop));
    } catch (error) {
      /* Storage can be unavailable in private or hardened browser modes. */
    }
  }

  function restoreSidebarState(onReady) {
    if (!sidebar) {
      onReady?.();
      return;
    }
    restoringSidebar = true;
    let state = {};
    let savedScroll = 0;
    try {
      state = parseJSON(localStorage.getItem(groupKey), {});
      savedScroll = Number.parseInt(sessionStorage.getItem(scrollKey) || "0", 10) || 0;
    } catch (error) {
      state = {};
    }
    sidebar.querySelectorAll("[data-sidebar-group]").forEach(function (group) {
      const name = group.dataset.sidebarGroup;
      if (Object.prototype.hasOwnProperty.call(state, name)) group.open = Boolean(state[name]);
      group.addEventListener("toggle", saveSidebarState);
    });
    const activeLink = sidebar.querySelector("a.active");
    const activeGroup = activeLink?.closest("[data-sidebar-group]");
    if (activeGroup) activeGroup.open = true;
    const title = activeLink?.querySelector("span")?.textContent?.trim();
    if (title) {
      const topbarTitle = document.getElementById("topbar-title");
      if (topbarTitle) topbarTitle.textContent = title === "Overview" ? "Operational overview" : title;
    }
    if (sidebarScroll) {
      const applySavedScroll = function () {
        const maxScroll = Math.max(0, sidebarScroll.scrollHeight - sidebarScroll.clientHeight);
        sidebarScroll.scrollTop = Math.min(savedScroll, maxScroll);
      };
      window.requestAnimationFrame(function () {
        window.requestAnimationFrame(function () {
          applySavedScroll();
          window.setTimeout(function () {
            applySavedScroll();
            restoringSidebar = false;
          }, 120);
          onReady?.();
        });
      });
      let scrollFrame = 0;
      sidebarScroll.addEventListener("scroll", function () {
        window.cancelAnimationFrame(scrollFrame);
        scrollFrame = window.requestAnimationFrame(saveSidebarState);
      }, { passive: true });
    } else {
      restoringSidebar = false;
      onReady?.();
    }
  }

  function trayOverlay() {
    let overlay = document.querySelector("[data-input-tray-overlay]");
    if (!overlay) {
      overlay = document.createElement("button");
      overlay.type = "button";
      overlay.className = "input-tray-overlay";
      overlay.setAttribute("data-input-tray-overlay", "");
      overlay.setAttribute("aria-label", "Close input tray");
      overlay.hidden = true;
      body.appendChild(overlay);
      overlay.addEventListener("click", closeInputTray);
    }
    return overlay;
  }

  function focusableElements(container) {
    return Array.from(container.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
      'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter(function (element) {
      return !element.hidden && element.getAttribute("aria-hidden") !== "true";
    });
  }

  function openInputTray(tray, opener) {
    if (!tray) return;
    if (activeTray && activeTray !== tray) closeInputTray();
    activeTray = tray;
    trayOpener = opener || document.activeElement;
    tray.classList.add("is-open");
    tray.removeAttribute("inert");
    tray.setAttribute("aria-hidden", "false");
    const overlay = trayOverlay();
    overlay.hidden = false;
    window.requestAnimationFrame(function () {
      overlay.classList.add("is-open");
      body.classList.add("input-tray-open");
      (tray.querySelector("[data-input-tray-close]") || focusableElements(tray)[0] || tray).focus();
    });
  }

  function closeInputTray() {
    if (!activeTray) return;
    const closing = activeTray;
    activeTray = null;
    closing.classList.remove("is-open");
    closing.setAttribute("aria-hidden", "true");
    closing.setAttribute("inert", "");
    if (closing.id) {
      document.querySelectorAll('[data-input-tray-open="' + closing.id + '"]').forEach(function (launcher) {
        launcher.setAttribute("aria-expanded", "false");
      });
    }
    const overlay = document.querySelector("[data-input-tray-overlay]");
    overlay?.classList.remove("is-open");
    body.classList.remove("input-tray-open");
    window.setTimeout(function () {
      if (overlay) overlay.hidden = true;
    }, 220);
    trayOpener?.focus?.();
    trayOpener = null;
  }

  function launchArea() {
    const explicit = document.querySelector(".page-heading-premium .heading-actions");
    if (explicit) return explicit;
    let actions = document.querySelector(".workspace-actions");
    if (actions) return actions;
    actions = document.createElement("div");
    actions.className = "workspace-actions";
    const heading = document.querySelector(".content > h1");
    const intro = heading?.nextElementSibling?.matches(".login-sub") ? heading.nextElementSibling : heading;
    if (intro) intro.insertAdjacentElement("afterend", actions);
    else document.querySelector(".content")?.prepend(actions);
    return actions;
  }

  function enhanceTray(card, index) {
    if (card.dataset.inputTrayReady === "true") return;
    card.dataset.inputTrayReady = "true";
    if (!card.id) card.id = "input-tray-" + index;
    const heading = card.querySelector("h2");
    const label = card.dataset.inputTrayLabel || heading?.textContent?.trim() || "Add record";
    card.classList.add("input-tray");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-hidden", "true");
    card.setAttribute("aria-label", label);
    card.setAttribute("inert", "");
    card.tabIndex = -1;

    const trayHeader = document.createElement("div");
    trayHeader.className = "input-tray-header";
    const eyebrow = document.createElement("span");
    eyebrow.textContent = "Input tray";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "input-tray-close";
    close.setAttribute("data-input-tray-close", "");
    close.setAttribute("aria-label", "Close " + label);
    close.innerHTML = "<span aria-hidden=\"true\">&times;</span>";
    trayHeader.append(eyebrow, close);
    card.prepend(trayHeader);
    card.querySelectorAll("[data-input-tray-close]").forEach(function (button) {
      button.addEventListener("click", closeInputTray);
    });

    let launchers = Array.from(document.querySelectorAll('[data-input-tray-open="' + card.id + '"]'));
    if (!launchers.length) {
      const launcher = document.createElement("button");
      launcher.type = "button";
      launcher.className = "btn btn-primary input-tray-launch";
      launcher.dataset.inputTrayOpen = card.id;
      launcher.innerHTML = '<span aria-hidden="true">+</span><span>' + label + "</span>";
      launchArea()?.appendChild(launcher);
      launchers = [launcher];
    }
    launchers.forEach(function (launcher) {
      launcher.setAttribute("aria-controls", card.id);
      launcher.setAttribute("aria-expanded", "false");
      launcher.addEventListener("click", function () {
        launcher.setAttribute("aria-expanded", "true");
        openInputTray(card, launcher);
      });
    });
    if (card.parentElement !== body) body.appendChild(card);
  }

  function enhanceInputTrays() {
    if (body.classList.contains("login-page") || body.classList.contains("map-page")) return;
    const explicit = Array.from(document.querySelectorAll("[data-input-tray]"));
    const candidates = Array.from(document.querySelectorAll(".content > .card")).filter(function (card) {
      if (card.hasAttribute("data-no-input-tray") || card.hasAttribute("data-input-tray")) return false;
      if (!card.querySelector("form") || card.querySelector("table, canvas")) return false;
      if (card.style.display === "none") return false;
      return true;
    });
    const surfaces = Array.from(document.querySelectorAll(".content > .card, .content > .kpi-grid, .content > [data-workspace-surface]"));
    const auto = surfaces.length > candidates.length ? candidates : [];
    explicit.concat(auto).forEach(enhanceTray);
  }

  function preparePremiumHover() {
    document.addEventListener("pointermove", function (event) {
      const surface = event.target.closest(".premium-hover, .kpi-card, .command-panel, .permit-card");
      if (!surface) return;
      const bounds = surface.getBoundingClientRect();
      surface.style.setProperty("--mx", event.clientX - bounds.left + "px");
      surface.style.setProperty("--my", event.clientY - bounds.top + "px");
    }, { passive: true });
  }

  function prepareTabs() {
    document.querySelectorAll('[role="tablist"]').forEach(function (tablist) {
      const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
      tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
          const targetId = tab.getAttribute("aria-controls");
          tabs.forEach(function (candidate) {
            const selected = candidate === tab;
            candidate.setAttribute("aria-selected", String(selected));
            candidate.tabIndex = selected ? 0 : -1;
            const panel = document.getElementById(candidate.getAttribute("aria-controls"));
            if (panel) panel.hidden = !selected;
          });
          document.getElementById(targetId)?.focus({ preventScroll: true });
        });
        tab.addEventListener("keydown", function (event) {
          if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
          event.preventDefault();
          const current = tabs.indexOf(tab);
          const offset = event.key === "ArrowRight" ? 1 : -1;
          tabs[(current + offset + tabs.length) % tabs.length].click();
          tabs[(current + offset + tabs.length) % tabs.length].focus();
        });
      });
    });
  }

  themeButton?.addEventListener("click", syncThemePresentation);
  syncThemePresentation();
  enhanceInputTrays();
  preparePremiumHover();
  prepareTabs();
  body.classList.add("foundation-ready");
  restoreSidebarState(revealUI);

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && activeTray) {
      closeInputTray();
      return;
    }
    if (event.key === "Escape" && sidebar?.classList.contains("open")) {
      closeMobileSidebar();
      navToggle?.focus();
      return;
    }
    if (event.key === "Tab" && activeTray) {
      const focusable = focusableElements(activeTray);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  document.addEventListener("click", function (event) {
    if (!sidebar?.classList.contains("open") || window.innerWidth > 768) return;
    if (sidebar.contains(event.target) || navToggle?.contains(event.target)) return;
    closeMobileSidebar();
  });

  sidebar?.querySelectorAll("a[data-nav]").forEach(function (link) {
    link.addEventListener("click", function () {
      saveSidebarState();
      if (window.innerWidth <= 768) closeMobileSidebar();
    });
  });
  window.addEventListener("pagehide", saveSidebarState);

  const backgroundVideo = document.querySelector(".login-background-video");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (backgroundVideo && reducedMotion.matches) backgroundVideo.pause();

})();
