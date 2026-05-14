import { type CSSProperties, useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Circle, Download, Eye, EyeOff, FolderCog, FolderOpen, KeyRound, Loader2, RotateCcw, Search, Square, Trash2, Wrench, Zap } from "lucide-react";

import {
  bootstrapEngine,
  clearExternalApiKey,
  fetchApiKeySettings,
  fetchComfyUIUpdateStatus,
  fetchComfyUIVersions,
  fetchComfyUILaunchSettings,
  fetchModelFolderSettings,
  fetchRuntimeStatus,
  rebuildComfyUI,
  startEngine,
  stopEngine,
  updateExternalApiKey,
  updateComfyUI,
  updateComfyUILaunchSettings,
  updateModelFolderSettings,
  type ApiKeyProviderId,
  type ApiKeySettingsResponse,
  type ComfyUILaunchSettings,
  type ComfyUIUpdateStatus,
  type ComfyUIVersionsResponse,
  type ComfyUIVramMode,
  type ModelFolderSettings,
  type RuntimeStatus,
} from "../../lib/api/noofyApi";
import { openFolder, selectFolder } from "../../lib/folderDialogs";
import { useAppPreferences } from "../../lib/useAppPreferences";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { useRuntimeStatus } from "../app/RuntimeStatusProvider";

interface EngineSettingsState {
  loading: boolean;
  runtime: RuntimeStatus | null;
  versions: ComfyUIVersionsResponse | null;
  launchSettings: ComfyUILaunchSettings | null;
  apiSettings: ApiKeySettingsResponse | null;
  modelFolderSettings: ModelFolderSettings | null;
  apiDrafts: Record<ApiKeyProviderId, string>;
  apiVisible: Record<ApiKeyProviderId, boolean>;
  apiStatus: { provider: ApiKeyProviderId; message: string; ok: boolean } | null;
  modelFolderStatus: { message: string; ok: boolean } | null;
  selectedVersion: string;
  selectedVramMode: ComfyUIVramMode;
  updateStatus: ComfyUIUpdateStatus | null;
  action: string | null;
  error: string | null;
  actionResult: { label: string; status: string; ok: boolean } | null;
}

const DEFAULT_VRAM_MODE: ComfyUIVramMode = "normal";

const initialState: EngineSettingsState = {
  loading: true,
  runtime: null,
  versions: null,
  launchSettings: null,
  apiSettings: null,
  modelFolderSettings: null,
  apiDrafts: {
    hugging_face: "",
    civitai: "",
    comfy_org: "",
  },
  apiVisible: {
    hugging_face: false,
    civitai: false,
    comfy_org: false,
  },
  apiStatus: null,
  modelFolderStatus: null,
  selectedVersion: "latest",
  selectedVramMode: DEFAULT_VRAM_MODE,
  updateStatus: null,
  action: null,
  error: null,
  actionResult: null,
};

const ACTION_OK_STATUSES = new Set(["prepared", "already_prepared", "started", "already_running", "stopped", "completed", "repair_completed_started"]);
const ACTION_RESULT_LABELS: Record<string, string> = {
  prepared: "Engine environment prepared successfully.",
  already_prepared: "Environment is already prepared.",
  bootstrap_failed: "Preparation failed. Check the backend logs for details.",
  requirements_missing: "ComfyUI requirements.txt is missing.",
  python_missing: "Bootstrap Python executable not found.",
  python_not_prepared: "Runtime Python is not prepared.",
  dependency_check_failed: "Dependencies could not be verified after install.",
  not_configured: "No managed runtime environment is configured.",
  started: "Engine started.",
  already_running: "Engine is already running.",
  repair_completed_started: "The managed ComfyUI environment was repaired and started.",
  repair_failed_fallback_active: "Repair failed, so Noofy started a previous working engine.",
  repair_failed_no_fallback: "Repair failed and no fallback engine could be started.",
  repair_blocked: "Automatic repair is temporarily blocked for this ComfyUI version.",
  external_unreachable: "Engine is in external mode and not reachable. Start ComfyUI manually.",
  stopped: "Engine stopped.",
  completed: "ComfyUI was updated and validated successfully.",
  blocked: "ComfyUI updates are not available in this runtime mode.",
  failed: "ComfyUI update failed. The existing engine was left unchanged.",
  updated: "ComfyUI launch setting was saved.",
  unchanged: "ComfyUI launch setting is already selected.",
  updated_restarted: "ComfyUI launch setting was saved and the managed engine restarted.",
  updated_restart_failed: "ComfyUI launch setting was saved, but the managed engine could not restart.",
};

const VRAM_MODE_OPTIONS: Array<{ value: ComfyUIVramMode; label: string; description: string }> = [
  {
    value: "cpu",
    label: "CPU only",
    description: "Runs without GPU acceleration, usually the slowest",
  },
  {
    value: "novram",
    label: "No VRAM",
    description: "Extreme memory-saving mode, very slow but may still use GPU",
  },
  {
    value: "lowvram",
    label: "Low VRAM",
    description: "For smaller GPUs",
  },
  {
    value: "normal",
    label: "Normal VRAM",
    description: "Recommended",
  },
  {
    value: "highvram",
    label: "High VRAM",
    description: "Faster if you have lots of VRAM",
  },
];

const VRAM_MODE_INDEX_BY_VALUE = new Map(VRAM_MODE_OPTIONS.map((option, index) => [option.value, index]));
const API_PROVIDERS: Array<{ id: ApiKeyProviderId; label: string; fieldId: string }> = [
  { id: "hugging_face", label: "Hugging Face API Key", fieldId: "hugging-face-api-key" },
  { id: "civitai", label: "Civitai API Key", fieldId: "civitai-api-key" },
  { id: "comfy_org", label: "ComfyUI Account API Key", fieldId: "comfy-org-api-key" },
];

function vramModeOption(mode: ComfyUIVramMode) {
  return (
    VRAM_MODE_OPTIONS.find((option) => option.value === mode) ??
    VRAM_MODE_OPTIONS[VRAM_MODE_INDEX_BY_VALUE.get(DEFAULT_VRAM_MODE) ?? 0]
  );
}

function actionResultMessage(result: { label: string; status: string }) {
  if (result.label === "rebuild") {
    if (result.status === "completed") return "ComfyUI environment was rebuilt and validated successfully.";
    if (result.status === "failed") return "ComfyUI environment rebuild failed. The existing engine was left unchanged.";
    if (result.status === "blocked") return "ComfyUI environment rebuild is not available in this runtime mode.";
  }
  return ACTION_RESULT_LABELS[result.status] ?? result.status;
}

function runtimeFromActionResult(result: Record<string, unknown>): RuntimeStatus | null {
  const comfyui = result.comfyui;
  if (comfyui && typeof comfyui === "object" && "reachable" in comfyui) {
    return comfyui as RuntimeStatus;
  }
  return null;
}

function credentialStoreUnavailableMessage(apiSettings: ApiKeySettingsResponse | null) {
  const store = apiSettings?.credential_store;
  if (!store) return "Noofy could not access the operating system credential store.";
  const parts = [
    store.error,
    store.guidance,
    store.display_path ? `Storage: ${store.display_path}` : null,
  ].filter(Boolean);
  return parts.join(" ");
}

export function EngineSettingsPage({ onNavigate }: { onNavigate: (route: AppRouteId) => void }) {
  const [state, setState] = useState<EngineSettingsState>(initialState);
  const { viewMode, setViewMode } = useAppPreferences();
  const runtimeStatus = useRuntimeStatus();

  async function refresh() {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const [runtime, versions, launchSettings, apiSettings, modelFolderSettings] = await Promise.all([
        fetchRuntimeStatus(),
        fetchComfyUIVersions(),
        fetchComfyUILaunchSettings(),
        fetchApiKeySettings(),
        fetchModelFolderSettings(),
      ]);
      setState((current) => ({
        ...current,
        loading: false,
        runtime,
        versions,
        launchSettings,
        apiSettings,
        modelFolderSettings,
        selectedVramMode: launchSettings.vram_mode,
      }));
      runtimeStatus.setRuntimeFromResponse(runtime);
    } catch (error) {
      void runtimeStatus.refreshRuntime({ force: true, silent: false });
      setState((current) => ({
        ...current,
        loading: false,
        runtime: null,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function runAction(label: string, action: () => Promise<Record<string, unknown>>) {
    setState((current) => ({ ...current, action: label, error: null, actionResult: null }));
    let polling = label === "restart";
    const pollRepairStatus = polling
      ? (async () => {
          while (polling) {
            await new Promise((resolve) => setTimeout(resolve, 500));
            try {
              const updateStatus = await fetchComfyUIUpdateStatus();
              if (updateStatus.operation === "repair") {
                setState((current) => ({ ...current, updateStatus }));
              }
            } catch {
              // The start action result remains the source of truth.
            }
          }
        })()
      : null;
    try {
      const result = await action();
      runtimeStatus.setRuntimeFromResponse(runtimeFromActionResult(result));
      const status = typeof result.status === "string" ? result.status : "unknown";
      const ok = ACTION_OK_STATUSES.has(status);
      setState((current) => ({
        ...current,
        actionResult: { label, status, ok },
      }));
      await refresh();
    } catch (error) {
      runtimeStatus.markActionFailure(error);
      void runtimeStatus.refreshRuntime({ force: true, silent: false });
      setState((current) => ({
        ...current,
        action: null,
        error: error instanceof Error ? error.message : String(error),
      }));
    } finally {
      polling = false;
      await pollRepairStatus;
      setState((current) => ({ ...current, action: null }));
    }
  }

  async function restartEngine() {
    const runtime = state.runtime;
    if (runtime?.mode === "managed" && (runtime.managed_process_running || runtime.reachable)) {
      await stopEngine();
    }
    return startEngine();
  }

  async function runComfyUIUpdate() {
    await runComfyUIJob("update", state.selectedVersion, updateComfyUI);
  }

  async function saveVramModeChange() {
    if (!state.launchSettings || state.selectedVramMode === state.launchSettings.vram_mode) return;
    setState((current) => ({
      ...current,
      action: "vram",
      error: null,
      actionResult: null,
    }));
    try {
      const result = await updateComfyUILaunchSettings(state.selectedVramMode);
      const ok = result.status !== "blocked" && result.status !== "updated_restart_failed";
      setState((current) => ({
        ...current,
        launchSettings: result.settings,
        selectedVramMode: result.settings.vram_mode,
        actionResult: { label: "vram", status: result.status, ok },
        error: null,
      }));
      await refresh();
    } catch (error) {
      runtimeStatus.markActionFailure(error);
      void runtimeStatus.refreshRuntime({ force: true, silent: false });
      setState((current) => ({
        ...current,
        action: null,
        error: error instanceof Error ? error.message : String(error),
      }));
    } finally {
      setState((current) => ({ ...current, action: null }));
    }
  }

  async function runComfyUIRebuild() {
    const selected = state.selectedVersion === "latest" ? "current" : state.selectedVersion;
    await runComfyUIJob("rebuild", selected, rebuildComfyUI);
  }

  async function checkComfyUIUpdates() {
    setState((current) => ({ ...current, action: "check-updates", error: null, actionResult: null }));
    try {
      const versions = await fetchComfyUIVersions({ checkUpstream: true });
      setState((current) => ({
        ...current,
        versions,
        selectedVersion: versions.latest_tag ? "latest" : current.selectedVersion,
      }));
    } catch (error) {
      void runtimeStatus.refreshRuntime({ force: true, silent: false });
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : String(error),
      }));
    } finally {
      setState((current) => ({ ...current, action: null }));
    }
  }

  async function runComfyUIJob(
    actionName: "update" | "rebuild",
    selected: string,
    starter: (version: string) => Promise<ComfyUIUpdateStatus>,
  ) {
    setState((current) => ({ ...current, action: actionName, error: null, actionResult: null, updateStatus: null }));
    try {
      let updateStatus = await starter(selected);
      setState((current) => ({ ...current, updateStatus }));
      while (updateStatus.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        updateStatus = await fetchComfyUIUpdateStatus();
        setState((current) => ({ ...current, updateStatus }));
      }
      const ok = updateStatus.status === "completed";
      setState((current) => ({
        ...current,
        actionResult: { label: actionName, status: updateStatus.status, ok },
      }));
      await refresh();
    } catch (error) {
      setState((current) => ({
        ...current,
        action: null,
        error: error instanceof Error ? error.message : String(error),
      }));
    } finally {
      setState((current) => ({ ...current, action: null }));
    }
  }

  async function saveApiKey(provider: ApiKeyProviderId) {
    const apiKey = state.apiDrafts[provider].trim();
    if (!apiKey) return;
    setState((current) => ({ ...current, action: `api-save-${provider}`, apiStatus: null, error: null }));
    try {
      const result = await updateExternalApiKey(provider, apiKey);
      setState((current) => ({
        ...current,
        apiSettings: current.apiSettings
          ? {
              ...current.apiSettings,
              providers: {
                ...current.apiSettings.providers,
                [provider]: result.provider,
              },
            }
          : current.apiSettings,
        apiDrafts: { ...current.apiDrafts, [provider]: "" },
        apiVisible: { ...current.apiVisible, [provider]: false },
        apiStatus: { provider, message: `${result.provider.label} API key saved.`, ok: true },
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        apiStatus: {
          provider,
          message: error instanceof Error ? error.message : String(error),
          ok: false,
        },
      }));
    } finally {
      setState((current) => ({ ...current, action: null }));
    }
  }

  async function clearApiKey(provider: ApiKeyProviderId) {
    setState((current) => ({ ...current, action: `api-clear-${provider}`, apiStatus: null, error: null }));
    try {
      const result = await clearExternalApiKey(provider);
      setState((current) => ({
        ...current,
        apiSettings: current.apiSettings
          ? {
              ...current.apiSettings,
              providers: {
                ...current.apiSettings.providers,
                [provider]: result.provider,
              },
            }
          : current.apiSettings,
        apiDrafts: { ...current.apiDrafts, [provider]: "" },
        apiVisible: { ...current.apiVisible, [provider]: false },
        apiStatus: { provider, message: `${result.provider.label} API key removed.`, ok: true },
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        apiStatus: {
          provider,
          message: error instanceof Error ? error.message : String(error),
          ok: false,
        },
      }));
    } finally {
      setState((current) => ({ ...current, action: null }));
    }
  }

  async function openCurrentModelFolder(path: string | null | undefined) {
    if (!path) return;
    try {
      await openFolder(path);
    } catch (error) {
      setState((current) => ({
        ...current,
        modelFolderStatus: {
          message: error instanceof Error ? error.message : String(error),
          ok: false,
        },
      }));
    }
  }

  async function chooseNoofyModelsFolder() {
    const selected = await selectFolder();
    if (!selected) return;
    await saveModelFolderSettings({ noofy_models_dir: selected });
  }

  async function chooseExternalComfyUIModelsFolder() {
    const selected = await selectFolder();
    if (!selected) return;
    await saveModelFolderSettings({ external_comfyui_models_dir: selected });
  }

  async function clearExternalComfyUIModelsFolder() {
    await saveModelFolderSettings({ external_comfyui_models_dir: "" });
  }

  async function saveModelFolderSettings(payload: {
    noofy_models_dir?: string;
    external_comfyui_models_dir?: string;
  }) {
    setState((current) => ({ ...current, action: "model-folder", modelFolderStatus: null, error: null }));
    try {
      const result = await updateModelFolderSettings(payload);
      setState((current) => ({
        ...current,
        modelFolderSettings: result.settings,
        modelFolderStatus: {
          message: result.restart_required
            ? "Model folder saved. Restart the Noofy engine so ComfyUI can scan the new model folder location."
            : "Model folder settings saved.",
          ok: true,
        },
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        modelFolderStatus: {
          message: error instanceof Error ? error.message : String(error),
          ok: false,
        },
      }));
    } finally {
      setState((current) => ({ ...current, action: null }));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const status = runtimeStatus.statusView;
  const environment = state.runtime?.environment;
  const versions = state.versions;
  const currentVersion =
    versions?.current?.tag ??
    state.runtime?.version?.active_tag ??
    (state.runtime?.version?.source_kind === "bundled" ? "Bundled ComfyUI" : "Unavailable");
  const sourceStatus = state.runtime?.version?.source_kind ?? "unknown";
  const updateBusy =
    state.action === "update" ||
    (state.updateStatus?.operation !== "rebuild" && state.updateStatus?.status === "running");
  const rebuildBusy =
    state.action === "rebuild" ||
    (state.updateStatus?.operation === "rebuild" && state.updateStatus.status === "running");
  const engineJobBusy = updateBusy || rebuildBusy;
  const checkUpdatesBusy = state.action === "check-updates";
  const currentRepairStatus = versions?.current?.repair_status;
  const currentIncompatibleReason = versions?.current?.incompatible_reason;
  const launchSettings = state.launchSettings;
  const vramBusy = state.action === "vram";
  const selectedVramOption = vramModeOption(state.selectedVramMode);
  const selectedVramIndex = VRAM_MODE_INDEX_BY_VALUE.get(selectedVramOption.value) ?? VRAM_MODE_INDEX_BY_VALUE.get(DEFAULT_VRAM_MODE) ?? 0;
  const vramSliderProgress = (selectedVramIndex / (VRAM_MODE_OPTIONS.length - 1)) * 100;
  const vramChanged = Boolean(launchSettings && state.selectedVramMode !== launchSettings.vram_mode);
  const vramControlsDisabled = !launchSettings?.applies_to_managed_runtime || vramBusy || state.action !== null;
  const vramSaveDisabled = !vramChanged || vramControlsDisabled;
  const apiSettings = state.apiSettings;
  const apiCredentialStoreUnavailable = apiSettings?.credential_store.available === false;
  const modelFolderSettings = state.modelFolderSettings;
  const modelFolderBusy = state.action === "model-folder";

  return (
    <AppLayout activeRoute="settings" status={status} onNavigate={onNavigate}>
      <section className="page-heading page-heading--compact" aria-labelledby="engine-settings-title">
        <div>
          <p className="eyebrow">ComfyUI engine</p>
          <h1 id="engine-settings-title">Engine Settings</h1>
          <p>Set up and manage the ComfyUI engine that runs workflows on this machine.</p>
        </div>
        <button className="secondary-button" type="button" onClick={() => void refresh()}>
          <RotateCcw size={16} aria-hidden="true" />
          Refresh
        </button>
      </section>

      {state.error ? (
        <div className="notice notice--error" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>The app could not reach the backend</strong>
            <span>Start the Noofy backend and try again.</span>
          </div>
        </div>
      ) : null}

      {state.actionResult ? (
        <div className={`notice ${state.actionResult.ok ? "notice--success" : "notice--error"}`} role="status">
          {state.actionResult.ok
            ? <CheckCircle2 size={18} aria-hidden="true" />
            : <AlertCircle size={18} aria-hidden="true" />}
          <div>
            <strong>{state.actionResult.ok ? "Done" : "Action did not complete"}</strong>
            <span>{actionResultMessage(state.actionResult)}</span>
          </div>
        </div>
      ) : null}

      <section className="settings-grid">
        <article className="settings-panel engine-status-card">
          <div className="engine-status-card__header">
            <div className="engine-status-card__title-row">
              <div className="engine-status-card__icon" aria-hidden="true">
                <Zap size={18} />
              </div>
              <div>
                <h2 className="engine-status-card__title">ComfyUI Engine</h2>
                <p className="engine-status-card__subtitle">Noofy runs AI workflows privately on your computer — nothing is sent to the cloud.</p>
              </div>
            </div>
            <span className={`status-pill status-pill--${status.tone}`}>
              {status.loading ? <Loader2 className="spin" size={14} aria-hidden="true" /> : <span />}
              <span>{status.label}</span>
            </span>
          </div>

          <ul className="engine-status-card__steps">
            <li className="engine-status-card__step">
              <div className={`engine-status-card__step-icon ${environment?.prepared ? "engine-status-card__step-icon--done" : "engine-status-card__step-icon--pending"}`} aria-hidden="true">
                {environment?.prepared ? <CheckCircle2 size={16} /> : <Circle size={16} />}
              </div>
              <div className="engine-status-card__step-body">
                <span className="engine-status-card__step-label">
                  {environment?.prepared ? "ComfyUI is installed" : "ComfyUI is not installed yet"}
                </span>
                <span className="engine-status-card__step-hint">
                  {environment?.prepared
                    ? "ComfyUI is installed and ready on this computer."
                    : <>Use the &ldquo;Set Up&rdquo; button below to install it.</>}
                </span>
              </div>
            </li>
            <li className="engine-status-card__step">
              <div className={`engine-status-card__step-icon ${state.runtime?.reachable ? "engine-status-card__step-icon--done" : "engine-status-card__step-icon--pending"}`} aria-hidden="true">
                {state.runtime?.reachable ? <CheckCircle2 size={16} /> : <Circle size={16} />}
              </div>
              <div className="engine-status-card__step-body">
                <span className="engine-status-card__step-label">
                  {state.runtime?.reachable ? "ComfyUI is active and ready" : "ComfyUI is not running"}
                </span>
                <span className="engine-status-card__step-hint">
                  {state.runtime?.reachable
                    ? "Your workflows can run on this computer right now."
                    : "Press Restart to activate it before running a workflow."}
                </span>
              </div>
              {state.runtime?.managed_process_running && state.runtime.pid ? (
                <span className="engine-status-card__pid">PID {state.runtime.pid}</span>
              ) : null}
            </li>
          </ul>

          <div className="button-row">
            <button
              className="primary-button primary-button--compact"
              type="button"
              disabled={state.action !== null}
              onClick={() => void runAction("restart", restartEngine)}
            >
              {state.action === "restart" ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <RotateCcw size={16} aria-hidden="true" />}
              Restart
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={state.action !== null}
              onClick={() => void runAction("stop", stopEngine)}
            >
              <Square size={16} aria-hidden="true" />
              Stop
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={state.action !== null}
              onClick={() => void runAction("bootstrap", bootstrapEngine)}
            >
              {state.action === "bootstrap" ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Wrench size={16} aria-hidden="true" />}
              {environment?.prepared ? "Repair Installation" : "Set Up"}
            </button>
          </div>
        </article>

        <article className="settings-panel vram-mode-card">
          <div className="panel-heading">
            <div>
              <h2>VRAM Mode</h2>
              <p>Choose the memory mode Noofy uses when it launches managed ComfyUI.</p>
            </div>
          </div>

          <div className="settings-option-group">
            <div className="vram-mode-card__summary" aria-live="polite">
              <strong>{selectedVramOption.label}</strong>
              <span>{selectedVramOption.description}</span>
            </div>
            <div className="vram-mode-slider">
              <label className="sr-only" htmlFor="vram-mode-slider">Managed launch mode</label>
              <input
                id="vram-mode-slider"
                type="range"
                min="0"
                max={VRAM_MODE_OPTIONS.length - 1}
                step="1"
                value={selectedVramIndex}
                disabled={vramControlsDisabled}
                aria-valuetext={`${selectedVramOption.label}: ${selectedVramOption.description}`}
                style={{ "--vram-slider-progress": `${vramSliderProgress}%` } as CSSProperties}
                onChange={(event) => {
                  const nextMode = VRAM_MODE_OPTIONS[Number(event.target.value)]?.value ?? DEFAULT_VRAM_MODE;
                  setState((current) => ({
                    ...current,
                    selectedVramMode: nextMode,
                    actionResult: current.actionResult?.label === "vram" ? null : current.actionResult,
                  }));
                }}
              />
              <div className="vram-mode-slider__labels" aria-hidden="true">
                {VRAM_MODE_OPTIONS.map((option) => (
                  <span className={option.value === selectedVramOption.value ? "is-selected" : ""} key={option.value}>
                    {option.label}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="button-row vram-mode-card__actions">
            <button
              className="primary-button primary-button--compact"
              type="button"
              disabled={vramSaveDisabled}
              onClick={() => void saveVramModeChange()}
            >
              {vramBusy ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <CheckCircle2 size={16} aria-hidden="true" />}
              Save
            </button>
          </div>

          {launchSettings && !launchSettings.applies_to_managed_runtime ? (
            <div className="notice notice--warning" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>Managed launch setting unavailable</strong>
                <span>{launchSettings.disabled_reason ?? "Switch to managed ComfyUI mode to use this setting."}</span>
              </div>
            </div>
          ) : null}

          {vramBusy ? (
            <div className="notice notice--success" role="status">
              <Loader2 className="spin" size={18} aria-hidden="true" />
              <div>
                <strong>Applying VRAM mode</strong>
                <span>Noofy is updating the managed ComfyUI launch configuration.</span>
              </div>
            </div>
          ) : null}
        </article>

        <article className="settings-panel api-settings-card">
          <div className="panel-heading">
            <div>
              <h2>APIs</h2>
              <p>Save model platform API keys in this computer&apos;s credential store.</p>
            </div>
          </div>

          {apiCredentialStoreUnavailable ? (
            <div className="notice notice--warning" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>Credential store unavailable</strong>
                <span>{credentialStoreUnavailableMessage(apiSettings)}</span>
              </div>
            </div>
          ) : null}

          {state.apiStatus ? (
            <div className={`notice ${state.apiStatus.ok ? "notice--success" : "notice--error"}`} role="status">
              {state.apiStatus.ok
                ? <CheckCircle2 size={18} aria-hidden="true" />
                : <AlertCircle size={18} aria-hidden="true" />}
              <div>
                <strong>{state.apiStatus.ok ? "Saved" : "Could not save API key"}</strong>
                <span>{state.apiStatus.message}</span>
              </div>
            </div>
          ) : null}

          <div className="api-key-list">
            {API_PROVIDERS.map((provider) => {
              const metadata = apiSettings?.providers[provider.id];
              const draft = state.apiDrafts[provider.id];
              const isVisible = state.apiVisible[provider.id];
              const saveAction = state.action === `api-save-${provider.id}`;
              const clearAction = state.action === `api-clear-${provider.id}`;
              const busy = saveAction || clearAction;
              const configuredCopy = metadata?.configured
                ? `Saved key ending in ${metadata.last_four ?? "...."}`
                : "No key saved";

              return (
                <div className="api-key-field" key={provider.id}>
                  <div className="api-key-field__label-row">
                    <label htmlFor={provider.fieldId}>{provider.label}</label>
                    <span>{configuredCopy}</span>
                  </div>
                  <div className="api-key-field__control">
                    <input
                      id={provider.fieldId}
                      type={isVisible ? "text" : "password"}
                      value={draft}
                      autoComplete="off"
                      spellCheck={false}
                      placeholder={metadata?.configured ? "Enter a replacement key" : "Paste API key"}
                      disabled={apiCredentialStoreUnavailable || busy}
                      onChange={(event) => {
                        const value = event.target.value;
                        setState((current) => ({
                          ...current,
                          apiDrafts: { ...current.apiDrafts, [provider.id]: value },
                          apiStatus: current.apiStatus?.provider === provider.id ? null : current.apiStatus,
                        }));
                      }}
                    />
                    <button
                      className="icon-button"
                      type="button"
                      aria-label={`${isVisible ? "Hide" : "Show"} ${provider.label}`}
                      title={`${isVisible ? "Hide" : "Show"} ${provider.label}`}
                      disabled={busy}
                      onClick={() => {
                        setState((current) => ({
                          ...current,
                          apiVisible: { ...current.apiVisible, [provider.id]: !current.apiVisible[provider.id] },
                        }));
                      }}
                    >
                      {isVisible ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                    </button>
                  </div>
                  <div className="button-row api-key-field__actions">
                    <button
                      className="primary-button primary-button--compact"
                      type="button"
                      aria-label={`Save ${provider.label}`}
                      disabled={apiCredentialStoreUnavailable || !draft.trim() || state.action !== null}
                      onClick={() => void saveApiKey(provider.id)}
                    >
                      {saveAction ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <KeyRound size={16} aria-hidden="true" />}
                      Save
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      aria-label={`Clear ${provider.label}`}
                      disabled={apiCredentialStoreUnavailable || !metadata?.configured || state.action !== null}
                      onClick={() => void clearApiKey(provider.id)}
                    >
                      {clearAction ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Trash2 size={16} aria-hidden="true" />}
                      Clear
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </article>

        <article className="settings-panel model-folder-card">
          <div className="panel-heading">
            <div>
              <h2>Model Folder</h2>
              <p>Choose where Noofy stores models and optionally connect models you already use in ComfyUI.</p>
            </div>
          </div>

          {state.modelFolderStatus ? (
            <div className={`notice ${state.modelFolderStatus.ok ? "notice--success" : "notice--error"}`} role="status">
              {state.modelFolderStatus.ok
                ? <CheckCircle2 size={18} aria-hidden="true" />
                : <AlertCircle size={18} aria-hidden="true" />}
              <div>
                <strong>{state.modelFolderStatus.ok ? "Saved" : "Folder action failed"}</strong>
                <span>{state.modelFolderStatus.message}</span>
              </div>
            </div>
          ) : null}

          <div className="model-folder-sections">
            <section className="model-folder-section" aria-labelledby="noofy-model-folder-title">
              <div>
                <h3 id="noofy-model-folder-title">Noofy Models Folder</h3>
                <p>Noofy downloads new models here by default. You can also add model files to these folders yourself.</p>
              </div>
              <div className="path-display" title={modelFolderSettings?.noofy_models_dir ?? ""}>
                {modelFolderSettings?.noofy_models_dir ?? "Loading..."}
              </div>
              <div className="button-row">
                <button
                  className="secondary-button"
                  type="button"
                  disabled={!modelFolderSettings?.noofy_models_dir || modelFolderBusy}
                  onClick={() => void openCurrentModelFolder(modelFolderSettings?.noofy_models_dir)}
                >
                  <FolderOpen size={16} aria-hidden="true" />
                  Open Folder
                </button>
                <button
                  className="primary-button primary-button--compact"
                  type="button"
                  disabled={modelFolderBusy || state.action !== null}
                  onClick={() => void chooseNoofyModelsFolder()}
                >
                  {modelFolderBusy ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <FolderCog size={16} aria-hidden="true" />}
                  Move Folder / Change Location
                </button>
              </div>
            </section>

            <section className="model-folder-section" aria-labelledby="external-comfyui-folder-title">
              <div>
                <h3 id="external-comfyui-folder-title">Existing ComfyUI Models Folder</h3>
                <p>If you already use ComfyUI, you can connect your existing ComfyUI models folder. Noofy will be able to reuse models from that folder, so you do not need to download the same models twice.</p>
              </div>
              <div className="path-display" title={modelFolderSettings?.external_comfyui_models_dir ?? ""}>
                {modelFolderSettings?.external_comfyui_models_dir ?? "Not connected"}
              </div>
              <div className="button-row">
                <button
                  className="secondary-button"
                  type="button"
                  disabled={!modelFolderSettings?.external_comfyui_models_dir || modelFolderBusy}
                  onClick={() => void openCurrentModelFolder(modelFolderSettings?.external_comfyui_models_dir)}
                >
                  <FolderOpen size={16} aria-hidden="true" />
                  Open Folder
                </button>
                <button
                  className="secondary-button"
                  type="button"
                  disabled={modelFolderBusy || state.action !== null}
                  onClick={() => void chooseExternalComfyUIModelsFolder()}
                >
                  <FolderCog size={16} aria-hidden="true" />
                  Choose Folder
                </button>
                <button
                  className="secondary-button"
                  type="button"
                  disabled={!modelFolderSettings?.external_comfyui_models_dir || modelFolderBusy || state.action !== null}
                  onClick={() => void clearExternalComfyUIModelsFolder()}
                >
                  <Trash2 size={16} aria-hidden="true" />
                  Disconnect
                </button>
              </div>
            </section>
          </div>
        </article>

        <article className="settings-panel">
          <div className="panel-heading">
            <div>
              <h2>ComfyUI Version</h2>
              <p>Install upstream ComfyUI releases into Noofy's managed sidecar.</p>
            </div>
            <button
              className="secondary-button"
              type="button"
              disabled={state.action !== null}
              onClick={() => void checkComfyUIUpdates()}
            >
              {checkUpdatesBusy ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Search size={16} aria-hidden="true" />}
              Check for Updates
            </button>
          </div>

          <dl className="detail-list">
            <div>
              <dt>Current</dt>
              <dd>{currentVersion}</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>{sourceStatus}</dd>
            </div>
          </dl>

          {versions?.release_fetch_error ? (
            <div className="notice notice--error" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>Could not load upstream releases</strong>
                <span>{versions.release_fetch_error}</span>
              </div>
            </div>
          ) : null}

          {versions && !versions.updates_allowed ? (
            <div className="notice notice--warning" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>Updates unavailable</strong>
                <span>{versions.disabled_reason ?? "ComfyUI updates are not available right now."}</span>
              </div>
            </div>
          ) : null}

          <div className="settings-option-group">
            <label className="settings-option settings-option--select">
              <div>
                <strong>Version</strong>
                <span>
                  {versions?.upstream_checked
                    ? "Upstream releases are validated locally before activation."
                    : "Press Check for Updates to load upstream ComfyUI releases."}
                </span>
              </div>
              <select
                value={state.selectedVersion}
                disabled={!versions?.updates_allowed || engineJobBusy}
                onChange={(event) => setState((current) => ({ ...current, selectedVersion: event.target.value }))}
              >
                <option value="latest">Latest version{versions?.latest_tag ? ` (${versions.latest_tag})` : ""}</option>
                {versions?.options.map((option) => (
                  <option value={option.tag} key={option.tag}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {state.updateStatus?.progress_label ? (
            <div className={`notice ${state.updateStatus.status === "failed" ? "notice--error" : "notice--success"}`} role="status">
              {state.updateStatus.status === "running"
                ? <Loader2 className="spin" size={18} aria-hidden="true" />
                : state.updateStatus.status === "failed"
                  ? <AlertCircle size={18} aria-hidden="true" />
                  : <CheckCircle2 size={18} aria-hidden="true" />}
              <div>
                <strong>{state.updateStatus.operation === "repair" ? `repair: ${state.updateStatus.phase}` : state.updateStatus.phase}</strong>
                <span>{state.updateStatus.error ?? state.updateStatus.progress_label}</span>
                {state.updateStatus.fallback_version ? (
                  <span>Fallback active: {state.updateStatus.fallback_version}</span>
                ) : null}
                {state.updateStatus.incompatible_version ? (
                  <span>{state.updateStatus.incompatible_version} failed Noofy validation.</span>
                ) : null}
              </div>
            </div>
          ) : null}

          {currentRepairStatus === "repair_blocked" ? (
            <div className="notice notice--warning" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>Automatic repair paused</strong>
                  <span>
                    Noofy reached the retry limit for this ComfyUI version.
                  {versions?.current?.repair_blocked_until ? ` Retry after ${versions.current.repair_blocked_until}.` : ""}
                  </span>
              </div>
            </div>
          ) : null}

          {versions?.current?.incompatible ? (
            <div className="notice notice--error" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>ComfyUI version failed validation</strong>
                <span>{currentIncompatibleReason ?? "This version changed behavior Noofy depends on."}</span>
              </div>
            </div>
          ) : null}

          <div className="button-row">
            <button
              className="primary-button primary-button--compact"
              type="button"
              disabled={!versions?.updates_allowed || state.action !== null}
              onClick={() => void runComfyUIUpdate()}
            >
              {updateBusy ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
              Update ComfyUI
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={!versions?.updates_allowed || state.action !== null || !versions.current}
              onClick={() => void runComfyUIRebuild()}
            >
              {rebuildBusy ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Wrench size={16} aria-hidden="true" />}
              Rebuild Environment
            </button>
          </div>
        </article>

        <article className="settings-panel">
          <div className="panel-heading">
            <div>
              <h2>Dashboard View</h2>
              <p>Choose how workflow dashboards are presented when you run a workflow.</p>
            </div>
          </div>

          <div className="settings-option-group">
            <label className="settings-option">
              <input
                type="radio"
                name="dashboard-view-mode"
                value="canvas"
                checked={viewMode === "canvas"}
                onChange={() => setViewMode("canvas")}
              />
              <div>
                <strong>Canvas</strong>
                <span>Interactive grid layout — drag to reposition widgets.</span>
              </div>
            </label>
            <label className="settings-option">
              <input
                type="radio"
                name="dashboard-view-mode"
                value="classic"
                checked={viewMode === "classic"}
                onChange={() => setViewMode("classic")}
              />
              <div>
                <strong>Classic</strong>
                <span>Simple two-panel inputs and preview layout.</span>
              </div>
            </label>
          </div>
        </article>
      </section>
    </AppLayout>
  );
}
