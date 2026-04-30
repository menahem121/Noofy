import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import "./styles/global.css";

async function loadDesktopRuntimeConfig() {
  if (window.__NOOFY_RUNTIME_CONFIG__ || !window.__TAURI_INTERNALS__) {
    return;
  }

  const { invoke } = await import("@tauri-apps/api/core");
  window.__NOOFY_RUNTIME_CONFIG__ =
    await invoke<NonNullable<Window["__NOOFY_RUNTIME_CONFIG__"]>>("noofy_runtime_config");
}

function renderApp() {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

loadDesktopRuntimeConfig().finally(renderApp);
