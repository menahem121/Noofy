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
  const detail = startupErrorMessage(error);
  const main = document.createElement("main");
  main.setAttribute("role", "alert");
  main.style.maxWidth = "720px";
  main.style.margin = "18vh auto";
  main.style.padding = "0 24px";
  main.style.fontFamily = "system-ui, sans-serif";
  main.style.color = "#191b1f";

  const title = document.createElement("h1");
  title.textContent = "Noofy could not start.";
  title.style.fontSize = "1.35rem";
  title.style.margin = "0 0 0.75rem";

  const body = document.createElement("p");
  body.textContent =
    "Noofy could not start its local service on this computer. Restart Noofy after reviewing the startup detail below.";
  body.style.lineHeight = "1.5";
  body.style.margin = "0 0 1rem";

  const pre = document.createElement("pre");
  pre.textContent = detail;
  pre.style.whiteSpace = "pre-wrap";
  pre.style.overflowWrap = "anywhere";
  pre.style.maxHeight = "34vh";
  pre.style.overflow = "auto";
  pre.style.padding = "12px";
  pre.style.border = "1px solid #d8dde8";
  pre.style.borderRadius = "6px";
  pre.style.background = "#f7f8fb";
  pre.style.fontSize = "0.86rem";
  pre.style.lineHeight = "1.45";

  main.append(title, body, pre);
  root.replaceChildren(main);
}

function startupErrorMessage(error: unknown) {
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  if (typeof error === "string" && error.trim()) {
    return error;
  }
  return "Noofy desktop did not receive its startup connection details.";
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
