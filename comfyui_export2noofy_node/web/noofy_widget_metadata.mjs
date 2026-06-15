const SCHEMA_VERSION = "0.1.0";

function nonEmptyString(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function safeRead(read) {
  try {
    return read();
  } catch {
    return undefined;
  }
}

function scalarOptionValue(option) {
  if (["string", "number", "boolean"].includes(typeof option)) {
    return String(option);
  }
  if (Array.isArray(option)) {
    for (let index = option.length - 1; index >= 0; index -= 1) {
      const value = scalarOptionValue(option[index]);
      if (value !== null) return value;
    }
    return null;
  }
  if (!option || typeof option !== "object") return null;
  for (const key of ["value", "id", "name", "label", "title", "content"]) {
    const value = scalarOptionValue(option[key]);
    if (value !== null) return value;
  }
  return null;
}

function normalizeOptions(rawOptions) {
  let candidates = rawOptions;
  if (
    rawOptions
    && typeof rawOptions !== "string"
    && typeof rawOptions[Symbol.iterator] === "function"
    && !Array.isArray(rawOptions)
  ) {
    candidates = Array.from(rawOptions);
  } else if (rawOptions && typeof rawOptions === "object" && !Array.isArray(rawOptions)) {
    candidates = Object.keys(rawOptions);
  }
  if (!Array.isArray(candidates)) return [];

  const seen = new Set();
  const normalized = [];
  for (const candidate of candidates) {
    const value = scalarOptionValue(candidate);
    if (value === null || seen.has(value)) continue;
    seen.add(value);
    normalized.push(value);
  }
  return normalized;
}

async function resolveCandidate(candidate, widget, node) {
  if (typeof candidate !== "function") return candidate;
  return await candidate.call(widget, node, widget);
}

function selectElementOptions(widget) {
  const element = widget?.element ?? widget?.inputEl ?? widget?.select ?? null;
  if (!element?.options) return [];
  return Array.from(element.options, (option) => option?.value ?? option?.textContent);
}

async function widgetOptions(widget, node) {
  const candidates = [
    safeRead(() => widget?.options?.values),
    safeRead(() => widget?.options?.options),
    safeRead(() => widget?.options?.items),
    safeRead(() => widget?.values),
    safeRead(() => widget?.items),
    safeRead(() => (
      Array.isArray(widget?.options) || typeof widget?.options === "function"
        ? widget.options
        : undefined
    )),
    safeRead(() => selectElementOptions(widget)),
  ];

  for (const candidate of candidates) {
    try {
      const options = normalizeOptions(await resolveCandidate(candidate, widget, node));
      if (options.length) return options;
    } catch {
      // Export should continue when one custom widget cannot expose its values.
    }
  }
  return [];
}

export async function collectComfyUIWidgetMetadata(nodes, promptGraph = null) {
  const metadataNodes = {};
  for (const node of Array.isArray(nodes) ? nodes : []) {
    if (node?.id === undefined || !Array.isArray(node.widgets)) continue;
    const promptNode = promptGraph?.[String(node.id)];
    const promptInputs = promptNode?.inputs;
    const inputs = {};
    for (const widget of node.widgets) {
      const inputName = nonEmptyString(widget?.name);
      if (!inputName) continue;
      if (
        promptGraph
        && (!promptInputs || !Object.prototype.hasOwnProperty.call(promptInputs, inputName))
      ) {
        continue;
      }
      const options = await widgetOptions(widget, node);
      if (!options.length) continue;
      const record = { options };
      const displayName = nonEmptyString(safeRead(() => widget?.label));
      const tooltip = nonEmptyString(
        safeRead(() => widget?.tooltip) ?? safeRead(() => widget?.options?.tooltip),
      );
      if (displayName) record.display_name = displayName;
      if (tooltip) record.tooltip = tooltip;
      inputs[inputName] = record;
    }
    if (Object.keys(inputs).length) {
      metadataNodes[String(node.id)] = { inputs };
    }
  }
  return {
    schema_version: SCHEMA_VERSION,
    nodes: metadataNodes,
  };
}
