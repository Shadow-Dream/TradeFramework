const puppeteer = require('/root/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');

const BASE = process.env.TRADE_WEB_BASE || 'https://trade.duckduckrun.com';
const EMAIL = process.env.TRADE_AUTH_EMAIL || '';
const PASSWORD = process.env.TRADE_AUTH_PASSWORD || '';

function assert(condition, message, payload = null) {
  if (condition) return;
  const error = new Error(message);
  error.payload = payload;
  throw error;
}

async function main() {
  assert(EMAIL && PASSWORD, 'TRADE_AUTH_EMAIL and TRADE_AUTH_PASSWORD are required');
  const browser = await puppeteer.launch({
    headless: false,
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.stack || error.message));

  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 30000 });
  const unauthenticatedApi = await page.evaluate(async () => {
    const response = await fetch('/api/health', { credentials: 'include' });
    return response.status;
  });
  assert(unauthenticatedApi === 401, 'Unauthenticated API was not rejected', unauthenticatedApi);

  await page.goto(`${BASE}/data`, { waitUntil: 'networkidle2', timeout: 30000 });
  assert(new URL(page.url()).pathname === '/login', 'Protected page did not redirect to login', page.url());
  await page.screenshot({ path: '/tmp/trade-auth-login-vnc.png', fullPage: true });
  await page.type('#loginEmail', EMAIL);
  await page.type('#loginPassword', PASSWORD);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }),
    page.click('#loginBtn'),
  ]);
  await page.waitForFunction((email) => (
    window.__tradeAuth?.user?.email === email && (window.__tradeState?.datasets || []).length >= 0
  ), { timeout: 30000 }, EMAIL.toLowerCase());

  const cookies = await page.cookies(BASE);
  const sessionCookie = cookies.find((cookie) => cookie.name === 'trade_session');
  const csrfCookie = cookies.find((cookie) => cookie.name === 'trade_csrf');
  assert(sessionCookie?.secure && sessionCookie?.httpOnly && sessionCookie?.sameSite === 'Strict',
    'Session cookie security attributes are incorrect');
  assert(csrfCookie?.secure && !csrfCookie?.httpOnly && csrfCookie?.sameSite === 'Strict',
    'CSRF cookie security attributes are incorrect');

  const securityChecks = await page.evaluate(async () => {
    const health = await fetch('/api/health', { credentials: 'same-origin' });
    const noCsrf = await fetch('/api/data/proxy', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const wrongCsrf = await fetch('/api/data/proxy', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'forged' },
      body: '{}',
    });
    return { health: health.status, noCsrf: noCsrf.status, wrongCsrf: wrongCsrf.status };
  });
  assert(securityChecks.health === 200, 'Authenticated API access failed', securityChecks);
  assert(securityChecks.noCsrf === 403 && securityChecks.wrongCsrf === 403,
    'CSRF bypass was accepted', securityChecks);

  await page.click('#accountBtn');
  await page.waitForSelector('#accountDialog[open]');
  const accountDialog = await page.evaluate(() => ({
    identity: document.querySelector('#accountIdentity')?.textContent || '',
    passwordFields: document.querySelectorAll('#accountDialog input[type="password"]').length,
  }));
  assert(accountDialog.identity.includes(EMAIL.toLowerCase()) && accountDialog.passwordFields === 3,
    'Account security dialog is incomplete', accountDialog);
  await page.click('#cancelAccountBtn');

  await page.reload({ waitUntil: 'networkidle2' });
  await page.waitForFunction((email) => window.__tradeAuth?.user?.email === email, { timeout: 30000 }, EMAIL.toLowerCase());
  await page.screenshot({ path: '/tmp/trade-auth-authenticated-vnc.png', fullPage: true });

  const isolated = await browser.createBrowserContext();
  const attacker = await isolated.newPage();
  await attacker.setCookie({
    name: 'trade_session',
    value: 'forged-session-token',
    url: BASE,
    secure: true,
    httpOnly: true,
    sameSite: 'Strict',
  });
  await attacker.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 30000 });
  const attackerChecks = await attacker.evaluate(async () => {
    const api = await fetch('/api/health', { credentials: 'include' });
    const jupyter = await fetch('/jupyter/api/status', { credentials: 'include' });
    return { api: api.status, jupyter: jupyter.status };
  });
  assert(attackerChecks.api === 401 && attackerChecks.jupyter === 401,
    'Forged or missing session reached a protected service', attackerChecks);
  await isolated.close();

  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }),
    page.click('#logoutBtn'),
  ]);
  assert(new URL(page.url()).pathname === '/login', 'Logout did not return to login', page.url());
  const afterLogout = await page.evaluate(async () => (await fetch('/api/health')).status);
  assert(afterLogout === 401, 'Revoked session still accessed API', afterLogout);
  assert(pageErrors.length === 0, 'Browser page errors occurred', pageErrors);

  await browser.close();
  console.log(JSON.stringify({
    unauthenticatedApi,
    securityChecks,
    attackerChecks,
    cookieSecurity: {
      session: { secure: sessionCookie.secure, httpOnly: sessionCookie.httpOnly, sameSite: sessionCookie.sameSite },
      csrf: { secure: csrfCookie.secure, httpOnly: csrfCookie.httpOnly, sameSite: csrfCookie.sameSite },
    },
    afterLogout,
    pageErrors,
  }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (error.payload) console.error(JSON.stringify(error.payload, null, 2));
  process.exit(1);
});
