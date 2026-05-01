import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const EXTENSION_NAME = "Noofy.Export";
const BUTTON_ID = "noofy-export-button";
const BUTTON_GROUP_ID = "noofy-export-button-group";
const BUTTON_CLASS = "noofy-export-button";
const BUTTON_TEXT = "Export2Noofy";
const MOUNT_RETRY_MS = 500;
const MOUNT_RETRY_LIMIT = 60;
let mountPromise = null;

function showMessage(message) {
  if (app?.ui?.dialog?.show) {
    app.ui.dialog.show(message);
    return;
  }
  window.alert(message);
}

function getWorkflowName() {
  const activeWorkflow = app?.workflowManager?.activeWorkflow;
  if (activeWorkflow?.name) {
    return activeWorkflow.name;
  }

  const fileInput = document.querySelector("input[type='file'][accept*='.json']");
  if (fileInput?.files?.[0]?.name) {
    return fileInput.files[0].name.replace(/\.json$/i, "");
  }

  if (document.title) {
    return document.title.replace(/\s*-\s*ComfyUI\s*$/i, "").trim();
  }

  return "Exported ComfyUI Workflow";
}

function filenameFromContentDisposition(header) {
  if (!header) {
    return null;
  }
  const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    return decodeURIComponent(utf8Match[1].replace(/"/g, ""));
  }
  const asciiMatch = header.match(/filename="?([^";]+)"?/i);
  return asciiMatch ? asciiMatch[1] : null;
}

async function collectPromptPayload() {
  if (!app?.graphToPrompt) {
    throw new Error("ComfyUI graph export API is not available in this frontend.");
  }

  const graphExport = await app.graphToPrompt();
  if (!graphExport?.output) {
    throw new Error("Could not convert the current workflow to ComfyUI API prompt format.");
  }

  return {
    prompt: graphExport.output,
    workflow: graphExport.workflow ?? null,
    workflow_name: getWorkflowName(),
    client_id: api.clientId ?? null,
    started_at: new Date().toISOString(),
  };
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename || "workflow.noofy";
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

async function parseErrorResponse(response) {
  try {
    const data = await response.json();
    return data?.error || response.statusText || "Noofy export failed.";
  } catch {
    return response.statusText || "Noofy export failed.";
  }
}

async function exportToNoofy(button) {
  const previousText = button.textContent;
  button.disabled = true;
  button.textContent = "Exporting to Noofy...";

  try {
    const payload = await collectPromptPayload();
    const response = await api.fetchApi("/noofy/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(await parseErrorResponse(response));
    }

    const filename =
      filenameFromContentDisposition(response.headers.get("Content-Disposition")) ||
      `${payload.workflow_name || "workflow"}.noofy`;
    const blob = await response.blob();
    downloadBlob(blob, filename);
    showMessage(`Noofy export complete: ${filename}`);
  } catch (error) {
    showMessage(error?.message || "Noofy export failed.");
  } finally {
    button.disabled = false;
    button.textContent = previousText;
  }
}

function createButton() {
  const button = document.createElement("button");
  button.id = BUTTON_ID;
  button.className = BUTTON_CLASS;
  button.type = "button";
  button.textContent = BUTTON_TEXT;
  button.title = "Run the current workflow once and export a tested .noofy package";
  button.addEventListener("click", () => exportToNoofy(button));
  styleButton(button);
  return button;
}

async function createComfyMenuButton() {
  const [{ ComfyButton }, { ComfyButtonGroup }] = await Promise.all([
    import("../../scripts/ui/components/button.js"),
    import("../../scripts/ui/components/buttonGroup.js"),
  ]);

  const comfyButton = new ComfyButton({
    icon: "share",
    action: () => exportToNoofy(comfyButton.element),
    tooltip: "Run the current workflow once and export a tested .noofy package",
    content: BUTTON_TEXT,
    classList: "comfyui-button comfyui-menu-mobile-collapse primary",
  });
  comfyButton.element.id = BUTTON_ID;
  comfyButton.element.classList.add(BUTTON_CLASS);
  styleButton(comfyButton.element);

  const group = new ComfyButtonGroup(comfyButton.element);
  group.element.id = BUTTON_GROUP_ID;
  return group.element;
}

function styleButton(button) {
  button.style.display = "inline-flex";
  button.style.alignItems = "center";
  button.style.justifyContent = "center";
  button.style.height = "32px";
  button.style.padding = "0 16px";
  button.style.border = "0";
  button.style.borderRadius = "var(--p-button-border-radius, 6px)";
  button.style.background = "linear-gradient(90deg, #b733f4 0%, #df3c8f 100%)";
  button.style.color = "#fff";
  button.style.font = "inherit";
  button.style.fontSize = "14px";
  button.style.fontWeight = "700";
  button.style.lineHeight = "1";
  button.style.whiteSpace = "nowrap";
  button.style.cursor = "pointer";
  button.style.boxShadow = "0 6px 14px rgba(196, 49, 184, 0.28)";
}

function noofyGroups() {
  return [...document.querySelectorAll(`[id="${BUTTON_GROUP_ID}"]`)];
}

function noofyButtons() {
  return [...document.querySelectorAll(`[id="${BUTTON_ID}"], .${BUTTON_CLASS}`)];
}

function removeNoofyButtons() {
  for (const group of noofyGroups()) {
    group.remove();
  }
  for (const button of noofyButtons()) {
    button.remove();
  }
}

function keepOnlyModernGroup(groupToKeep) {
  for (const group of noofyGroups()) {
    if (group !== groupToKeep) {
      group.remove();
    }
  }
  for (const button of noofyButtons()) {
    if (!groupToKeep.contains(button)) {
      button.remove();
    }
  }
}

async function mountModernMenuButton() {
  const existingGroup = document.getElementById(BUTTON_GROUP_ID);
  if (existingGroup) {
    keepOnlyModernGroup(existingGroup);
    return true;
  }

  const settingsGroup = app.menu?.settingsGroup?.element;
  if (!settingsGroup) {
    return false;
  }

  try {
    removeNoofyButtons();
    const group = await createComfyMenuButton();
    settingsGroup.before(group);
    keepOnlyModernGroup(group);
    return true;
  } catch (error) {
    console.warn("[Noofy Export] Could not mount ComfyUI menu button.", error);
    return false;
  }
}

function mountFallback(button) {
  const menu = document.querySelector(".comfy-menu") || document.querySelector("#comfy-menu");
  if (menu) {
    menu.appendChild(button);
    return;
  }

  button.style.position = "fixed";
  button.style.top = "12px";
  button.style.right = "120px";
  button.style.zIndex = "10000";
  document.body.appendChild(button);
}

function ensureButton() {
  let button = document.getElementById(BUTTON_ID);
  if (!button) {
    button = createButton();
  }
  return button;
}

function mountLegacyButton() {
  const modernGroup = document.getElementById(BUTTON_GROUP_ID);
  if (modernGroup) {
    keepOnlyModernGroup(modernGroup);
    return true;
  }

  const button = ensureButton();
  if (!button.isConnected) {
    mountFallback(button);
  }
  return false;
}

async function mountButton() {
  if (!mountPromise) {
    mountPromise = (async () => {
      return (await mountModernMenuButton()) || mountLegacyButton();
    })().finally(() => {
      mountPromise = null;
    });
  }
  return mountPromise;
}

function mountButtonWhenToolbarIsReady() {
  let attempts = 0;
  let observer = null;
  void mountButton();

  const interval = window.setInterval(() => {
    attempts += 1;
    void mountButton().then((mounted) => {
      if (mounted || attempts >= MOUNT_RETRY_LIMIT) {
        window.clearInterval(interval);
        observer?.disconnect();
      }
    });
  }, MOUNT_RETRY_MS);

  observer = new MutationObserver(() => {
    void mountButton().then((mounted) => {
      if (mounted) {
        observer.disconnect();
        window.clearInterval(interval);
      }
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

app.registerExtension({
  name: EXTENSION_NAME,
  setup() {
    mountButtonWhenToolbarIsReady();
  },
});
