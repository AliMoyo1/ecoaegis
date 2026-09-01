/* Apply the saved theme before styles render to prevent a light-frame flash. */
(function () {
  "use strict";
  const root = document.documentElement;
  const loginOnly = window.location.pathname === "/login";
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
})();
