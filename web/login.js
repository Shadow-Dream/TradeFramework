function safeNextPath() {
  const value = new URLSearchParams(location.search).get("next") || "/";
  return value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

function showLoginError(message = "") {
  const node = document.getElementById("loginError");
  node.textContent = message;
  node.hidden = !message;
}

let loginPending = false;

function setLoginPending(button, pending) {
  loginPending = Boolean(pending);
  button.disabled = loginPending;
  button.classList.toggle("button-loading", loginPending);
  for (const id of ["loginEmail", "loginPassword"]) {
    document.getElementById(id).disabled = loginPending;
  }
  if (loginPending) {
    button.setAttribute("aria-busy", "true");
    button.textContent = "Signing in…";
  } else {
    button.removeAttribute("aria-busy");
    button.textContent = "Sign in";
  }
}

async function existingSession() {
  const response = await fetch("/auth/session", {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    cache: "no-store",
  });
  if (response.ok) location.replace(safeNextPath());
}

document.getElementById("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (loginPending) return;
  const button = document.getElementById("loginBtn");
  let redirecting = false;
  let focusPassword = false;
  setLoginPending(button, true);
  showLoginError("");
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify({
        email: document.getElementById("loginEmail").value.trim(),
        password: document.getElementById("loginPassword").value,
      }),
    });
    const data = await response.json().catch(() => ({}));
    document.getElementById("loginPassword").value = "";
    if (!response.ok || !data.authenticated) throw new Error(data.error || "Sign in failed.");
    redirecting = true;
    location.replace(safeNextPath());
  } catch (error) {
    showLoginError(error?.message || "Sign in failed.");
    focusPassword = true;
  } finally {
    // Keep the pending state visible until the successful navigation commits.
    if (!redirecting) {
      setLoginPending(button, false);
      if (focusPassword) document.getElementById("loginPassword").focus();
    }
  }
});

existingSession().catch(() => {});
