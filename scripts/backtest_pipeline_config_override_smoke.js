#!/usr/bin/env node
"use strict";

const { execFileSync } = require("node:child_process");
const { isDeepStrictEqual } = require("node:util");

const BASE = process.env.TRADE_WEB_BASE || "http://10.130.130.66:30809";
const CONFIG = process.env.TRADE_CONFIG || "deploy/user/strategy-control-preview.json";
const CHROME = process.env.TRADE_CHROME_PATH || "/usr/bin/google-chrome";
const SCREENSHOT = process.env.TRADE_PIPELINE_CONFIG_SCREENSHOT
  || "/tmp/trade-backtest-pipeline-config-override.png";

const CONTRACT = Object.freeze({
  route: "/backtests",
  pipelineId: "tlm1d02",
  resourceScope: "resource",
  resourceInstanceId: "resource.config",
  moduleScope: "module",
  scalarModuleInstanceId: "tlm.market",
  scalarField: "recessCloseHour",
  cacheKey: "trade.backtest.build.v2",
  preparePath: "/api/backtest-submissions/prepare",
  submissionPath: "/api/backtests",
  configLayers: Object.freeze(["configOverride", "moduleConfigOverrides"]),
});

function loadPuppeteer() {
  const candidates = [
    process.env.TRADE_PUPPETEER_MODULE,
    "puppeteer-core",
    "puppeteer",
  ].filter(Boolean);
  const failures = [];
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (error) {
      failures.push(`${candidate}: ${error.message}`);
    }
  }
  throw new Error(
    `Unable to load Puppeteer. Set TRADE_PUPPETEER_MODULE. ${failures.join(" | ")}`,
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

async function waitForBacktestData(page) {
  await page.waitForFunction(() => (
    window.__tradeState?.datasets?.length
      && window.__tradeState?.samplers?.length
      && window.__tradeState?.environments?.length
      && window.__tradeState?.pipelineVersions?.length
      && window.__tradeState?.analyses?.length
  ), { timeout: 30000 });
}

async function chooseFirstAvailable(page, selector) {
  const choice = await page.$eval(selector, (select) => (
    [...select.options].map((option) => option.value).find(Boolean) || ""
  ));
  assert(choice, `No selectable value is available for ${selector}`);
  await page.select(selector, choice);
  return choice;
}

async function chooseExactPipeline(page) {
  const requestedVersion = process.env.TRADE_PIPELINE_VERSION || "";
  const candidates = await page.$$eval("#backtestPipelineSelect option", (options, input) => (
    options
      .map((option) => ({ value: option.value, label: option.textContent.trim() }))
      .filter(({ value }) => value.startsWith(`${input.pipelineId}::`))
  ), { pipelineId: CONTRACT.pipelineId });
  assert(candidates.length === 1, "Backtest Pipeline selector must expose one selected Version", candidates);
  const selected = candidates[0].value;
  const [, version = ""] = selected.split("::");
  assert(!requestedVersion || version === requestedVersion,
    `Expected ${CONTRACT.pipelineId} v${requestedVersion}, found v${version}`, candidates);
  await page.select("#backtestPipelineSelect", selected);
  return { key: selected, pipelineId: CONTRACT.pipelineId, version };
}

async function getExactPipeline(page, exact) {
  return page.evaluate(async ({ pipelineId, version }) => {
    const response = await fetch(
      `/api/pipelines/${encodeURIComponent(pipelineId)}/versions/${encodeURIComponent(version)}`,
      { method: "GET" },
    );
    const body = await response.json();
    return { status: response.status, body };
  }, exact);
}

async function openPipelineConfig(page) {
  await page.click("#configureBacktestPipeline");
  await page.waitForFunction(() => {
    const dialog = document.querySelector("#backtestSamplerConfigDialog");
    const loading = document.querySelector("#backtestConfigLoading");
    const workbench = document.querySelector("#backtestConfigWorkbench");
    return dialog?.open && loading?.hidden && !workbench?.hidden;
  }, { timeout: 30000 });
}

async function configDialogSnapshot(page) {
  return page.evaluate(() => {
    const instances = [...document.querySelectorAll("[data-backtest-config-instance]")]
      .map((node) => ({
        scope: node.dataset.backtestConfigScope || "",
        instanceId: node.dataset.backtestConfigInstance || "",
        name: node.querySelector("span")?.textContent?.trim() || "",
        state: node.querySelector("em")?.textContent?.trim() || "",
      }));
    const jsonText = document.querySelector("#backtestConfigJson")?.value || "{}";
    const rawConfig = document.querySelector("#backtestConfigFields [data-config-json-editor]")?.value || "";
    const parsedRawConfig = rawConfig ? JSON.parse(rawConfig) : null;
    const whitelistField = document.querySelector('[data-schema-field="whitelist"]');
    const blacklistField = document.querySelector('[data-schema-field="blacklist"]');
    const lines = (field) => field?.value
      ?.split("\n").map((value) => value.trim()).filter(Boolean) || [];
    return {
      heading: document.querySelector("#backtestConfigInstanceHeading")?.textContent?.trim() || "",
      count: Number(document.querySelector("#backtestConfigInstanceCount")?.textContent || 0),
      instances,
      draft: JSON.parse(jsonText),
      schemaFields: [...document.querySelectorAll("#backtestConfigFields [data-schema-field]")]
        .map((field) => ({
          name: field.dataset.schemaField || "",
          type: field.dataset.schemaType || "",
          value: "value" in field ? field.value : "",
        })),
      rawConfig,
      whitelist: whitelistField
        ? lines(whitelistField)
        : (parsedRawConfig?.observationInput?.whitelist || []),
      blacklist: blacklistField
        ? lines(blacklistField)
        : (parsedRawConfig?.observationInput?.blacklist || []),
    };
  });
}

async function selectConfigDescriptor(page, scope, instanceId) {
  await page.evaluate(({ descriptorScope, descriptorId }) => {
    const button = [...document.querySelectorAll("[data-backtest-config-instance]")]
      .find((candidate) => (
        candidate.dataset.backtestConfigScope === descriptorScope
          && candidate.dataset.backtestConfigInstance === descriptorId
      ));
    if (!button) throw new Error(`Missing ${descriptorScope} config descriptor '${descriptorId}'`);
    button.click();
  }, { descriptorScope: scope, descriptorId: instanceId });
}

async function setSchemaField(page, name, value) {
  await page.$eval(`[data-schema-field="${name}"]`, (input, next) => {
    input.value = Array.isArray(next) ? next.join("\n") : String(next);
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, value);
}

async function setPipelineWhitelist(page, value) {
  await page.evaluate((next) => {
    const field = document.querySelector('[data-schema-field="whitelist"]');
    if (field) {
      field.value = next.join("\n");
      field.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const editor = document.querySelector("#backtestConfigFields [data-config-json-editor]");
    if (!editor) throw new Error("Pipeline Resource Config editor is missing");
    const resourceConfig = JSON.parse(editor.value || "{}");
    resourceConfig.observationInput ||= {};
    resourceConfig.observationInput.whitelist = next;
    editor.value = JSON.stringify(resourceConfig, null, 2);
    editor.dispatchEvent(new Event("change", { bubbles: true }));
  }, value);
}

function rotated(values) {
  assert(Array.isArray(values) && values.length > 1,
    "Pipeline whitelist needs at least two entries for a semantics-preserving edit", values);
  return [...values.slice(1), values[0]];
}

function nextScalar(input) {
  const current = Number(input.value);
  const minimum = input.min === "" ? Number.NEGATIVE_INFINITY : Number(input.min);
  const maximum = input.max === "" ? Number.POSITIVE_INFINITY : Number(input.max);
  assert(Number.isFinite(current), `${CONTRACT.scalarField} must be numeric`, { current });
  if (current + 1 <= maximum) return current + 1;
  assert(current - 1 >= minimum, `${CONTRACT.scalarField} has no editable neighbour`, {
    current,
    minimum,
    maximum,
  });
  return current - 1;
}

async function auditReopenedConfig(page, expected) {
  await openPipelineConfig(page);
  const resource = await configDialogSnapshot(page);
  assert(isDeepStrictEqual(resource.draft, expected.draft),
    "Reopened Pipeline configuration lost an override layer", resource);
  assert(isDeepStrictEqual(resource.whitelist, expected.whitelist),
    "Reopened Pipeline whitelist differs from the applied value", resource);
  assert(isDeepStrictEqual(resource.blacklist, expected.blacklist),
    "Reopened Pipeline blacklist differs from the Version default", resource);
  await selectConfigDescriptor(
    page,
    CONTRACT.moduleScope,
    CONTRACT.scalarModuleInstanceId,
  );
  await page.waitForSelector(`[data-schema-field="${CONTRACT.scalarField}"]`);
  const scalar = await page.$eval(
    `[data-schema-field="${CONTRACT.scalarField}"]`,
    (input) => Number(input.value),
  );
  assert(scalar === expected.scalar,
    "Reopened Pipeline Module scalar differs from the applied value", { scalar, expected });
  return { resource, scalar };
}

async function main() {
  const puppeteer = loadPuppeteer();
  const session = sessionFromEnvironment();
  let browser;
  const pageErrors = [];
  const failedResponses = [];
  const prepareRequests = [];
  const prepareResponses = [];
  const resourceWrites = [];
  const submissionAttempts = [];
  try {
    browser = await puppeteer.launch({
      headless: true,
      executablePath: CHROME,
      args: ["--no-sandbox", "--disable-gpu", "--no-proxy-server"],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1720, height: 1080 });
    await page.setCookie(
      { name: "trade_session", value: session.token, url: BASE, httpOnly: true, sameSite: "Strict" },
      { name: "trade_csrf", value: session.csrf, url: BASE, sameSite: "Strict" },
    );
    await page.setRequestInterception(true);
    page.on("request", (request) => {
      const method = request.method();
      const url = new URL(request.url());
      const path = url.pathname;
      if (["GET", "HEAD", "OPTIONS"].includes(method)) {
        request.continue();
        return;
      }
      if (method === "POST" && path === CONTRACT.preparePath) {
        prepareRequests.push(JSON.parse(request.postData() || "{}"));
        request.continue();
        return;
      }
      const attempted = { method, path };
      resourceWrites.push(attempted);
      if (method === "POST" && path === CONTRACT.submissionPath) {
        submissionAttempts.push(attempted);
      }
      request.abort("blockedbyclient");
    });
    page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.pathname === CONTRACT.preparePath) {
        prepareResponses.push({ status: response.status(), path: url.pathname });
      }
      if (response.status() >= 500) {
        failedResponses.push({ status: response.status(), path: url.pathname });
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto(`${BASE}${CONTRACT.route}`, { waitUntil: "networkidle2", timeout: 30000 });
    await waitForBacktestData(page);
    await page.evaluate((key) => sessionStorage.removeItem(key), CONTRACT.cacheKey);

    const exact = await chooseExactPipeline(page);
    const selections = {
      pipeline: exact.key,
      dataset: await chooseFirstAvailable(page, "#backtestDataset"),
      sampler: await chooseFirstAvailable(page, "#backtestSampler"),
      environment: await chooseFirstAvailable(page, "#backtestEnvironmentSelect"),
      analysis: await chooseFirstAvailable(page, "#backtestAnalysisSelect"),
    };
    const before = await getExactPipeline(page, exact);
    assert(before.status === 200, "Exact Pipeline public GET failed", before);
    assert(before.body.pipelineId === exact.pipelineId
      && String(before.body.definition?.version) === exact.version,
    "Exact Pipeline GET does not match the selected identity", before.body);

    await openPipelineConfig(page);
    const initial = await configDialogSnapshot(page);
    const moduleIds = Object.keys(before.body.definition?.instances || {});
    assert(initial.heading === "Resource + Modules", "Pipeline config heading is incorrect", initial);
    assert(initial.count === moduleIds.length + 1, "Pipeline config descriptor count is incorrect", initial);
    assert(initial.instances[0]?.scope === CONTRACT.resourceScope
      && initial.instances[0]?.instanceId === CONTRACT.resourceInstanceId,
    "Pipeline Resource Config is not the first descriptor", initial);
    assert(isDeepStrictEqual(
      initial.instances.slice(1).map(({ instanceId }) => instanceId).sort(),
      [...moduleIds].sort(),
    ), "Pipeline config does not expose the exact Module instances", initial);
    assert(initial.instances.slice(1).every(({ scope }) => scope === CONTRACT.moduleScope),
      "An inner Pipeline descriptor has the wrong ownership scope", initial);
    assert(initial.whitelist.length > 1, "Pipeline whitelist is not visible", initial);
    assert(Array.isArray(initial.blacklist), "Pipeline blacklist is not visible", initial);

    const desiredWhitelist = rotated(initial.whitelist);
    await setPipelineWhitelist(page, desiredWhitelist);
    await selectConfigDescriptor(
      page,
      CONTRACT.moduleScope,
      CONTRACT.scalarModuleInstanceId,
    );
    await page.waitForSelector(`[data-schema-field="${CONTRACT.scalarField}"]`);
    const desiredScalar = await page.$eval(
      `[data-schema-field="${CONTRACT.scalarField}"]`,
      (input) => {
        const current = Number(input.value);
        const minimum = input.min === "" ? Number.NEGATIVE_INFINITY : Number(input.min);
        const maximum = input.max === "" ? Number.POSITIVE_INFINITY : Number(input.max);
        if (!Number.isFinite(current)) return null;
        return current + 1 <= maximum ? current + 1 : (current - 1 >= minimum ? current - 1 : null);
      },
    );
    assert(desiredScalar !== null, `${CONTRACT.scalarField} has no valid scalar edit`);
    await setSchemaField(page, CONTRACT.scalarField, desiredScalar);

    const draft = await page.$eval("#backtestConfigJson", (input) => JSON.parse(input.value));
    const expectedDraft = {
      configOverride: { observationInput: { whitelist: desiredWhitelist } },
      moduleConfigOverrides: {
        [CONTRACT.scalarModuleInstanceId]: { [CONTRACT.scalarField]: desiredScalar },
      },
    };
    assert(isDeepStrictEqual(draft, expectedDraft),
      "Pipeline JSON did not keep resource and Module overrides separate", { draft, expectedDraft });
    await page.click("#applyBacktestSamplerConfigBtn");
    await page.waitForFunction(() => !document.querySelector("#backtestSamplerConfigDialog")?.open);

    const prospectiveRequest = await page.evaluate(() => buildBacktestCompositionRequest());
    assert(prospectiveRequest, "Backtest request is incomplete after selecting exact resources");
    assert(isDeepStrictEqual(prospectiveRequest.pipeline.configOverride, expectedDraft.configOverride),
      "Prospective request lost Pipeline-owned configOverride", prospectiveRequest.pipeline);
    assert(isDeepStrictEqual(
      prospectiveRequest.pipeline.moduleConfigOverrides,
      expectedDraft.moduleConfigOverrides,
    ), "Prospective request lost Pipeline Module overrides", prospectiveRequest.pipeline);

    const reopenedBeforeBuild = await auditReopenedConfig(page, {
      draft: expectedDraft,
      whitelist: desiredWhitelist,
      blacklist: initial.blacklist,
      scalar: desiredScalar,
    });
    await page.click("#cancelBacktestSamplerConfigBtn");

    await page.click("#runBacktestBtn");
    await page.waitForFunction(() => (
      ["valid", "invalid"].includes(window.__tradeBacktestEntryState?.compositionValidation)
    ), { timeout: 30000 });
    const build = await page.evaluate(() => ({
      validation: window.__tradeBacktestEntryState.compositionValidation,
      message: window.__tradeBacktestEntryState.compositionMessage,
      button: document.querySelector("#runBacktestBtn")?.textContent?.trim() || "",
      request: buildBacktestCompositionRequest(),
      cache: JSON.parse(sessionStorage.getItem("trade.backtest.build.v2") || "null"),
    }));
    assert(build.validation === "valid" && build.button === "Run Backtest",
      "Public Build rejected the two-layer Pipeline override", build);
    assert(prepareRequests.length === 1 && prepareResponses[0]?.status === 200,
      "Build did not use exactly one successful prepare request", { prepareRequests, prepareResponses });
    assert(isDeepStrictEqual(prepareRequests[0], prospectiveRequest),
      "Build payload differs from the visible prospective request", prepareRequests[0]);
    assert(isDeepStrictEqual(build.request, prospectiveRequest),
      "Build changed the two-layer request", build.request);
    assert(isDeepStrictEqual(build.cache?.request, prospectiveRequest),
      "Build cache omitted a Pipeline override layer", build.cache?.request);

    await page.reload({ waitUntil: "networkidle2", timeout: 30000 });
    await waitForBacktestData(page);
    await page.waitForFunction(() => (
      window.__tradeBacktestEntryState?.compositionValidation === "valid"
        && document.querySelector("#runBacktestBtn")?.textContent?.trim() === "Run Backtest"
    ), { timeout: 30000 });
    const cacheRoundTrip = await page.evaluate(() => ({
      request: buildBacktestCompositionRequest(),
      pipelineConfigOverride: structuredClone(window.__tradeBacktestEntryState.pipelineConfigOverride),
      pipelineModuleConfigOverrides: structuredClone(
        window.__tradeBacktestEntryState.pipelineModuleConfigOverrides,
      ),
      cache: JSON.parse(sessionStorage.getItem("trade.backtest.build.v2") || "null"),
      validation: window.__tradeBacktestEntryState.compositionValidation,
      message: window.__tradeBacktestEntryState.compositionMessage,
    }));
    assert(isDeepStrictEqual(cacheRoundTrip.request, prospectiveRequest),
      "Reloaded Backtest request differs from the built request", cacheRoundTrip);
    assert(isDeepStrictEqual(cacheRoundTrip.pipelineConfigOverride, expectedDraft.configOverride),
      "Cache restore lost Pipeline configOverride", cacheRoundTrip);
    assert(isDeepStrictEqual(
      cacheRoundTrip.pipelineModuleConfigOverrides,
      expectedDraft.moduleConfigOverrides,
    ), "Cache restore lost Pipeline moduleConfigOverrides", cacheRoundTrip);
    assert(isDeepStrictEqual(cacheRoundTrip.cache?.request, prospectiveRequest),
      "Reloaded cache request differs from the built request", cacheRoundTrip.cache);

    const reopenedAfterReload = await auditReopenedConfig(page, {
      draft: expectedDraft,
      whitelist: desiredWhitelist,
      blacklist: initial.blacklist,
      scalar: desiredScalar,
    });
    await page.screenshot({ path: SCREENSHOT, fullPage: true });
    await page.click("#cancelBacktestSamplerConfigBtn");

    const after = await getExactPipeline(page, exact);
    assert(after.status === 200, "Exact Pipeline public GET failed after Build", after);
    assert(before.body.definition?.contentDigest === after.body.definition?.contentDigest
      && isDeepStrictEqual(before.body.definition, after.body.definition),
    "Backtest configuration mutated the immutable Pipeline resource", {
      before: before.body.definition?.contentDigest,
      after: after.body.definition?.contentDigest,
    });
    assert(resourceWrites.length === 0, "Browser attempted a persistent resource write", resourceWrites);
    assert(submissionAttempts.length === 0, "Smoke attempted to Run a Backtest", submissionAttempts);
    assert(pageErrors.length === 0, "Browser emitted page errors", pageErrors);
    assert(failedResponses.length === 0, "Server returned 5xx responses", failedResponses);

    console.log(JSON.stringify({
      exactPipeline: {
        pipelineId: exact.pipelineId,
        version: exact.version,
        contentDigest: before.body.definition?.contentDigest || "",
      },
      selections,
      descriptors: initial.instances,
      applied: expectedDraft,
      prospectiveRequest,
      reopenedBeforeBuild,
      build: {
        validation: build.validation,
        message: build.message,
        prepareStatus: prepareResponses[0]?.status,
      },
      cacheRoundTrip: {
        validation: cacheRoundTrip.validation,
        message: cacheRoundTrip.message,
        request: cacheRoundTrip.request,
      },
      reopenedAfterReload,
      network: {
        prepareRequests: prepareRequests.length,
        resourceWrites: resourceWrites.length,
        submissionAttempts: submissionAttempts.length,
        pageErrors: pageErrors.length,
        failedResponses: failedResponses.length,
      },
      screenshot: SCREENSHOT,
    }, null, 2));
  } finally {
    try {
      if (browser) await browser.close();
    } finally {
      deleteOwnedSession(session);
    }
  }
}

module.exports = {
  CONTRACT,
  nextScalar,
  rotated,
};

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error);
    if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
    process.exit(1);
  });
}
