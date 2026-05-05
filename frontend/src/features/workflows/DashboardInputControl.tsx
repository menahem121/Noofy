import { useEffect, useState } from "react";

import {
  fetchAssetBlobUrl,
  fetchAssetMetadata,
  type DashboardControlDef,
  type WorkflowInputDef,
} from "../../lib/api/noofyApi";

type DashboardInputControlVariant = "classic" | "canvas";

interface DashboardInputControlProps {
  control: DashboardControlDef;
  input: WorkflowInputDef;
  value: unknown;
  disabled?: boolean;
  variant?: DashboardInputControlVariant;
  onChange: (value: unknown) => void;
  onImageUpload: (file: File) => Promise<void>;
}

export function DashboardInputControl({
  control,
  input,
  value,
  disabled = false,
  variant = "classic",
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
            {renderControl(control, value, validation, disabled, variant, onChange, onImageUpload)}
            <span>{label}</span>
            {description ? <small>{description}</small> : null}
          </>
        ) : (
          <>
            <span>{label}</span>
            {description ? <small>{description}</small> : null}
            {renderControl(control, value, validation, disabled, variant, onChange, onImageUpload)}
          </>
        )}
      </label>
    );
  }

  return <>{renderControl(control, value, validation, disabled, variant, onChange, onImageUpload)}</>;
}

function renderControl(
  control: DashboardControlDef,
  value: unknown,
  validation: Record<string, unknown>,
  disabled: boolean,
  variant: DashboardInputControlVariant,
  onChange: (value: unknown) => void,
  onImageUpload: (file: File) => Promise<void>,
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
        <select
          className={selectClass}
          value={typeof value === "string" || typeof value === "number" ? String(value) : ""}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          {Array.isArray(validation.options)
            ? (validation.options as string[]).map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))
            : null}
        </select>
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
