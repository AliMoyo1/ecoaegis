/* Lone worker check-in module JS - vanilla (guide C2). */
const API = "/lone-worker/api";
const MY_USER_ID = parseInt(document.getElementById("lw-root").dataset.userId, 10);
let mySession = null;

async function loadSessions() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();

  mySession = data.sessions.find((s) => s.worker_id === MY_USER_ID) || null;
  document.getElementById("active-session-card").style.display = mySession ? "block" : "none";
  document.getElementById("start-session-card").style.display = mySession ? "none" : "block";
  if (mySession) {
    document.getElementById("active-session-body").innerHTML = `
      <strong>${mySession.session_ref}</strong> - ${mySession.location || "location not recorded"}<br>
      Check in by: ${new Date(mySession.expected_checkin_at).toLocaleString()}`;
    startManDownWatch();
  } else {
    stopManDownWatch();
  }

  const tbody = document.querySelector("#sessions-table tbody");
  tbody.innerHTML = "";
  for (const s of data.sessions) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${s.session_ref}</td>
      <td>${s.location || "-"}</td>
      <td>${new Date(s.started_at).toLocaleString()}</td>
      <td>${new Date(s.expected_checkin_at).toLocaleString()}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("geolocate-btn").addEventListener("click", () => {
  const status = document.getElementById("geolocate-status");
  if (!navigator.geolocation) { status.textContent = "Geolocation not supported"; return; }
  status.textContent = "Locating...";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      document.querySelector("[name='latitude']").value = pos.coords.latitude;
      document.querySelector("[name='longitude']").value = pos.coords.longitude;
      status.textContent = `Captured (${pos.coords.latitude.toFixed(5)}, ${pos.coords.longitude.toFixed(5)})`;
    },
    () => { status.textContent = "Could not get location"; },
    { timeout: 10000 }
  );
});

document.getElementById("checkin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const resp = await fetch(`${API}/start`, { method: "POST", body });
  const data = await resp.json();
  if (!data.ok) { alert(data.message || "Could not start check-in"); return; }
  e.target.reset();
  loadSessions();
});

document.getElementById("checkin-btn").addEventListener("click", async () => {
  if (!mySession) return;
  const resp = await fetch(`${API}/${mySession.id}/checkin`, { method: "POST" });
  const data = await resp.json();
  if (!data.ok) { alert(data.message || "Check-in failed"); return; }
  stopManDownWatch();
  loadSessions();
});

document.getElementById("extend-btn").addEventListener("click", async () => {
  if (!mySession) return;
  const fd = new FormData();
  fd.append("additional_minutes", "30");
  const resp = await fetch(`${API}/${mySession.id}/extend`, { method: "POST", body: fd });
  const data = await resp.json();
  if (!data.ok) { alert(data.message || "Extend failed"); return; }
  loadSessions();
});

document.getElementById("cancel-btn").addEventListener("click", async () => {
  if (!mySession) return;
  if (!confirm("Cancel this check-in session?")) return;
  const resp = await fetch(`${API}/${mySession.id}/cancel`, { method: "POST" });
  const data = await resp.json();
  if (!data.ok) { alert(data.message || "Cancel failed"); return; }
  stopManDownWatch();
  loadSessions();
});

// C2 step 3: man-down, a simple supplementary signal on top of the check-in
// timer (the reliable core, enforced server-side in scheduler.py regardless
// of whether any of this client code ever runs). While a session is active,
// track motion; after INACTIVITY_MS with none, prompt "are you OK?" and
// escalate immediately if unanswered within RESPONSE_MS. No accelerometer
// permission, no active session, or a phone just sitting still without one
// simply means this supplementary signal never fires - the timer still will.
const INACTIVITY_MS = 20 * 60 * 1000;
const RESPONSE_MS = 60 * 1000;
let lastMotionAt = Date.now();
let manDownInterval = null;
let manDownPromptOpen = false;

function onMotion() { lastMotionAt = Date.now(); }

function startManDownWatch() {
  if (manDownInterval) return;
  lastMotionAt = Date.now();
  if (window.DeviceMotionEvent) window.addEventListener("devicemotion", onMotion);
  manDownInterval = setInterval(checkInactivity, 60 * 1000);
}

function stopManDownWatch() {
  if (manDownInterval) { clearInterval(manDownInterval); manDownInterval = null; }
  window.removeEventListener("devicemotion", onMotion);
}

function checkInactivity() {
  if (!mySession || manDownPromptOpen) return;
  if (Date.now() - lastMotionAt < INACTIVITY_MS) return;
  manDownPromptOpen = true;
  const responded = confirm("No movement detected for a while. Still OK? Cancel = alert my contacts now.");
  manDownPromptOpen = false;
  if (responded) {
    lastMotionAt = Date.now();
  } else {
    fetch(`${API}/${mySession.id}/man-down`, { method: "POST" });
  }
}

loadSessions();
