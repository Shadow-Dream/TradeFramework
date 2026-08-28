#!/usr/bin/env node
'use strict';

const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const BASE = process.env.TRADE_WEB_BASE || 'http://10.130.130.66:30809';
const CONFIG = process.env.TRADE_CONFIG || 'deploy/user/strategy-control-preview.json';
const CHROME = process.env.TRADE_CHROME_PATH || '/usr/bin/google-chrome';
const SCREENSHOT_DIR = process.env.TRADE_GRAPH_SCREENSHOT_DIR || '/tmp';
const VIEWPORT = { width: 1600, height: 1000 };

function loadPuppeteer() {
  const requested = process.env.TRADE_PUPPETEER_MODULE;
  const candidates = [requested, 'puppeteer-core', 'puppeteer'].filter(Boolean);
  const failures = [];
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (error) {
      failures.push(`${candidate}: ${error.message}`);
    }
  }
  throw new Error(
    `Unable to load Puppeteer. Set TRADE_PUPPETEER_MODULE to a module or absolute module path. ${failures.join(' | ')}`,
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
    return { token: parsed.token, csrf: parsed.csrf || '', owned: false };
  }
  if (process.env.TRADE_SESSION_TOKEN) {
    return {
      token: process.env.TRADE_SESSION_TOKEN,
      csrf: process.env.TRADE_CSRF_TOKEN || '',
      owned: false,
    };
  }
  const code = [
    'import json, secrets, time',
    'from engine.service import control_api as control; from engine.control import auth as trade_auth',
    `config = control.load_config(${JSON.stringify(CONFIG)})`,
    'trade_auth.ensure_default_user(config)',
    'token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), int(time.time())',
    'with trade_auth.connect(config) as connection:',
    '    user = connection.execute("SELECT user_id FROM users WHERE status = ? ORDER BY created_at LIMIT 1", ("active",)).fetchone()',
    '    connection.execute("INSERT INTO sessions (token_hash, user_id, csrf_hash, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)", (trade_auth.opaque_token_hash(token), user["user_id"], trade_auth.opaque_token_hash(csrf), now, now + 3600, now))',
    '    connection.commit()',
    'print(json.dumps({"token": token, "csrf": csrf}))',
  ].join('\n');
  return {
    ...JSON.parse(execFileSync('python3', ['-c', code], { encoding: 'utf8' })),
    owned: true,
  };
}

function deleteOwnedSession(session) {
  if (!session?.owned || !session.token) return;
  const code = [
    'import sys',
    'from engine.service import control_api as control; from engine.control import auth as trade_auth',
    `config = control.load_config(${JSON.stringify(CONFIG)})`,
    'with trade_auth.connect(config) as connection:',
    '    connection.execute("DELETE FROM sessions WHERE token_hash = ?", (trade_auth.opaque_token_hash(sys.argv[1]),))',
    '    connection.commit()',
  ].join('\n');
  execFileSync('python3', ['-c', code, session.token], { encoding: 'utf8' });
}

async function waitForGraph(page, rootSelector) {
  await page.waitForFunction((selector) => {
    const root = document.querySelector(selector);
    return Boolean(root?.__liteGraphGraph && root?.__liteGraphCanvas && root?.__liteGraphNodeMeta);
  }, { timeout: 30000 }, rootSelector);
  await page.waitForFunction((selector) => {
    const root = document.querySelector(selector);
    const stage = root?.querySelector('.alpha-litegraph-stage');
    const inspector = root?.querySelector('.alpha-litegraph-inspector');
    return stage?.getBoundingClientRect().width > 0
      && inspector?.getBoundingClientRect().width > 0;
  }, { timeout: 10000 }, rootSelector);
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

async function graphState(page, rootSelector) {
  return page.$eval(rootSelector, (root) => {
    const graph = root.__liteGraphGraph;
    const meta = root.__liteGraphNodeMeta;
    const snapshot = structuredClone(root.__liteGraphLastSnapshot);
    const producersByWire = new Map();
    Object.entries(snapshot?.alphaGraph?.inputs || {}).forEach(([inputId, binding]) => {
      if (binding?.wire) producersByWire.set(binding.wire, `input:${inputId}`);
    });
    Object.entries(snapshot?.instances || {}).forEach(([instanceId, instance]) => {
      Object.entries(instance.outputs || {}).forEach(([port, wire]) => {
        if (wire) producersByWire.set(wire, `module:${instanceId}.${port}`);
      });
    });
    const expectedLinks = [];
    const requiredTargets = [];
    const missingRequiredSnapshotInputs = [];
    graph._nodes.forEach((node) => {
      const item = meta.get(node.id) || {};
      if (item.entity !== 'module') return;
      const instance = snapshot?.instances?.[item.id] || {};
      Object.entries(item.definition?.ports?.inputs || {}).forEach(([port, spec], slot) => {
        const target = `module:${item.id}.${port}`;
        const wire = instance.inputs?.[port] || '';
        if (wire) expectedLinks.push(`${producersByWire.get(wire) || `missing:${wire}`}>${target}`);
        if (spec?.required !== false) {
          requiredTargets.push(target);
          if (!wire) missingRequiredSnapshotInputs.push(target);
          if (wire && !producersByWire.has(wire)) missingRequiredSnapshotInputs.push(`${target} [producer]`);
          if (node.inputs?.[slot]?.link == null) missingRequiredSnapshotInputs.push(`${target} [rendered]`);
        }
      });
    });
    Object.entries(snapshot?.alphaGraph?.outputs || {}).forEach(([outputId, binding]) => {
      if (binding?.wire) {
        expectedLinks.push(`${producersByWire.get(binding.wire) || `missing:${binding.wire}`}>output:${outputId}`);
      }
    });
    const nodes = graph._nodes.map((node) => {
      const item = meta.get(node.id) || {};
      return `${item.entity || 'unknown'}:${item.id || node.id}`;
    }).sort();
    const semanticLinks = Object.values(graph.links || {}).filter(Boolean).map((link) => {
      const source = meta.get(link.origin_id) || {};
      const target = meta.get(link.target_id) || {};
      const sourceNode = graph.getNodeById(link.origin_id);
      const targetNode = graph.getNodeById(link.target_id);
      const sourcePort = source.entity === 'module'
        ? Object.keys(source.definition?.ports?.outputs || {})[link.origin_slot]
        : '';
      const targetPort = target.entity === 'module'
        ? Object.keys(target.definition?.ports?.inputs || {})[link.target_slot]
        : '';
      const sourceKey = source.entity === 'module'
        ? `module:${source.id}.${sourcePort}`
        : source.entity === 'input' ? `input:${source.id}` : `unknown:${sourceNode?.id}`;
      const targetKey = target.entity === 'module'
        ? `module:${target.id}.${targetPort}`
        : target.entity === 'output' ? `output:${target.id}` : `unknown:${targetNode?.id}`;
      return `${sourceKey}>${targetKey}`;
    }).sort();
    return {
      nodes,
      links: semanticLinks,
      expectedLinks: expectedLinks.sort(),
      requiredTargets: requiredTargets.sort(),
      missingRequiredSnapshotInputs: [...new Set(missingRequiredSnapshotInputs)].sort(),
      validation: structuredClone(root.__validationState || { errors: [], warnings: [] }),
      snapshot,
    };
  });
}

async function clearSelection(page, rootSelector) {
  await page.$eval(rootSelector, (root) => root.__liteGraphCanvas.deselectAllNodes());
  await page.waitForSelector(`${rootSelector} [data-graph-explorer-filter]`);
}

async function auditGraph(page, descriptor) {
  const { label, route, rootSelector } = descriptor;
  await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await waitForGraph(page, rootSelector);

  const baseAudit = await page.$eval(rootSelector, (root) => {
    const rect = (node) => {
      const value = node.getBoundingClientRect();
      return {
        left: value.left,
        top: value.top,
        right: value.right,
        bottom: value.bottom,
        width: value.width,
        height: value.height,
      };
    };
    const inspector = root.querySelector('.alpha-litegraph-inspector');
    const toolbar = root.querySelector('.alpha-litegraph-toolbar');
    const addInput = [...root.querySelectorAll('[data-graph-input]')];
    const addOutput = [...root.querySelectorAll('[data-graph-output]')];
    const ancestors = [];
    for (let node = root.parentElement; node && ancestors.length < 10; node = node.parentElement) {
      const style = getComputedStyle(node);
      ancestors.push({
        name: `${node.tagName.toLowerCase()}${node.id ? `#${node.id}` : ''}${node.classList.length ? `.${[...node.classList].slice(0, 3).join('.')}` : ''}`,
        rect: rect(node),
        display: style.display,
        overflow: `${style.overflowX}/${style.overflowY}`,
      });
      if (node === document.body) break;
    }
    return {
      path: location.pathname,
      routeBlueprint: document.body.classList.contains('route-blueprint'),
      root: rect(root),
      body: rect(root.querySelector('.alpha-litegraph-body')),
      stage: rect(root.querySelector('.alpha-litegraph-stage')),
      inspector: rect(inspector),
      toolbarRows: toolbar.querySelectorAll('.alpha-litegraph-toolbar-row').length,
      primaryRows: toolbar.querySelectorAll('.alpha-litegraph-toolbar-row-primary').length,
      secondaryRows: toolbar.querySelectorAll('.alpha-litegraph-toolbar-row-secondary').length,
      moduleSearch: Boolean(inspector.querySelector('[data-graph-module]')),
      explorerFilter: Boolean(inspector.querySelector('[data-graph-explorer-filter]')),
      explorerModes: [...inspector.querySelectorAll('[data-graph-explorer-mode]')]
        .map((button) => button.textContent.trim().split(' (')[0]),
      addInputCount: addInput.length,
      addOutputCount: addOutput.length,
      addInputInInspector: addInput.every((button) => inspector.contains(button)),
      addOutputInInspector: addOutput.every((button) => inspector.contains(button)),
      toolbarHasBoundaries: Boolean(toolbar.querySelector('[data-graph-input], [data-graph-output]')),
      nodeCount: root.__liteGraphGraph._nodes.length,
      ancestors,
      globalOverflow: {
        x: document.documentElement.scrollWidth - innerWidth,
        y: document.documentElement.scrollHeight - innerHeight,
      },
    };
  });
  assert(baseAudit.path === route.split('?')[0], `${label}: wrong route`, baseAudit);
  assert(baseAudit.routeBlueprint, `${label}: Blueprint route class is missing`, baseAudit);
  assert(baseAudit.toolbarRows === 2 && baseAudit.primaryRows === 1 && baseAudit.secondaryRows === 1,
    `${label}: toolbar is not the confirmed two-row design`, baseAudit);
  assert(baseAudit.moduleSearch && baseAudit.explorerFilter,
    `${label}: Node Explorer search or filter is missing`, baseAudit);
  assert(baseAudit.explorerModes.join('|') === 'All|Outputs|Issues',
    `${label}: Node Explorer modes are incomplete`, baseAudit);
  assert(baseAudit.addInputCount === 1 && baseAudit.addOutputCount === 1
      && baseAudit.addInputInInspector && baseAudit.addOutputInInspector
      && !baseAudit.toolbarHasBoundaries,
    `${label}: Add Input/Output is not owned exclusively by the right sidebar`, baseAudit);
  assert(Math.abs(baseAudit.inspector.width - 360) <= 1,
    `${label}: right sidebar is not 360px`, baseAudit);
  assert(baseAudit.root.left >= 0 && baseAudit.root.right <= VIEWPORT.width + 1
      && baseAudit.root.top >= 0 && baseAudit.root.bottom <= VIEWPORT.height + 1
      && baseAudit.globalOverflow.x <= 1 && baseAudit.globalOverflow.y <= 1,
    `${label}: Blueprint layout overflows the viewport`, baseAudit);
  const baseline = await graphState(page, rootSelector);
  assert(JSON.stringify(baseline.links) === JSON.stringify(baseline.expectedLinks),
    `${label}: rendered semantic connections do not match the initial snapshot`, baseline);
  assert(baseline.missingRequiredSnapshotInputs.length === 0,
    `${label}: a required Module input is absent from the snapshot or rendered graph`, baseline);
  if (descriptor.requireValidInitialGraph) {
    assert((baseline.validation.errors || []).length === 0,
      `${label}: initial graph has local validation errors`, baseline.validation);
  }
  if (descriptor.requiredConnectedTarget) {
    assert(baseline.requiredTargets.includes(descriptor.requiredConnectedTarget)
        && baseline.links.some((link) => link.endsWith(`>${descriptor.requiredConnectedTarget}`)),
      `${label}: required semantic input '${descriptor.requiredConnectedTarget}' is not connected`, baseline);
  }

  const moduleSearch = `${rootSelector} [data-graph-module]`;
  await page.focus(moduleSearch);
  await page.waitForFunction((selector) => (
    document.querySelector(selector)?.querySelectorAll('[data-graph-module-choice]').length > 0
  ), { timeout: 5000 }, rootSelector);
  const searchAudit = await page.$eval(rootSelector, (root) => ({
    optionCount: root.querySelectorAll('[data-graph-module-choice]').length,
    versionLabels: [...root.querySelectorAll('[data-graph-module-choice]')]
      .map((button) => button.textContent.trim())
      .filter((labelText) => /(?:^|\s)[vV]\d+(?:\s|$)/.test(labelText)),
  }));
  assert(searchAudit.optionCount > 0 && searchAudit.versionLabels.length === 0,
    `${label}: Add Module search exposes no current Modules or leaks version rows`, searchAudit);
  let multiSelection = false;
  let emptyGraphModuleAdd = false;
  let portActionsRendered = false;
  if (baseAudit.nodeCount === 0) {
    await page.click(`${rootSelector} [data-graph-module-choice]`);
    await page.click(`${rootSelector} [data-graph-add-module]`);
    emptyGraphModuleAdd = true;
  } else {
    await page.keyboard.press('Escape');
    await clearSelection(page, rootSelector);
    const explorerBefore = await page.$$eval(
      `${rootSelector} .alpha-litegraph-explorer-item`,
      (rows) => rows.map((row) => row.querySelector('.alpha-litegraph-explorer-item-title')?.textContent.trim() || ''),
    );
    assert(explorerBefore.length === baseAudit.nodeCount,
      `${label}: Node Explorer does not list every graph node`, { explorerBefore, baseAudit });
    const query = explorerBefore[0].slice(0, Math.max(1, Math.min(6, explorerBefore[0].length)));
    await page.$eval(`${rootSelector} [data-graph-explorer-filter]`, (field, value) => {
      field.value = value;
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }, query);
    const filteredCount = await page.$$eval(`${rootSelector} .alpha-litegraph-explorer-item`, (rows) => rows.length);
    assert(filteredCount > 0 && filteredCount <= explorerBefore.length,
      `${label}: Node Explorer filter returned an invalid result`, { query, filteredCount, explorerBefore });
    await page.keyboard.press('Escape');
    await page.waitForFunction((selector, count) => (
      document.querySelector(selector)?.querySelectorAll('.alpha-litegraph-explorer-item').length === count
    ), {}, rootSelector, explorerBefore.length);
    await page.click(`${rootSelector} .alpha-litegraph-explorer-item`);
    await page.waitForSelector(`${rootSelector} [data-graph-selected-detail]`);
    const hasConnectedModule = await page.$eval(rootSelector, (root) => {
      const graph = root.__liteGraphGraph;
      const meta = root.__liteGraphNodeMeta;
      const candidate = graph._nodes.find((node) => (
        meta.get(node.id)?.entity === 'module'
        && node.inputs?.some((input) => input?.link != null)
        && node.outputs?.length
      ));
      if (!candidate) return false;
      root.__liteGraphCanvas.deselectAllNodes();
      root.__liteGraphCanvas.selectNode(candidate);
      return true;
    });
    if (hasConnectedModule) {
      await page.waitForSelector(`${rootSelector} [data-graph-port-action="add-downstream"]`);
      const portActions = await page.$$eval(
        `${rootSelector} [data-graph-port-action]`,
        (buttons) => [...new Set(buttons.map((button) => button.textContent.trim()))],
      );
      assert(['Replace', 'Use Existing', 'Disconnect', 'Add Downstream']
        .every((action) => portActions.includes(action)),
      `${label}: selected Module does not render the confirmed port actions`, portActions);
      portActionsRendered = true;
    }
    if (baseAudit.nodeCount >= 2) {
      await page.$eval(rootSelector, (root) => {
        root.__liteGraphCanvas.deselectAllNodes();
        root.__liteGraphCanvas.selectNodes(root.__liteGraphGraph._nodes.slice(0, 2), false);
      });
      await page.waitForFunction((selector) => (
        /2 nodes selected/.test(document.querySelector(`${selector} [data-graph-selected-detail]`)?.textContent || '')
      ), {}, rootSelector);
      multiSelection = true;
    }
    await clearSelection(page, rootSelector);
    await page.click(`${rootSelector} [data-graph-input]`);
  }
  await page.waitForFunction((selector, count) => {
    const root = document.querySelector(selector);
    return root.__liteGraphGraph._nodes.length === count + 1
      && !root.querySelector('[data-graph-undo]').disabled;
  }, { timeout: 5000 }, rootSelector, baseline.nodes.length);
  await page.waitForSelector(`${rootSelector} [data-graph-selected-detail]`);
  const detailAudit = await page.$eval(rootSelector, (root) => ({
    selected: Object.keys(root.__liteGraphCanvas.selected_nodes || {}).length,
    hasDetail: Boolean(root.querySelector('[data-graph-selected-detail]')),
    hasInputs: [...root.querySelectorAll('.alpha-litegraph-inspector-section-title')]
      .some((node) => node.textContent.trim() === 'Inputs'),
    hasOutputs: [...root.querySelectorAll('.alpha-litegraph-inspector-section-title')]
      .some((node) => node.textContent.trim() === 'Outputs'),
  }));
  assert(detailAudit.selected === 1 && detailAudit.hasDetail
      && detailAudit.hasInputs && detailAudit.hasOutputs,
    `${label}: selecting or adding a node does not show its complete details`, detailAudit);
  const afterAdd = await graphState(page, rootSelector);
  assert(JSON.stringify(afterAdd.links) === JSON.stringify(baseline.links),
    `${label}: adding an isolated node changed existing links`, { baseline, afterAdd });

  await page.click(`${rootSelector} [data-graph-undo]`);
  await page.waitForFunction((selector, count) => (
    document.querySelector(selector).__liteGraphGraph._nodes.length === count
  ), {}, rootSelector, baseline.nodes.length);
  const afterUndo = await graphState(page, rootSelector);
  assert(JSON.stringify(afterUndo.nodes) === JSON.stringify(baseline.nodes)
      && JSON.stringify(afterUndo.links) === JSON.stringify(baseline.links),
    `${label}: Undo did not restore the graph topology`, { baseline, afterUndo });
  await page.waitForFunction((selector) => !document.querySelector(selector).querySelector('[data-graph-redo]').disabled,
    {}, rootSelector);

  await page.click(`${rootSelector} [data-graph-redo]`);
  try {
    await page.waitForFunction((selector, count) => (
      document.querySelector(selector).__liteGraphGraph._nodes.length === count + 1
    ), { timeout: 5000 }, rootSelector, baseline.nodes.length);
  } catch (error) {
    error.payload = await page.$eval(rootSelector, (root) => ({
      label: root.querySelector('[data-graph-context-name]')?.textContent?.trim() || '',
      nodes: root.__liteGraphGraph._nodes.map((node) => ({ id: node.id, title: node.title })),
      undoDisabled: root.querySelector('[data-graph-undo]').disabled,
      redoDisabled: root.querySelector('[data-graph-redo]').disabled,
      snapshot: root.__liteGraphLastSnapshot,
      status: root.querySelector('[data-graph-status]')?.textContent?.trim() || '',
    }));
    throw error;
  }
  const afterRedo = await graphState(page, rootSelector);
  assert(JSON.stringify(afterRedo.nodes) === JSON.stringify(afterAdd.nodes)
      && JSON.stringify(afterRedo.links) === JSON.stringify(baseline.links),
    `${label}: Redo did not restore the edit without changing links`, { baseline, afterAdd, afterRedo });

  await page.click(`${rootSelector} [data-graph-undo]`);
  await page.waitForFunction((selector, count) => (
    document.querySelector(selector).__liteGraphGraph._nodes.length === count
  ), {}, rootSelector, baseline.nodes.length);
  await page.$eval(rootSelector, (root) => root.__flushPendingEmit());
  const restored = await graphState(page, rootSelector);
  assert(JSON.stringify(restored.nodes) === JSON.stringify(baseline.nodes)
      && JSON.stringify(restored.links) === JSON.stringify(baseline.links)
      && JSON.stringify(canonical(restored.snapshot)) === JSON.stringify(canonical(baseline.snapshot)),
    `${label}: final Undo did not restore the original snapshot and connections`, { baseline, restored });

  await page.click(`${rootSelector} [data-graph-fullscreen]`);
  await page.waitForFunction((selector) => document.querySelector(selector).classList.contains('alpha-graph-fullscreen'),
    {}, rootSelector);
  await new Promise((resolve) => setTimeout(resolve, 100));
  const fullscreenAudit = await page.$eval(rootSelector, (root) => {
    const rect = (node) => {
      const value = node.getBoundingClientRect();
      return {
        left: value.left,
        top: value.top,
        right: value.right,
        bottom: value.bottom,
        width: value.width,
        height: value.height,
      };
    };
    return {
      root: rect(root),
      body: rect(root.querySelector('.alpha-litegraph-body')),
      stage: rect(root.querySelector('.alpha-litegraph-stage')),
      inspector: rect(root.querySelector('.alpha-litegraph-inspector')),
      globalOverflowX: document.documentElement.scrollWidth - innerWidth,
      globalOverflowY: document.documentElement.scrollHeight - innerHeight,
    };
  });
  assert(Math.abs(fullscreenAudit.root.left - 12) <= 1
      && Math.abs(fullscreenAudit.root.top - 12) <= 1
      && Math.abs(fullscreenAudit.root.right - (VIEWPORT.width - 12)) <= 1
      && Math.abs(fullscreenAudit.root.bottom - (VIEWPORT.height - 12)) <= 1
      && Math.abs(fullscreenAudit.inspector.width - 360) <= 1
      && fullscreenAudit.globalOverflowX <= 1 && fullscreenAudit.globalOverflowY <= 1,
    `${label}: Fullscreen is not a 12px inset surface or it overflows`, fullscreenAudit);

  await page.click(`${rootSelector} [data-graph-toggle-explorer]`);
  await page.waitForFunction((selector) => {
    const root = document.querySelector(selector);
    return root.classList.contains('alpha-explorer-collapsed')
      && getComputedStyle(root.querySelector('.alpha-litegraph-inspector')).display === 'none'
      && root.querySelector('[data-graph-toggle-explorer]').textContent.trim() === 'Show Sidebar';
  }, {}, rootSelector);
  await page.click(`${rootSelector} [data-graph-toggle-explorer]`);
  await page.waitForFunction((selector) => {
    const root = document.querySelector(selector);
    const inspector = root.querySelector('.alpha-litegraph-inspector');
    return !root.classList.contains('alpha-explorer-collapsed')
      && Math.abs(inspector.getBoundingClientRect().width - 360) <= 1
      && root.querySelector('[data-graph-toggle-explorer]').textContent.trim() === 'Hide Sidebar';
  }, {}, rootSelector);
  await page.click(`${rootSelector} [data-graph-fullscreen]`);
  await page.waitForFunction((selector) => !document.querySelector(selector).classList.contains('alpha-graph-fullscreen'),
    {}, rootSelector);
  await new Promise((resolve) => setTimeout(resolve, 100));
  const exitAudit = await page.$eval(rootSelector, (root) => {
    const rect = (node) => {
      const value = node.getBoundingClientRect();
      return {
        left: value.left,
        top: value.top,
        right: value.right,
        bottom: value.bottom,
        width: value.width,
        height: value.height,
      };
    };
    return {
      root: rect(root),
      body: rect(root.querySelector('.alpha-litegraph-body')),
      stage: rect(root.querySelector('.alpha-litegraph-stage')),
      inspector: rect(root.querySelector('.alpha-litegraph-inspector')),
      globalOverflowX: document.documentElement.scrollWidth - innerWidth,
      globalOverflowY: document.documentElement.scrollHeight - innerHeight,
    };
  });
  assert(Math.abs(exitAudit.root.left - baseAudit.root.left) <= 1
      && Math.abs(exitAudit.root.top - baseAudit.root.top) <= 1
      && Math.abs(exitAudit.root.right - baseAudit.root.right) <= 1
      && Math.abs(exitAudit.root.bottom - baseAudit.root.bottom) <= 1
      && Math.abs(exitAudit.stage.width - baseAudit.stage.width) <= 1
      && Math.abs(exitAudit.stage.height - baseAudit.stage.height) <= 1
      && Math.abs(exitAudit.inspector.width - 360) <= 1
      && exitAudit.globalOverflowX <= 1 && exitAudit.globalOverflowY <= 1,
    `${label}: exiting Fullscreen did not restore the normal layout`, { normal: baseAudit, exit: exitAudit });

  const screenshot = path.join(SCREENSHOT_DIR, `trade-module-graph-${label.toLowerCase()}.png`);
  await page.screenshot({ path: screenshot, fullPage: false });
  return {
    label,
    route,
    nodeCount: baseAudit.nodeCount,
    linkCount: baseline.links.length,
    moduleSearchOptions: searchAudit.optionCount,
    multiSelection,
    emptyGraphModuleAdd,
    portActionsRendered,
    screenshot,
    toolbarRows: baseAudit.toolbarRows,
    inspectorWidth: baseAudit.inspector.width,
    normal: {
      root: baseAudit.root,
      body: baseAudit.body,
      stage: baseAudit.stage,
      inspector: baseAudit.inspector,
      ancestors: baseAudit.ancestors,
    },
    fullscreen: fullscreenAudit,
    exit: exitAudit,
  };
}

async function discoverResources(page) {
  await page.goto(`${BASE}/pipeline`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => Object.keys(window.__tradeState?.pipelines || {}).length > 0,
    { timeout: 30000 });
  const pipelineId = process.env.TRADE_GRAPH_PIPELINE_ID || await page.evaluate(() => {
    const state = window.__tradeState;
    const currentIds = new Set((state.pipelineVersions || [])
      .filter((row) => row.current === true).map((row) => row.pipelineId));
    return Object.values(state.pipelines || {})
      .filter((row) => row.status === 'active' && currentIds.has(row.pipelineId))
      .sort((left, right) => String(left.pipelineId).localeCompare(String(right.pipelineId)))[0]?.pipelineId
      || [...currentIds].sort()[0]
      || Object.keys(state.pipelines || {}).sort()[0];
  });

  await page.goto(`${BASE}/environment`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => window.__tradeState?.environments?.length > 0, { timeout: 30000 });
  const environmentKey = await page.evaluate((override) => {
    const rows = window.__tradeState.environments || [];
    if (override) return override;
    const current = rows.find((row) => row.current === true);
    const latest = [...rows].sort((left, right) => Number(right.version || 0) - Number(left.version || 0))[0];
    const row = current || latest;
    return row ? `${row.environmentId}::${row.version}` : '';
  }, process.env.TRADE_GRAPH_ENVIRONMENT_KEY || '');

  await page.goto(`${BASE}/analysis`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => window.__tradeState?.analyses?.length > 0, { timeout: 30000 });
  const analysisKey = await page.evaluate((override) => {
    const rows = window.__tradeState.analyses || [];
    if (override) return override;
    const current = rows.find((row) => row.current === true);
    const latest = [...rows].sort((left, right) => Number(right.version || 0) - Number(left.version || 0))[0];
    const row = current || latest;
    return row ? `${row.analysisId}::${row.version}` : '';
  }, process.env.TRADE_GRAPH_ANALYSIS_KEY || '');

  assert(pipelineId && environmentKey && analysisKey,
    'Could not discover one current Pipeline, Environment, and Analysis',
    { pipelineId, environmentKey, analysisKey });
  return { pipelineId, environmentKey, analysisKey };
}

async function main() {
  const puppeteer = loadPuppeteer();
  const session = sessionFromEnvironment();
  let browser;
  const pageErrors = [];
  const failedResponses = [];
  try {
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
    browser = await puppeteer.launch({
      headless: true,
      executablePath: CHROME,
      args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server'],
    });
    const page = await browser.newPage();
    await page.setViewport(VIEWPORT);
    await page.setExtraHTTPHeaders({ 'X-Forwarded-Proto': 'https' });
    const secure = BASE.startsWith('https://');
    const cookies = [
      { name: 'trade_session', value: session.token, url: BASE, secure, httpOnly: true, sameSite: 'Strict' },
    ];
    if (session.csrf) {
      cookies.push({ name: 'trade_csrf', value: session.csrf, url: BASE, secure, sameSite: 'Strict' });
    }
    await page.setCookie(...cookies);
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('response', (response) => {
      if (response.status() >= 500) failedResponses.push(`${response.status()} ${response.url()}`);
    });

    const resources = await discoverResources(page);
    const descriptors = [
      {
        label: 'Signal',
        route: `/signal-blueprint?pipelineId=${encodeURIComponent(resources.pipelineId)}`,
        rootSelector: '#alphaGraphBuilder',
      },
      {
        label: 'Environment',
        route: `/environment-blueprint?environment=${encodeURIComponent(resources.environmentKey)}`,
        rootSelector: '#environmentGraphBuilder',
        requireValidInitialGraph: true,
        requiredConnectedTarget: 'module:account.time',
      },
      {
        label: 'Analysis',
        route: `/analysis-blueprint?analysis=${encodeURIComponent(resources.analysisKey)}`,
        rootSelector: '#analysisGraphBuilder',
      },
    ];
    const requestedSurfaces = new Set(String(process.env.TRADE_GRAPH_SURFACES || '')
      .split(',').map((value) => value.trim().toLowerCase()).filter(Boolean));
    const activeDescriptors = requestedSurfaces.size
      ? descriptors.filter((descriptor) => requestedSurfaces.has(descriptor.label.toLowerCase()))
      : descriptors;
    assert(activeDescriptors.length > 0, 'TRADE_GRAPH_SURFACES selected no known graph surfaces', {
      requested: [...requestedSurfaces],
      available: descriptors.map((descriptor) => descriptor.label),
    });
    const results = [];
    for (const descriptor of activeDescriptors) results.push(await auditGraph(page, descriptor));
    if (results.some((result) => result.nodeCount >= 2)) {
      assert(results.some((result) => result.multiSelection),
        'A populated graph was available but multi-selection was not exercised', results);
      assert(results.some((result) => result.portActionsRendered),
        'No populated graph rendered the confirmed Module port actions', results);
    }
    assert(pageErrors.length === 0, 'Browser emitted page errors', pageErrors);
    assert(failedResponses.length === 0, 'Browser received server errors', failedResponses);
    console.log(JSON.stringify({ resources, results, pageErrors, failedResponses }, null, 2));
  } finally {
    if (browser) await browser.close();
    deleteOwnedSession(session);
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
