import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const EXTENSION_NAME = "Noofy.Export";
const BUTTON_ID = "noofy-export-button";
const BUTTON_GROUP_ID = "noofy-export-button-group";
const BUTTON_CLASS = "noofy-export-button";
const BUTTON_TEXT = "Export2Noofy";
const MOUNT_RETRY_MS = 500;
const MOUNT_RETRY_LIMIT = 60;
const CATEGORY_OPTIONS = [
  "Txt2img",
  "Img2img",
  "txt2audio",
  "audio2audio",
  "txt2vid",
  "img2vid",
  "imgTo3D",
  "txtTo3D",
  "img2text",
  "audio2txt",
  "vid2vid",
  "Inpainting",
  "Outpainting",
  "Upscaling",
  "Style Transfer",
  "Swapping",
  "Character Consistency",
  "Pose Control",
  "Depth Control",
  "Canny / Line Control",
  "Background Replacement",
  "Background Removal",
  "Restoration",
  "All-in-one",
];
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

function formatBytes(value) {
  if (!Number.isFinite(value)) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

async function fetchAssetCandidates(payload) {
  const response = await api.fetchApi("/noofy/export/assets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await parseErrorResponse(response));
  }
  const data = await response.json();
  return {
    assets: Array.isArray(data?.assets) ? data.assets : [],
    suggested_category: typeof data?.suggested_category === "string" ? data.suggested_category : "",
  };
}

function chooseIncludedAssets(candidates) {
  const visible = candidates.filter((candidate) => candidate?.selectable || candidate?.reason);
  if (visible.length === 0) {
    return Promise.resolve([]);
  }

  return new Promise((resolve, reject) => {
    const overlay = document.createElement("div");
    overlay.style.position = "fixed";
    overlay.style.inset = "0";
    overlay.style.zIndex = "100000";
    overlay.style.background = "rgba(10, 10, 16, 0.56)";
    overlay.style.display = "flex";
    overlay.style.alignItems = "center";
    overlay.style.justifyContent = "center";
    overlay.style.padding = "20px";

    const panel = document.createElement("div");
    panel.style.width = "min(560px, 100%)";
    panel.style.maxHeight = "80vh";
    panel.style.overflow = "auto";
    panel.style.background = "var(--comfy-menu-bg, #20212a)";
    panel.style.color = "var(--fg-color, #fff)";
    panel.style.border = "1px solid rgba(255,255,255,0.16)";
    panel.style.borderRadius = "8px";
    panel.style.boxShadow = "0 20px 60px rgba(0,0,0,0.45)";
    panel.style.padding = "18px";

    const title = document.createElement("h2");
    title.textContent = "Assets Included:";
    title.style.margin = "0 0 6px";
    title.style.fontSize = "18px";
    panel.appendChild(title);

    const intro = document.createElement("p");
    intro.textContent = "Checked workflow input files will be bundled into the .noofy package as creator defaults.";
    intro.style.margin = "0 0 14px";
    intro.style.opacity = "0.78";
    intro.style.fontSize = "13px";
    panel.appendChild(intro);

    const list = document.createElement("div");
    list.style.display = "grid";
    list.style.gap = "8px";
    const checkboxes = [];
    for (const candidate of visible) {
      const row = document.createElement("label");
      row.style.display = "grid";
      row.style.gridTemplateColumns = "auto 1fr";
      row.style.gap = "10px";
      row.style.alignItems = "start";
      row.style.padding = "10px";
      row.style.border = "1px solid rgba(255,255,255,0.12)";
      row.style.borderRadius = "6px";
      row.style.opacity = candidate.selectable ? "1" : "0.58";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = Boolean(candidate.selectable);
      checkbox.disabled = !candidate.selectable;
      checkbox.value = candidate.id;
      checkboxes.push(checkbox);
      row.appendChild(checkbox);

      const body = document.createElement("div");
      const name = document.createElement("div");
      name.textContent = `${candidate.filename || "Input asset"} · node ${candidate.node_id} ${candidate.input_name}`;
      name.style.fontWeight = "700";
      name.style.fontSize = "13px";
      body.appendChild(name);

      const details = document.createElement("div");
      details.textContent = candidate.selectable
        ? `${candidate.expected_kind}${candidate.size_bytes ? ` · ${formatBytes(candidate.size_bytes)}` : ""}`
        : candidate.reason || "Unavailable";
      details.style.fontSize = "12px";
      details.style.opacity = "0.72";
      body.appendChild(details);
      row.appendChild(body);
      list.appendChild(row);
    }
    panel.appendChild(list);

    const actions = document.createElement("div");
    actions.style.display = "flex";
    actions.style.justifyContent = "flex-end";
    actions.style.gap = "8px";
    actions.style.marginTop = "16px";

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.style.padding = "8px 12px";
    cancel.addEventListener("click", () => {
      overlay.remove();
      reject(new Error("Noofy export canceled."));
    });
    actions.appendChild(cancel);

    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.textContent = "Continue export";
    confirm.style.padding = "8px 12px";
    confirm.style.fontWeight = "700";
    confirm.addEventListener("click", () => {
      const selected = checkboxes.filter((checkbox) => checkbox.checked && !checkbox.disabled).map((checkbox) => checkbox.value);
      overlay.remove();
      resolve(selected);
    });
    actions.appendChild(confirm);
    panel.appendChild(actions);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
  });
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function tagsFromInput(value) {
  const tags = [];
  const seen = new Set();
  for (const part of String(value || "").split(",")) {
    const tag = cleanText(part);
    const key = tag.toLowerCase();
    if (!tag || seen.has(key)) continue;
    seen.add(key);
    tags.push(tag);
  }
  return tags;
}

function createDialogOverlay() {
  const overlay = document.createElement("div");
  overlay.style.position = "fixed";
  overlay.style.inset = "0";
  overlay.style.zIndex = "100000";
  overlay.style.background = "rgba(10, 10, 16, 0.56)";
  overlay.style.display = "flex";
  overlay.style.alignItems = "center";
  overlay.style.justifyContent = "center";
  overlay.style.padding = "20px";

  const panel = document.createElement("div");
  panel.style.width = "min(560px, 100%)";
  panel.style.maxHeight = "80vh";
  panel.style.overflow = "auto";
  panel.style.background = "var(--comfy-menu-bg, #20212a)";
  panel.style.color = "var(--fg-color, #fff)";
  panel.style.border = "1px solid rgba(255,255,255,0.16)";
  panel.style.borderRadius = "8px";
  panel.style.boxShadow = "0 20px 60px rgba(0,0,0,0.45)";
  panel.style.padding = "18px";
  overlay.appendChild(panel);
  return { overlay, panel };
}

function appendTextField(panel, labelText, value, options = {}) {
  const label = document.createElement("label");
  label.style.display = "grid";
  label.style.gap = "6px";
  label.style.marginTop = "12px";

  const labelSpan = document.createElement("span");
  labelSpan.textContent = labelText;
  labelSpan.style.fontSize = "12px";
  labelSpan.style.fontWeight = "700";
  label.appendChild(labelSpan);

  const input = document.createElement("input");
  input.type = "text";
  input.value = value || "";
  input.placeholder = options.placeholder || "";
  input.style.width = "100%";
  input.style.boxSizing = "border-box";
  input.style.padding = "9px 10px";
  input.style.borderRadius = "6px";
  input.style.border = "1px solid rgba(255,255,255,0.14)";
  input.style.background = "rgba(255,255,255,0.06)";
  input.style.color = "inherit";
  label.appendChild(input);
  panel.appendChild(label);
  return input;
}

function chooseWorkflowDetails(payload, suggestedCategory) {
  return new Promise((resolve, reject) => {
    const { overlay, panel } = createDialogOverlay();

    const title = document.createElement("h2");
    title.textContent = "Workflow details";
    title.style.margin = "0 0 6px";
    title.style.fontSize = "18px";
    panel.appendChild(title);

    const intro = document.createElement("p");
    intro.textContent = "These details help Noofy show the workflow in the library.";
    intro.style.margin = "0 0 14px";
    intro.style.opacity = "0.78";
    intro.style.fontSize = "13px";
    panel.appendChild(intro);

    const name = appendTextField(panel, "Name", payload.workflow_name || "Exported ComfyUI Workflow");
    const description = appendTextField(panel, "One-line description", "", {
      placeholder: "What this workflow helps someone make",
    });

    const categoryLabel = document.createElement("label");
    categoryLabel.style.display = "grid";
    categoryLabel.style.gap = "6px";
    categoryLabel.style.marginTop = "12px";
    const categorySpan = document.createElement("span");
    categorySpan.textContent = suggestedCategory ? "Suggested category" : "Category";
    categorySpan.style.fontSize = "12px";
    categorySpan.style.fontWeight = "700";
    categoryLabel.appendChild(categorySpan);
    const category = document.createElement("select");
    category.style.width = "100%";
    category.style.boxSizing = "border-box";
    category.style.padding = "9px 10px";
    category.style.borderRadius = "6px";
    category.style.border = "1px solid rgba(255,255,255,0.14)";
    category.style.background = "rgba(255,255,255,0.06)";
    category.style.color = "inherit";
    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = "No category";
    category.appendChild(emptyOption);
    for (const option of CATEGORY_OPTIONS) {
      const item = document.createElement("option");
      item.value = option;
      item.textContent = option;
      category.appendChild(item);
    }
    category.value = CATEGORY_OPTIONS.includes(suggestedCategory) ? suggestedCategory : "";
    categoryLabel.appendChild(category);
    panel.appendChild(categoryLabel);

    const tags = appendTextField(panel, "Tags", "", { placeholder: "portrait, cleanup, starter" });
    const author = appendTextField(panel, "Author", "");
    const website = appendTextField(panel, "Website", "");

    const actions = document.createElement("div");
    actions.style.display = "flex";
    actions.style.justifyContent = "flex-end";
    actions.style.gap = "8px";
    actions.style.marginTop = "16px";

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.style.padding = "8px 12px";
    cancel.addEventListener("click", () => {
      overlay.remove();
      reject(new Error("Noofy export canceled."));
    });
    actions.appendChild(cancel);

    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.textContent = "Export package";
    confirm.style.padding = "8px 12px";
    confirm.style.fontWeight = "700";
    confirm.addEventListener("click", () => {
      const workflowName = cleanText(name.value);
      if (!workflowName) {
        name.focus();
        return;
      }
      overlay.remove();
      resolve({
        name: workflowName,
        description: cleanText(description.value),
        category: category.value,
        tags: tagsFromInput(tags.value),
        author: cleanText(author.value),
        website: cleanText(website.value),
      });
    });
    actions.appendChild(confirm);
    panel.appendChild(actions);
    document.body.appendChild(overlay);
    name.focus();
    name.select();
  });
}

async function exportToNoofy(button) {
  const previousText = button.textContent;
  button.disabled = true;
  button.textContent = "Exporting to Noofy...";

  try {
    const payload = await collectPromptPayload();
    button.textContent = "Checking assets...";
    const assetPreflight = await fetchAssetCandidates(payload);
    const selectedAssetIds = await chooseIncludedAssets(assetPreflight.assets);
    const exportMetadata = await chooseWorkflowDetails(payload, assetPreflight.suggested_category);
    button.textContent = "Exporting to Noofy...";
    const response = await api.fetchApi("/noofy/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, selected_asset_ids: selectedAssetIds, export_metadata: exportMetadata }),
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
