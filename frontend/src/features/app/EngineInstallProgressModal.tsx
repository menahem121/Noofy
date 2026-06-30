import { Loader2 } from "lucide-react";

import type { RuntimeStatus } from "../../lib/api/noofyApi";
import { useRuntimeStatus } from "./RuntimeStatusProvider";

const DEFAULT_INSTALL_LABEL = "Preparing ComfyUI engine dependencies";

export function EngineInstallProgressModal() {
  const runtimeStatus = useRuntimeStatus();
  const install = engineInstallProgress(runtimeStatus.runtime);

  if (!install) return null;

  return (
    <div
      className="modal-backdrop engine-install-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="engine-install-title"
      aria-describedby="engine-install-description"
    >
      <section className="engine-install-modal" aria-busy="true">
        <div className="engine-install-modal__header">
          <span className="engine-install-modal__icon" aria-hidden="true">
            <Loader2 className="spin" size={24} />
          </span>
          <div>
            <p className="eyebrow">Engine setup</p>
            <h2 id="engine-install-title">Installing ComfyUI engine</h2>
            <p id="engine-install-description">
              Noofy is preparing the local ComfyUI engine. This one-time setup can take several minutes.
            </p>
          </div>
        </div>

        <div className="engine-install-progress">
          <div
            className="engine-install-progress__track"
            role="progressbar"
            aria-label="Engine installation progress"
            aria-valuetext={install.label}
          >
            <span />
          </div>
          <p>{install.label}</p>
        </div>
      </section>
    </div>
  );
}

export function engineInstallProgress(runtime: RuntimeStatus | null) {
  if (!runtime?.environment_bootstrap_running) return null;
  const label = runtime.environment_bootstrap_label?.trim() || DEFAULT_INSTALL_LABEL;
  return { label };
}
