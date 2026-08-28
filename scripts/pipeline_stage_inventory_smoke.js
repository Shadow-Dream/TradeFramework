#!/usr/bin/env node
"use strict";

const { execFileSync } = require("node:child_process");
const { isDeepStrictEqual } = require("node:util");

const BASE = process.env.TRADE_WEB_BASE || "http://10.130.130.66:30809";
const CONFIG = process.env.TRADE_CONFIG || "deploy/user/strategy-control-preview.json";
const CHROME = process.env.TRADE_CHROME_PATH || "/usr/bin/google-chrome";

const CONTRACT = Object.freeze({
  pipelineRoute: "/pipeline/builder",
  singleStages: Object.freeze([
    Object.freeze({ stage: "target", kind: "Target" }),
    Object.freeze({ stage: "universe", kind: "Universe" }),
  ]),
  stageKinds: Object.freeze({
    universe: "Universe",
    signal: "Signal",
    target: "Target",
    constraint: "Constraint",
  }),
  stageCardModes: Object.freeze({
    container: "container",
    singleModule: "single-module",
    singleSlot: "single-slot",
  }),
  singleModuleSelector: ".pipeline-single-module-card[data-single-stage-module]",
  singleModuleMetadataSelector: ".pipeline-single-module-identity[data-single-module-meta]",
  singleModuleConfigureSelector: ".pipeline-single-module-configure[data-configure-stage-module][data-instance]",
  singleSlotSelector: ".pipeline-single-stage-slot",
  stageContainerSelector: ".pipeline-stage-container",
  stageTagListSelector: ".pipeline-stage-tag-list.loaded-tags",
  stageModuleTagSelector: ".pipeline-stage-tag-list.loaded-tags .loaded-tag[data-configure-stage-module][data-instance]",
  probePrefix: "__pipeline_stage_inventory_probe__",
});

function loadPuppeteer() {
  const requested = process.env.TRADE_PUPPETEER_MODULE;
  const candidates = [requested, "puppeteer-core", "puppeteer"].filter(Boolean);
  const failures = [];
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (error) {
      failures.push(`${candidate}: ${error.message}`);
    }
  }
  throw new Error(
    `Unable to load Puppeteer. Set TRADE_PUPPETEER_MODULE to a module or absolute module path. ${failures.join(" | ")}`,
  );
}

function assert(condition, message, payload = null) {
  if (condition) return;
  const error = new Error(message);
  error.payload = payload;
  throw error;
}

function sessionFromEnvironment() {
  if (process.env.TRADE_SESSION_JSON) {
    const parsed = JSON.parse(process.env.TRADE_SESSION_JSON);
    return { token: parsed.token, csrf: parsed.csrf || "", owned: false };
  }
  if (process.env.TRADE_SESSION_TOKEN) {
    return {
      token: process.env.TRADE_SESSION_TOKEN,
      csrf: process.env.TRADE_CSRF_TOKEN || "",
      owned: false,
    };
  }
  const code = [
    "import json, secrets, time",
    "from engine.service import control_api as control; from engine.control import auth as trade_auth",
    `config = control.load_config(${JSON.stringify(CONFIG)})`,
    "trade_auth.ensure_default_user(config)",
    "token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), int(time.time())",
    "with trade_auth.connect(config) as connection:",
    "    user = connection.execute(\"SELECT user_id FROM users WHERE status = ? ORDER BY created_at LIMIT 1\", (\"active\",)).fetchone()",
    "    connection.execute(\"INSERT INTO sessions (token_hash, user_id, csrf_hash, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)\", (trade_auth.opaque_token_hash(token), user[\"user_id\"], trade_auth.opaque_token_hash(csrf), now, now + 3600, now))",
    "    connection.commit()",
    "print(json.dumps({\"token\": token, \"csrf\": csrf}))",
  ].join("\n");
  return {
    ...JSON.parse(execFileSync("python3", ["-c", code], { encoding: "utf8" })),
    owned: true,
  };
}

function deleteOwnedSession(session) {
  if (!session?.owned || !session.token) return;
  const code = [
    "import sys",
    "from engine.service import control_api as control; from engine.control import auth as trade_auth",
    `config = control.load_config(${JSON.stringify(CONFIG)})`,
    "with trade_auth.connect(config) as connection:",
    "    connection.execute(\"DELETE FROM sessions WHERE token_hash = ?\", (trade_auth.opaque_token_hash(sys.argv[1]),))",
    "    connection.commit()",
  ].join("\n");
  execFileSync("python3", ["-c", code, session.token], { encoding: "utf8" });
}

function stageReferences(draft = {}) {
  return {
    universe: [...(draft.stages?.universe || [])].map(String),
    signal: [...(draft.alphaGraph?.nodes || [])].map(String),
    target: [...(draft.stages?.target || [])].map(String),
    constraint: [...(draft.stages?.constraint || [])].map(String),
  };
}

function ownershipAudit(draft = {}) {
  const references = stageReferences(draft);
  const ownership = new Map();
  Object.entries(references).forEach(([stage, instanceIds]) => {
    instanceIds.forEach((instanceId) => {
      const owners = ownership.get(instanceId) || [];
      owners.push(stage);
      ownership.set(instanceId, owners);
    });
  });
  const instances = draft.instances || {};
  const orphanIds = Object.keys(instances).filter((instanceId) => !ownership.has(instanceId));
  const multiplyOwnedIds = [...ownership.entries()]
    .filter(([, owners]) => owners.length !== 1)
    .map(([instanceId]) => instanceId);
  const missingReferences = [];
  const kindMismatches = [];
  Object.entries(references).forEach(([stage, instanceIds]) => {
    instanceIds.forEach((instanceId) => {
      const instance = instances[instanceId];
      if (!instance) {
        missingReferences.push({ stage, instanceId });
      } else if (instance.kind !== CONTRACT.stageKinds[stage]) {
        kindMismatches.push({
          stage,
          instanceId,
          expected: CONTRACT.stageKinds[stage],
          actual: instance.kind || "",
        });
      }
    });
  });
  return {
    references,
    orphanIds,
    multiplyOwnedIds,
    missingReferences,
    kindMismatches,
  };
}

async function settleLayout(page) {
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready.catch(() => undefined);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}

async function pipelineCandidates(page) {
  await page.goto(`${BASE}/pipeline`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForFunction(() => Object.keys(window.__tradeState?.pipelines || {}).length > 0, {
    timeout: 30000,
  });
  return page.evaluate((requested) => {
    const rows = Object.values(window.__tradeState.pipelines || {});
    if (requested) return rows.some((row) => row.pipelineId === requested) ? [requested] : [];
    return rows
      .filter((row) => row.status === "active")
      .sort((left, right) => String(left.pipelineId).localeCompare(String(right.pipelineId)))
      .map((row) => row.pipelineId);
  }, String(process.env.TRADE_PIPELINE_ID || "").trim());
}

async function openPipeline(page, pipelineId) {
  await page.goto(
    `${BASE}${CONTRACT.pipelineRoute}?pipelineId=${encodeURIComponent(pipelineId)}`,
    { waitUntil: "domcontentloaded", timeout: 30000 },
  );
  await page.waitForFunction((expectedId) => (
    location.pathname === "/pipeline/builder"
    && document.querySelector("#pipelineId")?.value === expectedId
    && window.__tradeState?.pipelineDraft
    && document.querySelectorAll("#pipelineStageGrid .flow-node").length === 4
  ), { timeout: 60000 }, pipelineId);
  await settleLayout(page);
}

async function candidateStage(page) {
  return page.evaluate((contract) => {
    const draft = window.__tradeState.pipelineDraft;
    const stageReferences = {
      universe: [...(draft.stages?.universe || [])].map(String),
      signal: [...(draft.alphaGraph?.nodes || [])].map(String),
      target: [...(draft.stages?.target || [])].map(String),
      constraint: [...(draft.stages?.constraint || [])].map(String),
    };
    const owners = new Map();
    Object.entries(stageReferences).forEach(([stage, ids]) => ids.forEach((instanceId) => {
      const current = owners.get(instanceId) || [];
      current.push(stage);
      owners.set(instanceId, current);
    }));
    const clean = Object.entries(draft.instances || {}).every(([instanceId, instance]) => {
      const instanceOwners = owners.get(instanceId) || [];
      return instanceOwners.length === 1
        && instance.kind === contract.stageKinds[instanceOwners[0]];
    }) && [...owners.keys()].every((instanceId) => draft.instances?.[instanceId]);
    if (!clean) return null;

    const signalInstanceId = stageReferences.signal.find((instanceId) => (
      draft.instances?.[instanceId]?.kind === contract.stageKinds.signal
    )) || "";
    if (!signalInstanceId) return null;
    for (const specification of contract.singleStages.filter(({ stage }) => stage === "target")) {
      const references = stageReferences[specification.stage];
      const instanceId = references.length === 1 ? references[0] : "";
      const instance = draft.instances?.[instanceId];
      const definitions = Object.entries(window.__tradeState.pipelineModules || {})
        .map(([key, definition]) => ({ key, ...definition }))
        .filter((definition) => definition.kind === specification.kind && definition.status === "archived");
      const current = window.TradeVersionSelection.currentRows(definitions, ["kind", "moduleId"]);
      if (!instance || instance.kind !== specification.kind || !current.length) continue;
      const exact = current.find((definition) => (
        definition.moduleId === instance.moduleId
          && String(definition.version) === String(instance.version)
      ));
      const template = exact || current[0];
      return {
        ...specification,
        instanceId,
        templateKey: template.key,
        templateName: template.name || template.moduleId || template.key,
        templateModuleId: template.moduleId,
        templateVersion: String(template.version),
        signalInstanceId,
      };
    }
    return null;
  }, CONTRACT);
}

async function discoverPipelineStage(page) {
  const candidates = await pipelineCandidates(page);
  assert(candidates.length, "No requested or active Pipeline is available");
  for (const pipelineId of candidates) {
    await openPipeline(page, pipelineId);
    const stage = await candidateStage(page);
    if (stage) return { pipelineId, ...stage };
  }
  throw new Error("No clean Pipeline has both a loaded Signal tag and Target Module with a current replacement template");
}

async function installDraft(page, draft) {
  await page.evaluate((nextDraft) => {
    window.__tradeState.pipelineDraft = structuredClone(nextDraft);
    window.__tradePipelineActions.loadPipelineFormFromDefinition({ preferDraft: true });
  }, draft);
  await settleLayout(page);
}

async function stageDomSnapshot(page, stage) {
  return page.evaluate(({ stageName, contract }) => {
    const card = document.querySelector(`#pipelineStageGrid [data-stage="${stageName}"]`);
    if (!card) throw new Error(`Stage card '${stageName}' is absent`);
    const singleModule = card.matches(contract.singleModuleSelector) ? card : null;
    const metadata = singleModule?.querySelector(contract.singleModuleMetadataSelector) || null;
    const configure = singleModule?.querySelector(contract.singleModuleConfigureSelector) || null;
    const issueRows = [...card.querySelectorAll(".pipeline-stage-issue")].map((row) => ({
      instanceId: row.dataset.instance || "",
      status: row.dataset.stageReferenceIssue || row.dataset.stageModuleStatus || "",
      text: row.textContent.trim(),
      title: row.title || "",
      height: row.getBoundingClientRect().height,
      action: row.hasAttribute("data-unload-stage") ? "remove-instance" : "clear-reference",
    }));
    const select = card.querySelector(`[data-load-stage="${stageName}"]`);
    const loadButton = card.querySelector(`[data-load-stage-button="${stageName}"]`);
    return {
      mode: card.dataset.stageCardMode || "",
      loadedCount: Number(card.dataset.stageLoadedCount || 0),
      instanceCount: Number(card.dataset.stageInstanceCount || 0),
      issueCount: Number(card.dataset.stageIssueCount || 0),
      summary: card.querySelector(`[data-stage-summary="${stageName}"]`)?.textContent.trim() || "",
      loadAction: loadButton?.textContent.trim() || "",
      pickerCount: select ? 1 : 0,
      loadButtonCount: loadButton ? 1 : 0,
      tagListCount: card.querySelectorAll(contract.stageTagListSelector).length,
      singleSlotCount: card.matches(contract.singleSlotSelector) ? 1 : 0,
      singleModuleCount: singleModule ? 1 : 0,
      singleModule: singleModule ? {
        instanceId: singleModule.dataset.singleStageModule || "",
        name: singleModule.querySelector("h3")?.textContent.trim() || "",
        identityText: metadata?.textContent.trim() || "",
        identityTitle: metadata?.title || "",
        identity: {
          instanceId: metadata?.dataset.instanceId || "",
          moduleId: metadata?.dataset.moduleId || "",
          version: metadata?.dataset.version || "",
        },
        configure: {
          count: singleModule.querySelectorAll(contract.singleModuleConfigureSelector).length,
          text: configure?.textContent.trim() || "",
          stage: configure?.dataset.configureStageModule || "",
          instanceId: configure?.dataset.instance || "",
        },
        inlineRemoveCount: singleModule.querySelectorAll("[data-unload-stage]").length,
      } : null,
      issueRows,
    };
  }, { stageName: stage, contract: CONTRACT });
}

function assertSingleModuleCard(snapshot, expected = {}) {
  assert(snapshot.mode === CONTRACT.stageCardModes.singleModule
      && snapshot.singleModuleCount === 1
      && snapshot.singleSlotCount === 0,
  "Loaded single stage is not the Module card itself", snapshot);
  assert(snapshot.tagListCount === 0
      && snapshot.pickerCount === 0
      && snapshot.loadButtonCount === 0
      && snapshot.issueRows.length === 0,
  "Loaded single Module still renders a container, picker, loader, or issue row", snapshot);
  assert(snapshot.loadedCount === 1 && snapshot.instanceCount === 1 && snapshot.issueCount === 0,
    "Loaded single Module card counters are inconsistent", snapshot);
  assert(snapshot.singleModule.instanceId === expected.instanceId
      && snapshot.singleModule.name === expected.name
      && snapshot.singleModule.identity.instanceId === expected.instanceId
      && snapshot.singleModule.identity.moduleId === expected.moduleId
      && snapshot.singleModule.identity.version === String(expected.version)
      && snapshot.singleModule.identityText === `${expected.moduleId} · v${expected.version}`
      && snapshot.singleModule.identityTitle === `${expected.instanceId} · ${expected.moduleId} · v${expected.version}`,
  "Single Module card identity differs from the exact loaded instance", { snapshot, expected });
  assert(snapshot.singleModule.configure.count === 1
      && snapshot.singleModule.configure.text === "Configure"
      && snapshot.singleModule.configure.stage === expected.stage
      && snapshot.singleModule.configure.instanceId === expected.instanceId
      && snapshot.singleModule.inlineRemoveCount === 0,
  "Single Module card does not expose one Configure action or still exposes inline Remove", {
    snapshot,
    expected,
  });
}

function assertSingleStageSlot(snapshot, expected = {}) {
  assert(snapshot.mode === CONTRACT.stageCardModes.singleSlot
      && snapshot.singleSlotCount === 1
      && snapshot.singleModuleCount === 0,
  "Empty or invalid single stage is not one loader slot", snapshot);
  assert(snapshot.tagListCount === 0
      && snapshot.pickerCount === 1
      && snapshot.loadButtonCount === 1
      && snapshot.loadAction === "Load",
  "Single-stage loader slot renders a container or lacks picker + Load", snapshot);
  assert(snapshot.loadedCount === expected.loadedCount
      && snapshot.issueCount === expected.issueCount
      && snapshot.issueRows.length === expected.issueCount,
  "Single-stage loader slot counters do not match its draft state", { snapshot, expected });
  if (expected.issueCount) {
    assert(snapshot.summary.includes(`${expected.loadedCount} loaded`)
        && snapshot.summary.includes(`${expected.issueCount} issue${expected.issueCount === 1 ? "" : "s"}`),
    "Single-stage loader slot hides its draft issue summary", snapshot);
  }
}

async function loadActionsSnapshot(page) {
  return page.evaluate(() => [...document.querySelectorAll("#pipelineStageGrid .flow-node[data-stage]")]
    .map((card) => {
      const stage = card.dataset.stage || "";
      const button = card.querySelector(`[data-load-stage-button="${stage}"]`);
      return {
        stage,
        mode: card.dataset.stageCardMode || "",
        text: button?.textContent.trim() || "",
        loadButtonCount: button ? 1 : 0,
        pickerCount: card.querySelector(`[data-load-stage="${stage}"]`) ? 1 : 0,
      };
    }));
}

function assertModeCorrectLoadActions(actions) {
  assert(actions.length === 4,
    "Pipeline does not expose exactly four Stage cards", actions);
  assert(actions.every((action) => !action.text || action.text === "Load"),
    "A visible Pipeline loader uses an action other than Load", actions);
  assert(actions.every((action) => {
    if (action.mode === CONTRACT.stageCardModes.singleModule) {
      return action.loadButtonCount === 0 && action.pickerCount === 0;
    }
    if (action.mode === CONTRACT.stageCardModes.singleSlot) {
      return action.loadButtonCount === 1 && action.pickerCount === 1 && action.text === "Load";
    }
    if (action.mode === CONTRACT.stageCardModes.container && action.stage === "signal") {
      return action.loadButtonCount === 0 && action.pickerCount === 0;
    }
    if (action.mode === CONTRACT.stageCardModes.container && action.stage === "constraint") {
      return action.loadButtonCount === 1 && action.pickerCount === 1 && action.text === "Load";
    }
    return false;
  }), "Pipeline loader presence does not follow the Stage card mode", actions);
}

async function pipelineDraftSnapshot(page) {
  return page.evaluate(() => structuredClone(window.__tradeState?.pipelineDraft || {}));
}

async function openConfigureDialogAudit(page, stage, instanceId) {
  const before = await pipelineDraftSnapshot(page);
  await page.evaluate(({ expectedStage, expectedInstanceId }) => {
    const action = [...document.querySelectorAll("[data-configure-stage-module][data-instance]")]
      .find((button) => (
        button.dataset.configureStageModule === expectedStage
        && button.dataset.instance === expectedInstanceId
      ));
    if (!action) throw new Error(`Configure action is absent for ${expectedStage}:${expectedInstanceId}`);
    action.click();
  }, { expectedStage: stage, expectedInstanceId: instanceId });
  await page.waitForFunction(() => {
    const dialog = document.querySelector("#moduleLoadDialog");
    return dialog?.open && dialog.dataset.mode === "edit";
  }, { timeout: 5000 });
  const dialog = await page.evaluate(({ expectedStage, expectedInstanceId }) => {
    const draft = window.__tradeState?.pipelineDraft || {};
    const instance = draft.instances?.[expectedInstanceId];
    if (!instance) throw new Error(`Draft instance '${expectedInstanceId}' is absent`);
    const definition = Object.values(window.__tradeState?.pipelineModules || {}).find((candidate) => (
      candidate.kind === instance.kind
      && candidate.moduleId === instance.moduleId
      && String(candidate.version) === String(instance.version)
    ));
    if (!definition) throw new Error(`Exact Module definition is absent for '${expectedInstanceId}'`);
    const forms = window.TradeModuleForms;
    const inputNames = Object.keys(definition.ports?.inputs || {});
    const outputNames = Object.keys(definition.ports?.outputs || {});
    const outputFields = [...document.querySelectorAll("#moduleLoadOutputsFields [data-port-field]")];
    const scalarMutation = (() => {
      const properties = definition.configSchema?.properties || {};
      const valid = (value, specification) => {
        try {
          forms.validateSchemaValue(value, specification, "Acceptance probe");
          return true;
        } catch {
          return false;
        }
      };
      const propertyEntries = Object.entries(properties).sort(([, left], [, right]) => {
        const priority = (specification) => {
          const rawType = Array.isArray(specification?.type)
            ? specification.type.find((value) => value !== "null")
            : specification?.type;
          if (rawType === "boolean") return 0;
          if (rawType === "integer" || rawType === "number") return 1;
          if (specification?.enum?.length > 1) return 2;
          if (rawType === "string") return 3;
          return 4;
        };
        return priority(left) - priority(right);
      });
      for (const [key, specification] of propertyEntries) {
        if (specification?.const !== undefined
            || specification?.oneOf
            || specification?.anyOf
            || specification?.allOf) continue;
        const rawType = Array.isArray(specification?.type)
          ? specification.type.find((value) => value !== "null")
          : specification?.type;
        const type = rawType || (specification?.enum ? typeof specification.enum[0] : "string");
        const current = instance.config?.[key];
        if (specification?.enum?.length > 1
            && specification.enum.every((value) => typeof value === "string")) {
          const nextValue = specification.enum.find((value) => value !== current);
          if (nextValue !== undefined && valid(nextValue, specification)) {
            return { key, type: "enum", nextValue };
          }
        }
        if (type === "boolean") {
          const nextValue = current === true ? false : true;
          if (valid(nextValue, specification)) return { key, type, nextValue };
        }
        if (type === "integer" || type === "number") {
          const fallback = Number.isFinite(Number(specification.default))
            ? Number(specification.default)
            : (Number.isFinite(Number(specification.minimum)) ? Number(specification.minimum) : 0);
          const base = Number.isFinite(Number(current)) ? Number(current) : fallback;
          const step = Number.isFinite(Number(specification.multipleOf)) && Number(specification.multipleOf) > 0
            ? Number(specification.multipleOf)
            : (type === "integer" ? 1 : 0.5);
          const candidates = [base + step, base - step];
          for (const candidate of candidates) {
            const nextValue = type === "integer" ? Math.round(candidate) : candidate;
            if (nextValue !== current && valid(nextValue, specification)) {
              return { key, type, nextValue };
            }
          }
        }
        if (type === "string" && !specification.pattern && !specification.format) {
          const source = String(current ?? specification.default ?? "");
          const minLength = Number(specification.minLength || 0);
          const maxLength = Number.isFinite(Number(specification.maxLength))
            ? Number(specification.maxLength)
            : Number.POSITIVE_INFINITY;
          const candidates = [];
          if (source.length < maxLength) candidates.push(`${source}${"x".repeat(Math.max(1, minLength - source.length))}`);
          if (source.length) candidates.push(`${source.slice(0, -1)}${source.endsWith("x") ? "y" : "x"}`);
          for (const nextValue of candidates) {
            if (nextValue !== current && valid(nextValue, specification)) {
              return { key, type, nextValue };
            }
          }
        }
      }
      return null;
    })();
    return {
      stage: expectedStage,
      instanceId: expectedInstanceId,
      expected: {
        title: `Configure ${definition.name || "Untitled Module"}`,
        meta: `${instance.kind} · ${instance.moduleId} · v${instance.version} · ${expectedInstanceId}`,
        config: structuredClone(instance.config || {}),
        inputs: structuredClone(instance.inputs || {}),
        outputs: structuredClone(instance.outputs || {}),
      },
      actual: {
        dialogId: document.querySelector("#moduleLoadDialog")?.id || "",
        mode: document.querySelector("#moduleLoadDialog")?.dataset.mode || "",
        title: document.querySelector("#moduleLoadDialogTitle")?.textContent.trim() || "",
        meta: document.querySelector("#moduleLoadDialogMeta")?.textContent.trim() || "",
        instanceId: document.querySelector("#moduleLoadInstanceId")?.value || "",
        confirmText: document.querySelector("#confirmModuleLoadBtn")?.textContent.trim() || "",
        confirmDisabled: Boolean(document.querySelector("#confirmModuleLoadBtn")?.disabled),
        removeText: document.querySelector("#removeConfiguredModuleBtn")?.textContent.trim() || "",
        removeHidden: Boolean(document.querySelector("#removeConfiguredModuleBtn")?.hidden),
        config: forms.readSchemaFields(
          document.querySelector("#moduleLoadConfigFields"),
          definition.configSchema || {},
        ),
        inputs: forms.readParamFields(
          document.querySelector("#moduleLoadInputsFields"),
          inputNames.map((name) => ({ name, type: "dataKey" })),
        ),
        outputs: forms.readPortFields(
          document.querySelector("#moduleLoadOutputsFields"),
          definition.ports?.outputs || {},
        ),
        outputFieldCount: outputFields.length,
        expectedOutputFieldCount: outputNames.length,
        outputsReadonly: outputFields.every((field) => (
          field.readOnly && field.dataset.editLocked === "true"
        )),
      },
      scalarMutation,
    };
  }, { expectedStage: stage, expectedInstanceId: instanceId });
  assert(dialog.actual.dialogId === "moduleLoadDialog"
      && dialog.actual.mode === "edit"
      && dialog.actual.title === dialog.expected.title
      && dialog.actual.meta === dialog.expected.meta
      && dialog.actual.instanceId === instanceId
      && dialog.actual.confirmText === "Apply"
      && !dialog.actual.confirmDisabled
      && dialog.actual.removeText === "Remove"
      && !dialog.actual.removeHidden,
  "Configure did not open the shared Module dialog with edit actions and exact identity", dialog);
  assert(isDeepStrictEqual(dialog.actual.config, dialog.expected.config)
      && isDeepStrictEqual(dialog.actual.inputs, dialog.expected.inputs)
      && isDeepStrictEqual(dialog.actual.outputs, dialog.expected.outputs),
  "Configure dialog did not prefill the exact instance config and bindings", dialog);
  assert(dialog.actual.outputFieldCount === dialog.actual.expectedOutputFieldCount
      && dialog.actual.outputsReadonly,
  "Configure dialog does not preserve existing output DataKeys as read-only fields", dialog);
  return { before, dialog };
}

async function cancelConfigureDialog(page, audit) {
  await page.click("#cancelModuleLoadBtn");
  await page.waitForFunction(() => !document.querySelector("#moduleLoadDialog")?.open, {
    timeout: 5000,
  });
  const after = await pipelineDraftSnapshot(page);
  assert(isDeepStrictEqual(after, audit.before),
    "Cancel changed the Pipeline Draft", { before: audit.before, after });
  return { ...audit.dialog, draftUnchanged: true };
}

async function configurableEntries(page) {
  return page.evaluate(() => [...document.querySelectorAll("[data-configure-stage-module][data-instance]")]
    .map((button) => ({
      stage: button.dataset.configureStageModule || "",
      instanceId: button.dataset.instance || "",
    }))
    .filter((entry, index, rows) => entry.stage && entry.instanceId
      && rows.findIndex((candidate) => (
        candidate.stage === entry.stage && candidate.instanceId === entry.instanceId
      )) === index));
}

async function applyOneScalarConfig(page, preferredEntries) {
  const allEntries = await configurableEntries(page);
  const entries = [...preferredEntries, ...allEntries]
    .filter((entry, index, rows) => rows.findIndex((candidate) => (
      candidate.stage === entry.stage && candidate.instanceId === entry.instanceId
    )) === index);
  for (const entry of entries) {
    const audit = await openConfigureDialogAudit(page, entry.stage, entry.instanceId);
    if (!audit.dialog.scalarMutation) {
      await cancelConfigureDialog(page, audit);
      continue;
    }
    const mutation = audit.dialog.scalarMutation;
    await page.evaluate(({ key, type, nextValue }) => {
      const input = document.querySelector(
        `#moduleLoadConfigFields [data-schema-field="${CSS.escape(key)}"]`,
      );
      if (!input) throw new Error(`Config field '${key}' is absent`);
      input.value = type === "boolean" ? String(Boolean(nextValue)) : String(nextValue);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }, mutation);
    await page.waitForFunction(() => !document.querySelector("#confirmModuleLoadBtn")?.disabled, {
      timeout: 5000,
    });
    await page.click("#confirmModuleLoadBtn");
    await page.waitForFunction(() => !document.querySelector("#moduleLoadDialog")?.open, {
      timeout: 5000,
    });
    const after = await pipelineDraftSnapshot(page);
    const expected = structuredClone(audit.before);
    expected.instances[entry.instanceId].config ||= {};
    expected.instances[entry.instanceId].config[mutation.key] = mutation.nextValue;
    assert(isDeepStrictEqual(after, expected),
      "Apply changed anything beyond one safe scalar config value", {
        entry,
        mutation,
        before: audit.before,
        expected,
        after,
      });
    const beforeOwnership = ownershipAudit(audit.before);
    const afterOwnership = ownershipAudit(after);
    assert(isDeepStrictEqual(afterOwnership, beforeOwnership)
        && !afterOwnership.orphanIds.length
        && !afterOwnership.multiplyOwnedIds.length
        && !afterOwnership.missingReferences.length
        && !afterOwnership.kindMismatches.length,
    "Apply changed Stage ownership or created an invalid instance reference", {
      beforeOwnership,
      afterOwnership,
    });
    const preserved = {
      instanceMapKey: Object.prototype.hasOwnProperty.call(after.instances, entry.instanceId),
      identity: ["instanceId", "kind", "moduleId", "version"].every((key) => (
        after.instances[entry.instanceId]?.[key] === audit.before.instances[entry.instanceId]?.[key]
      )),
      stageReferences: isDeepStrictEqual(stageReferences(after), stageReferences(audit.before)),
      alphaGraphNodes: isDeepStrictEqual(after.alphaGraph?.nodes, audit.before.alphaGraph?.nodes),
      alphaGraph: isDeepStrictEqual(after.alphaGraph, audit.before.alphaGraph),
      inputs: isDeepStrictEqual(
        after.instances[entry.instanceId].inputs,
        audit.before.instances[entry.instanceId].inputs,
      ),
      outputs: isDeepStrictEqual(
        after.instances[entry.instanceId].outputs,
        audit.before.instances[entry.instanceId].outputs,
      ),
      otherInstances: isDeepStrictEqual(
        Object.fromEntries(Object.entries(after.instances).filter(([id]) => id !== entry.instanceId)),
        Object.fromEntries(Object.entries(audit.before.instances).filter(([id]) => id !== entry.instanceId)),
      ),
    };
    assert(Object.values(preserved).every(Boolean),
      "Apply did not preserve the instance identity, bindings, graph, Stage references, and other instances", {
        entry,
        mutation,
        preserved,
      });
    return {
      entry,
      mutation,
      identity: {
        instanceId: entry.instanceId,
        kind: after.instances[entry.instanceId].kind,
        moduleId: after.instances[entry.instanceId].moduleId,
        version: after.instances[entry.instanceId].version,
      },
      preserved: { ...preserved, ownership: afterOwnership },
      draftChangeCount: 1,
    };
  }
  throw new Error("No loaded configurable Module exposes a safe scalar config field");
}

async function removeThenCancel(page, stage, instanceId) {
  const audit = await openConfigureDialogAudit(page, stage, instanceId);
  await page.click("#removeConfiguredModuleBtn");
  await page.waitForFunction(() => (
    !document.querySelector("#moduleLoadDialog")?.open
    && document.querySelector("#unloadDialog")?.open
  ), { timeout: 5000 });
  const unload = await page.evaluate(() => ({
    title: document.querySelector("#unloadDialogTitle")?.textContent.trim() || "",
    text: document.querySelector("#unloadDialogText")?.textContent.trim() || "",
    confirmText: document.querySelector("#confirmUnloadBtn")?.textContent.trim() || "",
    cancelText: document.querySelector("#cancelUnloadBtn")?.textContent.trim() || "",
  }));
  assert(unload.title.startsWith("Unload ")
      && unload.text.includes(instanceId)
      && unload.confirmText === "Unload"
      && unload.cancelText === "Cancel",
  "Configure Remove did not open the existing unload confirmation", unload);
  await page.click("#cancelUnloadBtn");
  await page.waitForFunction(() => !document.querySelector("#unloadDialog")?.open, {
    timeout: 5000,
  });
  const after = await pipelineDraftSnapshot(page);
  assert(isDeepStrictEqual(after, audit.before),
    "Cancelling Configure Remove changed the Pipeline Draft", { before: audit.before, after });
  return { ...audit.dialog, unload, draftUnchanged: true };
}

async function configureNoSaveFlow(page, candidate) {
  const signal = await cancelConfigureDialog(
    page,
    await openConfigureDialogAudit(page, "signal", candidate.signalInstanceId),
  );
  const target = await cancelConfigureDialog(
    page,
    await openConfigureDialogAudit(page, "target", candidate.instanceId),
  );
  const applied = await applyOneScalarConfig(page, [
    { stage: "target", instanceId: candidate.instanceId },
    { stage: "signal", instanceId: candidate.signalInstanceId },
  ]);
  const removedThenCancelled = await removeThenCancel(page, "target", candidate.instanceId);
  return { signal, target, applied, removedThenCancelled };
}

async function replaceSingleStage(page, candidate) {
  const before = await page.evaluate(() => structuredClone(window.__tradeState.pipelineDraft));
  const beforeActions = await loadActionsSnapshot(page);
  assertModeCorrectLoadActions(beforeActions);
  const beforeDom = await stageDomSnapshot(page, candidate.stage);
  assert(beforeDom.mode === CONTRACT.stageCardModes.singleModule
      && beforeDom.singleModule?.instanceId === candidate.instanceId
      && beforeDom.pickerCount === 0
      && beforeDom.loadButtonCount === 0,
  "Loaded single Stage does not begin as the Module card itself", { candidate, beforeDom });
  const oldKindIds = Object.entries(before.instances || {})
    .filter(([, instance]) => instance?.kind === candidate.kind)
    .map(([instanceId]) => instanceId);

  const removeDialog = await openConfigureDialogAudit(page, candidate.stage, candidate.instanceId);
  await page.click("#removeConfiguredModuleBtn");
  await page.waitForFunction(() => (
    !document.querySelector("#moduleLoadDialog")?.open
    && document.querySelector("#unloadDialog")?.open
  ), { timeout: 5000 });
  await page.click("#confirmUnloadBtn");
  await page.waitForFunction((stage) => {
    const card = document.querySelector(`#pipelineStageGrid [data-stage="${stage}"]`);
    return card?.dataset.stageCardMode === "single-slot"
      && card.matches(".pipeline-single-stage-slot")
      && card.querySelector(`[data-load-stage="${stage}"]`)
      && card.querySelector(`[data-load-stage-button="${stage}"]`);
  }, { timeout: 10000 }, candidate.stage);
  await settleLayout(page);
  const afterUnload = await page.evaluate(() => structuredClone(window.__tradeState.pipelineDraft));
  const afterUnloadAudit = ownershipAudit(afterUnload);
  assert(afterUnload.stages?.[candidate.stage]?.length === 0
      && oldKindIds.every((instanceId) => !afterUnload.instances?.[instanceId]),
  "Unload did not turn the loaded single Module into an empty owned slot", {
    candidate,
    oldKindIds,
    afterUnload,
    afterUnloadAudit,
  });
  assert(!afterUnloadAudit.orphanIds.length && !afterUnloadAudit.multiplyOwnedIds.length
      && !afterUnloadAudit.missingReferences.length && !afterUnloadAudit.kindMismatches.length,
  "Unload created an orphan or invalid ownership", afterUnloadAudit);
  const emptyDom = await stageDomSnapshot(page, candidate.stage);
  assertSingleStageSlot(emptyDom, { loadedCount: 0, issueCount: 0 });
  const unloadedActions = await loadActionsSnapshot(page);
  assertModeCorrectLoadActions(unloadedActions);

  const replacementId = `${CONTRACT.probePrefix}_replace_${Date.now()}`;
  await page.evaluate(({ stage, templateKey }) => {
    const select = document.querySelector(`[data-load-stage="${stage}"]`);
    if (![...select.options].some((option) => option.value === templateKey)) {
      throw new Error(`Replacement template '${templateKey}' is absent from the empty slot`);
    }
    select.value = templateKey;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    document.querySelector(`[data-load-stage-button="${stage}"]`).click();
  }, candidate);
  await page.waitForSelector("#moduleLoadDialog[open]", { timeout: 5000 });
  await page.evaluate((instanceId) => {
    const input = document.querySelector("#moduleLoadInstanceId");
    input.value = instanceId;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }, replacementId);
  await page.waitForFunction(() => !document.querySelector("#confirmModuleLoadBtn")?.disabled, {
    timeout: 5000,
  });
  await page.evaluate(() => document.querySelector("#confirmModuleLoadBtn").click());
  await page.waitForFunction(({ stage, instanceId }) => (
    !document.querySelector("#moduleLoadDialog")?.open
    && document.querySelector(`[data-stage="${stage}"]`)?.dataset.stageCardMode === "single-module"
    && document.querySelector(`[data-stage="${stage}"]`)?.dataset.singleStageModule === instanceId
  ), { timeout: 10000 }, { stage: candidate.stage, instanceId: replacementId });
  await settleLayout(page);

  const after = await page.evaluate(() => structuredClone(window.__tradeState.pipelineDraft));
  const audit = ownershipAudit(after);
  assert(after.stages?.[candidate.stage]?.length === 1
      && after.stages[candidate.stage][0] === replacementId,
  "Single-stage Replace did not establish one exact new owner", { candidate, after, audit });
  assert(oldKindIds.every((instanceId) => !after.instances?.[instanceId]),
    "Single-stage Replace left an old same-kind instance in the draft", { oldKindIds, after, audit });
  assert(!audit.orphanIds.length && !audit.multiplyOwnedIds.length
      && !audit.missingReferences.length && !audit.kindMismatches.length,
  "Single-stage Replace created an orphan or invalid ownership", audit);
  const dom = await stageDomSnapshot(page, candidate.stage);
  assertSingleModuleCard(dom, {
    stage: candidate.stage,
    instanceId: replacementId,
    name: candidate.templateName,
    moduleId: candidate.templateModuleId,
    version: candidate.templateVersion,
  });
  const afterActions = await loadActionsSnapshot(page);
  assertModeCorrectLoadActions(afterActions);
  return {
    before,
    beforeDom,
    afterUnload,
    afterUnloadAudit,
    emptyDom,
    after,
    replacementId,
    oldKindIds,
    audit,
    dom,
    beforeActions,
    removeDialog: removeDialog.dialog,
    unloadedActions,
    afterActions,
  };
}

async function invalidDraftAudits(page, candidate, cleanDraft, replacementId) {
  const orphanId = `${CONTRACT.probePrefix}_orphan`;
  const missingId = `${CONTRACT.probePrefix}_missing`;
  const mismatchId = Object.entries(cleanDraft.instances || {})
    .find(([, instance]) => instance?.kind && instance.kind !== candidate.kind)?.[0];
  assert(mismatchId, "The selected Pipeline has no different-kind instance for the mismatch probe");

  const orphanDraft = structuredClone(cleanDraft);
  orphanDraft.instances[orphanId] = {
    ...structuredClone(orphanDraft.instances[replacementId]),
    instanceId: orphanId,
  };
  await installDraft(page, orphanDraft);
  const orphan = await stageDomSnapshot(page, candidate.stage);
  assertSingleStageSlot(orphan, {
    loadedCount: 1,
    issueCount: 1,
  });
  assert(orphan.issueRows[0]?.instanceId === orphanId
      && orphan.issueRows[0]?.status === "unreferenced-instance"
      && orphan.issueRows[0]?.action === "remove-instance"
      && orphan.issueRows[0]?.title.includes("Unreferenced instance"),
  "Orphan instance is not one explicit loader-slot issue", orphan);
  assertModeCorrectLoadActions(await loadActionsSnapshot(page));

  const missingDraft = structuredClone(cleanDraft);
  Object.entries(missingDraft.instances || {})
    .filter(([, instance]) => instance?.kind === candidate.kind)
    .forEach(([instanceId]) => delete missingDraft.instances[instanceId]);
  missingDraft.stages[candidate.stage] = [missingId];
  await installDraft(page, missingDraft);
  const missing = await stageDomSnapshot(page, candidate.stage);
  assertSingleStageSlot(missing, {
    loadedCount: 0,
    issueCount: 1,
  });
  assert(missing.issueRows[0]?.instanceId === missingId
      && missing.issueRows[0]?.status === "missing-instance"
      && missing.issueRows[0]?.action === "clear-reference"
      && missing.issueRows[0]?.title.includes("Missing instance"),
  "Missing instance reference is not one explicit loader-slot issue", missing);
  assertModeCorrectLoadActions(await loadActionsSnapshot(page));

  const mismatchDraft = structuredClone(cleanDraft);
  Object.entries(mismatchDraft.instances || {})
    .filter(([, instance]) => instance?.kind === candidate.kind)
    .forEach(([instanceId]) => delete mismatchDraft.instances[instanceId]);
  mismatchDraft.stages[candidate.stage] = [mismatchId];
  await installDraft(page, mismatchDraft);
  const mismatch = await stageDomSnapshot(page, candidate.stage);
  assertSingleStageSlot(mismatch, {
    loadedCount: 0,
    issueCount: 1,
  });
  assert(mismatch.issueRows[0]?.instanceId === mismatchId
      && mismatch.issueRows[0]?.status === "kind-mismatch"
      && mismatch.issueRows[0]?.action === "clear-reference"
      && mismatch.issueRows[0]?.title.includes(`Expected ${candidate.kind}`),
  "Kind-mismatched reference is not one explicit loader-slot issue", mismatch);
  assertModeCorrectLoadActions(await loadActionsSnapshot(page));

  await installDraft(page, cleanDraft);
  assertModeCorrectLoadActions(await loadActionsSnapshot(page));
  return { orphan, missing, mismatch };
}

async function main() {
  const puppeteer = loadPuppeteer();
  const session = sessionFromEnvironment();
  let browser;
  const blockedPipelineMutations = [];
  const pageErrors = [];
  const failedResponses = [];
  try {
    browser = await puppeteer.launch({
      headless: process.env.TRADE_HEADFUL !== "1",
      executablePath: CHROME,
      args: ["--no-sandbox", "--disable-gpu", "--no-proxy-server", "--proxy-bypass-list=*"],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1600, height: 1000 });
    await page.setExtraHTTPHeaders({ "X-Forwarded-Proto": "https" });
    const secure = BASE.startsWith("https://");
    const cookies = [
      { name: "trade_session", value: session.token, url: BASE, secure, httpOnly: true, sameSite: "Strict" },
    ];
    if (session.csrf) {
      cookies.push({ name: "trade_csrf", value: session.csrf, url: BASE, secure, sameSite: "Strict" });
    }
    await page.setCookie(...cookies);
    await page.setRequestInterception(true);
    page.on("request", (request) => {
      let pathname = "";
      try {
        pathname = new URL(request.url()).pathname;
      } catch {}
      const method = request.method().toUpperCase();
      const mutation = !["GET", "HEAD", "OPTIONS"].includes(method)
        && /^\/api\/pipelines(?:\/|$)/.test(pathname);
      if (mutation) {
        blockedPipelineMutations.push(`${method} ${pathname}`);
        request.abort("blockedbyclient");
      } else {
        request.continue();
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => {
      if (response.status() >= 500) failedResponses.push(`${response.status()} ${response.url()}`);
    });

    const candidate = await discoverPipelineStage(page);
    const configure = await configureNoSaveFlow(page, candidate);
    const replacement = await replaceSingleStage(page, candidate);
    const invalid = await invalidDraftAudits(
      page,
      candidate,
      replacement.after,
      replacement.replacementId,
    );
    assert(!blockedPipelineMutations.length,
      "The no-save browser test attempted to mutate a Pipeline resource", blockedPipelineMutations);
    assert(!pageErrors.length, "Browser emitted page errors", pageErrors);
    assert(!failedResponses.length, "Browser received server errors", failedResponses);

    console.log(JSON.stringify({
      pipelineId: candidate.pipelineId,
      stage: candidate.stage,
      configure,
      replacement: {
        oldInstanceIds: replacement.oldKindIds,
        newInstanceId: replacement.replacementId,
        ownership: replacement.audit,
        dom: replacement.dom,
      },
      injectedDrafts: invalid,
      persistence: {
        saved: false,
        blockedPipelineMutations,
      },
      pageErrors,
      failedResponses,
    }, null, 2));
  } finally {
    try {
      if (browser) await browser.close();
    } finally {
      deleteOwnedSession(session);
    }
  }
}

module.exports = Object.freeze({ CONTRACT, ownershipAudit, stageReferences });

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error.message);
    if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
    process.exit(1);
  });
}
