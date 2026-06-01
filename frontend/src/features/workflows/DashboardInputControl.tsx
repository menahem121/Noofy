import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import { Box, DownloadCloud, File as FileIcon, FileAudio, ImagePlus, RefreshCw, Trash2, Video, X } from "lucide-react";

import {
  dashboardAssetMediaUrl,
  fetchAssetBlobUrl,
  fetchAssetMetadata,
  galleryContentUrlById,
  updateExternalApiKey,
  type DashboardControlDef,
  type DashboardAssetMetadata,
  type WorkflowInputDef,
  type UploadProgress,
} from "../../lib/api/noofyApi";
import type { ApiKeyProviderId } from "../../lib/api/noofyApi";
import { audioMetadataLabel, fileMetadataLabel, isGalleryMediaReference, isUploadedAssetValue, videoMetadataLabel } from "./media";
import { ThreeDViewer } from "../three-d/ThreeDViewer";
import { GalleryPickerModal } from "./GalleryPickerModal";

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
  onFileUpload?: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onThreeDUpload?: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
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
  onFileUpload = async () => undefined,
  onThreeDUpload = async () => undefined,
}: DashboardInputControlProps) {
  const label = control.label || input.label;
  const description = control.description;
  const validation = input.validation ?? {};

  if (variant === "classic") {
    if (hideLabel) {
      return (
        <label className={`field-group field-group--grouped-child${control.type === "toggle" ? " field-group--inline" : ""}`}>
          {description ? <small>{description}</small> : null}
          {renderControl(control, input, value, validation, disabled, variant, onChange, onImageUpload, onAudioUpload, onVideoUpload, onFileUpload, onThreeDUpload, loraBrowser)}
        </label>
      );
    }

    return (
      <label className={`field-group${control.type === "toggle" ? " field-group--inline" : ""}`}>
        {control.type === "toggle" ? (
          <>
            {renderControl(control, input, value, validation, disabled, variant, onChange, onImageUpload, onAudioUpload, onVideoUpload, onFileUpload, onThreeDUpload, loraBrowser)}
            <span>{label}</span>
            {description ? <small>{description}</small> : null}
          </>
        ) : (
          <>
            <span>{label}</span>
            {description ? <small>{description}</small> : null}
            {renderControl(control, input, value, validation, disabled, variant, onChange, onImageUpload, onAudioUpload, onVideoUpload, onFileUpload, onThreeDUpload, loraBrowser)}
          </>
        )}
      </label>
    );
  }

  return <>{renderControl(control, input, value, validation, disabled, variant, onChange, onImageUpload, onAudioUpload, onVideoUpload, onFileUpload, onThreeDUpload, loraBrowser)}</>;
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
  onFileUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>,
  onThreeDUpload: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>,
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
      return (
        <AssetImageInput
          value={value}
          disabled={disabled}
          variant={variant}
          galleryEnabled
          validation={validation}
          onChange={onChange}
          onImageUpload={onImageUpload}
        />
      );

    case "load_image_mask":
      return (
        <AssetImageInput
          value={value}
          disabled={disabled}
          variant={variant}
          galleryEnabled={false}
          validation={validation}
          onChange={onChange}
          onImageUpload={onImageUpload}
        />
      );

    case "load_audio":
      return (
        <AssetAudioInput
          value={value}
          disabled={disabled}
          variant={variant}
          validation={validation}
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
          validation={validation}
          onChange={onChange}
          onVideoUpload={onVideoUpload}
        />
      );

    case "load_file":
      return (
        <AssetFileInput
          inputId={input.id}
          value={value}
          validation={validation}
          disabled={disabled}
          variant={variant}
          onChange={onChange}
          onFileUpload={onFileUpload}
        />
      );

    case "load_3d":
      return <AssetThreeDInput value={value} validation={validation} disabled={disabled} onChange={onChange} onThreeDUpload={onThreeDUpload} />;

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

const IMAGE_ACCEPTED_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".gif"];
const AUDIO_ACCEPTED_EXTENSIONS = [".wav", ".mp3", ".flac", ".ogg", ".m4a"];
const VIDEO_ACCEPTED_EXTENSIONS = [".mp4", ".mov", ".webm", ".mkv"];
const THREE_D_ACCEPTED_EXTENSIONS = [".glb", ".gltf", ".obj", ".stl", ".fbx", ".ply"];

function MediaSourceChooser({
  icon,
  uploadLabel,
  galleryLabel,
  uploadingLabel,
  uploadProgress,
  disabled,
  uploading = false,
  galleryEnabled = true,
  onUpload,
  onGallery,
}: {
  icon: ReactNode;
  uploadLabel: string;
  galleryLabel: string;
  uploadingLabel?: string;
  uploadProgress?: UploadProgress | null;
  disabled: boolean;
  uploading?: boolean;
  galleryEnabled?: boolean;
  onUpload: () => void;
  onGallery: () => void;
}) {
  if (!galleryEnabled) {
    return (
      <button className="dashboard-media-source dashboard-media-source--single" type="button" disabled={disabled || uploading} onClick={onUpload}>
        {icon}
        <span>{uploading ? uploadingLabel ?? uploadLabel : uploadLabel}</span>
        {uploading && uploadProgress ? <small>{uploadProgress.percent ?? 0}%</small> : null}
      </button>
    );
  }

  return (
    <div className="dashboard-media-source-split">
      <button className="dashboard-media-source dashboard-media-source--upload" type="button" disabled={disabled || uploading} onClick={onUpload}>
        {icon}
        <span>{uploading ? uploadingLabel ?? uploadLabel : "Upload from computer"}</span>
        {uploading && uploadProgress ? <small>{uploadProgress.percent ?? 0}%</small> : null}
      </button>
      <button className="dashboard-media-source dashboard-media-source--gallery" type="button" disabled={disabled || uploading} onClick={onGallery}>
        <ImagePlus size={22} aria-hidden="true" />
        <span>{galleryLabel}</span>
      </button>
    </div>
  );
}

function pickerAcceptedExtensions(validation: Record<string, unknown>, fallback: string[]): string[] {
  const extensions = Array.isArray(validation.accepted_extensions)
    ? validation.accepted_extensions.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
  return extensions.length > 0 ? extensions : fallback;
}

function pickerAcceptedMimeTypes(validation: Record<string, unknown>): string[] {
  return Array.isArray(validation.accepted_mime_types)
    ? validation.accepted_mime_types.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
}

function GallerySelectedActions({
  disabled,
  onReplace,
  onRemove,
}: {
  disabled: boolean;
  onReplace: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="dashboard-media-actions">
      <button className="secondary-button secondary-button--small" type="button" disabled={disabled} onClick={onReplace}>
        <RefreshCw size={14} aria-hidden="true" />
        Replace
      </button>
      <button className="secondary-button secondary-button--small" type="button" disabled={disabled} onClick={onRemove}>
        <Trash2 size={14} aria-hidden="true" />
        Remove
      </button>
    </div>
  );
}

function AssetImageInput({
  value,
  disabled,
  variant,
  galleryEnabled,
  validation,
  onChange,
  onImageUpload,
}: {
  value: unknown;
  disabled: boolean;
  variant: DashboardInputControlVariant;
  galleryEnabled: boolean;
  validation: Record<string, unknown>;
  onChange: (value: unknown) => void;
  onImageUpload: (file: File) => Promise<void>;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [galleryOpen, setGalleryOpen] = useState(false);
  const [replaceChoiceOpen, setReplaceChoiceOpen] = useState(false);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [originalFilename, setOriginalFilename] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const assetId = isUploadedAssetValue(value) ? value : null;
  const galleryReference = galleryEnabled && isGalleryMediaReference(value) && value.kind === "image" ? value : null;
  const hasSelection = Boolean(assetId || galleryReference);

  useEffect(() => {
    setBlobUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });

    if (galleryReference) {
      setMissing(false);
      return undefined;
    }

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
    if (!disabled) {
      setReplaceChoiceOpen(false);
      inputRef.current?.click();
    }
  }

  function handleSurfaceClick(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (hasSelection) setReplaceChoiceOpen(galleryEnabled);
    if (!galleryEnabled) openFilePicker();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    event.stopPropagation();
    if (hasSelection) setReplaceChoiceOpen(galleryEnabled);
    if (!galleryEnabled) openFilePicker();
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void onImageUpload(file);
  }

  function removeImage() {
    if (disabled) return;
    onChange(null);
    setOriginalFilename(null);
    setMissing(false);
    setReplaceChoiceOpen(false);
  }

  const stateClass = missing
    ? "dashboard-image-input--missing"
    : blobUrl || galleryReference
      ? "dashboard-image-input--preview"
      : hasSelection
        ? "dashboard-image-input--loading"
        : "dashboard-image-input--empty";
  const galleryImageUrl = galleryReference ? galleryContentUrlById(galleryReference.gallery_item_id) : null;
  const selectedFilename = galleryReference?.filename ?? originalFilename ?? assetId;

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
      {galleryOpen ? (
        <GalleryPickerModal
          kind="image"
          acceptedExtensions={pickerAcceptedExtensions(validation, IMAGE_ACCEPTED_EXTENSIONS)}
          acceptedMimeTypes={pickerAcceptedMimeTypes(validation)}
          onClose={() => setGalleryOpen(false)}
          onSelect={(reference) => {
            setReplaceChoiceOpen(false);
            onChange(reference);
          }}
        />
      ) : null}
      {blobUrl || galleryImageUrl || missing || hasSelection ? (
      <button
        className="dashboard-image-input__surface"
        type="button"
        disabled={disabled}
        onClick={handleSurfaceClick}
        onKeyDown={handleKeyDown}
      >
        {blobUrl || galleryImageUrl ? (
          <>
            <img src={blobUrl ?? galleryImageUrl ?? ""} alt={galleryReference ? "Gallery input" : "Uploaded input"} className="dashboard-image-input__preview" />
            <span className="dashboard-image-input__overlay">
              <span className="dashboard-image-input__filename">{selectedFilename}</span>
              <span className="dashboard-image-input__action">Replace image</span>
            </span>
          </>
        ) : missing ? (
          <>
            <span className="dashboard-image-input__icon" aria-hidden="true">
              <ImagePlus size={24} />
            </span>
            <span className="dashboard-image-input__title">Image could not be loaded</span>
            <span className="dashboard-image-input__hint">Upload from computer</span>
          </>
        ) : hasSelection ? (
          <>
            <span className="dashboard-image-input__icon" aria-hidden="true">
              <ImagePlus size={24} />
            </span>
            <span className="dashboard-image-input__title">Loading image...</span>
            <span className="dashboard-image-input__hint">{selectedFilename}</span>
          </>
        ) : null}
      </button>
      ) : (
        <MediaSourceChooser
          icon={<ImagePlus size={24} aria-hidden="true" />}
          uploadLabel="Upload from computer"
          galleryLabel="Choose from Gallery"
          disabled={disabled}
          galleryEnabled={galleryEnabled}
          onUpload={openFilePicker}
          onGallery={() => setGalleryOpen(true)}
        />
      )}
      {hasSelection ? <GallerySelectedActions disabled={disabled} onReplace={() => (galleryEnabled ? setReplaceChoiceOpen((current) => !current) : openFilePicker())} onRemove={removeImage} /> : null}
      {hasSelection && replaceChoiceOpen ? (
        <MediaSourceChooser
          icon={<ImagePlus size={22} aria-hidden="true" />}
          uploadLabel="Upload from computer"
          galleryLabel="Choose from Gallery"
          disabled={disabled}
          galleryEnabled={galleryEnabled}
          onUpload={openFilePicker}
          onGallery={() => setGalleryOpen(true)}
        />
      ) : null}
    </div>
  );
}

function AssetAudioInput({
  value,
  disabled,
  variant,
  validation,
  onChange,
  onAudioUpload,
}: {
  value: unknown;
  disabled: boolean;
  variant: DashboardInputControlVariant;
  validation: Record<string, unknown>;
  onChange: (value: unknown) => void;
  onAudioUpload: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const [galleryOpen, setGalleryOpen] = useState(false);
  const [replaceChoiceOpen, setReplaceChoiceOpen] = useState(false);
  const [metadata, setMetadata] = useState<DashboardAssetMetadata | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const assetId = isUploadedAssetValue(value) ? value : null;
  const galleryReference = isGalleryMediaReference(value) && value.kind === "audio" ? value : null;
  const hasSelection = Boolean(assetId || galleryReference);
  const mediaUrl = assetId ? dashboardAssetMediaUrl(assetId) : galleryReference ? galleryContentUrlById(galleryReference.gallery_item_id) : null;

  useEffect(() => () => uploadAbortRef.current?.abort(), []);

  useEffect(() => {
    setMetadata(null);
    setDuration(null);
    setError(null);
    if (galleryReference) {
      setDuration(galleryReference.duration_seconds ?? null);
      return;
    }
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
  }, [assetId, galleryReference]);

  function openFilePicker() {
    if (!disabled && !uploading) {
      setReplaceChoiceOpen(false);
      inputRef.current?.click();
    }
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
    setReplaceChoiceOpen(false);
  }

  const filename = galleryReference?.filename ?? metadata?.original_filename ?? assetId ?? "Audio file";
  const extension = galleryReference?.extension ?? metadata?.format ?? extensionFromFilename(filename);
  const mimeType = galleryReference?.mime_type ?? metadata?.content_type;
  const size = galleryReference?.size_bytes ?? metadata?.size;

  return (
    <div className={`dashboard-audio-input dashboard-audio-input--${variant}${hasSelection ? " dashboard-audio-input--selected" : ""}`}>
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
      {galleryOpen ? (
        <GalleryPickerModal
          kind="audio"
          acceptedExtensions={pickerAcceptedExtensions(validation, AUDIO_ACCEPTED_EXTENSIONS)}
          acceptedMimeTypes={pickerAcceptedMimeTypes(validation)}
          onClose={() => setGalleryOpen(false)}
          onSelect={(reference) => {
            setReplaceChoiceOpen(false);
            onChange(reference);
          }}
        />
      ) : null}
      {hasSelection && mediaUrl ? (
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
            <strong>{filename}</strong>
            <span>{audioMetadataLabel(extension, mimeType, size, duration, "Audio file")}</span>
          </div>
          <GallerySelectedActions disabled={disabled || uploading} onReplace={() => setReplaceChoiceOpen((current) => !current)} onRemove={removeAudio} />
        </div>
      ) : (
        <MediaSourceChooser
          icon={<FileAudio size={22} aria-hidden="true" />}
          uploadLabel="Upload from computer"
          uploadingLabel="Uploading audio..."
          galleryLabel="Choose from Gallery"
          uploadProgress={uploadProgress}
          disabled={disabled}
          uploading={uploading}
          onUpload={openFilePicker}
          onGallery={() => setGalleryOpen(true)}
        />
      )}
      {hasSelection && replaceChoiceOpen ? (
        <MediaSourceChooser
          icon={<FileAudio size={22} aria-hidden="true" />}
          uploadLabel="Upload from computer"
          galleryLabel="Choose from Gallery"
          disabled={disabled}
          uploading={uploading}
          onUpload={openFilePicker}
          onGallery={() => setGalleryOpen(true)}
        />
      ) : null}
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
  validation,
  onChange,
  onVideoUpload,
}: {
  value: unknown;
  disabled: boolean;
  variant: DashboardInputControlVariant;
  validation: Record<string, unknown>;
  onChange: (value: unknown) => void;
  onVideoUpload: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const [galleryOpen, setGalleryOpen] = useState(false);
  const [replaceChoiceOpen, setReplaceChoiceOpen] = useState(false);
  const [metadata, setMetadata] = useState<DashboardAssetMetadata | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [width, setWidth] = useState<number | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const assetId = isUploadedAssetValue(value) ? value : null;
  const galleryReference = isGalleryMediaReference(value) && value.kind === "video" ? value : null;
  const hasSelection = Boolean(assetId || galleryReference);
  const mediaUrl = assetId ? dashboardAssetMediaUrl(assetId) : galleryReference ? galleryContentUrlById(galleryReference.gallery_item_id) : null;

  useEffect(() => () => uploadAbortRef.current?.abort(), []);

  useEffect(() => {
    setMetadata(null);
    setDuration(null);
    setWidth(null);
    setHeight(null);
    setError(null);
    if (galleryReference) {
      setDuration(galleryReference.duration_seconds ?? null);
      setWidth(galleryReference.width ?? null);
      setHeight(galleryReference.height ?? null);
      return;
    }
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
  }, [assetId, galleryReference]);

  function openFilePicker() {
    if (!disabled && !uploading) {
      setReplaceChoiceOpen(false);
      inputRef.current?.click();
    }
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
    setReplaceChoiceOpen(false);
  }

  const filename = galleryReference?.filename ?? metadata?.original_filename ?? assetId ?? "Video file";
  const extension = galleryReference?.extension ?? metadata?.format ?? extensionFromFilename(filename);
  const mimeType = galleryReference?.mime_type ?? metadata?.content_type;
  const size = galleryReference?.size_bytes ?? metadata?.size;
  const fps = galleryReference?.fps ?? metadata?.fps;

  return (
    <div className={`dashboard-video-input dashboard-video-input--${variant}${hasSelection ? " dashboard-video-input--selected" : ""}`}>
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
      {galleryOpen ? (
        <GalleryPickerModal
          kind="video"
          acceptedExtensions={pickerAcceptedExtensions(validation, VIDEO_ACCEPTED_EXTENSIONS)}
          acceptedMimeTypes={pickerAcceptedMimeTypes(validation)}
          onClose={() => setGalleryOpen(false)}
          onSelect={(reference) => {
            setReplaceChoiceOpen(false);
            onChange(reference);
          }}
        />
      ) : null}
      {hasSelection && mediaUrl ? (
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
            <strong>{filename}</strong>
            <span>{videoMetadataLabel(extension, mimeType, size, duration, width, height, fps, "Video file")}</span>
          </div>
          <GallerySelectedActions disabled={disabled || uploading} onReplace={() => setReplaceChoiceOpen((current) => !current)} onRemove={removeVideo} />
        </div>
      ) : (
        <MediaSourceChooser
          icon={<Video size={24} aria-hidden="true" />}
          uploadLabel="Upload from computer"
          uploadingLabel="Uploading video..."
          galleryLabel="Choose from Gallery"
          uploadProgress={uploadProgress}
          disabled={disabled}
          uploading={uploading}
          onUpload={openFilePicker}
          onGallery={() => setGalleryOpen(true)}
        />
      )}
      {hasSelection && replaceChoiceOpen ? (
        <MediaSourceChooser
          icon={<Video size={22} aria-hidden="true" />}
          uploadLabel="Upload from computer"
          galleryLabel="Choose from Gallery"
          disabled={disabled}
          uploading={uploading}
          onUpload={openFilePicker}
          onGallery={() => setGalleryOpen(true)}
        />
      ) : null}
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

function AssetFileInput({
  inputId,
  value,
  validation,
  disabled,
  variant,
  onChange,
  onFileUpload,
}: {
  inputId: string;
  value: unknown;
  validation: Record<string, unknown>;
  disabled: boolean;
  variant: DashboardInputControlVariant;
  onChange: (value: unknown) => void;
  onFileUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const [metadata, setMetadata] = useState<DashboardAssetMetadata | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const assetId = typeof value === "string" ? value : null;
  const accept = fileAcceptString(validation);

  useEffect(() => () => uploadAbortRef.current?.abort(), []);

  useEffect(() => {
    setMetadata(null);
    setError(null);
    if (!assetId) return;

    let canceled = false;
    fetchAssetMetadata(assetId)
      .then((result) => {
        if (!canceled) setMetadata(result);
      })
      .catch(() => {
        if (!canceled) setError("File metadata could not be loaded. Choose another file if needed.");
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
      await onFileUpload(inputId, file, setUploadProgress, abortController.signal);
      setUploadProgress(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (uploadAbortRef.current === abortController) uploadAbortRef.current = null;
      setUploading(false);
    }
  }

  function removeFile() {
    if (disabled || uploading) return;
    onChange(null);
    setMetadata(null);
    setError(null);
  }

  const extension = metadata?.extension ?? extensionFromFilename(metadata?.original_filename ?? assetId ?? "");

  return (
    <div className={`dashboard-file-input dashboard-file-input--${variant}${assetId ? " dashboard-file-input--selected" : ""}`}>
      <input
        ref={inputRef}
        className="dashboard-image-input__file"
        type="file"
        accept={accept}
        disabled={disabled || uploading}
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => void handleFileChange(event)}
      />
      {assetId ? (
        <div className="dashboard-file-input__selected">
          <FileIcon size={24} aria-hidden="true" />
          <div className="dashboard-file-input__meta">
            <strong>{metadata?.original_filename ?? assetId}</strong>
            <span>{fileMetadataLabel(extension, metadata?.content_type, metadata?.size, "File")}</span>
          </div>
          <div className="dashboard-file-input__actions">
            <button className="secondary-button secondary-button--small" type="button" disabled={disabled || uploading} onClick={openFilePicker}>
              <RefreshCw size={14} aria-hidden="true" />
              Replace
            </button>
            <button className="secondary-button secondary-button--small" type="button" disabled={disabled || uploading} onClick={removeFile}>
              <Trash2 size={14} aria-hidden="true" />
              Remove
            </button>
          </div>
        </div>
      ) : (
        <button className="dashboard-file-input__empty" type="button" disabled={disabled || uploading} onClick={openFilePicker}>
          <FileIcon size={22} aria-hidden="true" />
          <span>{uploading ? "Uploading file..." : "Click here to upload file"}</span>
          {uploading && uploadProgress ? <small>{uploadProgress.percent ?? 0}%</small> : null}
        </button>
      )}
      {uploading && uploadProgress ? (
        <div className="dashboard-file-input__progress-row">
          <div className="dashboard-file-input__progress" aria-label="File upload progress">
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

function fileAcceptString(validation: Record<string, unknown>): string {
  const extensions = Array.isArray(validation.accepted_extensions)
    ? validation.accepted_extensions.filter((item): item is string => typeof item === "string" && item.trim().length > 0).map((item) => item.trim())
    : [];
  const mimeTypes = Array.isArray(validation.accepted_mime_types)
    ? validation.accepted_mime_types.filter((item): item is string => typeof item === "string" && item.trim().length > 0).map((item) => item.trim())
    : [];
  return [...extensions, ...mimeTypes].join(",");
}

function AssetThreeDInput({
  value,
  validation,
  disabled,
  onChange,
  onThreeDUpload,
}: {
  value: unknown;
  validation: Record<string, unknown>;
  disabled: boolean;
  onChange: (value: unknown) => void;
  onThreeDUpload: (file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [galleryOpen, setGalleryOpen] = useState(false);
  const [replaceChoiceOpen, setReplaceChoiceOpen] = useState(false);
  const [metadata, setMetadata] = useState<DashboardAssetMetadata | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const assetId = isUploadedAssetValue(value) ? value : null;
  const galleryReference = isGalleryMediaReference(value) && value.kind === "3d" ? value : null;
  const hasSelection = Boolean(assetId || galleryReference);
  const modelUrl = assetId ? dashboardAssetMediaUrl(assetId) : galleryReference ? galleryContentUrlById(galleryReference.gallery_item_id) : null;

  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    setMetadata(null);
    setError(null);
    if (galleryReference) return;
    if (!assetId) return;
    let canceled = false;
    fetchAssetMetadata(assetId).then((result) => { if (!canceled) setMetadata(result); }).catch(() => {
      if (!canceled) setError("3D model metadata could not be loaded.");
    });
    return () => { canceled = true; };
  }, [assetId, galleryReference]);

  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setReplaceChoiceOpen(false);
    abortRef.current = new AbortController();
    setError(null);
    setProgress({ loaded: 0, total: file.size || null, percent: 0 });
    try {
      await onThreeDUpload(file, setProgress, abortRef.current.signal);
      setProgress(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "3D model upload failed.");
    } finally {
      abortRef.current = null;
    }
  }

  function openFilePicker() {
    if (!disabled && !progress) {
      setReplaceChoiceOpen(false);
      inputRef.current?.click();
    }
  }

  function removeModel() {
    if (disabled || progress) return;
    onChange(null);
    setMetadata(null);
    setError(null);
    setReplaceChoiceOpen(false);
  }

  const filename = galleryReference?.filename ?? metadata?.original_filename ?? assetId ?? "3D model";
  const extension = galleryReference?.extension ?? metadata?.extension;
  const mimeType = galleryReference?.mime_type ?? metadata?.content_type;
  const size = galleryReference?.size_bytes ?? metadata?.size;
  return (
    <div className="dashboard-three-d-input">
      <input ref={inputRef} className="dashboard-image-input__file" type="file" accept=".glb,.gltf,.obj,.stl,.fbx,.ply" disabled={disabled || Boolean(progress)} onChange={(event) => void choose(event)} />
      {galleryOpen ? (
        <GalleryPickerModal
          kind="3d"
          acceptedExtensions={pickerAcceptedExtensions(validation, THREE_D_ACCEPTED_EXTENSIONS)}
          acceptedMimeTypes={pickerAcceptedMimeTypes(validation)}
          onClose={() => setGalleryOpen(false)}
          onSelect={(reference) => {
            setReplaceChoiceOpen(false);
            onChange(reference);
          }}
        />
      ) : null}
      {hasSelection && modelUrl ? (
        <>
          <ThreeDViewer url={modelUrl} filename={filename} size={size} />
          <div className="dashboard-file-input__selected">
            <Box size={24} />
            <div className="dashboard-file-input__meta"><strong>{filename}</strong><span>{fileMetadataLabel(extension, mimeType, size, "3D model")}</span></div>
            <GallerySelectedActions disabled={disabled || Boolean(progress)} onReplace={() => setReplaceChoiceOpen((current) => !current)} onRemove={removeModel} />
          </div>
        </>
      ) : (
        <MediaSourceChooser
          icon={<Box size={22} aria-hidden="true" />}
          uploadLabel="Upload from computer"
          uploadingLabel="Uploading 3D model..."
          galleryLabel="Choose from Gallery"
          uploadProgress={progress}
          disabled={disabled}
          uploading={Boolean(progress)}
          onUpload={openFilePicker}
          onGallery={() => setGalleryOpen(true)}
        />
      )}
      {hasSelection && replaceChoiceOpen ? (
        <MediaSourceChooser
          icon={<Box size={22} aria-hidden="true" />}
          uploadLabel="Upload from computer"
          galleryLabel="Choose from Gallery"
          disabled={disabled}
          uploading={Boolean(progress)}
          onUpload={openFilePicker}
          onGallery={() => setGalleryOpen(true)}
        />
      ) : null}
      {progress ? <button className="secondary-button secondary-button--small" type="button" onClick={() => abortRef.current?.abort()}><X size={14} />Cancel upload</button> : null}
      {error ? <small className="field-error">{error}</small> : null}
    </div>
  );
}

function extensionFromFilename(filename: string): string | null {
  const parts = filename.split(".");
  return parts.length > 1 ? `.${parts[parts.length - 1].toLowerCase()}` : null;
}
