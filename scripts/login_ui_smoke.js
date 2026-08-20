const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("web/login.js", "utf8");
const styles = fs.readFileSync("web/styles.css", "utf8");

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function classList() {
  const values = new Set();
  return {
    contains: (value) => values.has(value),
    toggle(value, enabled) {
      if (enabled) values.add(value);
      else values.delete(value);
    },
  };
}

async function runScenario(loginResponse) {
  const request = deferred();
  const attributes = new Map();
  const button = {
    disabled: false,
    textContent: "Sign in",
    classList: classList(),
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: (name) => attributes.delete(name),
    getAttribute: (name) => attributes.get(name),
  };
  const password = { value: "secret", focused: false, focus() { this.focused = true; } };
  const elements = {
    loginBtn: button,
    loginEmail: { value: "user@example.test" },
    loginPassword: password,
    loginError: { textContent: "", hidden: true },
  };
  let submit;
  let redirects = 0;
  const sandbox = {
    URLSearchParams,
    location: { search: "", replace: () => { redirects += 1; } },
    document: {
      getElementById(id) {
        if (id === "loginForm") return { addEventListener: (_name, listener) => { submit = listener; } };
        return elements[id];
      },
    },
    fetch(path) {
      if (path === "/auth/session") return Promise.resolve({ ok: false });
      assert.strictEqual(path, "/auth/login");
      return request.promise;
    },
  };
  vm.runInNewContext(source, sandbox, { filename: "web/login.js" });

  const completion = submit({ preventDefault() {} });
  assert.strictEqual(button.disabled, true);
  assert.strictEqual(button.classList.contains("button-loading"), true);
  assert.strictEqual(button.getAttribute("aria-busy"), "true");
  assert.strictEqual(button.textContent, "Signing in…");
  assert.strictEqual(elements.loginEmail.disabled, true);
  assert.strictEqual(elements.loginPassword.disabled, true);

  request.resolve(loginResponse);
  await completion;
  return { button, elements, password, redirects };
}

async function main() {
  assert.match(styles, /button\.button-loading::before/);
  assert.match(styles, /animation:\s*trade-spin/);

  const failed = await runScenario({
    ok: false,
    json: async () => ({ error: "Invalid credentials" }),
  });
  assert.strictEqual(failed.button.disabled, false);
  assert.strictEqual(failed.button.classList.contains("button-loading"), false);
  assert.strictEqual(failed.button.getAttribute("aria-busy"), undefined);
  assert.strictEqual(failed.button.textContent, "Sign in");
  assert.strictEqual(failed.elements.loginEmail.disabled, false);
  assert.strictEqual(failed.elements.loginPassword.disabled, false);
  assert.strictEqual(failed.elements.loginError.textContent, "Invalid credentials");
  assert.strictEqual(failed.password.focused, true);

  const succeeded = await runScenario({
    ok: true,
    json: async () => ({ authenticated: true }),
  });
  assert.strictEqual(succeeded.redirects, 1);
  assert.strictEqual(succeeded.button.disabled, true);
  assert.strictEqual(succeeded.button.classList.contains("button-loading"), true);
  assert.strictEqual(succeeded.button.textContent, "Signing in…");
  assert.strictEqual(succeeded.elements.loginEmail.disabled, true);
  assert.strictEqual(succeeded.elements.loginPassword.disabled, true);

  console.log("login UI smoke passed");
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
