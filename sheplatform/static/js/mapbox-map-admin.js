/* Coordinate and historical-site administration through the renderer adapter. */
(function () {
  "use strict";

  const renderer = window.ecoMapRenderer;
  const resolutionReview = document.getElementById("site-resolution-review");
  if (resolutionReview) {
    const resolutionFilter = document.getElementById("site-resolution-filter");
    const resolutionRefresh = document.getElementById("site-resolution-refresh");
    const resolutionCounts = document.getElementById("site-resolution-counts");
    const resolutionStatus = document.getElementById("site-resolution-status");
    const resolutionRows = document.getElementById("site-resolution-rows");
    const createForm = document.getElementById("site-resolution-create-form");
    const createRecordType = document.getElementById("site-resolution-create-record-type");
    const createRecordId = document.getElementById("site-resolution-create-record-id");
    const createContext = document.getElementById("site-resolution-create-context");
    const createCode = document.getElementById("site-resolution-create-code");
    const createName = document.getElementById("site-resolution-create-name");
    const createCity = document.getElementById("site-resolution-create-city");
    const createRegion = document.getElementById("site-resolution-create-region");
    const createType = document.getElementById("site-resolution-create-type");
    const createCancel = document.getElementById("site-resolution-create-cancel");
    let resolutionSites = [];

    function setResolutionStatus(message) { resolutionStatus.textContent = message; }
    function siteLabel(site) {
      return site ? `${site.site_code} - ${site.site_name}` : "Unlinked";
    }
    function addCell(row, value) {
      const cell = document.createElement("td");
      if (arguments.length > 1) {
        cell.textContent = value === null || value === undefined || value === "" ? "-" : String(value);
      }
      row.appendChild(cell);
      return cell;
    }
    async function post(url, body) {
      const response = await fetch(url, { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Site resolution could not be completed");
      return data;
    }
    function openCreate(record) {
      createForm.reset();
      createRecordType.value = record.record_type;
      createRecordId.value = String(record.id);
      createContext.textContent =
        `Create and link a canonical site for ${record.record_type_label} ${record.record_ref}`;
      createName.value = record.original_text || "";
      createForm.hidden = false;
      createCode.focus();
    }
    function closeCreate() {
      createForm.reset();
      createRecordType.value = "";
      createRecordId.value = "";
      createForm.hidden = true;
    }
    function renderRecord(record) {
      const row = document.createElement("tr");
      addCell(row, `${record.record_type_label} ${record.record_ref}`);
      addCell(row, record.original_text || "No location details");
      const resolverCell = addCell(row);
      const resolverLabel = document.createElement("strong");
      resolverLabel.textContent = record.resolver.status.replace(/_/g, " ");
      resolverCell.appendChild(resolverLabel);
      if (record.resolver.candidates.length) {
        const candidateText = document.createElement("div");
        candidateText.className = "map-muted-status";
        candidateText.textContent = record.resolver.candidates.map(siteLabel).join("; ");
        resolverCell.appendChild(candidateText);
      }
      const currentCell = addCell(row, siteLabel(record.current_site));
      if (record.decision === "skipped") {
        const skipped = document.createElement("div");
        skipped.className = "map-muted-status";
        skipped.textContent = record.decision_note ? `Skipped: ${record.decision_note}` : "Skipped";
        currentCell.appendChild(skipped);
      }
      const actionCell = addCell(row);
      if (record.current_site) {
        actionCell.textContent = "Linked";
        return row;
      }
      const select = document.createElement("select");
      select.setAttribute("aria-label", `Canonical site for ${record.record_ref}`);
      select.add(new Option("Choose a site", ""));
      for (const site of resolutionSites) select.add(new Option(siteLabel(site), String(site.id)));
      if (record.resolver.status === "matched" && record.resolver.candidates.length === 1) {
        select.value = String(record.resolver.candidates[0].id);
      }
      actionCell.appendChild(select);
      const controls = document.createElement("div");
      controls.className = "filters";
      const resolveButton = document.createElement("button");
      resolveButton.type = "button";
      resolveButton.className = "btn btn-primary";
      resolveButton.textContent = record.resolver.status === "matched" ? "Apply exact match" : "Link selected";
      resolveButton.addEventListener("click", async function () {
        if (!select.value) {
          setResolutionStatus("Choose a canonical site before linking this record.");
          select.focus();
          return;
        }
        resolveButton.disabled = true;
        const body = new FormData();
        body.append("site_id", select.value);
        try {
          await post(`/map/api/site-resolution/${encodeURIComponent(record.record_type)}/${record.id}/resolve`, body);
          setResolutionStatus(`${record.record_ref} linked and audited.`);
          document.dispatchEvent(new CustomEvent("site-resolution-updated"));
          await loadQueue();
        } catch (error) {
          setResolutionStatus(error.message || "Record could not be linked.");
          resolveButton.disabled = false;
        }
      });
      controls.appendChild(resolveButton);
      const skipButton = document.createElement("button");
      skipButton.type = "button";
      skipButton.className = "btn btn-secondary";
      skipButton.textContent = "Skip";
      skipButton.addEventListener("click", async function () {
        if (!window.confirm(`Skip ${record.record_ref} for now? The record remains unlinked.`)) return;
        skipButton.disabled = true;
        try {
          await post(`/map/api/site-resolution/${encodeURIComponent(record.record_type)}/${record.id}/skip`,
            new FormData());
          setResolutionStatus(`${record.record_ref} skipped and audited.`);
          await loadQueue();
        } catch (error) {
          setResolutionStatus(error.message || "Record could not be skipped.");
          skipButton.disabled = false;
        }
      });
      controls.appendChild(skipButton);
      const createButton = document.createElement("button");
      createButton.type = "button";
      createButton.className = "btn btn-secondary";
      createButton.textContent = "Create site";
      createButton.addEventListener("click", function () { openCreate(record); });
      controls.appendChild(createButton);
      actionCell.appendChild(controls);
      return row;
    }
    async function loadQueue() {
      setResolutionStatus("Loading review queue...");
      try {
        const query = new URLSearchParams({ status: resolutionFilter.value, limit: "100" });
        const response = await fetch(`/map/api/site-resolution?${query}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || "Review queue could not be loaded");
        resolutionSites = data.available_sites;
        resolutionRows.replaceChildren();
        for (const record of data.records) resolutionRows.appendChild(renderRecord(record));
        if (!data.records.length) {
          const row = document.createElement("tr");
          const cell = addCell(row, "No records in this queue.");
          cell.colSpan = 5;
          resolutionRows.appendChild(row);
        }
        const counts = data.counts;
        resolutionCounts.textContent =
          `${counts.pending} pending · ${counts.unlinked} unlinked · ${counts.linked} linked · ${counts.skipped} skipped`;
        setResolutionStatus(data.truncated ? `Showing the first ${data.records.length} records.`
          : `${data.records.length} records shown.`);
      } catch (error) {
        resolutionRows.replaceChildren();
        setResolutionStatus(error.message || "Review queue could not be loaded.");
      }
    }
    resolutionFilter.addEventListener("change", loadQueue);
    resolutionRefresh.addEventListener("click", loadQueue);
    createCancel.addEventListener("click", closeCreate);
    createForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (!createForm.reportValidity() || !createRecordType.value || !createRecordId.value) return;
      const body = new FormData();
      body.append("site_code", createCode.value);
      body.append("site_name", createName.value);
      body.append("city", createCity.value);
      body.append("region", createRegion.value);
      body.append("site_type", createType.value);
      try {
        const data = await post(
          `/map/api/site-resolution/${encodeURIComponent(createRecordType.value)}/${createRecordId.value}/create-site`,
          body);
        closeCreate();
        setResolutionStatus(`${data.site.site_code} created, linked, and audited.`);
        document.dispatchEvent(new CustomEvent("site-resolution-updated"));
        await loadQueue();
      } catch (error) {
        setResolutionStatus(error.message || "Site could not be created.");
      }
    });
    loadQueue();
  }

  const editor = document.getElementById("coordinate-editor");
  if (!editor || !renderer) return;
  const form = document.getElementById("coordinate-form");
  const siteSelect = document.getElementById("coordinate-site");
  const latitudeInput = document.getElementById("coordinate-latitude");
  const longitudeInput = document.getElementById("coordinate-longitude");
  const accuracyInput = document.getElementById("coordinate-accuracy");
  const sourceInput = document.getElementById("coordinate-source");
  const status = document.getElementById("coordinate-status");
  const selectButton = document.getElementById("coordinate-map-select");
  const deviceButton = document.getElementById("coordinate-device");
  const clearButton = document.getElementById("coordinate-clear");
  const sites = new Map();
  let selecting = false;

  function hasCoordinates(site) {
    return site && site.latitude !== null && site.latitude !== undefined &&
      site.longitude !== null && site.longitude !== undefined;
  }
  function selectedSite() { return sites.get(Number(siteSelect.value)); }
  function setStatus(message) { status.textContent = message; }
  function stopSelection() {
    selecting = false;
    selectButton.textContent = "Select on map";
    renderer.setSelectionHandler(null);
  }
  function setDraft(latitude, longitude, source, accuracy, moveMap) {
    const lat = Number(latitude);
    const lng = Number(longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || lat < -90 || lat > 90 ||
        lng < -180 || lng > 180) return false;
    latitudeInput.value = lat.toFixed(6);
    longitudeInput.value = lng.toFixed(6);
    sourceInput.value = source;
    accuracyInput.value = accuracy === null || accuracy === undefined ? "" : Number(accuracy).toFixed(1);
    const rendered = renderer.setDraft(lat, lng, function (dragLat, dragLng) {
      setDraft(dragLat, dragLng, "manual", null, false);
      setStatus("Draft marker moved. Save to apply this location.");
    }, moveMap !== false);
    if (!rendered && moveMap) setStatus("Coordinates are ready to save; the basemap is unavailable.");
    return true;
  }

  siteSelect.addEventListener("change", function () {
    stopSelection();
    renderer.removeDraft();
    const site = selectedSite();
    if (!site) {
      form.reset();
      setStatus("Choose a site to begin.");
    } else if (hasCoordinates(site)) {
      setDraft(site.latitude, site.longitude, site.coordinate_source || "manual",
        site.coordinate_accuracy_m, true);
      setStatus(`Current source: ${(site.coordinate_source || "legacy").replace(/_/g, " ")}.`);
    } else {
      latitudeInput.value = "";
      longitudeInput.value = "";
      accuracyInput.value = "";
      sourceInput.value = "manual";
      setStatus("This site has no recorded location.");
    }
  });
  for (const input of [latitudeInput, longitudeInput]) {
    input.addEventListener("input", function () {
      sourceInput.value = "manual";
      accuracyInput.value = "";
      setDraft(latitudeInput.value, longitudeInput.value, "manual", null, false);
    });
  }
  selectButton.addEventListener("click", function () {
    if (!selectedSite()) {
      setStatus("Choose a site before selecting a map location.");
      return;
    }
    if (selecting) {
      stopSelection();
      setStatus("Map selection cancelled.");
      return;
    }
    const available = renderer.setSelectionHandler(function (lat, lng) {
      setDraft(lat, lng, "manual", null, false);
      stopSelection();
      setStatus("Map location selected. Save to apply it.");
    });
    if (!available) {
      setStatus("Map selection is unavailable. Enter coordinates or use device location.");
      return;
    }
    selecting = true;
    selectButton.textContent = "Cancel map selection";
    setStatus("Click the map to position the draft marker.");
  });
  deviceButton.addEventListener("click", function () {
    if (!selectedSite()) {
      setStatus("Choose a site before using device location.");
      return;
    }
    if (!navigator.geolocation) {
      setStatus("This browser does not provide device location. Enter coordinates instead.");
      return;
    }
    setStatus("Requesting device location...");
    navigator.geolocation.getCurrentPosition(function (position) {
      const accuracy = position.coords.accuracy;
      setDraft(position.coords.latitude, position.coords.longitude, "device_gps", accuracy, true);
      setStatus(accuracy > 100
        ? `Device location captured with low accuracy (±${Math.round(accuracy)} m). Review before saving.`
        : `Device location captured (±${Math.round(accuracy)} m). Review and save.`);
    }, function (error) {
      setStatus(error.code === error.PERMISSION_DENIED
        ? "Device location permission was denied. Enter coordinates or select the map."
        : "Device location was unavailable. Enter coordinates or select the map.");
    }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
  });
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const site = selectedSite();
    if (!site || !form.reportValidity()) return;
    const replacing = hasCoordinates(site) &&
      (Number(site.latitude) !== Number(latitudeInput.value) ||
       Number(site.longitude) !== Number(longitudeInput.value));
    if (replacing && !window.confirm(
      "Replace this site's existing coordinates? The previous values remain in the audit trail.")) return;
    setStatus("Saving location...");
    try {
      const response = await fetch(`/map/api/sites/${site.id}/coords`, {
        method: "POST", body: new FormData(form),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Location could not be saved");
      sites.set(site.id, data.site);
      siteSelect.options[siteSelect.selectedIndex].textContent =
        `${data.site.site_name} (${data.site.site_code}) — located`;
      setDraft(data.site.latitude, data.site.longitude, data.site.coordinate_source,
        data.site.coordinate_accuracy_m, false);
      setStatus("Site location saved and audited.");
      await renderer.refresh();
    } catch (error) {
      setStatus(error.message || "Location could not be saved.");
    }
  });
  clearButton.addEventListener("click", async function () {
    const site = selectedSite();
    if (!site || !hasCoordinates(site)) {
      setStatus("The selected site has no saved location to clear.");
      return;
    }
    if (!window.confirm(
      "Clear this site's saved coordinates? The previous values remain in the audit trail.")) return;
    try {
      const response = await fetch(`/map/api/sites/${site.id}/coords`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Location could not be cleared");
      sites.set(site.id, data.site);
      siteSelect.options[siteSelect.selectedIndex].textContent =
        `${data.site.site_name} (${data.site.site_code}) — unlocated`;
      renderer.removeDraft();
      latitudeInput.value = "";
      longitudeInput.value = "";
      accuracyInput.value = "";
      sourceInput.value = "manual";
      setStatus("Site location cleared and audited.");
      await renderer.refresh();
    } catch (error) {
      setStatus(error.message || "Location could not be cleared.");
    }
  });

  async function loadSites() {
    try {
      const response = await fetch("/map/api/sites");
      if (!response.ok) throw new Error("Sites could not be loaded");
      const data = await response.json();
      sites.clear();
      siteSelect.replaceChildren(new Option("Choose a site", ""));
      for (const site of data.sites) {
        sites.set(site.id, site);
        siteSelect.add(new Option(
          `${site.site_name} (${site.site_code}) — ${hasCoordinates(site) ? "located" : "unlocated"}`,
          String(site.id)));
      }
      setStatus(data.sites.length ? "Choose a site to begin." : "No active sites are available.");
    } catch (error) {
      siteSelect.replaceChildren(new Option("Sites unavailable", ""));
      setStatus(error.message || "Sites could not be loaded.");
    }
  }
  document.addEventListener("site-resolution-updated", loadSites);

  const importForm = document.getElementById("coordinate-import-form");
  const importStatus = document.getElementById("coordinate-import-status");
  const importResults = document.getElementById("coordinate-import-results");
  const importSummary = document.getElementById("coordinate-import-summary");
  const importRows = document.getElementById("coordinate-import-rows");
  const importControls = document.getElementById("coordinate-import-commit-controls");
  const overwriteLabel = document.getElementById("coordinate-import-overwrite-label");
  const overwriteInput = document.getElementById("coordinate-import-overwrite");
  const commitButton = document.getElementById("coordinate-import-commit");
  let importId = null;
  function addImportCell(row, value) {
    const cell = document.createElement("td");
    cell.textContent = value === null || value === undefined ? "" : String(value);
    row.appendChild(cell);
  }
  function renderPreview(data) {
    const batch = data.batch;
    importId = batch.id;
    importResults.hidden = false;
    importSummary.textContent =
      `${batch.total_rows} rows: ${batch.valid_rows} valid, ${batch.conflict_rows} existing-location conflicts, ${batch.invalid_rows} invalid.`;
    importRows.replaceChildren();
    for (const item of data.rows) {
      const row = document.createElement("tr");
      for (const value of [item.row_number, item.site_code, item.latitude, item.longitude,
        item.status, item.error || (item.status === "conflict"
          ? "Existing coordinates require overwrite approval" : "")]) addImportCell(row, value);
      importRows.appendChild(row);
    }
    importControls.hidden = batch.invalid_rows !== 0;
    overwriteLabel.hidden = batch.conflict_rows === 0;
    overwriteInput.checked = false;
    importStatus.textContent = batch.invalid_rows === 0
      ? "Preview complete. Review every row before committing."
      : "Preview contains invalid rows. Correct the CSV and upload a new preview.";
  }
  importForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!importForm.reportValidity()) return;
    importId = null;
    importResults.hidden = true;
    importStatus.textContent = "Validating CSV without changing sites...";
    try {
      const response = await fetch("/map/api/coordinate-imports/preview", {
        method: "POST", body: new FormData(importForm),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Coordinate import could not be previewed");
      renderPreview(data);
    } catch (error) {
      importStatus.textContent = error.message || "Coordinate import could not be previewed.";
    }
  });
  commitButton.addEventListener("click", async function () {
    if (!importId) return;
    if (!overwriteLabel.hidden && !overwriteInput.checked) {
      importStatus.textContent = "Approve overwriting existing coordinates before committing this batch.";
      return;
    }
    if (!window.confirm("Commit this reviewed coordinate import? Each site change will be audited.")) return;
    const body = new FormData();
    body.append("overwrite_existing", overwriteInput.checked ? "true" : "false");
    try {
      const response = await fetch(`/map/api/coordinate-imports/${importId}/commit`, {
        method: "POST", body,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Coordinate import could not be committed");
      importStatus.textContent = `${data.updated_sites} site locations imported and audited.`;
      importControls.hidden = true;
      await loadSites();
      await renderer.refresh();
    } catch (error) {
      importStatus.textContent = error.message || "Coordinate import could not be committed.";
    }
  });
  loadSites();
})();
