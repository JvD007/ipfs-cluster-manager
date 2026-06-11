// IPFS Cluster Manager — dashboard front-end (vanilla JS).
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let currentPreview = null; // { confirm_token, cids, matched_count }

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const isJson = res.headers.get("content-type")?.includes("application/json");
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = (isJson && (body.error || body.detail)) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}

function fmtTs(ts) {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("nl-NL", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return ts; }
}

function shorten(s, n = 14) {
  if (!s) return "";
  return s.length > n + 6 ? `${s.slice(0, n)}…${s.slice(-6)}` : s;
}

function renderMeta(meta) {
  if (!meta || !Object.keys(meta).length) return "<span class='muted small'>—</span>";
  return Object.entries(meta)
    .map(([k, v]) => `<span class="metadata-chip">${escapeHtml(k)}=${escapeHtml(String(v))}</span>`)
    .join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------- Status / dashboard ----------

async function loadStatus() {
  const badge = $("#health-badge");
  badge.className = "badge badge-unknown";
  badge.textContent = "⏳ Verbinden…";
  try {
    const health = await api("/api/health");
    if (!health.ok) throw new Error(health.error || "Cluster niet bereikbaar");
    badge.className = "badge badge-ok";
    badge.textContent = "● Online";
  } catch (e) {
    badge.className = "badge badge-error";
    badge.textContent = `● Offline — ${e.message}`;
    return;
  }
  try {
    const s = await api("/api/status");
    $("#stat-peers").textContent = s.peer_count;
    $("#stat-pins").textContent = s.total_pins.toLocaleString("nl-NL");
    $("#stat-version").textContent = s.version?.version || "—";
    $("#stat-alerts").textContent = s.alerts.length;

    renderPeers(s.peers);
    renderAlerts(s.alerts);
  } catch (e) {
    console.error(e);
    alert(`Kon clusterstatus niet ophalen: ${e.message}`);
  }
}

function renderPeers(peers) {
  const tbody = $("#peers-table tbody");
  tbody.innerHTML = peers.map((p) => `
    <tr>
      <td>${escapeHtml(p.name)}</td>
      <td class="peer-id" title="${escapeHtml(p.id)}">${escapeHtml(shorten(p.id, 16))}</td>
      <td>${escapeHtml(p.cluster_version || "—")}</td>
      <td>${escapeHtml(p.ipfs_version || "—")}</td>
      <td class="num"><strong>${p.pins.toLocaleString("nl-NL")}</strong></td>
      <td>${
        p.error
          ? `<span class="badge badge-error">${escapeHtml(p.error)}</span>`
          : '<span class="badge badge-ok">healthy</span>'
      }</td>
    </tr>
  `).join("");
}

function renderAlerts(alerts) {
  const card = $("#alerts-card");
  if (!alerts.length) { card.style.display = "none"; return; }
  card.style.display = "";
  $("#alerts-json").textContent = JSON.stringify(alerts, null, 2);
}

// ---------- Filter & preview ----------

function readMetadata() {
  const raw = $("#f-metadata").value.trim();
  if (!raw) return {};
  const meta = {};
  raw.split(/[\r\n]+/).forEach((line) => {
    const [k, ...rest] = line.split("=");
    if (k && rest.length) meta[k.trim()] = rest.join("=").trim();
  });
  return meta;
}

function readCids() {
  const raw = $("#f-cids").value.trim();
  if (!raw) return [];
  return raw.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
}

function readFilterPayload() {
  const toIso = (val) => (val ? new Date(val).toISOString() : null);
  return {
    name_contains: $("#f-name-contains").value.trim() || null,
    name_regex: $("#f-name-regex").value.trim() || null,
    before: toIso($("#f-before").value) || null,
    after: toIso($("#f-after").value) || null,
    metadata: readMetadata(),
    status: $("#f-status").value || null,
    cids: readCids(),
  };
}

async function doPreview() {
  const btn = $("#preview-btn");
  btn.disabled = true;
  btn.textContent = "Bezig…";
  $("#result-panel").classList.add("hidden");
  try {
    const payload = readFilterPayload();
    const res = await api("/api/preview-unpin", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    currentPreview = res;
    renderPreview(res);
  } catch (e) {
    alert(`Preview mislukt: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Voorbeeld tonen";
  }
}

function renderPreview(res) {
  const panel = $("#preview-panel");
  panel.classList.remove("hidden");
  $("#preview-count").textContent = res.matched_count.toLocaleString("nl-NL");
  const tbody = $("#preview-table tbody");
  if (!res.matched_count) {
    tbody.innerHTML = `<tr><td colspan="5" class="muted">Geen pins voldoen aan dit filter.</td></tr>`;
  } else {
    tbody.innerHTML = res.sample.map((p) => `
      <tr>
        <td class="cid" title="${escapeHtml(p.cid)}">${escapeHtml(shorten(p.cid, 14))}</td>
        <td>${escapeHtml(p.name || "—")}</td>
        <td>${escapeHtml(fmtTs(p.timestamp))}</td>
        <td>${renderMeta(p.metadata)}</td>
        <td class="num">${(p.allocations || []).length}</td>
      </tr>
    `).join("");
  }
  $("#preview-truncated").classList.toggle("hidden", !res.truncated);
  $("#acknowledge").checked = false;
  $("#confirm-unpin").disabled = true;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------- Bulk unpin uitvoeren ----------

async function doUnpin() {
  if (!currentPreview || !currentPreview.cids.length) return;
  const acknowledge = $("#acknowledge").checked;
  if (!acknowledge) {
    alert("Vink eerst de bevestigingscheckbox aan.");
    return;
  }
  const confirmText = `Verwijder ${currentPreview.matched_count} pins permanent uit de cluster?`;
  if (!confirm(confirmText)) return;

  const btn = $("#confirm-unpin");
  btn.disabled = true;
  btn.textContent = "Bezig…";
  try {
    const res = await api("/api/bulk-unpin", {
      method: "POST",
      body: JSON.stringify({
        confirm_token: currentPreview.confirm_token,
        cids: currentPreview.cids,
        run_gc: $("#run-gc").checked,
      }),
    });
    renderResult(res);
    currentPreview = null;
    $("#preview-panel").classList.add("hidden");
    // Vernieuw de statistieken
    await loadStatus();
  } catch (e) {
    alert(`Bulk unpin mislukt: ${e.message}`);
  } finally {
    btn.textContent = "Bulk unpin uitvoeren";
    btn.disabled = false;
  }
}

function renderResult(res) {
  const panel = $("#result-panel");
  panel.classList.remove("hidden");
  $("#result-summary").innerHTML = `
    <div class="stat-pill">Aangevraagd: <strong>${res.requested}</strong></div>
    <div class="stat-pill ok">Geslaagd: <strong>${res.succeeded}</strong></div>
    <div class="stat-pill err">Mislukt: <strong>${res.failed}</strong></div>
    ${res.gc ? `<div class="stat-pill">GC: <code>${escapeHtml(JSON.stringify(res.gc))}</code></div>` : ""}
  `;
  const tbody = $("#result-table tbody");
  tbody.innerHTML = res.results.map((r) => `
    <tr>
      <td class="cid">${escapeHtml(shorten(r.cid, 14))}</td>
      <td>${r.ok ? '<span class="badge badge-ok">OK</span>' : '<span class="badge badge-error">FOUT</span>'}</td>
      <td>${escapeHtml(r.error || "—")}</td>
    </tr>
  `).join("");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------- Event listeners ----------

document.addEventListener("DOMContentLoaded", () => {
  loadStatus();
  $("#refresh-btn").addEventListener("click", loadStatus);
  $("#preview-btn").addEventListener("click", doPreview);
  $("#cancel-preview").addEventListener("click", () => {
    currentPreview = null;
    $("#preview-panel").classList.add("hidden");
  });
  $("#acknowledge").addEventListener("change", (e) => {
    $("#confirm-unpin").disabled = !e.target.checked || !currentPreview?.matched_count;
  });
  $("#confirm-unpin").addEventListener("click", doUnpin);
  // Auto-refresh elke 30s
  setInterval(loadStatus, 30000);
});
