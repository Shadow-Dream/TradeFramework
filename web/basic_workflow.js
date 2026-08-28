"use strict";

const BASIC_PROTOCOL_ID = "trade.basic-workflow";
const BASIC_MARKET_PROVIDER_ID = "eodhd-demo-us";
const MARKET_ENDPOINT = "/api/subsystems/basic/market";
const WORKSPACE_PATH = "/basic-workflow/workspace";
const MARKET_PAGE_SIZE = 50;
const BAR_SNAPSHOT_FIELDS = Object.freeze([
  "catalogSnapshotId", "providerId", "instrumentId", "period", "asOf", "firstTime", "lastTime",
  "barCount", "contentDigest", "datasetId", "datasetVersionId",
]);
const SNAPSHOT_JOB_FIELDS = Object.freeze([
  "jobId", "snapshotId", "providerId", "instrumentId", "period", "status", "attempts",
  "queuedAt", "startedAt", "completedAt", "error", "result",
]);

const state = {
  csrfToken: "",
  market: null,
  marketTab: "watchlist",
  marketPage: 1,
  syncBusy: false,
  watchlistBusy: false,
  initialSyncAttempted: false,
  snapshotPollTimer: null,
};

const byId = (id) => document.getElementById(id);

function requireObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object.`);
  return value;
}

function requireString(value, label) {
  if (typeof value !== "string" || !value || value !== value.trim()) throw new Error(`${label} must be a non-empty canonical string.`);
  return value;
}

function requireProtocol(value, label) {
  requireString(value, label);
  if (value !== BASIC_PROTOCOL_ID) throw new Error(`${label} is outside ${BASIC_PROTOCOL_ID}.`);
  return value;
}

function userFacingMessage(value, fallback) {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) return fallback;
  const opaqueIdentity = [
    /sha256:/i,
    /\b(?:snapshotId|catalogSnapshotId|contentDigest|datasetId|datasetVersionId|pipelineId|backtestId|visualizationId|visualizerId|instanceId|requestDigest|snapshotHash)\b/i,
    /\b(?:bt|job|pipe|viz|vis)_[0-9a-z_-]{8,}\b/i,
    /\b(?:basic-market-catalog|dataset|pipeline|backtest|visualization|visualizer|instance|snapshot)-[0-9a-z][0-9a-z._-]{5,}\b/i,
    /\b(?:Visualizer|Temporary Module|instance)\s+['"`][^'"`]+['"`]/i,
    /\b[0-9a-f]{24,}\b/i,
    /\b[0-9A-HJKMNP-TV-Z]{20,}\b/,
    /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i,
    /\bhash\b/i,
  ].some((pattern) => pattern.test(text));
  return opaqueIdentity ? fallback : text;
}

function requireExactFields(value, fields, label) {
  const record = requireObject(value, label);
  const actual = Object.keys(record).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new Error(`${label} must contain exactly ${expected.join(", ")}.`);
  }
  return record;
}

function requireContentDigest(value, label) {
  requireString(value, label);
  if (!/^sha256:[0-9a-f]{64}$/.test(value)) throw new Error(`${label} must be a canonical sha256 digest.`);
  return value;
}

function requireInstrument(value, label) {
  const instrument = requireObject(value, label);
  ["instrumentId", "symbol", "name", "exchange", "currency", "assetType"].forEach((field) => {
    requireString(instrument[field], `${label}.${field}`);
  });
  if (!Array.isArray(instrument.availablePeriods)
      || instrument.availablePeriods.some((period) => typeof period !== "string" || !period)) {
    throw new Error(`${label}.availablePeriods must be an array of period IDs.`);
  }
  return instrument;
}

function requireSnapshot(value, label = "Basic market snapshot") {
  const snapshot = requireObject(value, label);
  if (snapshot.schemaVersion !== 1) throw new Error(`${label}.schemaVersion must be 1.`);
  requireString(snapshot.snapshotId, `${label}.snapshotId`);
  requireProtocol(snapshot.protocolId, `${label}.protocolId`);
  ["providerId", "asOf", "contentDigest"].forEach((field) => requireString(snapshot[field], `${label}.${field}`));
  if (!Number.isInteger(snapshot.instrumentCount) || snapshot.instrumentCount < 0) {
    throw new Error(`${label}.instrumentCount must be a non-negative integer.`);
  }
  if (!Array.isArray(snapshot.instruments) || snapshot.instruments.length !== snapshot.instrumentCount) {
    throw new Error(`${label}.instruments does not match instrumentCount.`);
  }
  const ids = new Set();
  snapshot.instruments.forEach((instrument, index) => {
    requireInstrument(instrument, `${label}.instruments[${index}]`);
    if (ids.has(instrument.instrumentId)) throw new Error(`${label} contains duplicate instrument IDs.`);
    ids.add(instrument.instrumentId);
  });
  return snapshot;
}

function requireMarketEnvelope(value) {
  const envelope = requireExactFields(
    value,
    ["protocolId", "snapshot", "watchlist", "barSnapshots", "snapshotJobs"],
    "Basic market response",
  );
  requireProtocol(envelope.protocolId, "Basic market response.protocolId");
  if (envelope.snapshot !== null) requireSnapshot(envelope.snapshot);
  if (!Array.isArray(envelope.watchlist)) throw new Error("Basic market response.watchlist must be an array.");
  const snapshotIds = new Set((envelope.snapshot?.instruments || []).map((item) => item.instrumentId));
  const snapshotInstruments = new Map((envelope.snapshot?.instruments || []).map((item) => [item.instrumentId, item]));
  const watchIds = new Set();
  envelope.watchlist.forEach((instrument, index) => {
    requireInstrument(instrument, `Basic market response.watchlist[${index}]`);
    if (!snapshotIds.has(instrument.instrumentId)) throw new Error("Basic market watchlist contains an instrument outside its snapshot.");
    if (watchIds.has(instrument.instrumentId)) throw new Error("Basic market watchlist contains duplicates.");
    watchIds.add(instrument.instrumentId);
  });
  if (!Array.isArray(envelope.barSnapshots)) throw new Error("Basic market response.barSnapshots must be an array.");
  const barKeys = new Set();
  let previousKey = "";
  envelope.barSnapshots.forEach((value, index) => {
    const label = `Basic market response.barSnapshots[${index}]`;
    const record = requireExactFields(value, BAR_SNAPSHOT_FIELDS, label);
    ["catalogSnapshotId", "providerId", "instrumentId", "period", "asOf", "firstTime", "lastTime", "datasetId", "datasetVersionId"]
      .forEach((field) => requireString(record[field], `${label}.${field}`));
    requireContentDigest(record.contentDigest, `${label}.contentDigest`);
    if (!Number.isInteger(record.barCount) || record.barCount < 1) throw new Error(`${label}.barCount must be a positive integer.`);
    if (!snapshotIds.has(record.instrumentId)) throw new Error(`${label}.instrumentId is outside the current catalog snapshot.`);
    if (!snapshotInstruments.get(record.instrumentId).availablePeriods.includes(record.period)) {
      throw new Error(`${label}.period is unavailable for its current catalog instrument.`);
    }
    const key = `${record.instrumentId}\u0000${record.period}`;
    if (barKeys.has(key)) throw new Error("Basic market response.barSnapshots contains duplicate instrument periods.");
    if (previousKey && key < previousKey) throw new Error("Basic market response.barSnapshots must be stably sorted.");
    barKeys.add(key);
    previousKey = key;
  });
  if (!Array.isArray(envelope.snapshotJobs)) throw new Error("Basic market response.snapshotJobs must be an array.");
  const jobIds = new Set();
  envelope.snapshotJobs.forEach((value, index) => {
    const label = `Basic market response.snapshotJobs[${index}]`;
    const job = requireExactFields(value, SNAPSHOT_JOB_FIELDS, label);
    ["jobId", "snapshotId", "providerId", "instrumentId", "period", "status", "queuedAt"]
      .forEach((field) => requireString(job[field], `${label}.${field}`));
    if (!["queued", "running", "completed", "failed"].includes(job.status)) throw new Error(`${label}.status is invalid.`);
    if (!Number.isInteger(job.attempts) || job.attempts < 0) throw new Error(`${label}.attempts is invalid.`);
    if (typeof job.startedAt !== "string" || typeof job.completedAt !== "string" || typeof job.error !== "string") {
      throw new Error(`${label} timestamps and error must be strings.`);
    }
    if (!snapshotIds.has(job.instrumentId)) throw new Error(`${label}.instrumentId is outside the current catalog snapshot.`);
    if (jobIds.has(job.jobId)) throw new Error("Basic market response.snapshotJobs contains duplicate IDs.");
    jobIds.add(job.jobId);
    if (job.status === "completed") requireExactFields(job.result, BAR_SNAPSHOT_FIELDS, `${label}.result`);
    else if (job.result !== null) throw new Error(`${label}.result must be null before completion.`);
  });
  return envelope;
}

function redirectToLogin() {
  const next = `${location.pathname}${location.search}${location.hash}`;
  location.replace(`/login?next=${encodeURIComponent(next)}`);
}

async function authenticatedFetch(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  if (response.status === 401) redirectToLogin();
  return response;
}

async function getJson(path) {
  const response = await authenticatedFetch(path, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `${path} returned ${response.status}`);
  return payload;
}

async function postJson(path, payload) {
  const response = await authenticatedFetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken },
    body: JSON.stringify(payload),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result.accepted === false) throw new Error(result.error || `${path} returned ${response.status}`);
  return result;
}

function setStatus(message, error = false) {
  const target = byId("bwStatus");
  target.textContent = userFacingMessage(message, error ? "The market action failed." : "Stock catalog ready");
  target.classList.toggle("ok", !error);
  target.dataset.state = error ? "error" : "ready";
}

function formatInstant(value) {
  const instant = new Date(value);
  return Number.isNaN(instant.getTime()) ? value : instant.toLocaleString();
}

function watchlistIds() {
  return new Set((state.market?.watchlist || []).map((item) => item.instrumentId));
}

function dailyBarSnapshots() {
  return new Map((state.market?.barSnapshots || [])
    .filter((record) => record.period === "day")
    .map((record) => [record.instrumentId, record]));
}

function latestSnapshotJobs() {
  const jobs = new Map();
  (state.market?.snapshotJobs || [])
    .filter((record) => record.period === "day")
    .forEach((record) => jobs.set(record.instrumentId, record));
  return jobs;
}

function visibleInstruments() {
  const snapshot = state.market?.snapshot;
  const source = state.marketTab === "watchlist" ? (state.market?.watchlist || []) : (snapshot?.instruments || []);
  const query = byId("bwInstrumentSearch").value.trim().toLocaleLowerCase();
  return source.filter((instrument) => !query || [instrument.instrumentId, instrument.symbol, instrument.name, instrument.exchange]
    .some((value) => value.toLocaleLowerCase().includes(query)));
}

function marketPage(records, requestedPage = state.marketPage) {
  const total = records.length;
  const pageCount = Math.max(1, Math.ceil(total / MARKET_PAGE_SIZE));
  const page = Math.min(Math.max(1, requestedPage), pageCount);
  const startIndex = (page - 1) * MARKET_PAGE_SIZE;
  const endIndex = Math.min(startIndex + MARKET_PAGE_SIZE, total);
  return {
    page,
    pageCount,
    total,
    start: total ? startIndex + 1 : 0,
    end: endIndex,
    records: records.slice(startIndex, endIndex),
  };
}

function workspaceUrl(instrument) {
  const snapshot = state.market?.snapshot;
  if (!snapshot) throw new Error("A market snapshot is required.");
  const query = new URLSearchParams({ snapshotId: snapshot.snapshotId, instrumentId: instrument.instrumentId, period: "day" });
  return `${WORKSPACE_PATH}?${query.toString()}`;
}

function instrumentRow(instrument, watched, barsByInstrument, jobsByInstrument) {
  const row = document.createElement("article");
  row.className = "bw-market-row";
  row.dataset.instrumentId = instrument.instrumentId;
  row.setAttribute("role", "listitem");
  const open = document.createElement("a");
  open.className = "bw-market-open";
  open.href = workspaceUrl(instrument);
  open.setAttribute("aria-label", `Open ${instrument.symbol} workspace`);
  const symbol = document.createElement("strong");
  symbol.textContent = instrument.symbol;
  const identity = document.createElement("span");
  identity.textContent = instrument.name;
  const venue = document.createElement("span");
  venue.textContent = `${instrument.exchange} · ${instrument.currency}`;
  const dataAsOf = document.createElement("span");
  dataAsOf.className = "bw-data-as-of";
  const barSnapshot = barsByInstrument.get(instrument.instrumentId);
  const snapshotJob = jobsByInstrument.get(instrument.instrumentId);
  if (snapshotJob?.status === "queued") dataAsOf.textContent = "Automatic snapshot queued…";
  else if (snapshotJob?.status === "running") dataAsOf.textContent = "Fetching automatic snapshot…";
  else if (snapshotJob?.status === "failed") dataAsOf.textContent = "Automatic snapshot failed";
  else if (barSnapshot) {
    dataAsOf.textContent = `Snapshot cached · ${formatInstant(barSnapshot.lastTime)}`;
    dataAsOf.title = `Cached K-line snapshot through ${barSnapshot.lastTime}`;
  } else if (snapshotJob?.status === "completed" && snapshotJob.result) {
    dataAsOf.textContent = `Snapshot ready · ${formatInstant(snapshotJob.result.lastTime)}`;
    dataAsOf.title = `Automatic K-line snapshot through ${snapshotJob.result.lastTime}`;
  }
  dataAsOf.dataset.snapshotState = snapshotJob?.status || (barSnapshot ? "cached" : "missing");
  const action = document.createElement("span");
  action.className = "bw-open-label";
  action.textContent = "Open workspace";
  open.append(symbol, identity, venue, dataAsOf, action);
  const favorite = document.createElement("button");
  favorite.type = "button";
  favorite.className = "bw-watch-toggle";
  favorite.dataset.watchInstrumentId = instrument.instrumentId;
  favorite.textContent = watched ? "★" : "☆";
  favorite.disabled = state.watchlistBusy || state.syncBusy;
  favorite.setAttribute("aria-label", `${watched ? "Remove" : "Add"} ${instrument.symbol} ${watched ? "from" : "to"} watchlist`);
  row.append(open, favorite);
  return row;
}

function renderMarket() {
  const snapshot = state.market?.snapshot || null;
  const syncButton = byId("bwSyncMarket");
  syncButton.disabled = state.syncBusy || state.watchlistBusy;
  syncButton.textContent = state.syncBusy ? "Refreshing stock list…" : "Refresh stock list";
  byId("bwInstrumentSearch").disabled = !snapshot;
  let activeTabId = "";
  document.querySelectorAll("[data-market-tab]").forEach((button) => {
    const active = button.dataset.marketTab === state.marketTab;
    button.classList.toggle("active", active);
    button.disabled = !snapshot;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    if (active) activeTabId = button.id;
  });
  byId("bwMarketTabPanel").setAttribute("aria-labelledby", activeTabId);
  if (!snapshot) {
    byId("bwSnapshotTitle").textContent = "No stock catalog";
    byId("bwSnapshotMeta").textContent = "Refresh the online stock list to create a saved catalog.";
  } else {
    byId("bwSnapshotTitle").textContent = `${snapshot.instrumentCount} stocks`;
    byId("bwSnapshotMeta").textContent = `Catalog updated ${formatInstant(snapshot.asOf)}`;
  }
  const filtered = visibleInstruments();
  const pagination = marketPage(filtered);
  state.marketPage = pagination.page;
  byId("bwListCount").textContent = snapshot ? `${pagination.total} matches` : "0 matches";
  byId("bwPageRange").textContent = `${pagination.start}–${pagination.end} of ${pagination.total}`;
  byId("bwPreviousPage").disabled = !snapshot || pagination.page <= 1;
  byId("bwNextPage").disabled = !snapshot || pagination.page >= pagination.pageCount;
  const watched = watchlistIds();
  const barsByInstrument = dailyBarSnapshots();
  const jobsByInstrument = latestSnapshotJobs();
  const root = byId("bwInstrumentList");
  root.replaceChildren();
  root.scrollTop = 0;
  if (!pagination.records.length) {
    const empty = document.createElement("p");
    empty.className = "bw-empty muted";
    empty.textContent = snapshot
      ? (state.marketTab === "watchlist" ? "The watchlist is empty. Use All stocks to add symbols." : "No stock matches this search.")
      : "There is no stock catalog yet.";
    root.append(empty);
  } else {
    pagination.records.forEach((instrument) => root.append(instrumentRow(
      instrument,
      watched.has(instrument.instrumentId),
      barsByInstrument,
      jobsByInstrument,
    )));
  }
}

async function loadMarket() {
  const previousSnapshotId = state.market?.snapshot?.snapshotId || "";
  state.market = requireMarketEnvelope(await getJson(MARKET_ENDPOINT));
  if ((state.market.snapshot?.snapshotId || "") !== previousSnapshotId) state.marketPage = 1;
  renderMarket();
  const activeSnapshotJobs = (state.market.snapshotJobs || [])
    .filter((job) => ["queued", "running"].includes(job.status));
  setStatus(activeSnapshotJobs.length
    ? `${activeSnapshotJobs.length} automatic K-line snapshot${activeSnapshotJobs.length === 1 ? "" : "s"} in progress…`
    : (state.market.snapshot ? "Stock catalog ready · saved K-line snapshots are cached" : "No stock catalog available"));
  if (state.snapshotPollTimer) clearTimeout(state.snapshotPollTimer);
  state.snapshotPollTimer = null;
  if ((state.market.snapshotJobs || []).some((job) => ["queued", "running"].includes(job.status))) {
    state.snapshotPollTimer = setTimeout(() => {
      state.snapshotPollTimer = null;
      void loadMarket().catch(() => setStatus("Snapshot status refresh failed.", true));
    }, 1500);
  }
  return state.market;
}

async function syncMarket() {
  if (state.syncBusy || state.watchlistBusy) return;
  state.syncBusy = true;
  renderMarket();
  setStatus("Refreshing stock list…");
  try {
    const response = await postJson(`${MARKET_ENDPOINT}/sync`, { providerId: BASIC_MARKET_PROVIDER_ID });
    requireProtocol(response.protocolId, "Market sync response.protocolId");
    if (response.accepted !== true) throw new Error("Market sync was not accepted.");
    await loadMarket();
  } catch {
    setStatus("Stock list refresh failed.", true);
  } finally {
    state.syncBusy = false;
    renderMarket();
  }
}

function selectMarketTab(tab) {
  if (!state.market?.snapshot || !["watchlist", "all"].includes(tab)) return;
  state.marketTab = tab;
  state.marketPage = 1;
  renderMarket();
}

async function toggleWatchlist(instrumentId) {
  const snapshot = state.market?.snapshot;
  if (!snapshot || state.watchlistBusy || state.syncBusy) return;
  if (!snapshot.instruments.some((item) => item.instrumentId === instrumentId)) throw new Error("Selected instrument is outside the current snapshot.");
  const ids = watchlistIds();
  if (ids.has(instrumentId)) ids.delete(instrumentId); else ids.add(instrumentId);
  state.watchlistBusy = true;
  renderMarket();
  try {
    const response = await postJson("/api/subsystems/basic/watchlist", {
      snapshotId: snapshot.snapshotId,
      instrumentIds: [...ids].sort(),
    });
    requireProtocol(response.protocolId, "Watchlist response.protocolId");
    if (response.accepted !== true || response.snapshotId !== snapshot.snapshotId) throw new Error("Watchlist response did not preserve the current snapshot.");
    await loadMarket();
    if ((response.snapshotJobs || []).length) {
      setStatus("Automatic K-line snapshot queued in the background");
    } else if (dailyBarSnapshots().has(instrumentId)) {
      setStatus("Watchlist updated · K-line snapshot already cached");
    }
  } catch {
    setStatus("Watchlist update failed.", true);
  } finally {
    state.watchlistBusy = false;
    renderMarket();
  }
}

async function main() {
  byId("bwSyncMarket").addEventListener("click", () => void syncMarket());
  byId("bwInstrumentSearch").addEventListener("input", () => {
    state.marketPage = 1;
    renderMarket();
  });
  byId("bwPreviousPage").addEventListener("click", () => {
    state.marketPage -= 1;
    renderMarket();
  });
  byId("bwNextPage").addEventListener("click", () => {
    state.marketPage += 1;
    renderMarket();
  });
  byId("bwInstrumentList").addEventListener("click", (event) => {
    const toggle = event.target.closest?.("[data-watch-instrument-id]");
    if (!toggle) return;
    event.preventDefault();
    event.stopPropagation();
    void toggleWatchlist(toggle.dataset.watchInstrumentId);
  });
  const marketTabs = [...document.querySelectorAll("[data-market-tab]")];
  marketTabs.forEach((button, index) => {
    button.addEventListener("click", () => selectMarketTab(button.dataset.marketTab));
    button.addEventListener("keydown", (event) => {
      let nextIndex = null;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % marketTabs.length;
      if (event.key === "ArrowLeft") nextIndex = (index - 1 + marketTabs.length) % marketTabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = marketTabs.length - 1;
      if (nextIndex === null || !state.market?.snapshot) return;
      event.preventDefault();
      const next = marketTabs[nextIndex];
      selectMarketTab(next.dataset.marketTab);
      next.focus();
    });
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted || !state.csrfToken) return;
    void loadMarket().catch(() => setStatus("Stock catalog reload failed.", true));
  });
  try {
    const session = await getJson("/auth/session");
    state.csrfToken = requireString(session.csrfToken, "Session CSRF token");
    const market = await loadMarket();
    if (market.snapshot === null && !state.initialSyncAttempted) {
      state.initialSyncAttempted = true;
      await syncMarket();
    }
  } catch {
    state.market = { protocolId: BASIC_PROTOCOL_ID, snapshot: null, watchlist: [], barSnapshots: [], snapshotJobs: [] };
    renderMarket();
    setStatus("The stock catalog is unavailable.", true);
  }
}

void main();
