import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, Play, RotateCcw, Square, Wrench } from "lucide-react";

import {
  bootstrapEngine,
  fetchRuntimeStatus,
  startEngine,
  stopEngine,
  type RuntimeStatus,
} from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";

interface EngineSettingsState {
  loading: boolean;
  runtime: RuntimeStatus | null;
  action: string | null;
  error: string | null;
}

const initialState: EngineSettingsState = {
  loading: true,
  runtime: null,
  action: null,
  error: null,
};

export function EngineSettingsPage({ onNavigate }: { onNavigate: (route: AppRouteId) => void }) {
  const [state, setState] = useState<EngineSettingsState>(initialState);

  async function refresh() {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const runtime = await fetchRuntimeStatus();
      setState((current) => ({ ...current, loading: false, runtime }));
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        runtime: null,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function runAction(label: string, action: () => Promise<unknown>) {
    setState((current) => ({ ...current, action: label, error: null }));
    try {
      await action();
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

  return (
    <AppLayout activeRoute="settings" status={status} onNavigate={onNavigate}>
      <section className="page-heading page-heading--compact" aria-labelledby="engine-settings-title">
        <div>
          <p className="eyebrow">Local engine</p>
          <h1 id="engine-settings-title">Engine Settings</h1>
          <p>Prepare, start, and inspect the local AI engine without exposing ComfyUI details.</p>
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

      <section className="settings-grid">
        <article className="settings-panel">
          <div className="panel-heading">
            <div>
              <h2>Engine Status</h2>
              <p>{status.description}</p>
            </div>
            <span className={`status-pill status-pill--${status.tone}`}>
              {status.loading ? <Loader2 className="spin" size={14} aria-hidden="true" /> : <span />}
              <span>{status.label}</span>
            </span>
          </div>

          <dl className="detail-list">
            <div>
              <dt>Mode</dt>
              <dd>{state.runtime?.mode ?? "Unavailable"}</dd>
            </div>
            <div>
              <dt>Process</dt>
              <dd>{state.runtime?.managed_process_running ? `Running as ${state.runtime.pid}` : "Not running"}</dd>
            </div>
            <div>
              <dt>Runtime environment</dt>
              <dd>{environment?.prepared ? "Prepared" : "Not prepared"}</dd>
            </div>
          </dl>

          <div className="button-row">
            <button
              className="primary-button primary-button--compact"
              type="button"
              disabled={state.action !== null}
              onClick={() => void runAction("bootstrap", bootstrapEngine)}
            >
              <Wrench size={16} aria-hidden="true" />
              Prepare Engine
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
              <h2>First Run Checklist</h2>
              <p>The desktop shell will eventually run this preparation automatically.</p>
            </div>
          </div>

          <ul className="check-list">
            <li>
              <CheckCircle2 size={17} aria-hidden="true" />
              Backend API is the only frontend boundary.
            </li>
            <li className={environment?.prepared ? "" : "check-list__muted"}>
              <CheckCircle2 size={17} aria-hidden="true" />
              Local engine environment is prepared.
            </li>
            <li className={state.runtime?.reachable ? "" : "check-list__muted"}>
              <CheckCircle2 size={17} aria-hidden="true" />
              Local AI engine is reachable.
            </li>
          </ul>
        </article>
      </section>
    </AppLayout>
  );
}
