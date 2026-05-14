import { useEffect, useState } from "react";
import { DownloadCloud } from "lucide-react";

import {
  fetchAssetBlobUrl,
  fetchAssetMetadata,
  updateExternalApiKey,
  type DashboardControlDef,
  type WorkflowInputDef,
} from "../../lib/api/noofyApi";
import type { ApiKeyProviderId } from "../../lib/api/noofyApi";

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
  loraBrowser?: LoraBrowserControlProps;
  onChange: (value: unknown) => void;
  onImageUpload: (file: File) => Promise<void>;
}

export function DashboardInputControl({
  control,
  input,
  value,
  disabled = false,
  variant = "classic",
  loraBrowser,
  onChange,
  onImageUpload,
}: DashboardInputControlProps) {
  const label = control.label || input.label;
  const description = control.description;
  const validation = input.validation ?? {};

  if (variant === "classic") {
    return (
      <label className={`field-group${control.type === "toggle" ? " field-group--inline" : ""}`}>
        {control.type === "toggle" ? (
          <>
            {renderControl(control, value, validation, disabled, variant, onChange, onImageUpload, loraBrowser)}
            <span>{label}</span>
            {description ? <small>{description}</small> : null}
          </>
        ) : (
          <>
            <span>{label}</span>
            {description ? <small>{description}</small> : null}
            {renderControl(control, value, validation, disabled, variant, onChange, onImageUpload, loraBrowser)}
          </>
        )}
      </label>
    );
  }

  return <>{renderControl(control, value, validation, disabled, variant, onChange, onImageUpload, loraBrowser)}</>;
}

function renderControl(
  control: DashboardControlDef,
  value: unknown,
  validation: Record<string, unknown>,
  disabled: boolean,
  variant: DashboardInputControlVariant,
  onChange: (value: unknown) => void,
  onImageUpload: (file: File) => Promise<void>,
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
        <div className={variant === "canvas" ? "canvas-widget-slider" : undefined}>
          <input
            type="range"
            min={typeof validation.min === "number" ? validation.min : 0}
            max={typeof validation.max === "number" ? validation.max : 100}
            step={typeof validation.step === "number" ? validation.step : 1}
            value={typeof value === "number" ? value : 0}
            disabled={disabled}
            onChange={(event) => onChange(Number(event.target.value))}
          />
          {variant === "canvas" ? (
            <span className="canvas-widget-slider__value">
              {typeof value === "number" ? value : 0}
              {typeof validation.unit === "string" ? validation.unit : ""}
            </span>
          ) : (
            <small>
              {typeof value === "number" ? value : 0}
              {typeof validation.unit === "string" ? validation.unit : "px"}
            </small>
          )}
        </div>
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

function ModelSelect({
  className,
  value,
  validation,
  disabled,
  extraOptions = [],
  onChange,
}: {
  className?: string;
  value: unknown;
  validation: Record<string, unknown>;
  disabled: boolean;
  extraOptions?: string[];
  onChange: (value: unknown) => void;
}) {
  const selectedValue = typeof value === "string" || typeof value === "number" ? String(value) : "";
  const options = mergeOptions(Array.isArray(validation.options) ? (validation.options as string[]) : [], extraOptions, selectedValue);
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

function mergeOptions(options: string[], extraOptions: string[], selectedValue: string): string[] {
  const seen = new Set<string>();
  const merged: string[] = [];
  for (const option of [...options, ...extraOptions, selectedValue]) {
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
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [originalFilename, setOriginalFilename] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const assetId = typeof value === "string" ? value : null;

  useEffect(() => {
    setBlobUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });

    if (variant !== "canvas" || !assetId) {
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
  }, [assetId, variant]);

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
          setMissing(true);
        }
      });

    return () => {
      canceled = true;
    };
  }, [assetId]);

  if (variant === "classic") {
    return (
      <>
        {assetId ? (
          <small className="field-group__hint">Loaded: {originalFilename ?? assetId}</small>
        ) : null}
        <input
          type="file"
          accept="image/*"
          disabled={disabled}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void onImageUpload(file);
          }}
        />
      </>
    );
  }

  return (
    <div className="canvas-widget-image-input">
      {blobUrl ? (
        <img src={blobUrl} alt="Uploaded input" className="canvas-widget-image-input__preview" />
      ) : missing ? (
        <span className="canvas-widget-image-input__missing">Image not found — please re-upload</span>
      ) : assetId ? (
        <span className="canvas-widget-image-input__hint">Loaded</span>
      ) : null}
      {assetId && !missing ? (
        <span className="canvas-widget-image-input__filename">{originalFilename ?? assetId}</span>
      ) : null}
      <input
        type="file"
        accept="image/*"
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void onImageUpload(file);
        }}
      />
    </div>
  );
}
