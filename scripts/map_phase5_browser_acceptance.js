const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const baseUrl = process.argv[2] || "http://127.0.0.1:8092";
const artifactDir = process.argv[3] || path.join(process.cwd(), "phase5-browser-artifacts");
const browserPath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

function check(condition, message) {
  if (!condition) throw new Error(message);
}

function monitor(page, label, externalRequests) {
  const pageErrors = [];
  const consoleErrors = [];
  const failedResponses = [];
  page.on("pageerror", (error) => pageErrors.push(`${label}: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(`${label}: ${message.text()}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== baseUrl) externalRequests.push(request.url());
  });
  return { pageErrors, consoleErrors, failedResponses };
}

async function signIn(page) {
  await page.locator("#login-email").fill("superadmin@she.local");
  await page.locator("#login-password").fill("ChangeMe!123");
  await Promise.all([
    page.waitForURL(`${baseUrl}/`),
    page.locator("button[type='submit']").click(),
  ]);
  await page.locator("body.foundation-ready").waitFor();
}

async function commonAccessibility(page, label) {
  const result = await page.evaluate(() => {
    const visible = (element) => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0;
    };
    const ids = [...document.querySelectorAll("[id]")].map((element) => element.id);
    const duplicateIds = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
    const missingAlt = [...document.querySelectorAll("img")]
      .filter(visible)
      .filter((image) => !image.hasAttribute("alt"))
      .map((image) => image.outerHTML.slice(0, 120));
    const accessibleName = (element) => {
      const aria = element.getAttribute("aria-label") || element.getAttribute("aria-labelledby");
      const labels = element.labels ? [...element.labels].map((item) => item.textContent.trim()).join(" ") : "";
      return aria || labels || element.textContent.trim() || element.title || "";
    };
    const controls = [...document.querySelectorAll("input:not([type='hidden']), select, textarea, button")]
      .filter(visible)
      .filter((element) => !accessibleName(element))
      .map((element) => `${element.tagName.toLowerCase()}#${element.id || "(no-id)"}`);
    const headings = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")]
      .filter(visible)
      .map((heading) => Number(heading.tagName.slice(1)));
    const headingJumps = headings.filter((level, index) => index > 0 && level > headings[index - 1] + 1);
    return {
      duplicateIds,
      missingAlt,
      unnamedControls: controls,
      mainCount: document.querySelectorAll("main").length,
      h1Count: document.querySelectorAll("h1").length,
      headingJumps,
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  });
  check(result.duplicateIds.length === 0, `${label}: duplicate IDs: ${result.duplicateIds.join(", ")}`);
  check(result.missingAlt.length === 0, `${label}: visible images without alt text`);
  check(result.unnamedControls.length === 0, `${label}: unnamed controls: ${result.unnamedControls.join(", ")}`);
  check(result.mainCount === 1, `${label}: expected one main landmark, found ${result.mainCount}`);
  check(result.h1Count === 1, `${label}: expected one h1, found ${result.h1Count}`);
  check(result.headingJumps.length === 0, `${label}: heading hierarchy skips a level`);
  check(!result.horizontalOverflow, `${label}: horizontal overflow detected`);
}

async function verifySkipLink(page) {
  const skip = page.locator(".skip-link");
  await skip.focus();
  check(await skip.evaluate((element) => document.activeElement === element), "Skip link cannot receive focus");
  check(await skip.evaluate((element) => {
    const style = getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none" && element.getBoundingClientRect().height > 0;
  }), "Skip link is not visible when focused");
  await page.keyboard.press("Enter");
  check(await page.locator("#main-content").evaluate((element) => document.activeElement === element),
    "Skip link did not move focus to main content");
}

async function verifyKeyboardFocus(page) {
  await page.locator("#main-content").focus();
  const visited = [];
  let visibleFocusCount = 0;
  for (let index = 0; index < 18; index += 1) {
    await page.keyboard.press("Tab");
    const state = await page.evaluate(() => {
      const element = document.activeElement;
      const style = getComputedStyle(element);
      return {
        key: `${element.tagName}:${element.id || element.getAttribute("href") || element.textContent.trim().slice(0, 25)}`,
        focusVisible: element.matches(":focus-visible"),
        indicator: style.outlineStyle !== "none" || style.boxShadow !== "none" || style.borderColor !== "rgba(0, 0, 0, 0)",
      };
    });
    visited.push(state.key);
    if (state.focusVisible && state.indicator) visibleFocusCount += 1;
  }
  check(new Set(visited).size >= 8, "Keyboard focus did not progress through enough distinct controls");
  check(visibleFocusCount >= 8, "Keyboard focus indicators were not visible on enough controls");
}

async function contrastFor(page, selector) {
  return page.locator(selector).first().evaluate((element) => {
    function rgba(value) {
      const match = value.match(/rgba?\(([^)]+)\)/);
      if (!match) return null;
      const parts = match[1].split(/[ ,/]+/).filter(Boolean).map(Number);
      return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
    }
    function blend(top, bottom) {
      const alpha = top.a + bottom.a * (1 - top.a);
      if (!alpha) return { r: 255, g: 255, b: 255, a: 1 };
      return {
        r: (top.r * top.a + bottom.r * bottom.a * (1 - top.a)) / alpha,
        g: (top.g * top.a + bottom.g * bottom.a * (1 - top.a)) / alpha,
        b: (top.b * top.a + bottom.b * bottom.a * (1 - top.a)) / alpha,
        a: alpha,
      };
    }
    function luminance(color) {
      const values = [color.r, color.g, color.b].map((channel) => {
        const value = channel / 255;
        return value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
      });
      return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722;
    }
    let background = { r: 255, g: 255, b: 255, a: 1 };
    const layers = [];
    let current = element;
    while (current) {
      const color = rgba(getComputedStyle(current).backgroundColor);
      if (color && color.a > 0) layers.push(color);
      current = current.parentElement;
    }
    for (let index = layers.length - 1; index >= 0; index -= 1) background = blend(layers[index], background);
    const foreground = blend(rgba(getComputedStyle(element).color), background);
    const light = Math.max(luminance(foreground), luminance(background));
    const dark = Math.min(luminance(foreground), luminance(background));
    return Number(((light + 0.05) / (dark + 0.05)).toFixed(2));
  });
}

async function verifyThemeContrast(page, theme) {
  const themeButton = page.locator("#theme-toggle");
  const current = await page.evaluate(() => document.documentElement.dataset.theme || document.body.dataset.theme);
  if (current !== theme) await themeButton.click();
  check((await themeButton.getAttribute("aria-pressed")) === String(theme === "dark"),
    `${theme}: theme toggle state is incorrect`);
  const checks = [
    [".side-nav a[data-nav='/map']", 4.5],
    [".page-heading-premium h1", 3.0],
    ["#map-provider-status p", 4.5],
    ["#map-data-status", 4.5],
  ];
  for (const [selector, threshold] of checks) {
    const ratio = await contrastFor(page, selector);
    check(ratio >= threshold, `${theme}: ${selector} contrast ${ratio}:1 is below ${threshold}:1`);
  }
}

async function primaryRun(browser) {
  const externalRequests = [];
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    colorScheme: "light",
    reducedMotion: "no-preference",
  });
  const page = await context.newPage();
  const errors = monitor(page, "primary", externalRequests);

  await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle" });
  await page.locator("body.foundation-ready").waitFor();
  await commonAccessibility(page, "login");
  check(await page.locator(".login-eyebrow").innerText() === "Enterprise safety, connected",
    "Login eyebrow copy is incorrect");
  check((await page.locator(".login-background-video").evaluate((video) => video.readyState)) >= 1,
    "Login background video metadata did not load");
  await page.screenshot({ path: path.join(artifactDir, "login-light.png"), fullPage: true });

  await signIn(page);
  await commonAccessibility(page, "dashboard");
  await verifySkipLink(page);
  await Promise.all([page.waitForURL(`${baseUrl}/map`), page.locator("a[data-nav='/map']").click()]);
  await page.waitForTimeout(1200);
  const startupState = await page.evaluate(() => ({
    engine: document.getElementById("map")?.dataset.engine,
    lifecycle: document.getElementById("map")?.dataset.lifecycleState || "unset",
    continuityHidden: document.getElementById("map-continuity")?.hidden,
    providerStatus: document.getElementById("map-provider-status")?.innerText,
    mapboxLoaded: Boolean(window.mapboxgl),
    scripts: [...document.scripts].map((script) => script.src).filter(Boolean),
  }));
  console.log(`Map startup state: ${JSON.stringify(startupState)}`);
  await page.waitForFunction(() => document.getElementById("map-continuity")?.hidden === false,
    null, { timeout: 15000 });
  await page.locator("#map-data-status:not([data-state='loading'])").waitFor();
  await commonAccessibility(page, "map fallback");

  check(await page.locator("#map").getAttribute("data-engine") === "mapbox", "Mapbox engine was not selected");
  check(await page.locator("#map").getAttribute("data-lifecycle-state") === "failed",
    "Missing-token flow did not fail closed before constructing Mapbox");
  check(await page.locator("#map").isHidden(), "Provider-free mode did not hide the unusable map canvas");
  check(await page.locator("#map-continuity").isVisible(), "Provider-free continuity panel is not visible");
  check((await page.locator("#map-provider-status").innerText()).includes("Basemap unavailable"),
    "Honest basemap-unavailable status is missing");
  check((await page.locator("#map-continuity-message").innerText()).includes("Authorized EcoAegis records remain available"),
    "Continuity explanation is missing");
  check(await page.locator("#map-continuity-list li").count() >= 1,
    "Continuity list did not render an explicit record or empty state");
  check(await page.locator("#map .mapboxgl-canvas").count() === 0,
    "Unexpected Mapbox canvas was constructed");
  check(errors.failedResponses.some((item) => item.includes("503") && item.includes("/map/api/provider-session")),
    "Expected missing-token provider-session refusal was not observed");

  const sameOriginData = await page.evaluate(() => performance.getEntriesByType("resource")
    .map((entry) => entry.name)
    .filter((url) => url.includes("/map/api/manifest") || url.includes("/map/api/layers/"))
    .every((url) => new URL(url).origin === location.origin));
  check(sameOriginData, "Operational GeoJSON was requested from outside the application origin");
  check(externalRequests.filter((url) => /mapbox\.com/i.test(url)).length === 0,
    `Mapbox network traffic occurred before admission: ${externalRequests.join(", ")}`);

  await verifyThemeContrast(page, "light");
  await page.screenshot({ path: path.join(artifactDir, "map-fallback-light.png"), fullPage: true });
  await verifyThemeContrast(page, "dark");
  await verifyKeyboardFocus(page);
  await page.screenshot({ path: path.join(artifactDir, "map-fallback-dark.png"), fullPage: true });

  const unexpectedResponses = errors.failedResponses.filter((item) =>
    !(item.includes("503") && item.includes("/map/api/provider-session")));
  const unexpectedConsoleErrors = errors.consoleErrors.filter((item) =>
    !item.includes("Failed to load resource: the server responded with a status of 503"));
  check(unexpectedResponses.length === 0, `Unexpected failed responses: ${unexpectedResponses.join(" | ")}`);
  check(errors.pageErrors.length === 0, `Page errors: ${errors.pageErrors.join(" | ")}`);
  check(unexpectedConsoleErrors.length === 0, `Console errors: ${unexpectedConsoleErrors.join(" | ")}`);
  await context.close();
}

async function reducedMotionRun(browser) {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle" });
  await page.locator("body.foundation-ready").waitFor();
  check(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches),
    "Reduced-motion preference was not active");
  check(await page.locator(".login-background-video").evaluate((video) => video.paused),
    "Login background video was not paused for reduced motion");
  await context.close();
}

async function forcedColorsRun(browser) {
  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    colorScheme: "light",
    forcedColors: "active",
  });
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle" });
  await signIn(page);
  check(await page.evaluate(() => matchMedia("(forced-colors: active)").matches),
    "Forced-colors mode was not active");
  check(await page.locator("#theme-toggle").isVisible(), "Theme control disappeared in forced-colors mode");
  check(await page.locator("a[data-nav='/map']").isVisible(), "Map navigation disappeared in forced-colors mode");
  check(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1),
    "Forced-colors dashboard has horizontal overflow");
  await page.screenshot({ path: path.join(artifactDir, "dashboard-forced-colors.png"), fullPage: true });
  await context.close();
}

async function mobileRun(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: "light" });
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle" });
  await signIn(page);
  await page.locator("#nav-toggle").click();
  check(await page.locator("#sidebar").evaluate((element) => element.classList.contains("open")),
    "Mobile navigation did not open");
  await page.keyboard.press("Escape");
  check(!await page.locator("#sidebar").evaluate((element) => element.classList.contains("open")),
    "Escape did not close mobile navigation");
  check(await page.locator("#nav-toggle").evaluate((element) => document.activeElement === element),
    "Mobile navigation focus was not restored after Escape");
  await page.locator("#nav-toggle").click();
  await Promise.all([page.waitForURL(`${baseUrl}/map`), page.locator("a[data-nav='/map']").click()]);
  check(!await page.locator("#sidebar").evaluate((element) => element.classList.contains("open")),
    "Mobile navigation stayed open after route change");
  check(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1),
    "Mobile map fallback has horizontal overflow");
  await page.screenshot({ path: path.join(artifactDir, "map-fallback-mobile.png"), fullPage: true });
  await context.close();
}

(async () => {
  fs.mkdirSync(artifactDir, { recursive: true });
  const browser = await chromium.launch({ executablePath: browserPath, headless: true });
  try {
    await primaryRun(browser);
    await reducedMotionRun(browser);
    await forcedColorsRun(browser);
    await mobileRun(browser);
    const result = {
      passed: true,
      checked_at: new Date().toISOString(),
      browser: await browser.version(),
      scenarios: ["primary", "reduced-motion", "forced-colors", "mobile"],
      provider_mode: "mapbox selected, public token absent, provider-free continuity",
    };
    fs.writeFileSync(path.join(artifactDir, "browser-acceptance.json"), `${JSON.stringify(result, null, 2)}\n`);
    console.log(`Phase 5 browser acceptance passed. Artifacts: ${artifactDir}`);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
