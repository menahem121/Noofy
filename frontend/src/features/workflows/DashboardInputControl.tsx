import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { DownloadCloud, FileAudio, ImagePlus, RefreshCw, Trash2, Video, X } from "lucide-react";

import {
  dashboardAssetMediaUrl,
  fetchAssetBlobUrl,
  fetchAssetMetadata,
  updateExternalApiKey,
  type DashboardControlDef,
  type DashboardAssetMetadata,
  type WorkflowInputDef,
  type UploadProgress,
} from "../../lib/api/noofyApi";
import type { ApiKeyProviderId } from "../../lib/api/noofyApi";
import { audioMetadataLabel, videoMetadataLabel } from "./media";

type DashboardInputControlVariant = "classic" | "canvas";

export interface LoraBrowserControlProps {
  enabled: boolean;
  disabledReason?: string;
  extraOptions?: string[];
  onOpen: () => void;
}

interface DashboardInputControlProps {
  control: DashboardControlDef;
  input: WorkflowInputDef;
  value: unknown;
  disabled?: boolean;
  variant?: DashboardInputControlVariant;
  hideLabel?: boolean;
  loraBrowser?: LoraBrowserControlProps;
  onChange: (value: unknown) => void;
  onImageUpload: (file: File) => Promise<void>;
  onAudioUpload?: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onVideoUpload?: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
}

export function DashboardInputControl({
  control,
  input,
  value,
  disabled = false,
  variant = "classic",
  hideLabel = false,
  loraBrowser,
  onChange,
  onImageUpload,
  onAudioUpload = async () => undefined,
  onVideoUpload = async () => undefined,
}: DashboardInputControlProps) {
  const label = control.label || input.label;
  const description = control.description;
  const validation = input.validation ?? {};

  if (variant === "classic") {
    if (hideLabel) {
      return (
        <label className={`field-group field-group--grouped-child${control.type === "toggle" ? " field-group--inline" : ""}`}>
          {description ? <small>{description}</small> : null}
          {renderControl(control, input, value, validation, disabled, variant, onChange, onImageUpload, onAudioUpload, onVideoUpload, loraBrowser)}
        </label>
      );
    }

    return (
      <label className={`field-group${control.type === "toggle" ? " field-group--inline" : ""}`}>
        {control.type === "toggle" ? (
          <>
            {renderControl(control, input, value, validation, disabled, variant, onChange, onImageUpload, onAudioUpload, onVideoUpload, loraBrowser)}
            <span>{label}</span>
            {description ? <small>{description}</small> : null}
          </>
        ) : (
          <>
            <span>{label}</span>
            {description ? <small>{description}</small> : null}
            {renderControl(control, input, value, validation, disabled, variant, onChange, onImageUpload, onAudioUpload, onVideoUpload, loraBrowser)}
          </>
        )}
      </label>
    );
  }

  return <>{renderControl(control, input, value, validation, disabled, variant, onChange, onImageUpload, onAudioUpload, onVideoUpload, loraBrowser)}</>;
}

function renderControl(
  control: DashboardControlDef,
  input: WorkflowInputDef,
  value: unknown,
  validation: Record<string, unknown>,
  disabled: boolean,
  variant: DashboardInputControlVariant,
  onChange: (value: unknown) => void,
  onImageUpload: (file: File) => Promise<void>,
  onAudioUpload: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>,
  onVideoUpload: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>,
  loraBrowser?: LoraBrowserControlProps,
) {
  const inputClass = variant === "canvas" ? "canvas-widget-input" : undefined;
  const textareaClass = variant === "canvas" ? "canvas-widget-textarea" : undefined;
  const selectClass = variant === "canvas" ? "canvas-widget-select" : undefined;

  switch (control.type) {
    case "textarea":
      return (
        <textarea
          className={textareaClass}
          value={typeof value === "string" ? value : ""}
          rows={variant === "canvas" ? 4 : 5}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
      );

    case "string_field":
      return (
        <input
          className={inputClass}
          type="text"
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
      );

    case "int_field":
      return (
        <input
          className={inputClass}
          type="number"
          value={typeof value === "number" ? value : 0}
          min={typeof validation.min === "number" ? validation.min : undefined}
          max={typeof validation.max === "number" ? validation.max : undefined}
          step={typeof validation.step === "number" ? validation.step : 1}
          disabled={disabled}
          onChange={(event) => onChange(Number(event.target.value))}
        />
      );

    case "seed_widget":
      return (
        <input
          className={inputClass}
          type="number"
          min={0}
          value={typeof value === "number" ? value : 0}
          disabled={disabled}
          onChange={(event) => onChange(Number(event.target.value))}
        />
      );

    case "slider":
      return (
        <DashboardSliderControl
          control={control}
          input={input}
          value={value}
          validation={validation}
          disabled={disabled}
          variant={variant}
          onChange={onChange}
        />
      );

    case "toggle":
      return (
        <label className={variant === "canvas" ? "canvas-widget-toggle" : undefined}>
          <input
            type="checkbox"
            checked={Boolean(value)}
            disabled={disabled}
            onChange={(event) => onChange(event.target.checked)}
          />
          {variant === "canvas" ? <span>{Boolean(value) ? "On" : "Off"}</span> : null}
        </label>
      );

    case "load_image":
    case "load_image_mask":
      return (
        <AssetImageInput
          value={value}
          disabled={disabled}
          variant={variant}
          onImageUpload={onImageUpload}
        />
      );

    case "load_audio":
      return (
        <AssetAudioInput
          value={value}
          disabled={disabled}
          variant={variant}
          onChange={onChange}
          onAudioUpload={onAudioUpload}
        />
      );

    case "load_video":
      return (
        <AssetVideoInput
          value={value}
          disabled={disabled}
          variant={variant}
          onChange={onChange}
          onVideoUpload={onVideoUpload}
        />
      );

    case "select":
      return (
        <ModelSelect
          className={selectClass}
          value={value}
          validation={validation}
          disabled={disabled}
          onChange={onChange}
        />
      );

    case "lora_loader":
      return (
        <LoraLoaderInput
          className={selectClass}
          value={value}
          validation={validation}
          disabled={disabled}
          variant={variant}
          browser={loraBrowser}
          onChange={onChange}
        />
      );

    case "api_credential":
      return (
        <ApiCredentialInput
          control={control}
          value={value}
          disabled={disabled}
          variant={variant}
          onChange={onChange}
        />
      );

    default:
      return (
        <input
          className={inputClass}
          type="text"
          value={typeof value === "string" ? value : String(value ?? "")}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
      );
  }
}

function DashboardSliderControl({
  control,
  input,
  value,
  validation,
  disabled,
  variant,
  onChange,
}: {
  control: DashboardControlDef;
  input: WorkflowInputDef;
  value: unknown;
  validation: Record<string, unknown>;
  disabled: boolean;
  variant: DashboardInputControlVariant;
  onChange: (value: unknown) => void;
}) {
  const min = typeof validation.min === "number" ? validation.min : 0;
  const rawMax = typeof validation.max === "number" ? validation.max : 100;
  const step = typeof validation.step === "number" && validation.step > 0 ? validation.step : 1;
  const max = rawMax > min ? rawMax : min + step;
  const rawNumericValue = typeof value === "number" && Number.isFinite(value) ? value : min;
  const numericValue = normalizeSliderValue(rawNumericValue, min, max, step);
  const progress = max > min ? clamp(((numericValue - min) / (max - min)) * 100, 0, 100) : 0;
  const unit = sliderUnit(control, input, validation);
  const className = `dashboard-slider dashboard-slider--${variant}${
    variant === "canvas" ? " canvas-widget-slider" : ""
  }`;

  useEffect(() => {
    if (!disabled && !approximatelyEqual(rawNumericValue, numericValue)) {
      onChange(numericValue);
    }
  }, [disabled, numericValue, onChange, rawNumericValue]);

  return (
    <div
      className={className}
      style={{ "--dashboard-slider-progress": `${progress}%` } as CSSProperties}
    >
      <div className="dashboard-slider__control-row">
        <input
          className="dashboard-slider__input"
          type="range"
          min={min}
          max={max}
          step={step}
          value={numericValue}
          disabled={disabled}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        <output className="dashboard-slider__value">
          {formatSliderValue(numericValue, unit)}
        </output>
      </div>
      <div className="dashboard-slider__range-labels" aria-hidden="true">
        <span>{formatSliderValue(min, unit)}</span>
        <span>{formatSliderValue(max, unit)}</span>
      </div>
    </div>
  );
}

function sliderUnit(
  control: DashboardControlDef,
  input: WorkflowInputDef,
  validation: Record<string, unknown>,
): string {
  if (typeof validation.unit === "string") return validation.unit;

  const identity = `${control.id} ${control.label ?? ""} ${input.id} ${input.label}`.toLowerCase();
  return /\b(width|height)\b/.test(identity) ? "px" : "";
}

function formatSliderValue(value: number, unit: string): string {
  return `${value}${unit}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function normalizeSliderValue(value: number, min: number, max: number, step: number): number {
  const clamped = clamp(value, min, max);
  const snapped = min + Math.round((clamped - min) / step) * step;
  return roundSliderValue(clamp(snapped, min, max), step);
}

function roundSliderValue(value: number, step: number): number {
  const decimals = decimalPlaces(step);
  return decimals > 0 ? Number(value.toFixed(Math.min(decimals + 2, 12))) : value;
}

function decimalPlaces(value: number): number {
  const [, decimal = ""] = String(value).split(".");
  return decimal.length;
}

function approximatelyEqual(a: number, b: number): boolean {
  return Math.abs(a - b) < 1e-9;
}

function ModelSelect({
  className,
  value,
  validation,
  disabled,
  leadingOptions = [],
  extraOptions = [],
  onChange,
}: {
  className?: string;
  value: unknown;
  validation: Record<string, unknown>;
  disabled: boolean;
  leadingOptions?: string[];
  extraOptions?: string[];
  onChange: (value: unknown) => void;
}) {
  const rawSelectedValue = typeof value === "string" || typeof value === "number" ? String(value) : "";
  const selectedValue = rawSelectedValue || leadingOptions[0] || "";
  const options = mergeOptions(
    leadingOptions,
    Array.isArray(validation.options) ? (validation.options as string[]) : [],
    extraOptions,
    selectedValue,
  );
  return (
    <select
      className={className}
      value={selectedValue}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  );
}

function LoraLoaderInput({
  className,
  value,
  validation,
  disabled,
  variant,
  browser,
  onChange,
}: {
  className?: string;
  value: unknown;
  validation: Record<string, unknown>;
  disabled: boolean;
  variant: DashboardInputControlVariant;
  browser?: LoraBrowserControlProps;
  onChange: (value: unknown) => void;
}) {
  const buttonDisabled = disabled || !browser?.enabled;
  const reason = browser?.disabledReason;
  return (
    <div className={`lora-loader-control lora-loader-control--${variant}`}>
      <ModelSelect
        className={className}
        value={value}
        validation={validation}
        disabled={disabled}
        leadingOptions={["None"]}
        extraOptions={browser?.extraOptions}
        onChange={onChange}
      />
      <button
        className="secondary-button secondary-button--small lora-loader-control__browse"
        type="button"
        disabled={buttonDisabled}
        title={buttonDisabled ? reason : "Search and download LoRAs"}
        onClick={() => browser?.onOpen()}
      >
        <DownloadCloud size={14} aria-hidden="true" />
        Download more LoRAs
      </button>
    </div>
  );
}

function mergeOptions(
  leadingOptions: string[] = [],
  options: string[],
  extraOptions: string[] = [],
  selectedValue: string,
): string[] {
  const seen = new Set<string>();
  const merged: string[] = [];
  for (const option of [...leadingOptions, ...options, ...extraOptions, selectedValue]) {
    if (!option || seen.has(option)) continue;
    seen.add(option);
    merged.push(option);
  }
  return merged;
}

function ApiCredentialInput({
  control,
  value,
  disabled,
  variant,
  onChange,
}: {
  control: DashboardControlDef;
  value: unknown;
  disabled: boolean;
  variant: DashboardInputControlVariant;
  onChange: (value: unknown) => void;
}) {
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const provider = control.provider === "comfy_org" ? "comfy_org" : null;
  const metadata = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const lastFour = typeof metadata.last_four === "string" ? metadata.last_four : null;
  const configured = Boolean(metadata.configured || lastFour);
  const inputClass = variant === "canvas" ? "canvas-widget-input" : undefined;

  async function save() {
    if (!provider || !draft.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const result = await updateExternalApiKey(provider as ApiKeyProviderId, draft.trim());
      onChange({
        kind: "api_key_ref",
        provider,
        secret_ref: control.secret_ref ?? `api-key:${provider}`,
        configured: result.provider.configured,
        last_four: result.provider.last_four,
      });
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="api-credential-control">
      <input
        className={inputClass}
        type="password"
        value={draft}
        placeholder={configured ? `Saved key ending in ${lastFour ?? "****"}` : "Paste key"}
        disabled={disabled || saving || !provider}
        autoComplete="off"
        onChange={(event) => setDraft(event.target.value)}
      />
      <button
        className="secondary-button"
        type="button"
        disabled={disabled || saving || !provider || !draft.trim()}
        onClick={() => void save()}
      >
        {configured ? "Replace" : "Save"}
      </button>
      {error ? <small className="field-error">{error}</small> : null}
    </div>
  );
}

function AssetImageInput({
  value,
  disabled,
  variant,
  onImageUpload,
}: {
  value: unknown;
  disabled: boolean;
  variant: DashboardInputControlVariant;
  onImageUpload: (file: File) => Promise<void>;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [originalFilename, setOriginalFilename] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const assetId = typeof value === "string" ? value : null;
  const hasAsset = Boolean(assetId);

  useEffect(() => {
    setBlobUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });

    if (!assetId) {
      setMissing(false);
      return undefined;
    }

    let canceled = false;
    let objectUrl: string | null = null;
    fetchAssetBlobUrl(assetId)
      .then((url) => {
        if (canceled) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setBlobUrl(url);
      })
      .catch(() => {
        if (!canceled) setMissing(true);
      });

    return () => {
      canceled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [assetId]);

  useEffect(() => {
    if (!assetId) {
      setOriginalFilename(null);
      setMissing(false);
      return;
    }

    let canceled = false;
    setMissing(false);
    fetchAssetMetadata(assetId)
      .then((metadata) => {
        if (!canceled) setOriginalFilename(metadata.original_filename);
      })
      .catch(() => {
        if (!canceled) {
          setOriginalFilename(null);
        }
      });

    return () => {
      canceled = true;
    };
  }, [assetId]);

  function openFilePicker() {
    if (!disabled) inputRef.current?.click();
  }

  function handleSurfaceClick(event: MouseEvent<HTMLSpanElement>) {
    event.preventDefault();
    event.stopPropagation();
    openFilePicker();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLSpanElement>) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    event.stopPropagation();
    openFilePicker();
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void onImageUpload(file);
  }

  const stateClass = missing
    ? "dashboard-image-input--missing"
    : blobUrl
      ? "dashboard-image-input--preview"
      : hasAsset
        ? "dashboard-image-input--loading"
        : "dashboard-image-input--empty";

  return (
    <div className={`dashboard-image-input dashboard-image-input--${variant} ${stateClass}`}>
      <input
        ref={inputRef}
        className="dashboard-image-input__file"
        type="file"
        accept="image/*"
        disabled={disabled}
        tabIndex={-1}
        aria-hidden="true"
        onChange={handleFileChange}
      />
      <span
        className="dashboard-image-input__surface"
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        onClick={handleSurfaceClick}
        onKeyDown={handleKeyDown}
      >
        {blobUrl ? (
          <>
            <img src={blobUrl} alt="Uploaded input" className="dashboard-image-input__preview" />
            <span className="dashboard-image-input__overlay">
              <span className="dashboard-image-input__filename">{originalFilename ?? assetId}</span>
              <span className="dashboard-image-input__action">Click here to replace image</span>
            </span>
          </>
        ) : missing ? (
          <>
            <span className="dashboard-image-input__icon" aria-hidden="true">
              <ImagePlus size={24} />
            </span>
            <span className="dashboard-image-input__title">Image could not be loaded</span>
            <span className="dashboard-image-input__hint">Click here to upload an image</span>
          </>
        ) : assetId ? (
          <>
            <span className="dashboard-image-input__icon" aria-hidden="true">
              <ImagePlus size={24} />
            </span>
            <span className="dashboard-image-input__title">Loading image...</span>
            <span className="dashboard-image-input__hint">{originalFilename ?? assetId}</span>
          </>
        ) : (
          <>
            <span className="dashboard-image-input__icon" aria-hidden="true">
              <ImagePlus size={24} />
            </span>
            <span className="dashboard-image-input__title">Click here to upload an image</span>
          </>
        )}
      </span>
    </div>
  );
}

function AssetAudioInput({
  value,
  disabled,
  variant,
  onChange,
  onAudioUpload,
}: {
  value: unknown;
  disabled: boolean;
  variant: DashboardInputControlVariant;
  onChange: (value: unknown) => void;
  onAudioUpload: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const [metadata, setMetadata] = useState<DashboardAssetMetadata | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const assetId = typeof value === "string" ? value : null;
  const mediaUrl = assetId ? dashboardAssetMediaUrl(assetId) : null;

  useEffect(() => () => uploadAbortRef.current?.abort(), []);

  useEffect(() => {
    setMetadata(null);
    setDuration(null);
    setError(null);
    if (!assetId) {
      return;
    }

    let canceled = false;
    fetchAssetMetadata(assetId)
      .then((result) => {
        if (!canceled) {
          setMetadata(result);
          if (typeof result.duration_seconds === "number") setDuration(result.duration_seconds);
        }
      })
      .catch(() => {
        if (!canceled) setError("Audio metadata could not be loaded. Choose another file if playback fails.");
      });

    return () => {
      canceled = true;
    };
  }, [assetId]);

  function openFilePicker() {
    if (!disabled && !uploading) inputRef.current?.click();
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    setUploadProgress({ loaded: 0, total: file.size || null, percent: 0 });
    setError(null);
    const abortController = new AbortController();
    uploadAbortRef.current = abortController;
    try {
      await onAudioUpload(file, setUploadProgress, abortController.signal);
      setUploadProgress(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (uploadAbortRef.current === abortController) uploadAbortRef.current = null;
      setUploading(false);
    }
  }

  function removeAudio() {
    if (disabled || uploading) return;
    onChange(null);
    setMetadata(null);
    setDuration(null);
    setError(null);
  }

  return (
    <div className={`dashboard-audio-input dashboard-audio-input--${variant}${assetId ? " dashboard-audio-input--selected" : ""}`}>
      <input
        ref={inputRef}
        className="dashboard-image-input__file"
        type="file"
        accept="audio/wav,audio/x-wav,audio/mpeg,audio/mp3,audio/flac,audio/x-flac,audio/ogg,application/ogg,audio/mp4,audio/x-m4a,.wav,.mp3,.flac,.ogg,.m4a"
        disabled={disabled || uploading}
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => void handleFileChange(event)}
      />
      {assetId && mediaUrl ? (
        <div className="dashboard-audio-input__selected">
          <audio
            className="dashboard-audio-input__player"
            controls
            src={mediaUrl}
            preload="metadata"
            onLoadedMetadata={(event) => {
              const nextDuration = event.currentTarget.duration;
              if (Number.isFinite(nextDuration)) setDuration(nextDuration);
            }}
            onError={() => setError("Audio could not be loaded. Choose another file.")}
          />
          <div className="dashboard-audio-input__meta">
            <strong>{metadata?.original_filename ?? assetId}</strong>
            <span>{audioMetadataLabel(metadata?.format, metadata?.content_type, metadata?.size, duration, "Audio file")}</span>
          </div>
          <div className="dashboard-audio-input__actions">
            <button className="secondary-button secondary-button--small" type="button" disabled={disabled || uploading} onClick={openFilePicker}>
              <RefreshCw size={14} aria-hidden="true" />
              Replace
            </button>
            <button className="secondary-button secondary-button--small" type="button" disabled={disabled || uploading} onClick={removeAudio}>
              <Trash2 size={14} aria-hidden="true" />
              Remove
            </button>
          </div>
        </div>
      ) : (
        <button className="dashboard-audio-input__empty" type="button" disabled={disabled || uploading} onClick={openFilePicker}>
          <FileAudio size={22} aria-hidden="true" />
          <span>{uploading ? "Uploading audio..." : "Click here to upload audio"}</span>
          {uploading && uploadProgress ? <small>{uploadProgress.percent ?? 0}%</small> : null}
        </button>
      )}
      {uploading && uploadProgress ? (
        <div className="dashboard-audio-input__progress-row">
          <div className="dashboard-audio-input__progress" aria-label="Audio upload progress">
            <span style={{ width: `${uploadProgress.percent ?? 0}%` }} />
          </div>
          <button className="secondary-button secondary-button--small" type="button" onClick={() => uploadAbortRef.current?.abort()}>
            <X size={14} aria-hidden="true" />
            Cancel upload
          </button>
        </div>
      ) : null}
      {error ? <small className="field-error">{error}</small> : null}
    </div>
  );
}

function AssetVideoInput({
  value,
  disabled,
  variant,
  onChange,
  onVideoUpload,
}: {
  value: unknown;
  disabled: boolean;
  variant: DashboardInputControlVariant;
  onChange: (value: unknown) => void;
  onVideoUpload: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const [metadata, setMetadata] = useState<DashboardAssetMetadata | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [width, setWidth] = useState<number | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const assetId = typeof value === "string" ? value : null;
  const mediaUrl = assetId ? dashboardAssetMediaUrl(assetId) : null;

  useEffect(() => () => uploadAbortRef.current?.abort(), []);

  useEffect(() => {
    setMetadata(null);
    setDuration(null);
    setWidth(null);
    setHeight(null);
    setError(null);
    if (!assetId) return;

    let canceled = false;
    fetchAssetMetadata(assetId)
      .then((result) => {
        if (canceled) return;
        setMetadata(result);
        if (typeof result.duration_seconds === "number") setDuration(result.duration_seconds);
        if (typeof result.width === "number") setWidth(result.width);
        if (typeof result.height === "number") setHeight(result.height);
      })
      .catch(() => {
        if (!canceled) setError("Video metadata could not be loaded. Choose another file if playback fails.");
      });

    return () => {
      canceled = true;
    };
  }, [assetId]);

  function openFilePicker() {
    if (!disabled && !uploading) inputRef.current?.click();
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    setUploadProgress({ loaded: 0, total: file.size || null, percent: 0 });
    setError(null);
    const abortController = new AbortController();
    uploadAbortRef.current = abortController;
    try {
      await onVideoUpload(file, setUploadProgress, abortController.signal);
      setUploadProgress(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (uploadAbortRef.current === abortController) uploadAbortRef.current = null;
      setUploading(false);
    }
  }

  function removeVideo() {
    if (disabled || uploading) return;
    onChange(null);
    setMetadata(null);
    setDuration(null);
    setWidth(null);
    setHeight(null);
    setError(null);
  }

  return (
    <div className={`dashboard-video-input dashboard-video-input--${variant}${assetId ? " dashboard-video-input--selected" : ""}`}>
      <input
        ref={inputRef}
        className="dashboard-image-input__file"
        type="file"
        accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.webm,.mkv"
        disabled={disabled || uploading}
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => void handleFileChange(event)}
      />
      {assetId && mediaUrl ? (
        <div className="dashboard-video-input__selected">
          <video
            className="dashboard-video-input__player"
            controls
            src={mediaUrl}
            preload="metadata"
            onLoadedMetadata={(event) => {
              const player = event.currentTarget;
              if (Number.isFinite(player.duration)) setDuration(player.duration);
              if (player.videoWidth > 0) setWidth(player.videoWidth);
              if (player.videoHeight > 0) setHeight(player.videoHeight);
            }}
            onError={() => setError("Video could not be loaded. Choose another file.")}
          />
          <div className="dashboard-video-input__meta">
            <strong>{metadata?.original_filename ?? assetId}</strong>
            <span>{videoMetadataLabel(metadata?.format, metadata?.content_type, metadata?.size, duration, width, height, metadata?.fps, "Video file")}</span>
          </div>
          <div className="dashboard-video-input__actions">
            <button className="secondary-button secondary-button--small" type="button" disabled={disabled || uploading} onClick={openFilePicker}>
              <RefreshCw size={14} aria-hidden="true" />
              Replace
            </button>
            <button className="secondary-button secondary-button--small" type="button" disabled={disabled || uploading} onClick={removeVideo}>
              <Trash2 size={14} aria-hidden="true" />
              Remove
            </button>
          </div>
        </div>
      ) : (
        <button className="dashboard-video-input__empty" type="button" disabled={disabled || uploading} onClick={openFilePicker}>
          <Video size={24} aria-hidden="true" />
          <span>{uploading ? "Uploading video..." : "Click here to upload video"}</span>
          {uploading && uploadProgress ? <small>{uploadProgress.percent ?? 0}%</small> : null}
        </button>
      )}
      {uploading && uploadProgress ? (
        <div className="dashboard-video-input__progress-row">
          <div className="dashboard-video-input__progress" aria-label="Video upload progress">
            <span style={{ width: `${uploadProgress.percent ?? 0}%` }} />
          </div>
          <button className="secondary-button secondary-button--small" type="button" onClick={() => uploadAbortRef.current?.abort()}>
            <X size={14} aria-hidden="true" />
            Cancel upload
          </button>
        </div>
      ) : null}
      {error ? <small className="field-error">{error}</small> : null}
    </div>
  );
}
