import React from "react";
import ReactDOM from "react-dom/client";

import "./styles/global.css";

async function loadDesktopRuntimeConfig() {
  if (window.__NOOFY_RUNTIME_CONFIG__ || !window.__TAURI_INTERNALS__) {
    return;
  }

  const { invoke } = await import("@tauri-apps/api/core");
  window.__NOOFY_RUNTIME_CONFIG__ =
    await invoke<NonNullable<Window["__NOOFY_RUNTIME_CONFIG__"]>>("noofy_runtime_config");

  if (!window.__NOOFY_RUNTIME_CONFIG__?.apiBaseUrl || !window.__NOOFY_RUNTIME_CONFIG__?.apiToken) {
    throw new Error("Noofy desktop did not receive its startup connection details.");
  }
}

async function renderApp() {
  const { default: App } = await import("./App");
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

function renderStartupError(error: unknown) {
  console.error("[noofy] desktop startup failed:", error);
  const root = document.getElementById("root");
  if (!root) return;
  root.innerHTML = `
    <main role="alert" style="max-width: 680px; margin: 20vh auto; padding: 0 24px; font-family: system-ui, sans-serif; color: #191b1f;">
      <h1 style="font-size: 1.35rem; margin: 0 0 0.75rem;">Noofy could not start.</h1>
      <p style="line-height: 1.5; margin: 0;">Noofy could not connect to its local service on this computer. Restart Noofy. If this keeps happening, reinstall or repair Noofy.</p>
    </main>
  `;
}

async function start() {
  try {
    await loadDesktopRuntimeConfig();
    await renderApp();
  } catch (error) {
    renderStartupError(error);
  }
}

void start();
