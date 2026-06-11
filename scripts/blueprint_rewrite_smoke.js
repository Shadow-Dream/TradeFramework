const puppeteer = require('/root/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const BASE = process.env.BLUEPRINT_BASE || 'http://127.0.0.1:30808';
const VIEWPORT = { width: 1440, height: 920 };

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function assert(condition, message, payload = null) {
  if (!condition) {
    const error = new Error(message);
    error.payload = payload;
    throw error;
  }
}

async function snapshot(page) {
  return page.evaluate(() => {
    const stage = document.querySelector('.alpha-x6-canvas') || document.querySelector('.alpha-blueprint-stage');
    const stageRect = stage?.getBoundingClientRect();
    const graph = document.querySelector('#alphaGraphBuilder')?.__x6Graph;
    const state = document.querySelector('#alphaGraphBuilder')?.__x6State;
    return {
      pathname: location.pathname,
      routeBlueprint: document.body.classList.contains('route-blueprint'),
      x6Graph: !!graph,
      stage: stageRect ? { width: Math.round(stageRect.width), height: Math.round(stageRect.height) } : null,
      nodes: graph ? graph.getNodes().map((node) => {
        const bbox = node.getBBox();
        return {
          id: node.id,
          title: node.attr('label/text'),
          left: Math.round(bbox.x),
          top: Math.round(bbox.y),
          right: Math.round(bbox.x + bbox.width),
          bottom: Math.round(bbox.y + bbox.height),
          width: Math.round(bbox.width),
          height: Math.round(bbox.height),
          ports: node.getPorts().map((port) => port.id),
        };
      }) : [],
      edgeCount: graph ? graph.getEdges().length : 0,
      edgeLabels: [...document.querySelectorAll('.x6-edge-label text')].map((node) => node.textContent.trim()).filter(Boolean),
      stateInputs: state ? Object.fromEntries(Object.entries(state.instances).map(([id, instance]) => [id, instance.inputs || {}])) : {},
    };
  });
}

function indexByTitle(nodes) {
  return Object.fromEntries(nodes.map((node) => [node.title, node]));
}

function assertNoOverflow(snapshot, scopeLabel) {
  snapshot.nodes.forEach((node) => {
    assert(node.left >= 0 && node.top >= 0, `${scopeLabel}: node escaped top/left bounds`, node);
    assert(
      node.right <= snapshot.stage.width && node.bottom <= snapshot.stage.height,
      `${scopeLabel}: node escaped stage bounds`,
      node,
    );
  });
}

async function clickNodeByIndex(page, index = 0) {
  const point = await page.evaluate((index) => {
    const node = document.querySelectorAll('.x6-node')[index];
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, index);
  if (!point) throw new Error(`x6 node ${index} missing`);
  await page.mouse.click(point.x, point.y);
}

async function main() {
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.setViewport(VIEWPORT);

  try {
    await page.goto(`${BASE}/blueprint`, { waitUntil: 'networkidle2', timeout: 30000 });
    await sleep(1200);
    const initial = await snapshot(page);
    assert(initial.routeBlueprint, 'body.route-blueprint missing on /blueprint', initial);
    assert(initial.x6Graph, 'X6 graph missing on /blueprint', initial);
    assert(initial.nodes.length === 2, 'expected 2 default nodes', initial);
    assert(initial.edgeCount === 1, 'expected 1 default edge', initial);
    assert(initial.edgeLabels.includes('P -> IN') || initial.edgeLabels.includes('P -> IN'), 'default edge label missing', initial);
    assertNoOverflow(initial, 'initial');
    const initialByTitle = indexByTitle(initial.nodes);
    assert(initialByTitle.Price.ports.includes('out:price'), 'default source port missing', initialByTitle.Price);
    assert(initialByTitle.SMA.ports.includes('in:value'), 'default target input missing', initialByTitle.SMA);

    await clickNodeByIndex(page, 0);
    await sleep(200);
    const selected = await snapshot(page);
    assertNoOverflow(selected, 'selected');
    const selectedByTitle = indexByTitle(selected.nodes);
    assert(
      initialByTitle.Price.left === selectedByTitle.Price.left && initialByTitle.Price.top === selectedByTitle.Price.top,
      'selected node jumped position on expand',
      { initial: initialByTitle.Price, selected: selectedByTitle.Price },
    );
    assert(selectedByTitle.Price.ports.length >= 6, 'selected source did not expose full outputs', selectedByTitle.Price);
    const selectedId = await page.evaluate(() => document.querySelector('#alphaGraphBuilder').__x6Selection?.() || '');
    assert(selectedId && selectedId.includes('price-source'), 'clicking source node did not update x6 selection state', { selectedId, selected });
    await page.mouse.click(40, 40);
    await sleep(100);
    const clearedId = await page.evaluate(() => document.querySelector('#alphaGraphBuilder').__x6Selection?.() || '');
    assert(clearedId === '', 'blank click did not clear x6 selection state', { clearedId });
    await clickNodeByIndex(page, 0);
    await sleep(100);

    await page.goto(`${BASE}/blueprint`, { waitUntil: 'networkidle2', timeout: 30000 });
    await sleep(1200);
    await clickNodeByIndex(page, 0);
    await sleep(200);

    const points = await page.evaluate(() => {
      const out = [...document.querySelectorAll('[port]')].find((el) => el.getAttribute('port') === 'out:price');
      const input = [...document.querySelectorAll('[port]')].find((el) => el.getAttribute('port') === 'in:value');
      const or = out?.getBoundingClientRect();
      const ir = input?.getBoundingClientRect();
      return {
        out: or ? { x: or.left + or.width / 2, y: or.top + or.height / 2 } : null,
        input: ir ? { x: ir.left + ir.width / 2, y: ir.top + ir.height / 2 } : null,
      };
    });
    assert(points.out && points.input, 'ports missing for connect test', points);
    await page.mouse.move(points.out.x, points.out.y);
    await page.mouse.down();
    await page.mouse.move(points.input.x, points.input.y, { steps: 12 });
    await sleep(120);
    const duringPending = await page.evaluate(() => ({
      pendingSource: document.querySelector('#alphaGraphBuilder').__x6PendingSource?.() || '',
      texts: [...document.querySelectorAll('.x6-node text')].map((node) => node.textContent.trim()).filter(Boolean),
    }));
    assert(duringPending.pendingSource && duringPending.pendingSource.includes('price-source'), 'x6 pending source did not activate after port press', duringPending);
    await page.mouse.up();
    await sleep(500);
    const pendingOrConnected = await snapshot(page);
    assert(pendingOrConnected.edgeCount === 1, 'x6 edge count drifted after connect interaction', pendingOrConnected);
    const pendingOrConnectedByTitle = indexByTitle(pendingOrConnected.nodes);
    assert(pendingOrConnectedByTitle.Price.ports.includes('out:price'), 'x6 source lost primary output after connect interaction', pendingOrConnectedByTitle.Price);
    assert(pendingOrConnectedByTitle.SMA.ports.includes('in:value'), 'x6 target lost input after connect interaction', pendingOrConnectedByTitle.SMA);
    const inputValues = Object.values(pendingOrConnected.stateInputs).map((inputs) => Object.values(inputs || {}));
    assert(inputValues.flat().some((wire) => wire === 'price-source.price'), 'x6 connect interaction did not preserve expected input wire', pendingOrConnected);

    await page.setViewport({ width: 1180, height: 760 });
    await sleep(300);
    const resized = await snapshot(page);
    assertNoOverflow(resized, 'resized');

    await page.goto(`${BASE}/pipeline`, { waitUntil: 'networkidle2', timeout: 30000 });
    await sleep(700);
    await page.click('#pipelineSectionAlphaBtn');
    await sleep(1000);
    const pipelineAlpha = await snapshot(page);
    assert(pipelineAlpha.pathname === '/blueprint', 'pipeline alpha tab did not route to /blueprint', pipelineAlpha);
    assert(pipelineAlpha.routeBlueprint, 'pipeline alpha route did not apply pure canvas chrome', pipelineAlpha);
    assert(pipelineAlpha.x6Graph, 'pipeline alpha route did not mount x6 graph', pipelineAlpha);
    assertNoOverflow(pipelineAlpha, 'pipelineAlpha');

    console.log(JSON.stringify({ initial, selected, duringPending, pendingOrConnected, resized, pipelineAlpha }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
