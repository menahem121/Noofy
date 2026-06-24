import type {
  DashboardControlDef,
  UploadProgress,
  WorkflowInputDef,
} from "../../lib/api/noofyApi";
import type { SeedMode } from "../../lib/seedControl";
import type { DashboardTopLevelControlItem } from "./dashboardTopLevelItems";
import {
  DashboardInputControl,
  type LoraBrowserControlProps,
} from "./DashboardInputControl";

export function DashboardInputControls({
  items,
  inputIndex,
  inputValues,
  seedModes,
  onSeedModeChange,
  onChange,
  onImageUpload,
  onGalleryImageMaskPrepare,
  onImageMaskApply,
  onAudioUpload,
  onVideoUpload,
  onFileUpload,
  onThreeDUpload,
  loraBrowserFor,
}: {
  items: DashboardTopLevelControlItem[];
  inputIndex: Map<string, WorkflowInputDef>;
  inputValues: Record<string, unknown>;
  seedModes: Record<string, SeedMode>;
  onSeedModeChange: (inputId: string, mode: SeedMode) => void;
  onChange: (id: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onGalleryImageMaskPrepare: (inputId: string, galleryItemId: string) => Promise<string>;
  onImageMaskApply: (sourceAssetId: string, mask: Blob) => Promise<string>;
  onAudioUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onVideoUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onFileUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onThreeDUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  loraBrowserFor?: (control: DashboardControlDef, input: WorkflowInputDef) => LoraBrowserControlProps | undefined;
}) {
  return (
    <>
      {items.map((item) => {
        if (item.kind === "group") {
          return (
            <section className="field-group dashboard-control-group" key={item.id}>
              <span>{item.group.title}</span>
              {item.group.description ? <small>{item.group.description}</small> : null}
              <div className="dashboard-control-group__controls">
                {item.controls.map((control) => (
                  <ClassicDashboardInputControl
                    key={control.id}
                    control={control}
                    inputIndex={inputIndex}
                    inputValues={inputValues}
                    seedModes={seedModes}
                    onSeedModeChange={onSeedModeChange}
                    grouped
                    onChange={onChange}
                    onImageUpload={onImageUpload}
                    onGalleryImageMaskPrepare={onGalleryImageMaskPrepare}
                    onImageMaskApply={onImageMaskApply}
                    onAudioUpload={onAudioUpload}
                    onVideoUpload={onVideoUpload}
                    onFileUpload={onFileUpload}
                    onThreeDUpload={onThreeDUpload}
                    loraBrowserFor={loraBrowserFor}
                  />
                ))}
              </div>
            </section>
          );
        }
        return (
          <ClassicDashboardInputControl
            key={item.id}
            control={item.control}
            inputIndex={inputIndex}
            inputValues={inputValues}
            seedModes={seedModes}
            onSeedModeChange={onSeedModeChange}
            onChange={onChange}
            onImageUpload={onImageUpload}
            onGalleryImageMaskPrepare={onGalleryImageMaskPrepare}
            onImageMaskApply={onImageMaskApply}
            onAudioUpload={onAudioUpload}
            onVideoUpload={onVideoUpload}
            onFileUpload={onFileUpload}
            onThreeDUpload={onThreeDUpload}
            loraBrowserFor={loraBrowserFor}
          />
        );
      })}
    </>
  );
}

function ClassicDashboardInputControl({
  control,
  inputIndex,
  inputValues,
  seedModes,
  onSeedModeChange,
  grouped = false,
  onChange,
  onImageUpload,
  onGalleryImageMaskPrepare,
  onImageMaskApply,
  onAudioUpload,
  onVideoUpload,
  onFileUpload,
  onThreeDUpload,
  loraBrowserFor,
}: {
  control: DashboardControlDef;
  inputIndex: Map<string, WorkflowInputDef>;
  inputValues: Record<string, unknown>;
  seedModes: Record<string, SeedMode>;
  onSeedModeChange: (inputId: string, mode: SeedMode) => void;
  grouped?: boolean;
  onChange: (id: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onGalleryImageMaskPrepare: (inputId: string, galleryItemId: string) => Promise<string>;
  onImageMaskApply: (sourceAssetId: string, mask: Blob) => Promise<string>;
  onAudioUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onVideoUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onFileUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onThreeDUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  loraBrowserFor?: (control: DashboardControlDef, input: WorkflowInputDef) => LoraBrowserControlProps | undefined;
}) {
  if (control.type === "note") {
    return (
      <section className="dashboard-note-card">
        <h3>{control.label}</h3>
        <p>{control.description || "No note text added yet."}</p>
      </section>
    );
  }
  const inputId = control.input_id ?? control.id;
  const input = control.type === "api_credential"
    ? credentialInputForControl(control)
    : inputIndex.get(inputId);
  if (!input) return null;
  const value = inputValues[input.id];

  return (
    <DashboardInputControl
      control={control}
      input={input}
      value={value}
      hideLabel={grouped}
      loraBrowser={loraBrowserFor?.(control, input)}
      seedMode={seedModes[input.id]}
      onSeedModeChange={(mode) => onSeedModeChange(input.id, mode)}
      onChange={(v) => onChange(input.id, v)}
      onImageUpload={(file) => onImageUpload(input.id, file)}
      onGalleryImageMaskPrepare={onGalleryImageMaskPrepare}
      onImageMaskApply={onImageMaskApply}
      onAudioUpload={(file, onProgress, signal) => onAudioUpload(input.id, file, onProgress, signal)}
      onVideoUpload={(file, onProgress, signal) => onVideoUpload(input.id, file, onProgress, signal)}
      onFileUpload={onFileUpload}
      onThreeDUpload={(file, onProgress, signal) => onThreeDUpload(input.id, file, onProgress, signal)}
    />
  );
}

function credentialInputForControl(control: DashboardControlDef): WorkflowInputDef {
  return {
    id: control.input_id ?? control.id,
    label: control.label || "ComfyUI Account API Key",
    control: "api_credential",
    binding: { node_id: "", input_name: "" },
    default: {
      kind: "api_key_ref",
      provider: control.provider ?? "comfy_org",
      secret_ref: control.secret_ref ?? "api-key:comfy_org",
    },
    validation: {},
  };
}

export function FallbackInputs({
  inputs,
  inputValues,
  onChange,
}: {
  inputs: WorkflowInputDef[];
  inputValues: Record<string, unknown>;
  onChange: (id: string, value: unknown) => void;
}) {
  if (inputs.length === 0) {
    return (
      <>
        <label className="field-group">
          <span>Prompt</span>
          <textarea
            value={typeof inputValues["prompt"] === "string" ? inputValues["prompt"] : ""}
            onChange={(event) => onChange("prompt", event.target.value)}
            rows={7}
          />
        </label>
        <div className="input-grid">
          <label className="field-group">
            <span>Variation ID</span>
            <input
              type="number"
              min={0}
              value={typeof inputValues["seed"] === "number" ? inputValues["seed"] : 5}
              onChange={(event) => onChange("seed", Number(event.target.value))}
            />
          </label>
          <label className="field-group">
            <span>Width</span>
            <input
              type="range"
              min={256}
              max={1024}
              step={64}
              value={typeof inputValues["width"] === "number" ? inputValues["width"] : 512}
              onChange={(event) => onChange("width", Number(event.target.value))}
            />
            <small>{typeof inputValues["width"] === "number" ? inputValues["width"] : 512}px</small>
          </label>
          <label className="field-group">
            <span>Height</span>
            <input
              type="range"
              min={256}
              max={1024}
              step={64}
              value={typeof inputValues["height"] === "number" ? inputValues["height"] : 512}
              onChange={(event) => onChange("height", Number(event.target.value))}
            />
            <small>{typeof inputValues["height"] === "number" ? inputValues["height"] : 512}px</small>
          </label>
        </div>
      </>
    );
  }

  return (
    <>
      {inputs.map((input) => {
        const value = inputValues[input.id];
        return (
          <label key={input.id} className="field-group">
            <span>{input.label}</span>
            <input
              type="text"
              value={typeof value === "string" || typeof value === "number" ? String(value) : ""}
              onChange={(event) => onChange(input.id, event.target.value)}
            />
          </label>
        );
      })}
    </>
  );
}
