(function () {
  "use strict";

  function initialiseUserDirectory() {
    const search = document.getElementById("user-search");
    const roleFilter = document.getElementById("user-role-filter");
    const resultCount = document.getElementById("user-result-count");
    const noResults = document.getElementById("user-no-results");
    const rows = Array.from(document.querySelectorAll("[data-user-row]"));

    if (!search || !roleFilter || !resultCount) return;

    function applyFilters() {
      const query = search.value.trim().toLocaleLowerCase();
      const role = roleFilter.value;
      let visible = 0;

      rows.forEach(function (row) {
        const matchesQuery = !query || row.textContent.toLocaleLowerCase().includes(query);
        const matchesRole = !role || row.dataset.role === role;
        const matches = matchesQuery && matchesRole;
        row.hidden = !matches;
        if (matches) visible += 1;
      });

      if (noResults) noResults.hidden = visible !== 0;
      resultCount.textContent = visible + " user" + (visible === 1 ? "" : "s");
    }

    search.addEventListener("input", applyFilters);
    roleFilter.addEventListener("change", applyFilters);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialiseUserDirectory, { once: true });
  } else {
    initialiseUserDirectory();
  }
})();
