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

function declaredInputSpec(node, inputName) {
  const inputGroups = safeRead(() => node?.constructor?.nodeData?.input);
  if (!inputGroups || typeof inputGroups !== "object") return null;
  for (const groupName of ["required", "optional", "hidden"]) {
    const group = inputGroups[groupName];
    if (group && Object.prototype.hasOwnProperty.call(group, inputName)) {
      return { groupName, spec: group[inputName] };
    }
  }
  return null;
}

function normalizedInputType(rawType) {
  if (typeof rawType === "string" && rawType.trim()) return rawType.trim().toUpperCase();
  if (Array.isArray(rawType)) return "COMBO";
  return null;
}

function semanticWidgetMetadata(widget, node, inputName) {
  const declared = declaredInputSpec(node, inputName);
  const inputSpec = declared?.spec;
  const rawType = Array.isArray(inputSpec) ? inputSpec[0] : null;
  const inputOptions = Array.isArray(inputSpec) && inputSpec[1] && typeof inputSpec[1] === "object"
    ? inputSpec[1]
    : {};
  const widgetOptionsRecord = widget?.options && typeof widget.options === "object"
    ? widget.options
    : {};
  const record = {};
  const inputType = normalizedInputType(rawType) ?? normalizedInputType(safeRead(() => widget?.type));
  if (inputType) record.input_type = inputType;
  if (declared?.groupName) record.input_group = declared.groupName;
  for (const flag of ["image_upload", "audio_upload", "video_upload", "file_upload"]) {
    if (inputOptions[flag] === true || widgetOptionsRecord[flag] === true) {
      record[flag] = true;
    }
  }
  return record;
}

function declaredOutputTypes(node) {
  const rawOutputs = safeRead(() => node?.constructor?.nodeData?.output);
  if (!Array.isArray(rawOutputs)) return [];
  return rawOutputs
    .map((output) => nonEmptyString(output))
    .filter((output) => output !== null);
}

export async function collectComfyUIWidgetMetadata(nodes, promptGraph = null) {
  const metadataNodes = {};
  for (const node of Array.isArray(nodes) ? nodes : []) {
    if (node?.id === undefined) continue;
    const promptNode = promptGraph?.[String(node.id)];
    if (promptGraph && !promptNode) continue;
    const promptInputs = promptNode?.inputs;
    const inputs = {};
    for (const widget of Array.isArray(node.widgets) ? node.widgets : []) {
      const inputName = nonEmptyString(widget?.name);
      if (!inputName) continue;
      if (
        promptGraph
        && (!promptInputs || !Object.prototype.hasOwnProperty.call(promptInputs, inputName))
      ) {
        continue;
      }
      const options = await widgetOptions(widget, node);
      const record = semanticWidgetMetadata(widget, node, inputName);
      if (options.length) record.options = options;
      const displayName = nonEmptyString(safeRead(() => widget?.label));
      const tooltip = nonEmptyString(
        safeRead(() => widget?.tooltip) ?? safeRead(() => widget?.options?.tooltip),
      );
      if (displayName) record.display_name = displayName;
      if (tooltip) record.tooltip = tooltip;
      if (!Object.keys(record).length) continue;
      inputs[inputName] = record;
    }
    const nodeRecord = {};
    const outputs = declaredOutputTypes(node);
    if (outputs.length) nodeRecord.outputs = outputs;
    if (Object.keys(inputs).length) nodeRecord.inputs = inputs;
    if (Object.keys(nodeRecord).length) metadataNodes[String(node.id)] = nodeRecord;
  }
  return {
    schema_version: SCHEMA_VERSION,
    nodes: metadataNodes,
  };
}
