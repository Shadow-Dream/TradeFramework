#!/usr/bin/env node
"use strict";

const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const { isDeepStrictEqual } = require("node:util");

const BASE = process.env.TRADE_WEB_BASE || "http://10.130.130.66:30809";
const CONFIG = process.env.TRADE_CONFIG || "deploy/user/strategy-control-preview.json";
const CHROME = process.env.TRADE_CHROME_PATH || "/usr/bin/google-chrome";
const SCREENSHOT_DIR = process.env.TRADE_PIPELINE_PICKER_SCREENSHOT_DIR || "/tmp";
const VIEWPORT = Object.freeze({ width: 1600, height: 1000 });

const CONTRACT = Object.freeze({
  targetStage: "target",
  pipelineStages: Object.freeze(["universe", "signal", "target", "constraint"]),
  singleStages: Object.freeze(["universe", "target"]),
  containerStages: Object.freeze(["signal", "constraint"]),
  stageCardModes: Object.freeze({
    container: "container",
    singleModule: "single-module",
    singleSlot: "single-slot",
  }),
  menuVariant: "pipeline-module",
  maxMenuItemHeight: 56,
  geometryTolerance: 0.5,
  titleMaxFontSize: 13,
  subtitleMaxFontSize: 10,
  pipelineRoute: "/pipeline/builder",
  signalRoute: "/signal-blueprint",
  titleSelector: ":scope > span",
  subtitleSelector: ":scope > small",
  singleModuleSelector: ".pipeline-single-module-card[data-single-stage-module]",
  singleModuleMetadataSelector: ".pipeline-single-module-identity[data-single-module-meta]",
  singleModuleConfigureSelector: ".pipeline-single-module-configure[data-configure-stage-module][data-instance]",
  singleSlotSelector: ".pipeline-single-stage-slot",
  stageContainerSelector: ".pipeline-stage-container",
  stageTagListSelector: ".pipeline-stage-tag-list.loaded-tags",
  stageModuleTagSelector: ".pipeline-stage-tag-list.loaded-tags .loaded-tag[data-configure-stage-module][data-instance]",
  stageCardWidth: 210,
  stageTagHeight: 28,
  stageTrailingGapMax: 14,
  stageKinds: Object.freeze({
    universe: "Universe",
    signal: "Signal",
    target: "Target",
    constraint: "Constraint",
  }),
  currentIdentityFields: Object.freeze(["kind", "moduleId"]),
  geometrySelectors: Object.freeze({
    panel: "#pipelineDefinitionPanel",
    header: "#pipelineDefinitionPanel > .panel-head",
    composer: "#pipelineComposerSection",
    viewport: "#pipelineFlowViewport",
    canvas: "#pipelineFlowCanvas",
    board: "#pipelineStageGrid",
    observation: ".pipeline-observation-input",
    footer: ".pipeline-version-footer",
  }),
  computedStyleProperties: Object.freeze([
    "display",
    "position",
    "width",
    "height",
    "minHeight",
    "gridTemplateColumns",
    "alignItems",
    "justifyContent",
    "gap",
    "padding",
    "marginBottom",
    "overflow",
    "backgroundColor",
    "fontSize",
    "fontWeight",
    "lineHeight",
    "whiteSpace",
    "transform",
  ]),
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

function sortedUnique(values) {
  return [...new Set(values)].sort((left, right) => String(left).localeCompare(String(right)));
}

function sameStringSet(left, right) {
  return JSON.stringify(sortedUnique(left)) === JSON.stringify(sortedUnique(right));
}

function sameUniqueStringSet(left, right) {
  return left.length === right.length
    && new Set(left).size === left.length
    && new Set(right).size === right.length
    && sameStringSet(left, right);
}

function snapshotDifferences(expected, actual, options = {}) {
  const tolerance = Number(options.geometryTolerance ?? CONTRACT.geometryTolerance);
  const differences = [];
  const geometryKeys = new Set(["x", "y", "width", "height", "right", "bottom"]);

  const compare = (left, right, path = "snapshot") => {
    if (differences.length >= 40) return;
    if (typeof left === "number" && typeof right === "number") {
      const key = path.split(".").at(-1)?.replace(/\[\d+\]$/, "") || "";
      const allowed = geometryKeys.has(key) ? tolerance : 0;
      if (Math.abs(left - right) > allowed) differences.push({ path, expected: left, actual: right });
      return;
    }
    if (Array.isArray(left) || Array.isArray(right)) {
      if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) {
        differences.push({ path, expected: left, actual: right });
        return;
      }
      left.forEach((value, index) => compare(value, right[index], `${path}[${index}]`));
      return;
    }
    if (left && right && typeof left === "object" && typeof right === "object") {
      const keys = sortedUnique([...Object.keys(left), ...Object.keys(right)]);
      keys.forEach((key) => compare(left[key], right[key], `${path}.${key}`));
      return;
    }
    if (left !== right) differences.push({ path, expected: left, actual: right });
  };

  compare(expected, actual);
  return differences;
}

async function settleLayout(page) {
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready.catch(() => undefined);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}

async function discoverPipelineId(page) {
  await page.goto(`${BASE}/pipeline`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForFunction(() => Object.keys(window.__tradeState?.pipelines || {}).length > 0, {
    timeout: 30000,
  });
  const requested = String(process.env.TRADE_PIPELINE_ID || "").trim();
  const discovered = await page.evaluate((override) => {
    const rows = Object.values(window.__tradeState?.pipelines || {});
    if (override) return rows.some((row) => row.pipelineId === override) ? override : "";
    const active = rows.filter((row) => row.status === "active")
      .sort((left, right) => String(left.pipelineId).localeCompare(String(right.pipelineId)));
    return (active[0] || rows[0])?.pipelineId || "";
  }, requested);
  assert(discovered, requested
    ? `Requested Pipeline '${requested}' is unavailable`
    : "No active Pipeline is available for the picker smoke test");
  return discovered;
}

async function openPipelineBuilder(page, pipelineId) {
  await page.goto(
    `${BASE}${CONTRACT.pipelineRoute}?pipelineId=${encodeURIComponent(pipelineId)}`,
    { waitUntil: "domcontentloaded", timeout: 30000 },
  );
  await page.waitForFunction((expectedId) => {
    const nodes = document.querySelectorAll("#pipelineStageGrid .flow-node");
    return location.pathname === "/pipeline/builder"
      && document.querySelector("#pipelineId")?.value === expectedId
      && nodes.length === 4
      && [...nodes].every((node) => Boolean(node.dataset.stageCardMode));
  }, { timeout: 60000 }, pipelineId);
  await settleLayout(page);
}

async function exposeEmptyTargetSlot(page) {
  await page.evaluate((targetStage) => {
    const draft = window.__tradeState?.pipelineDraft;
    if (!draft) throw new Error("Pipeline Draft is unavailable");
    draft.stages ||= {};
    draft.stages[targetStage] = [];
    Object.entries(draft.instances || {})
      .filter(([, instance]) => instance?.kind === "Target")
      .forEach(([instanceId]) => delete draft.instances[instanceId]);
    window.__tradePipelineActions.loadPipelineFormFromDefinition({ preferDraft: true });
  }, CONTRACT.targetStage);
  await page.waitForFunction((contract) => {
    const card = document.querySelector(`#pipelineStageGrid [data-stage="${contract.targetStage}"]`);
    const select = card?.querySelector(`[data-load-stage="${contract.targetStage}"]`);
    return card?.dataset.stageCardMode === contract.stageCardModes.singleSlot
      && card.matches(contract.singleSlotSelector)
      && select?.options?.length > 1
      && select.nextElementSibling?.classList.contains("hierarchical-select")
      && select.__hierarchicalMenu;
  }, { timeout: 10000 }, CONTRACT);
  await settleLayout(page);
}

async function openTargetMenu(page) {
  await page.click('#pipelineStageGrid [data-stage="target"] .hierarchical-select-trigger');
  await page.waitForFunction(() => {
    const select = document.querySelector('[data-load-stage="target"]');
    return Boolean(select?.__hierarchicalMenu && !select.__hierarchicalMenu.hidden);
  }, { timeout: 5000 });
  await page.evaluate(() => {
    const select = document.querySelector('[data-load-stage="target"]');
    select?.__hierarchicalMenu?.querySelectorAll("details").forEach((details) => {
      details.open = true;
    });
  });
  await settleLayout(page);
}

async function targetPickerAudit(page) {
  return page.evaluate((contract) => {
    const select = document.querySelector(`[data-load-stage="${contract.targetStage}"]`);
    const menu = select?.__hierarchicalMenu;
    if (!select || !menu) throw new Error("Target Module picker is not mounted");
    const trigger = select.nextElementSibling?.querySelector(".hierarchical-select-trigger");
    const definitions = Object.entries(window.__tradeState?.pipelineModules || {})
      .map(([key, definition]) => ({ key, ...definition }))
      .filter((definition) => definition.kind === "Target" && definition.status === "archived");
    const expected = window.TradeVersionSelection.currentRows(definitions, contract.currentIdentityFields);
    const catalog = window.__tradeState?.repositoryCatalogs?.modules;
    const catalogItems = catalog?.items || [];
    const allOptions = [...select.options];
    const options = allOptions.filter((option) => option.value);
    const buttons = [...menu.querySelectorAll(".hierarchical-item")];
    const rootMenuItems = [...menu.children].filter((element) => element.matches(".hierarchical-item"));
    const folderSummaries = [...menu.querySelectorAll(".hierarchical-folder > summary")]
      .map((summary) => summary.textContent.trim());

    const titleFor = (definition) => definition.name || definition.moduleId || "Untitled Module";
    const subtitleFor = (definition) => `${definition.moduleId || "unknown"} · v${definition.version || "-"}`;
    const styleRecord = (element) => {
      const style = getComputedStyle(element);
      return {
        fontSize: parseFloat(style.fontSize || "0"),
        fontWeight: style.fontWeight,
        lineHeight: style.lineHeight,
        whiteSpace: style.whiteSpace,
      };
    };
    const rectRecord = (element) => {
      const rect = element.getBoundingClientRect();
      return {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        right: rect.right,
        bottom: rect.bottom,
      };
    };

    const rows = expected.map((definition) => {
      const option = options.find((candidate) => candidate.value === definition.key);
      const expectedTitle = titleFor(definition);
      const expectedSubtitle = subtitleFor(definition);
      const button = buttons.find((candidate) => (
        candidate.querySelector(contract.titleSelector)?.textContent.trim() === expectedTitle
        && candidate.querySelector(contract.subtitleSelector)?.textContent.trim() === expectedSubtitle
      ));
      const catalogItem = catalogItems.find((candidate) => (
        String(candidate.versionKey || "") === definition.key
        || String(candidate.itemId || "") === definition.key
      ));
      const title = button?.querySelector(contract.titleSelector);
      const subtitle = button?.querySelector(contract.subtitleSelector);
      return {
        key: definition.key,
        moduleId: definition.moduleId,
        version: String(definition.version || ""),
        description: String(definition.description || "").trim(),
        expectedTitle,
        expectedSubtitle,
        optionFound: Boolean(option),
        optionTitle: option?.textContent.trim() || "",
        optionSubtitle: option?.dataset.hierarchySubtitle || "",
        optionPath: option?.parentElement?.tagName === "OPTGROUP" ? option.parentElement.label : "/",
        catalogFound: Boolean(catalogItem),
        catalogPath: catalogItem?.folderPath || "",
        buttonFound: Boolean(button),
        buttonIsRootItem: button?.parentElement === menu,
        title: title?.textContent.trim() || "",
        subtitle: subtitle?.textContent.trim() || "",
        titleStyle: title ? styleRecord(title) : null,
        subtitleStyle: subtitle ? styleRecord(subtitle) : null,
        buttonRect: button ? rectRecord(button) : null,
      };
    });

    return {
      actualKeys: options.map((option) => option.value),
      expectedKeys: expected.map((definition) => definition.key),
      emptyTrigger: {
        title: trigger?.querySelector(contract.titleSelector)?.textContent.trim() || "",
        subtitle: trigger?.querySelector(contract.subtitleSelector)?.textContent.trim() || "",
        ariaLabel: trigger?.getAttribute("aria-label")?.trim() || "",
      },
      placeholderOptions: allOptions
        .filter((option) => option.dataset.hierarchyPlaceholder === "true")
        .map((option) => ({ value: option.value, label: option.textContent.trim() })),
      placeholderMenuLabels: buttons
        .map((button) => button.querySelector(contract.titleSelector)?.textContent.trim() || "")
        .filter((label) => /^select\s+module\b/i.test(label)),
      rootMenuItemLabels: rootMenuItems
        .map((button) => button.querySelector(contract.titleSelector)?.textContent.trim() || ""),
      menuVariant: menu.dataset.hierarchyVariant || "",
      menuRect: rectRecord(menu),
      rootModuleItemCount: rows.filter((row) => row.buttonIsRootItem).length,
      folderSummaries,
      rows,
    };
  }, CONTRACT);
}

function assertTargetPicker(audit) {
  assert(audit.emptyTrigger.title === "" && audit.emptyTrigger.subtitle === "",
    "Empty Target picker trigger renders placeholder text instead of remaining blank", audit.emptyTrigger);
  assert(Boolean(audit.emptyTrigger.ariaLabel),
    "Empty Target picker has no accessible name", audit.emptyTrigger);
  assert(audit.placeholderOptions.length === 1
      && audit.placeholderOptions[0].value === ""
      && audit.placeholderOptions[0].label === "",
  "Target picker native placeholder is not exactly one blank hidden choice", audit.placeholderOptions);
  assert(audit.placeholderMenuLabels.length === 0,
    "Target picker menu exposes a Select Module placeholder item", audit.placeholderMenuLabels);
  assert(audit.rootMenuItemLabels.length === 0,
    "Target picker menu exposes root-level Module items instead of folder placement", audit.rootMenuItemLabels);
  assert(sameStringSet(audit.actualKeys, audit.expectedKeys),
    "Target picker exposes historical or omits current Module identities", audit);
  assert(audit.actualKeys.length === audit.expectedKeys.length,
    "Target picker contains duplicate current Module options", audit);
  assert(audit.menuVariant === CONTRACT.menuVariant,
    "Target picker did not use the compact Pipeline Module menu", audit);
  assert(audit.rows.some((row) => row.description),
    "Target Modules provide no description with which to verify title isolation", audit.rows);
  assert(audit.rows.every((row) => row.optionFound && row.buttonFound),
    "A current Target Module is absent from the native select or visible menu", audit.rows);
  assert(audit.rows.every((row) => row.catalogFound && row.catalogPath && row.catalogPath !== "/"),
    "A Target Module has no non-root repository placement", audit.rows);
  assert(audit.rows.every((row) => row.optionPath === row.catalogPath),
    "Target picker optgroups do not match Module catalog folders", audit.rows);
  const requiredFolders = sortedUnique(audit.rows.flatMap((row) => (
    row.catalogPath.split("/").filter(Boolean)
  )));
  assert(requiredFolders.every((folder) => audit.folderSummaries.includes(folder)),
    "Target picker omitted one or more repository folder levels", {
      requiredFolders,
      renderedFolders: audit.folderSummaries,
    });
  assert(audit.rootModuleItemCount === 0,
    "Target Module choices were rendered at menu root instead of inside their folders", audit.rows);
  assert(audit.rows.every((row) => row.title === row.expectedTitle && row.optionTitle === row.expectedTitle),
    "Target Module title contains version metadata or description", audit.rows);
  assert(audit.rows.every((row) => row.subtitle === row.expectedSubtitle
      && row.optionSubtitle === row.expectedSubtitle),
  "Target Module subtitle does not contain the exact moduleId and current version", audit.rows);
  assert(audit.rows.every((row) => !row.description
      || (!row.title.includes(row.description) && !row.subtitle.includes(row.description))),
  "Target Module description leaked into the picker title or subtitle", audit.rows);
  assert(audit.rows.every((row) => row.buttonRect.height <= CONTRACT.maxMenuItemHeight),
    `Target Module menu item exceeds ${CONTRACT.maxMenuItemHeight}px`, audit.rows);
  assert(audit.rows.every((row) => row.titleStyle.fontSize <= CONTRACT.titleMaxFontSize
      && row.titleStyle.whiteSpace === "nowrap"),
  "Target Module title is not compact and single-line", audit.rows);
  assert(audit.rows.every((row) => row.subtitleStyle.fontSize <= CONTRACT.subtitleMaxFontSize
      && row.subtitleStyle.whiteSpace === "nowrap"),
  "Target Module subtitle is not compact and single-line", audit.rows);
}

async function pipelineStageAudit(page, pipelineId) {
  return page.evaluate(async ({ contract, expectedPipelineId }) => {
    const response = await fetch(`/api/pipelines/${encodeURIComponent(expectedPipelineId)}`);
    if (!response.ok) throw new Error(`Unable to load exact Pipeline definition (${response.status})`);
    const payload = await response.json();
    const exact = payload.definition || {};
    const draft = window.__tradeState?.pipelineDraft || {};
    const moduleDefinitions = Object.values(window.__tradeState?.pipelineModules || {});
    const referencesFor = (source, stage, draftSource = false) => {
      if (stage === "signal") {
        const graph = draftSource ? source?.alphaGraph : source?.signalGraph;
        return Array.isArray(graph?.nodes) ? graph.nodes.map(String) : [];
      }
      return Array.isArray(source?.stages?.[stage]) ? source.stages[stage].map(String) : [];
    };
    const exactReferences = Object.fromEntries(contract.pipelineStages
      .map((stage) => [stage, referencesFor(exact, stage)]));
    const draftReferences = Object.fromEntries(contract.pipelineStages
      .map((stage) => [stage, referencesFor(draft, stage, true)]));
    const exactInstanceIds = Object.keys(exact.instances || {});
    const draftInstanceIds = Object.keys(draft.instances || {});
    const exactDefinitionFor = (instance) => moduleDefinitions.find((definition) => (
      definition.kind === instance?.kind
      && definition.moduleId === instance?.moduleId
      && String(definition.version) === String(instance?.version)
    )) || null;
    const identity = (instanceId, instance) => ({
      instanceId: String(instanceId || ""),
      kind: String(instance?.kind || ""),
      moduleId: String(instance?.moduleId || ""),
      version: String(instance?.version ?? ""),
    });
    const validReferencesFor = (source, references, stage) => [...new Set(references)]
      .filter((instanceId) => {
        const instance = source?.instances?.[instanceId];
        return instance
          && instance.kind === contract.stageKinds[stage]
          && exactDefinitionFor(instance);
      });
    const validExactReferences = Object.fromEntries(contract.pipelineStages.map((stage) => [
      stage,
      validReferencesFor(exact, exactReferences[stage], stage),
    ]));
    const validDraftReferences = Object.fromEntries(contract.pipelineStages.map((stage) => [
      stage,
      validReferencesFor(draft, draftReferences[stage], stage),
    ]));
    const expectedInstances = exactInstanceIds.map((instanceId) => {
      const instance = exact.instances[instanceId];
      const draftInstance = draft.instances?.[instanceId];
      const definition = exactDefinitionFor(instance);
      return {
        instanceId,
        exactIdentity: identity(instanceId, instance),
        draftIdentity: identity(instanceId, draftInstance),
        draftFound: Boolean(draftInstance),
        exactDefinitionFound: Boolean(definition),
        expectedName: definition?.name || instance?.moduleId || instanceId,
        visibleIdentityText: `${instance?.moduleId || "unknown"} · v${instance?.version ?? "-"}`,
        exactIdentityText: `${instanceId} · ${instance?.moduleId || "unknown"} · v${instance?.version ?? "-"}`,
        expectedStages: contract.pipelineStages
          .filter((stage) => exactReferences[stage].includes(instanceId)),
      };
    });
    const rectRecord = (element) => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return Object.fromEntries(["x", "y", "width", "height", "right", "bottom"]
        .map((key) => [key, rect[key]]));
    };
    const matchingNodes = (root, selector) => root
      ? [...(root.matches(selector) ? [root] : []), ...root.querySelectorAll(selector)]
      : [];
    const cards = Object.fromEntries(contract.pipelineStages.map((stage) => {
      const card = document.querySelector(`#pipelineStageGrid .flow-node[data-stage="${stage}"]`);
      const tagAreas = matchingNodes(card, contract.stageTagListSelector);
      const tagArea = tagAreas[0] || null;
      const tags = [...(card?.querySelectorAll(contract.stageModuleTagSelector) || [])]
        .map((tag) => ({
          stage,
          configureStage: String(tag.dataset.configureStageModule || ""),
          instanceId: String(tag.dataset.instance || ""),
          status: String(tag.dataset.stageModuleStatus || ""),
          hasUnloadAction: tag.hasAttribute("data-unload-stage"),
          name: tag.textContent.trim(),
          title: tag.getAttribute("title")?.trim() || "",
          ariaLabel: tag.getAttribute("aria-label")?.trim() || "",
          rect: rectRecord(tag),
        }));
      const singleModuleCards = matchingNodes(card, contract.singleModuleSelector);
      const singleModule = singleModuleCards[0] || null;
      const singleMetadata = singleModule?.querySelector(contract.singleModuleMetadataSelector) || null;
      const singleConfigure = singleModule?.querySelector(contract.singleModuleConfigureSelector) || null;
      const singleSlots = matchingNodes(card, contract.singleSlotSelector);
      const containers = matchingNodes(card, contract.stageContainerSelector);
      const select = card?.querySelector(`[data-load-stage="${stage}"]`) || null;
      const loadButton = card?.querySelector(`[data-load-stage-button="${stage}"]`) || null;
      const cardRect = rectRecord(card);
      const visibleChildren = [...(card?.children || [])].filter((child) => {
        const style = getComputedStyle(child);
        const rect = child.getBoundingClientRect();
        return style.display !== "none" && rect.width > 0 && rect.height > 0;
      });
      const contentBottom = visibleChildren.length
        ? Math.max(...visibleChildren.map((child) => child.getBoundingClientRect().bottom))
        : cardRect?.bottom || 0;
      const tagAreaStyle = tagArea ? getComputedStyle(tagArea) : null;
      return [stage, {
        mode: String(card?.dataset.stageCardMode || ""),
        rect: cardRect,
        inlineHeight: card?.style.height || "",
        computedMinHeight: card ? getComputedStyle(card).minHeight : "",
        trailingGap: cardRect ? cardRect.bottom - contentBottom : Number.NaN,
        title: card?.querySelector("h3")?.textContent.trim() || "",
        containerCount: containers.length,
        singleSlotCount: singleSlots.length,
        singleModuleCount: singleModuleCards.length,
        singleModule: singleModule ? {
          instanceId: String(singleModule.dataset.singleStageModule || ""),
          title: singleModule.querySelector("h3")?.textContent.trim() || "",
          identityText: singleMetadata?.textContent.trim() || "",
          identityTitle: singleMetadata?.getAttribute("title")?.trim() || "",
          identity: {
            instanceId: String(singleMetadata?.dataset.instanceId || ""),
            moduleId: String(singleMetadata?.dataset.moduleId || ""),
            version: String(singleMetadata?.dataset.version || ""),
          },
          configure: {
            count: singleModule.querySelectorAll(contract.singleModuleConfigureSelector).length,
            text: singleConfigure?.textContent.trim() || "",
            stage: String(singleConfigure?.dataset.configureStageModule || ""),
            instanceId: String(singleConfigure?.dataset.instance || ""),
          },
          inlineRemoveCount: singleModule.querySelectorAll("[data-unload-stage]").length,
        } : null,
        tagAreaCount: tagAreas.length,
        tagAreaRect: rectRecord(tagArea),
        tagAreaStyle: tagAreaStyle ? {
          borderTopWidth: tagAreaStyle.borderTopWidth,
          borderTopStyle: tagAreaStyle.borderTopStyle,
          borderTopColor: tagAreaStyle.borderTopColor,
        } : null,
        tagInstanceIds: tags.map((tag) => tag.instanceId),
        tags,
        declaredCount: Number(tagArea?.dataset.stageModuleCount ?? Number.NaN),
        pickerCount: select ? 1 : 0,
        loadAction: loadButton?.textContent.trim() || "",
        loadButtonCount: loadButton ? 1 : 0,
        summaryText: card?.querySelector(`[data-stage-summary="${stage}"]`)?.textContent.trim() || "",
      }];
    }));
    const domTags = contract.containerStages.flatMap((stage) => cards[stage].tags);
    const renderedSingleModules = contract.singleStages
      .map((stage) => cards[stage].singleModule)
      .filter(Boolean);
    const emptyPickers = contract.pipelineStages
      .filter((stage) => cards[stage].pickerCount === 1)
      .map((stage) => {
        const select = document.querySelector(`#pipelineStageGrid select[data-load-stage="${stage}"]`);
        const trigger = select?.nextElementSibling?.querySelector(".hierarchical-select-trigger");
        const menu = select?.__hierarchicalMenu;
        const placeholderOptions = [...(select?.options || [])]
          .filter((option) => option.dataset.hierarchyPlaceholder === "true");
        const menuLabels = [...(menu?.querySelectorAll(".hierarchical-item") || [])]
          .map((button) => button.querySelector(contract.titleSelector)?.textContent.trim() || "");
        return {
          stage,
          value: select?.value || "",
          title: trigger?.querySelector(contract.titleSelector)?.textContent.trim() || "",
          subtitle: trigger?.querySelector(contract.subtitleSelector)?.textContent.trim() || "",
          ariaLabel: trigger?.getAttribute("aria-label")?.trim() || "",
          placeholderOptions: placeholderOptions
            .map((option) => ({ value: option.value, label: option.textContent.trim() })),
          placeholderMenuLabels: menuLabels.filter((label) => /^select\s+module\b/i.test(label)),
        };
      });
    const exactSignalGraph = exact.signalGraph || {};
    const signalCounts = {
      modules: exactReferences.signal.length,
      inputs: Object.keys(exactSignalGraph.inputs || {}).length,
      outputs: Object.keys(exactSignalGraph.outputs || {}).length,
    };
    const plural = (count, singular) => `${count} ${singular}${count === 1 ? "" : "s"}`;
    const expectedSignalSummary = [
      plural(signalCounts.modules, "Module"),
      plural(signalCounts.inputs, "Graph input"),
      plural(signalCounts.outputs, "Graph output"),
    ].join(" · ");
    return {
      pipelineId: exact.pipelineId || expectedPipelineId,
      exactInstanceIds,
      draftInstanceIds,
      exactReferences,
      draftReferences,
      validExactReferences,
      validDraftReferences,
      expectedInstances,
      domTags,
      cards,
      emptyPickers,
      renderedInstanceIds: [
        ...domTags.map((tag) => tag.instanceId),
        ...renderedSingleModules.map((module) => module.instanceId),
      ],
      signal: {
        ...signalCounts,
        summaryText: cards.signal?.summaryText || "",
        expectedSummaryText: expectedSignalSummary,
        expectedRenderedGraphNodes: signalCounts.modules + signalCounts.inputs + signalCounts.outputs,
      },
    };
  }, { contract: CONTRACT, expectedPipelineId: pipelineId });
}

function assertPipelineStages(audit) {
  assert(audit.pipelineId,
    "Pipeline stage audit received no exact Pipeline identity", audit);
  assert(sameUniqueStringSet(audit.draftInstanceIds, audit.exactInstanceIds),
    "Pipeline Draft instances differ from the current exact Pipeline definition", audit);
  assert(audit.emptyPickers.every((picker) => (
    picker.value === ""
      && picker.title === ""
      && picker.subtitle === ""
      && Boolean(picker.ariaLabel)
      && picker.placeholderOptions.length === 1
      && picker.placeholderOptions[0].value === ""
      && picker.placeholderOptions[0].label === ""
      && picker.placeholderMenuLabels.length === 0
  )), "An empty Pipeline picker renders placeholder text or lacks an accessible name", audit.emptyPickers);

  CONTRACT.pipelineStages.forEach((stage) => {
    const card = audit.cards[stage];
    assert(sameUniqueStringSet(audit.draftReferences[stage], audit.exactReferences[stage]),
      `${stage} Draft references differ from the exact Pipeline definition`, audit);
    assert(sameUniqueStringSet(audit.validExactReferences[stage], audit.exactReferences[stage]),
      `${stage} exact definition contains an invalid or duplicate Stage reference`, audit);
    assert(sameUniqueStringSet(audit.validDraftReferences[stage], audit.draftReferences[stage]),
      `${stage} Draft contains an invalid or duplicate Stage reference`, audit);
    assert(Math.abs(card.rect.width - CONTRACT.stageCardWidth) <= CONTRACT.geometryTolerance,
      `${stage} card does not retain the compact 210px width`, card);
    assert(card.inlineHeight === ""
        && card.trailingGap <= CONTRACT.stageTrailingGapMax + CONTRACT.geometryTolerance,
    `${stage} card does not shrink-wrap its visible content`, card);
  });

  const expectedValidInstanceIds = CONTRACT.pipelineStages
    .flatMap((stage) => audit.validDraftReferences[stage]);
  assert(sameUniqueStringSet(audit.renderedInstanceIds, expectedValidInstanceIds),
    "Rendered single-Module cards and container tags do not equal exact Stage references", audit);
  assert(audit.expectedInstances.every((expected) => expected.draftFound
      && expected.exactDefinitionFound
      && expected.expectedStages.length === 1
      && JSON.stringify(expected.draftIdentity) === JSON.stringify(expected.exactIdentity)),
  "A Draft instance or its exact Module version cannot be reconciled with the exact Pipeline", audit.expectedInstances);

  CONTRACT.singleStages.forEach((stage) => {
    const card = audit.cards[stage];
    const references = audit.validDraftReferences[stage];
    if (references.length === 1) {
      const expected = audit.expectedInstances.find((candidate) => candidate.instanceId === references[0]);
      assert(card.mode === CONTRACT.stageCardModes.singleModule
          && card.singleModuleCount === 1
          && card.singleSlotCount === 0
          && card.containerCount === 0,
      `${stage} loaded single stage is not the Module card itself`, card);
      assert(card.tagAreaCount === 0 && card.tags.length === 0
          && card.pickerCount === 0 && card.loadButtonCount === 0,
      `${stage} loaded single Module still renders a container tag list or loader`, card);
      assert(card.singleModule?.instanceId === expected.instanceId
          && card.singleModule.title === expected.expectedName
          && card.singleModule.identity.instanceId === expected.instanceId
          && card.singleModule.identity.moduleId === expected.exactIdentity.moduleId
          && card.singleModule.identity.version === expected.exactIdentity.version
          && card.singleModule.identityText === expected.visibleIdentityText
          && card.singleModule.identityTitle === expected.exactIdentityText,
      `${stage} single Module card hides or misstates its exact identity`, { card, expected });
      assert(card.singleModule.configure.count === 1
          && card.singleModule.configure.text === "Configure"
          && card.singleModule.configure.stage === stage
          && card.singleModule.configure.instanceId === expected.instanceId
          && card.singleModule.inlineRemoveCount === 0,
      `${stage} single Module card does not expose one Configure action or still exposes inline Remove`, {
        card,
        expected,
      });
    } else {
      assert(references.length === 0,
        `${stage} single stage contains more than one valid reference`, references);
      assert(card.mode === CONTRACT.stageCardModes.singleSlot
          && card.singleSlotCount === 1
          && card.singleModuleCount === 0
          && card.containerCount === 0,
      `${stage} empty single stage is not one loader slot`, card);
      assert(card.tagAreaCount === 0 && card.tags.length === 0
          && card.pickerCount === 1 && card.loadAction === "Load",
      `${stage} empty single slot does not expose exactly its picker and Load action`, card);
    }
  });

  const lightNeutralBorder = (color) => {
    const channels = String(color || "").match(/[\d.]+/g)?.slice(0, 3).map(Number) || [];
    return channels.length === 3
      && Math.min(...channels) >= 180
      && Math.max(...channels) - Math.min(...channels) <= 45;
  };
  CONTRACT.containerStages.forEach((stage) => {
    const card = audit.cards[stage];
    assert(card.mode === CONTRACT.stageCardModes.container
        && card.containerCount === 1
        && card.singleModuleCount === 0
        && card.singleSlotCount === 0,
    `${stage} multi-Module stage is not a Stage container`, card);
    assert(card.tagAreaCount === 1
        && sameUniqueStringSet(card.tagInstanceIds, audit.validDraftReferences[stage])
        && card.declaredCount === card.tagInstanceIds.length,
    `${stage} container tag list does not equal its exact valid references`, card);
    assert(card.tagAreaStyle?.borderTopWidth === "1px"
        && card.tagAreaStyle?.borderTopStyle === "solid"
        && lightNeutralBorder(card.tagAreaStyle?.borderTopColor),
    `${stage} container tag list lacks its 1px light-gray boundary`, card);
  });

  assert(audit.domTags.every((tag) => {
    const expected = audit.expectedInstances.find((candidate) => candidate.instanceId === tag.instanceId);
    return expected
      && tag.status === "loaded"
      && tag.stage === expected.expectedStages[0]
      && tag.configureStage === tag.stage
      && !tag.hasUnloadAction
      && tag.name === expected.expectedName
      && tag.title.includes(expected.exactIdentityText)
      && tag.ariaLabel.includes(expected.exactIdentityText)
      && Math.abs(tag.rect.height - CONTRACT.stageTagHeight) <= CONTRACT.geometryTolerance;
  }), "A container Module tag hides, enlarges, or misstates its exact identity", {
    expected: audit.expectedInstances,
    actual: audit.domTags,
  });
  const visibleActions = CONTRACT.pipelineStages.map((stage) => audit.cards[stage].loadAction).filter(Boolean);
  assert(visibleActions.every((label) => label === "Load"),
    "A visible Pipeline loader uses an action other than Load", visibleActions);
  assert(new Set(CONTRACT.pipelineStages.map((stage) => Math.round(audit.cards[stage].rect.height))).size > 1,
    "Pipeline cards remain forced to one fixed height instead of shrink-wrapping content", audit.cards);
  assert(audit.signal.summaryText === audit.signal.expectedSummaryText,
    "Signal summary does not distinguish Modules from Graph input/output boundaries", audit.signal);
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
    const fieldHints = (containerSelector, fieldSelector, dataKey) => [
      ...document.querySelectorAll(`${containerSelector} ${fieldSelector}`),
    ].map((field) => ({
      name: String(field.dataset[dataKey] || ""),
      hint: field.closest(".structured-field")?.querySelector(".field-hint")?.textContent.trim() || "",
    }));
    return {
      stage: expectedStage,
      instanceId: expectedInstanceId,
      expected: {
        title: `Configure ${definition.name || "Untitled Module"}`,
        meta: `${instance.kind} · ${instance.moduleId} · v${instance.version} · ${expectedInstanceId}`,
        config: structuredClone(instance.config || {}),
        inputs: structuredClone(instance.inputs || {}),
        outputs: structuredClone(instance.outputs || {}),
        inputFieldCount: inputNames.length,
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
        inputSchemaHints: fieldHints(
          "#moduleLoadInputsFields",
          "[data-param-field]",
          "paramField",
        ),
        outputSchemaHints: fieldHints(
          "#moduleLoadOutputsFields",
          "[data-port-field]",
          "portField",
        ),
      },
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
  const conciseSchemaHint = ({ name, hint }) => Boolean(name)
    && /^[^{}]+ · (Required|Optional)$/.test(hint)
    && !/properties/i.test(hint);
  assert(dialog.actual.inputSchemaHints.length === dialog.expected.inputFieldCount
      && dialog.actual.inputSchemaHints.every(conciseSchemaHint),
  "Configure input hints are not concise type + Required/Optional labels", dialog.actual.inputSchemaHints);
  assert(dialog.actual.outputSchemaHints.length === dialog.actual.expectedOutputFieldCount
      && dialog.actual.outputSchemaHints.every(conciseSchemaHint),
  "Configure output hints are not concise type + Required/Optional labels", dialog.actual.outputSchemaHints);
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

async function removeThenCancelAudit(page, stage, instanceId) {
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

async function pipelineSnapshot(page) {
  return page.evaluate((contract) => {
    const rectRecord = (element) => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return Object.fromEntries(["x", "y", "width", "height", "right", "bottom"]
        .map((key) => [key, rect[key]]));
    };
    const styleRecord = (element) => {
      if (!element) return null;
      const style = getComputedStyle(element);
      return Object.fromEntries(contract.computedStyleProperties.map((property) => [property, style[property]]));
    };
    const elementRecord = (element) => ({
      rect: rectRecord(element),
      style: styleRecord(element),
    });
    const elements = Object.fromEntries(Object.entries(contract.geometrySelectors)
      .map(([name, selector]) => [name, elementRecord(document.querySelector(selector))]));
    const nodes = [...document.querySelectorAll("#pipelineStageGrid .flow-node")]
      .sort((left, right) => String(left.dataset.stage).localeCompare(String(right.dataset.stage)))
      .map((node) => ({
        stage: node.dataset.stage,
        mode: node.dataset.stageCardMode || "",
        className: node.className,
        node: elementRecord(node),
        head: elementRecord(node.querySelector(".component-head")),
        title: elementRecord(node.querySelector("h3")),
        singleModule: elementRecord(node.matches(contract.singleModuleSelector)
          ? node : node.querySelector(contract.singleModuleSelector)),
        singleIdentity: elementRecord(node.querySelector(contract.singleModuleMetadataSelector)),
        singleSlot: elementRecord(node.matches(contract.singleSlotSelector)
          ? node : node.querySelector(contract.singleSlotSelector)),
        container: elementRecord(node.matches(contract.stageContainerSelector)
          ? node : node.querySelector(contract.stageContainerSelector)),
        modules: elementRecord(node.querySelector(contract.stageTagListSelector)),
        tags: [...node.querySelectorAll(contract.stageModuleTagSelector)].map(elementRecord),
        loadRow: elementRecord(node.querySelector(".load-row")),
        helper: elementRecord(node.querySelector("[data-stage-summary]")),
      }));
    return {
      route: location.pathname,
      bodyClasses: [...document.body.classList].sort(),
      scroll: { x: scrollX, y: scrollY },
      elements,
      nodes,
    };
  }, CONTRACT);
}

async function roundTripSignal(page, pipelineId, expectedSignal) {
  const before = await pipelineSnapshot(page);
  await page.click('#pipelineStageGrid [data-open-alpha-details="signal"]');
  await page.waitForFunction((expectedId) => (
    location.pathname === "/signal-blueprint"
      && new URLSearchParams(location.search).get("pipelineId") === expectedId
      && document.querySelector("#alphaGraphBuilder")?.__liteGraphGraph
  ), { timeout: 60000 }, pipelineId);
  await settleLayout(page);
  const signal = await page.evaluate(() => {
    const root = document.querySelector("#alphaGraphBuilder");
    const sidebar = root?.querySelector(".alpha-litegraph-inspector");
    return {
      route: location.pathname,
      nodeCount: root?.__liteGraphGraph?._nodes?.length || 0,
      sidebarWidth: sidebar?.getBoundingClientRect().width || 0,
    };
  });
  assert(signal.nodeCount === expectedSignal.expectedRenderedGraphNodes,
    "Signal Graph node count does not equal Module plus Graph boundary counts", {
      actualNodeCount: signal.nodeCount,
      expectedSignal,
    });
  await page.screenshot({ path: `${SCREENSHOT_DIR}/trade-pipeline-picker-signal.png` });
  await page.click("#alphaGraphBuilder [data-graph-back]");
  await page.waitForFunction((expectedId) => (
    location.pathname === "/pipeline/builder"
      && document.querySelector("#pipelineId")?.value === expectedId
      && document.querySelectorAll("#pipelineStageGrid .flow-node").length === 4
  ), { timeout: 60000 }, pipelineId);
  await settleLayout(page);
  const after = await pipelineSnapshot(page);
  const differences = snapshotDifferences(before, after);
  assert(differences.length === 0,
    "Pipeline geometry or computed styles changed after visiting Signal", differences);
  return { before, signal, after, differences };
}

async function main() {
  const puppeteer = loadPuppeteer();
  const session = sessionFromEnvironment();
  let browser;
  const blockedPipelineMutations = [];
  const pageErrors = [];
  const failedResponses = [];
  try {
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
    browser = await puppeteer.launch({
      headless: process.env.TRADE_HEADFUL !== "1",
      executablePath: CHROME,
      args: ["--no-sandbox", "--disable-gpu", "--no-proxy-server", "--proxy-bypass-list=*"],
    });
    const page = await browser.newPage();
    await page.setViewport(VIEWPORT);
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

    const pipelineId = await discoverPipelineId(page);
    await openPipelineBuilder(page, pipelineId);
    const stages = await pipelineStageAudit(page, pipelineId);
    assertPipelineStages(stages);
    const signalInstanceId = stages.validDraftReferences.signal[0] || "";
    const targetInstanceId = stages.validDraftReferences.target[0] || "";
    assert(signalInstanceId && targetInstanceId,
      "Configure acceptance requires one loaded Signal tag and one loaded Target single Module", {
        signal: stages.validDraftReferences.signal,
        target: stages.validDraftReferences.target,
      });
    const signalConfigureOpen = await openConfigureDialogAudit(page, "signal", signalInstanceId);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/trade-pipeline-signal-configure.png` });
    const signalConfigure = await cancelConfigureDialog(page, signalConfigureOpen);
    const targetConfigureOpen = await openConfigureDialogAudit(page, "target", targetInstanceId);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/trade-pipeline-target-configure.png` });
    const targetConfigure = await cancelConfigureDialog(page, targetConfigureOpen);
    const targetRemove = await removeThenCancelAudit(page, "target", targetInstanceId);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/trade-pipeline-stage-card-modes.png` });
    await exposeEmptyTargetSlot(page);
    await openTargetMenu(page);
    const picker = await targetPickerAudit(page);
    assertTargetPicker(picker);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/trade-pipeline-module-picker.png` });
    await page.evaluate(() => {
      const select = document.querySelector('[data-load-stage="target"]');
      if (select?.__hierarchicalMenu) select.__hierarchicalMenu.hidden = true;
      select?.nextElementSibling?.querySelector(".hierarchical-select-trigger")
        ?.setAttribute("aria-expanded", "false");
    });
    await openPipelineBuilder(page, pipelineId);
    const restoredStages = await pipelineStageAudit(page, pipelineId);
    assertPipelineStages(restoredStages);
    const roundTrip = await roundTripSignal(page, pipelineId, restoredStages.signal);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/trade-pipeline-picker-return.png` });

    assert(blockedPipelineMutations.length === 0,
      "The no-save browser test attempted to mutate a Pipeline resource", blockedPipelineMutations);
    assert(pageErrors.length === 0, "Browser emitted page errors", pageErrors);
    assert(failedResponses.length === 0, "Browser received server errors", failedResponses);
    console.log(JSON.stringify({
      pipelineId,
      picker,
      stages,
      configure: {
        signal: signalConfigure,
        target: targetConfigure,
        targetRemove,
      },
      roundTrip: {
        signal: roundTrip.signal,
        geometryDifferenceCount: roundTrip.differences.length,
      },
      pageErrors,
      failedResponses,
      persistence: {
        saved: false,
        blockedPipelineMutations,
      },
      screenshots: [
        `${SCREENSHOT_DIR}/trade-pipeline-stage-card-modes.png`,
        `${SCREENSHOT_DIR}/trade-pipeline-signal-configure.png`,
        `${SCREENSHOT_DIR}/trade-pipeline-target-configure.png`,
        `${SCREENSHOT_DIR}/trade-pipeline-module-picker.png`,
        `${SCREENSHOT_DIR}/trade-pipeline-picker-signal.png`,
        `${SCREENSHOT_DIR}/trade-pipeline-picker-return.png`,
      ],
    }, null, 2));
  } finally {
    try {
      if (browser) await browser.close();
    } finally {
      deleteOwnedSession(session);
    }
  }
}

module.exports = Object.freeze({
  CONTRACT,
  sameStringSet,
  sameUniqueStringSet,
  snapshotDifferences,
});

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error.message);
    if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
    process.exit(1);
  });
}
