const fs = require('node:fs');
const vm = require('node:vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const html = fs.readFileSync('web/index.html', 'utf8');
const service = fs.readFileSync('engine_service.py', 'utf8');
const previewConfig = JSON.parse(fs.readFileSync('deploy/user/strategy-control-preview.json', 'utf8'));
assert(html.includes('data-view="mining-kline"'), 'Mining / K Line navigation is missing');
assert(html.includes('href="/mining_kline.css"'), 'Mining stylesheet is not loaded as a same-origin asset');
assert(html.includes('src="/mining_kline.js"'), 'Mining controller is not loaded as a same-origin asset');
assert(html.indexOf('/mining_kline.js') < html.indexOf('/app.js'), 'Mining controller must load before app bridge');
assert(service.includes('"/mining/k-line"'), 'Mining SPA route is not protected/served');
assert(service.includes("connect-src 'self'"), 'CSP must keep Mining API traffic same-origin');
assert(previewConfig.miningAutoStart === true, 'User preview Mining worker is not enabled');
assert(previewConfig.miningRoot === '/file/share/data_jyz/.trade-engine-preview-mining', 'User preview Mining evidence root is not isolated from source');

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id, value: '', hidden: false, innerHTML: '', dataset: {},
      addEventListener() {}, querySelectorAll() { return []; },
      reset() {}, showModal() {}, close() {}, insertAdjacentHTML() {},
    });
  }
  return elements.get(id);
}
const sandbox = {
  window: {},
  console,
  setTimeout: () => 1,
  clearTimeout() {},
  encodeURIComponent,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('web/mining_kline.js', 'utf8'), sandbox);
assert(typeof sandbox.window.TradeMiningKLine?.create === 'function', 'Mining controller factory was not installed');
const controller = sandbox.window.TradeMiningKLine.create({
  $: element,
  escapeHtml: (value) => String(value),
  isActive: () => true,
  onError: (error) => { throw error; },
  postJson: async () => ({ accepted: true }),
  getJson: async (path) => {
    if (path === '/api/mining/providers') return { providers: [] };
    if (path === '/api/mining/health') return { workerAlive: true, jobs: 1, metrics: { pages_committed: 2 } };
    if (path === '/api/mining/jobs') return { jobs: [{ jobId: 'smoke-job', name: 'Smoke', provider: 'fake', status: 'queued', currentRecords: 1, pageCount: 2, openGaps: 0 }] };
    if (path === '/api/mining/jobs/smoke-job') return { job: { jobId: 'smoke-job', name: 'Smoke', provider: 'fake', status: 'queued', cursor: {} }, gaps: [], records: [] };
    throw new Error(`Unexpected API path ${path}`);
  },
});

controller.load(true).then(() => {
  assert(element('miningHealthStrip').innerHTML.includes('Alive'), 'Mining health did not render');
  assert(element('miningJobList').innerHTML.includes('smoke-job'), 'Mining job list did not render');
  assert(!element('miningJobDetail').hidden, 'Mining detail did not render');
  assert(element('miningJobDetail').innerHTML.includes('>Pause<'), 'A queued mining job must be pausable');
  console.log(JSON.stringify({ assets: true, csp: true, render: true }));
}).catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
