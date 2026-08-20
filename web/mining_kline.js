(function installMiningKLine(global) {
  "use strict";

  function create(deps) {
    const { $, escapeHtml, getJson, postJson, publishOperation, isActive, onError } = deps;
    const state = {
      providers: [], jobs: [], health: null, selectedJobId: "", detail: null,
    };
    let loaded = false;
    let pollTimer = null;

    const view = $("mining-kline");
    if (view) {
      view.innerHTML = `
        <div id="miningHealthStrip" class="mining-health-strip" aria-live="polite"></div>
        <div class="panel">
          <div class="panel-head">
            <div><h2>K Line Mining</h2><span class="muted">Durable provider-native pages, resumable checkpoints and revision evidence</span></div>
            <div class="toolbar"><button id="refreshMiningBtn" type="button">Refresh</button><button id="addMiningJobBtn" type="button">Add Job</button></div>
          </div>
          <div id="miningJobList" class="mining-job-list"></div>
        </div>
        <div id="miningJobDetail" class="panel mining-job-detail" hidden></div>`;
    }

    function statusClass(status) {
      if (["succeeded", "queued"].includes(status)) return "ok";
      if (status === "blocked") return "danger";
      if (["retry_wait", "paused"].includes(status)) return "warn";
      return "active";
    }

    function renderHealth() {
      const root = $("miningHealthStrip");
      if (!root) return;
      const health = state.health || {};
      const metrics = health.metrics || {};
      root.innerHTML = [
        ["Worker", health.workerAlive ? "Alive" : "Unavailable", health.workerAlive ? "ok" : "danger"],
        ["Jobs", health.jobs ?? 0, ""],
        ["Active", health.active ?? 0, "active"],
        ["Blocked", health.blocked ?? 0, health.blocked ? "danger" : ""],
        ["Pages", metrics.pages_committed ?? 0, ""],
        ["Observed", metrics.records_observed ?? 0, ""],
        ["Recovered", metrics.orphan_files_recovered ?? 0, metrics.orphan_files_recovered ? "warn" : ""],
      ].map(([label, value, tone]) => `
        <div class="mining-health-card ${tone}">
          <span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>
        </div>`).join("");
    }

    function renderJobs() {
      const root = $("miningJobList");
      if (!root) return;
      if (!state.jobs.length) {
        root.innerHTML = '<div class="mining-empty"><strong>No mining jobs</strong><span class="muted">Add a provider job to begin durable local accumulation.</span></div>';
        return;
      }
      root.innerHTML = state.jobs.map((job) => `
        <button type="button" class="mining-job-row ${job.jobId === state.selectedJobId ? "selected" : ""}"
          data-mining-job="${escapeHtml(job.jobId)}">
          <span><strong>${escapeHtml(job.name)}</strong><small>${escapeHtml(job.provider)} · ${escapeHtml(job.jobId)}</small></span>
          <span class="mining-status ${statusClass(job.status)}">${escapeHtml(job.status)}</span>
          <span><strong>${escapeHtml(job.currentRecords ?? 0)}</strong><small>current records</small></span>
          <span><strong>${escapeHtml(job.pageCount ?? 0)}</strong><small>durable pages</small></span>
          <span><strong>${escapeHtml(job.openGaps ?? 0)}</strong><small>open gaps</small></span>
          <span><strong>${escapeHtml(job.consecutiveFailures ?? 0)}</strong><small>failures</small></span>
        </button>`).join("");
      root.querySelectorAll("[data-mining-job]").forEach((button) => {
        button.addEventListener("click", async () => {
          state.selectedJobId = button.dataset.miningJob;
          renderJobs();
          await loadDetail(state.selectedJobId);
        });
      });
    }

    function renderDetail() {
      const root = $("miningJobDetail");
      const detail = state.detail;
      if (!root) return;
      if (!detail?.job) {
        root.hidden = true;
        root.innerHTML = "";
        return;
      }
      const job = detail.job;
      const active = ["leased", "fetching", "committing"].includes(job.status);
      const paused = job.status === "paused";
      const canPause = !paused && job.status !== "blocked";
      const gaps = detail.gaps || [];
      const records = detail.records || [];
      root.hidden = false;
      root.innerHTML = `
        <div class="panel-head">
          <div><h2>${escapeHtml(job.name)}</h2><span class="muted">${escapeHtml(job.provider)} · checkpoint ${escapeHtml(JSON.stringify(job.cursor))}</span></div>
          <div class="toolbar">
            ${canPause ? '<button type="button" data-mining-action="pause">Pause</button>' : ""}
            ${paused || job.status === "blocked" ? '<button type="button" data-mining-action="resume">Resume</button>' : ""}
            <button type="button" data-mining-action="run-now" ${active || paused ? "disabled" : ""}>Run now</button>
          </div>
        </div>
        <div class="mining-detail-body">
          ${job.lastError ? `<p class="mining-error">${escapeHtml(job.lastError)}</p>` : ""}
          <section><h3>Continuity gaps</h3><div class="mining-gap-list">
            ${gaps.length ? gaps.map((gap) => `
              <div class="mining-gap-row">
                <span><strong>${escapeHtml(gap.estimatedMissing)}</strong> estimated missing</span>
                <code>${escapeHtml(gap.missingStart)} → ${escapeHtml(gap.missingEnd)}</code>
                <span class="mining-status ${statusClass(gap.status)}">${escapeHtml(gap.status)}</span>
                <button type="button" data-mining-refill="${escapeHtml(gap.gapId)}" ${gap.status === "backfill_queued" ? "disabled" : ""}>Queue refill</button>
              </div>`).join("") : '<span class="muted">No numeric continuity gaps detected.</span>'}
          </div></section>
          <section><h3>Latest provider-native records</h3><div class="mining-record-list">
            ${records.length ? records.map((entry) => `
              <article><header><code>${escapeHtml(JSON.stringify(entry.identity))}</code><span>revision ${escapeHtml(entry.revision)} · ${entry.isFinal ? "final" : "provisional"}</span></header>
              <pre>${escapeHtml(JSON.stringify(entry.record, null, 2))}</pre></article>`).join("") : '<span class="muted">No committed records yet.</span>'}
          </div></section>
          <p class="mining-native-note">Raw responses and provider-native JSONL partitions are retained under the independent mining root. No Dataset is published automatically.</p>
        </div>`;
      root.querySelectorAll("[data-mining-action]").forEach((button) => {
        button.addEventListener("click", () => runAction(button.dataset.miningAction).catch(onError));
      });
      root.querySelectorAll("[data-mining-refill]").forEach((button) => {
        button.addEventListener("click", () => runRefill(button.dataset.miningRefill).catch(onError));
      });
    }

    async function loadDetail(jobId) {
      if (!jobId) {
        state.detail = null;
      } else {
        state.detail = await getJson(`/api/mining/jobs/${encodeURIComponent(jobId)}`);
      }
      renderDetail();
    }

    function schedulePoll() {
      clearTimeout(pollTimer);
      if (!isActive()) return;
      pollTimer = setTimeout(() => load(true).catch(onError), 5000);
    }

    async function load(force = false) {
      if (!force && loaded) {
        renderHealth(); renderJobs(); renderDetail(); schedulePoll();
        return;
      }
      const [providers, health, jobs] = await Promise.all([
        getJson("/api/mining/providers"),
        getJson("/api/mining/health"),
        getJson("/api/mining/jobs"),
      ]);
      state.providers = providers.providers || [];
      state.health = health;
      state.jobs = jobs.jobs || [];
      state.jobs.forEach((job) => {
        const status = ["leased", "fetching", "committing"].includes(job.status) ? "progress"
          : job.status === "blocked" ? "failed"
            : job.status === "succeeded" ? "completed" : "waiting";
        const updatedAt = [job.updatedAt, job.lastRunAt, job.createdAt]
          .find((value) => value && Number.isFinite(Date.parse(value))) || new Date().toISOString();
        publishOperation?.({
          operationId: `mining:${job.jobId}`,
          kind: "mining",
          resourceId: String(job.jobId),
          status,
          message: String(job.status || "Mining").slice(0, 512),
          ...(status === "failed" ? { errorCode: "mining_blocked" } : {}),
          updatedAt: new Date(updatedAt).toISOString(),
        });
      });
      if (!state.jobs.some((job) => job.jobId === state.selectedJobId)) {
        state.selectedJobId = state.jobs[0]?.jobId || "";
      }
      renderHealth(); renderJobs();
      await loadDetail(state.selectedJobId);
      loaded = true;
      schedulePoll();
    }

    async function runAction(action) {
      if (!state.selectedJobId) return;
      await postJson(`/api/mining/jobs/${encodeURIComponent(state.selectedJobId)}/${action}`, {});
      loaded = false;
      await load(true);
    }

    async function runRefill(gapId) {
      if (!state.selectedJobId || !gapId) return;
      await postJson(`/api/mining/jobs/${encodeURIComponent(state.selectedJobId)}/gaps/${encodeURIComponent(gapId)}/refill`, {});
      loaded = false;
      await load(true);
    }

    function populateProviderForm() {
      const select = $("miningJobProvider");
      if (!select) return;
      select.innerHTML = state.providers.map((provider) => `
        <option value="${escapeHtml(provider.providerId)}">${escapeHtml(provider.label)}</option>`).join("");
      const selected = state.providers.find((provider) => provider.providerId === select.value) || state.providers[0];
      if (selected) {
        select.value = selected.providerId;
        $("miningProviderConfig").value = JSON.stringify(selected.configExample || {}, null, 2);
      }
    }

    $("refreshMiningBtn")?.addEventListener("click", () => load(true).catch(onError));
    $("addMiningJobBtn")?.addEventListener("click", () => {
      $("miningJobForm")?.reset();
      $("miningScheduleSeconds").value = "60";
      $("miningOverlapRecords").value = "2";
      $("miningJobError").hidden = true;
      populateProviderForm();
      $("miningJobDialog")?.showModal();
    });
    $("cancelMiningJobBtn")?.addEventListener("click", () => $("miningJobDialog")?.close());
    $("miningJobProvider")?.addEventListener("change", (event) => {
      const selected = state.providers.find((provider) => provider.providerId === event.target.value);
      if (selected) $("miningProviderConfig").value = JSON.stringify(selected.configExample || {}, null, 2);
    });
    $("miningJobForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const errorNode = $("miningJobError");
      errorNode.hidden = true;
      try {
        const providerConfig = JSON.parse($("miningProviderConfig").value);
        if (!providerConfig || Array.isArray(providerConfig) || typeof providerConfig !== "object") {
          throw new Error("Provider config must be a JSON object.");
        }
        const payload = {
          name: $("miningJobName").value.trim(),
          provider: $("miningJobProvider").value,
          providerConfig,
          scheduleSeconds: Number($("miningScheduleSeconds").value),
          overlapRecords: Number($("miningOverlapRecords").value),
        };
        const jobId = $("miningJobId").value.trim();
        const continuity = $("miningContinuityStep").value.trim();
        if (jobId) payload.jobId = jobId;
        if (continuity) payload.continuityStep = Number(continuity);
        const result = await postJson("/api/mining/jobs", payload);
        state.selectedJobId = result.job?.jobId || "";
        $("miningJobDialog").close();
        loaded = false;
        await load(true);
      } catch (error) {
        errorNode.textContent = error.message;
        errorNode.hidden = false;
      }
    });

    return {
      load,
      deactivate() {
        clearTimeout(pollTimer);
        pollTimer = null;
      },
      state,
    };
  }

  global.TradeMiningKLine = { create };
})(window);
