import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Circle, Download, Loader2, Play, RotateCcw, Square, Wrench, Zap } from "lucide-react";

import {
  bootstrapEngine,
  fetchComfyUIUpdateStatus,
  fetchComfyUIVersions,
  fetchComfyUILaunchSettings,
  fetchRuntimeStatus,
  rebuildComfyUI,
  startEngine,
  stopEngine,
  updateComfyUI,
  updateComfyUILaunchSettings,
  type ComfyUILaunchSettings,
  type ComfyUIUpdateStatus,
  type ComfyUIVersionsResponse,
  type ComfyUIVramMode,
  type RuntimeStatus,
} from "../../lib/api/noofyApi";
import { useAppPreferences } from "../../lib/useAppPreferences";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";

interface EngineSettingsState {
  loading: boolean;
  runtime: RuntimeStatus | null;
  versions: ComfyUIVersionsResponse | null;
  launchSettings: ComfyUILaunchSettings | null;
  selectedVersion: string;
  selectedVramMode: ComfyUIVramMode;
  updateStatus: ComfyUIUpdateStatus | null;
  action: string | null;
  error: string | null;
  actionResult: { label: string; status: string; ok: boolean } | null;
}

const initialState: EngineSettingsState = {
  loading: true,
  runtime: null,
  versions: null,
  launchSettings: null,
  selectedVersion: "latest",
  selectedVramMode: "normal",
  updateStatus: null,
  action: null,
  error: null,
  actionResult: null,
};

const ACTION_OK_STATUSES = new Set(["prepared", "already_prepared", "already_running", "completed", "repair_completed_started"]);
const ACTION_RESULT_LABELS: Record<string, string> = {
  prepared: "Engine environment prepared successfully.",
  already_prepared: "Environment is already prepared.",
  bootstrap_failed: "Preparation failed. Check the backend logs for details.",
  requirements_missing: "ComfyUI requirements.txt is missing.",
  python_missing: "Bootstrap Python executable not found.",
  python_not_prepared: "Runtime Python is not prepared.",
  dependency_check_failed: "Dependencies could not be verified after install.",
  not_configured: "No managed runtime environment is configured.",
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

function actionResultMessage(result: { label: string; status: string }) {
  if (result.label === "rebuild") {
    if (result.status === "completed") return "ComfyUI environment was rebuilt and validated successfully.";
    if (result.status === "failed") return "ComfyUI environment rebuild failed. The existing engine was left unchanged.";
    if (result.status === "blocked") return "ComfyUI environment rebuild is not available in this runtime mode.";
  }
  return ACTION_RESULT_LABELS[result.status] ?? result.status;
}

export function EngineSettingsPage({ onNavigate }: { onNavigate: (route: AppRouteId) => void }) {
  const [state, setState] = useState<EngineSettingsState>(initialState);
  const { viewMode, setViewMode } = useAppPreferences();

  async function refresh() {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const [runtime, versions, launchSettings] = await Promise.all([
        fetchRuntimeStatus(),
        fetchComfyUIVersions(),
        fetchComfyUILaunchSettings(),
      ]);
      setState((current) => ({
        ...current,
        loading: false,
        runtime,
        versions,
        launchSettings,
        selectedVramMode: launchSettings.vram_mode,
      }));
    } catch (error) {
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
    let polling = label === "start";
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
      const status = typeof result.status === "string" ? result.status : "unknown";
      const ok = ACTION_OK_STATUSES.has(status);
      setState((current) => ({
        ...current,
        actionResult: { label, status, ok },
      }));
      await refresh();
    } catch (error) {
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

  async function runComfyUIUpdate() {
    await runComfyUIJob("update", state.selectedVersion, updateComfyUI);
  }

  async function runVramModeChange(vramMode: ComfyUIVramMode) {
    setState((current) => ({
      ...current,
      action: "vram",
      selectedVramMode: vramMode,
      error: null,
      actionResult: null,
    }));
    try {
      const result = await updateComfyUILaunchSettings(vramMode);
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

  useEffect(() => {
    void refresh();
  }, []);

  const status = runtimeStatusCopy(state);
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
  const currentRepairStatus = versions?.current?.repair_status;
  const currentIncompatibleReason = versions?.current?.incompatible_reason;
  const launchSettings = state.launchSettings;
  const vramBusy = state.action === "vram";

  return (
    <AppLayout activeRoute="settings" status={status} onNavigate={onNavigate}>
      <section className="page-heading page-heading--compact" aria-labelledby="engine-settings-title">
        <div>
          <p className="eyebrow">Local AI engine</p>
          <h1 id="engine-settings-title">Engine Settings</h1>
          <p>Set up and manage the AI engine that runs workflows on this machine.</p>
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
                <h2 className="engine-status-card__title">Local AI</h2>
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
                    : "Press Start to activate it before running a workflow."}
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
              onClick={() => void runAction("bootstrap", bootstrapEngine)}
            >
              {state.action === "bootstrap" ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Wrench size={16} aria-hidden="true" />}
              {environment?.prepared ? "Repair Installation" : "Set Up"}
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={state.action !== null}
              onClick={() => void runAction("start", startEngine)}
            >
              <Play size={16} aria-hidden="true" />
              Start
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
          </div>
        </article>

        <article className="settings-panel">
          <div className="panel-heading">
            <div>
              <h2>VRAM Mode</h2>
              <p>Choose the memory mode Noofy uses when it launches managed ComfyUI.</p>
            </div>
          </div>

          <div className="settings-option-group">
            <label className="settings-option settings-option--select">
              <div>
                <strong>Managed launch mode</strong>
                <span>
                  Normal VRAM uses ComfyUI defaults and passes no VRAM launch flag.
                </span>
              </div>
              <select
                value={state.selectedVramMode}
                disabled={!launchSettings?.applies_to_managed_runtime || vramBusy || state.action !== null}
                onChange={(event) => void runVramModeChange(event.target.value as ComfyUIVramMode)}
              >
                {launchSettings?.options.map((option) => (
                  <option value={option.value} key={option.value}>
                    {option.label}
                  </option>
                )) ?? <option value="normal">Normal VRAM</option>}
              </select>
            </label>
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

        <article className="settings-panel">
          <div className="panel-heading">
            <div>
              <h2>ComfyUI Version</h2>
              <p>Install upstream ComfyUI releases into Noofy's managed sidecar.</p>
            </div>
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
                <span>Upstream releases are validated locally before activation.</span>
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
