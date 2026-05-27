import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  FolderCog,
  ImagePlus,
  KeyRound,
  Loader2,
  X,
} from "lucide-react";

import {
  completeOnboarding,
  fetchApiKeySettings,
  fetchModelFolderSettings,
  fetchOnboardingState,
  updateExternalApiKey,
  updateModelFolderSettings,
  type ApiKeyProviderId,
  type ApiKeySettingsResponse,
  type ModelFolderSettings,
  type WorkflowSummary,
} from "../../lib/api/noofyApi";
import { selectFolder } from "../../lib/folderDialogs";

const TOTAL_STEPS = 4;
const TEXT_TO_IMAGE_WORKFLOW_ID = "text_to_image_v0";

const API_PROVIDERS: Array<{ id: ApiKeyProviderId; label: string; fieldId: string }> = [
  { id: "civitai", label: "CivitAI", fieldId: "onboarding-civitai-api-key" },
  { id: "hugging_face", label: "Hugging Face", fieldId: "onboarding-hugging-face-api-key" },
];

interface FirstLaunchOnboardingProps {
  workflows: WorkflowSummary[];
  hasLoadedWorkflows: boolean;
  refreshWorkflows: () => Promise<WorkflowSummary[] | null>;
  onOpenWorkflow: (workflowId: string, workflowName?: string) => void;
  onBrowseWorkflows: () => void;
}

type Visibility = "checking" | "open" | "hidden";
type FolderChoice = "undecided" | "yes" | "not_now";

export function FirstLaunchOnboarding({
  workflows,
  hasLoadedWorkflows,
  refreshWorkflows,
  onOpenWorkflow,
  onBrowseWorkflows,
}: FirstLaunchOnboardingProps) {
  const [visibility, setVisibility] = useState<Visibility>("checking");
  const [stepIndex, setStepIndex] = useState(0);
  const [apiSettings, setApiSettings] = useState<ApiKeySettingsResponse | null>(null);
  const [modelFolders, setModelFolders] = useState<ModelFolderSettings | null>(null);
  const [apiSettingsLoadFailed, setApiSettingsLoadFailed] = useState(false);
  const [modelFoldersLoadFailed, setModelFoldersLoadFailed] = useState(false);
  const [apiDrafts, setApiDrafts] = useState<Record<ApiKeyProviderId, string>>({
    civitai: "",
    hugging_face: "",
    comfy_org: "",
  });
  const [savingApiProvider, setSavingApiProvider] = useState<ApiKeyProviderId | null>(null);
  const [apiMessage, setApiMessage] = useState<{ provider: ApiKeyProviderId; text: string; ok: boolean } | null>(null);
  const [folderChoice, setFolderChoice] = useState<FolderChoice>("undecided");
  const [folderAction, setFolderAction] = useState<"external" | "noofy" | null>(null);
  const [folderMessage, setFolderMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const [completionError, setCompletionError] = useState<string | null>(null);
  const [completing, setCompleting] = useState(false);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const refreshRequestedRef = useRef(false);
  const completionInFlightRef = useRef(false);

  useEffect(() => {
    let active = true;

    async function loadOnboarding() {
      try {
        const onboarding = await fetchOnboardingState();
        if (!active) return;
        if (onboarding.completed) {
          setVisibility("hidden");
          return;
        }
        setVisibility("open");
      } catch {
        if (active) setVisibility("hidden");
        return;
      }

      const [apiResult, folderResult] = await Promise.allSettled([
        fetchApiKeySettings(),
        fetchModelFolderSettings(),
      ]);
      if (!active) return;
      if (apiResult.status === "fulfilled") {
        setApiSettings(apiResult.value);
      } else {
        setApiSettingsLoadFailed(true);
      }
      if (folderResult.status === "fulfilled") {
        setModelFolders(folderResult.value);
      } else {
        setModelFoldersLoadFailed(true);
      }
    }

    void loadOnboarding();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (visibility !== "open" || hasLoadedWorkflows || refreshRequestedRef.current) return;
    refreshRequestedRef.current = true;
    void refreshWorkflows();
  }, [hasLoadedWorkflows, refreshWorkflows, visibility]);

  const starterWorkflows = useMemo(() => textToImageWorkflows(workflows), [workflows]);
  const selectedStarter = starterWorkflows.find((workflow) => workflow.id === selectedWorkflowId) ?? starterWorkflows[0] ?? null;

  useEffect(() => {
    if (starterWorkflows.length === 0) {
      setSelectedWorkflowId(null);
      return;
    }
    setSelectedWorkflowId((current) => {
      if (current && starterWorkflows.some((workflow) => workflow.id === current)) return current;
      return starterWorkflows.find((workflow) => workflow.id === TEXT_TO_IMAGE_WORKFLOW_ID)?.id ?? starterWorkflows[0].id;
    });
  }, [starterWorkflows]);

  const markCompleteAndClose = useCallback(
    async (afterComplete?: () => void) => {
      if (completionInFlightRef.current) return;
      completionInFlightRef.current = true;
      setCompleting(true);
      setCompletionError(null);
      try {
        await completeOnboarding();
        setVisibility("hidden");
        afterComplete?.();
      } catch (error) {
        setCompletionError(error instanceof Error ? error.message : String(error));
      } finally {
        completionInFlightRef.current = false;
        setCompleting(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (visibility !== "open") return undefined;

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      void markCompleteAndClose();
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [markCompleteAndClose, visibility]);

  if (visibility !== "open") return null;

  const stepNumber = stepIndex + 1;
  const apiCredentialStoreUnavailable = apiSettingsLoadFailed || apiSettings?.credential_store.available === false;

  function goNext() {
    setStepIndex((current) => Math.min(current + 1, TOTAL_STEPS - 1));
  }

  function goBack() {
    setStepIndex((current) => Math.max(current - 1, 0));
  }

  async function saveApiKey(provider: ApiKeyProviderId) {
    const apiKey = apiDrafts[provider].trim();
    if (!apiKey) return;
    setSavingApiProvider(provider);
    setApiMessage(null);
    try {
      const result = await updateExternalApiKey(provider, apiKey);
      setApiSettings((current) =>
        current
          ? {
              ...current,
              providers: {
                ...current.providers,
                [provider]: result.provider,
              },
            }
          : current,
      );
      setApiDrafts((current) => ({ ...current, [provider]: "" }));
      setApiMessage({
        provider,
        text: `${providerLabel(provider)} is configured. Noofy will only show its saved status from now on.`,
        ok: true,
      });
    } catch (error) {
      setApiMessage({
        provider,
        text: error instanceof Error ? error.message : String(error),
        ok: false,
      });
    } finally {
      setSavingApiProvider(null);
    }
  }

  async function chooseExternalComfyUIModelsFolder() {
    setFolderChoice("yes");
    const selected = await selectFolder();
    if (!selected) return;
    await saveModelFolders({ external_comfyui_models_dir: selected }, "external");
  }

  async function chooseNoofyModelsFolder() {
    const selected = await selectFolder();
    if (!selected) return;
    await saveModelFolders({ noofy_models_dir: selected }, "noofy");
  }

  async function saveModelFolders(
    payload: { noofy_models_dir?: string; external_comfyui_models_dir?: string },
    action: "external" | "noofy",
  ) {
    setFolderAction(action);
    setFolderMessage(null);
    try {
      const result = await updateModelFolderSettings(payload);
      setModelFolders(result.settings);
      setFolderMessage({
        text:
          action === "external"
            ? "Existing ComfyUI models folder connected for reuse."
            : "Noofy Models folder saved.",
        ok: true,
      });
    } catch (error) {
      setFolderMessage({
        text: error instanceof Error ? error.message : String(error),
        ok: false,
      });
    } finally {
      setFolderAction(null);
    }
  }

  function startCreating() {
    if (selectedStarter) {
      void markCompleteAndClose(() => onOpenWorkflow(selectedStarter.id, selectedStarter.name));
      return;
    }
    void markCompleteAndClose(onBrowseWorkflows);
  }

  return (
    <div
      className="modal-backdrop onboarding-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) setVisibility("hidden");
      }}
    >
      <section
        className="onboarding-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboarding-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="onboarding-modal__header">
          <div>
            <p className="eyebrow">Step {stepNumber} of {TOTAL_STEPS}</p>
            <h2 id="onboarding-title">{stepTitle(stepIndex)}</h2>
            <p>{stepSubtitle(stepIndex)}</p>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="Close onboarding"
            title="Close onboarding"
            disabled={completing}
            onClick={() => void markCompleteAndClose()}
          >
            {completing ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <X size={17} aria-hidden="true" />}
          </button>
        </header>

        <div className="onboarding-progress" aria-hidden="true">
          {Array.from({ length: TOTAL_STEPS }, (_, index) => (
            <span
              className={`onboarding-progress__step${index <= stepIndex ? " onboarding-progress__step--active" : ""}`}
              key={index}
            />
          ))}
        </div>

        <div className="onboarding-modal__body">
          {stepIndex === 0 ? (
            <ApiKeysStep
              apiSettings={apiSettings}
              apiDrafts={apiDrafts}
              savingProvider={savingApiProvider}
              credentialStoreUnavailable={apiCredentialStoreUnavailable}
              settingsLoadFailed={apiSettingsLoadFailed}
              message={apiMessage}
              onDraftChange={(provider, value) => {
                setApiDrafts((current) => ({ ...current, [provider]: value }));
                setApiMessage((current) => (current?.provider === provider ? null : current));
              }}
              onSave={(provider) => void saveApiKey(provider)}
            />
          ) : null}

          {stepIndex === 1 ? (
            <ExistingComfyUIModelsStep
              choice={folderChoice}
              modelFolders={modelFolders}
              settingsLoadFailed={modelFoldersLoadFailed}
              busy={folderAction === "external"}
              message={folderMessage}
              onChoose={() => void chooseExternalComfyUIModelsFolder()}
              onNotNow={() => setFolderChoice("not_now")}
            />
          ) : null}

          {stepIndex === 2 ? (
            <NoofyModelsStep
              modelFolders={modelFolders}
              settingsLoadFailed={modelFoldersLoadFailed}
              busy={folderAction === "noofy"}
              message={folderMessage}
              onChoose={() => void chooseNoofyModelsFolder()}
            />
          ) : null}

          {stepIndex === 3 ? (
            <StartCreatingStep
              workflows={starterWorkflows}
              selectedWorkflowId={selectedStarter?.id ?? ""}
              onSelectedWorkflowChange={setSelectedWorkflowId}
            />
          ) : null}

          {completionError ? (
            <div className="notice notice--error notice--compact" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>Onboarding could not be saved</strong>
                <span>{completionError}</span>
              </div>
            </div>
          ) : null}
        </div>

        <footer className="onboarding-modal__footer">
          <button
            className="secondary-button"
            type="button"
            disabled={completing}
            onClick={() => void markCompleteAndClose()}
          >
            Skip
          </button>
          <div className="onboarding-modal__footer-actions">
            {stepIndex > 0 ? (
              <button className="secondary-button" type="button" disabled={completing} onClick={goBack}>
                Back
              </button>
            ) : null}
            {stepIndex < TOTAL_STEPS - 1 ? (
              <button
                className="primary-button primary-button--compact"
                type="button"
                disabled={completing}
                onClick={goNext}
              >
                Next
              </button>
            ) : (
              <>
                <button
                  className="secondary-button"
                  type="button"
                  disabled={completing}
                  onClick={() => void markCompleteAndClose()}
                >
                  Finish
                </button>
                <button
                  className="primary-button primary-button--compact"
                  type="button"
                  disabled={completing}
                  onClick={startCreating}
                >
                  {completing ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <ImagePlus size={16} aria-hidden="true" />}
                  {selectedStarter ? "Start Creating" : "Browse Workflows"}
                </button>
              </>
            )}
          </div>
        </footer>
      </section>
    </div>
  );
}

function ApiKeysStep({
  apiSettings,
  apiDrafts,
  savingProvider,
  credentialStoreUnavailable,
  settingsLoadFailed,
  message,
  onDraftChange,
  onSave,
}: {
  apiSettings: ApiKeySettingsResponse | null;
  apiDrafts: Record<ApiKeyProviderId, string>;
  savingProvider: ApiKeyProviderId | null;
  credentialStoreUnavailable: boolean;
  settingsLoadFailed: boolean;
  message: { provider: ApiKeyProviderId; text: string; ok: boolean } | null;
  onDraftChange: (provider: ApiKeyProviderId, value: string) => void;
  onSave: (provider: ApiKeyProviderId) => void;
}) {
  return (
    <div className="onboarding-step">
      <p className="onboarding-step__intro">
        API keys are optional. Adding them helps Noofy download missing or new models from CivitAI and Hugging Face when those providers require access.
      </p>
      <p className="onboarding-step__privacy">
        Your keys stay on this computer. Noofy stores them locally, uses each key only for the provider you configure, and never includes full keys in logs, diagnostics, exports, or workflow packages.
      </p>

      {credentialStoreUnavailable ? (
        <div className="notice notice--warning notice--compact" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>{settingsLoadFailed ? "API key settings are unavailable" : "API key storage is unavailable"}</strong>
            <span>
              {settingsLoadFailed
                ? "Noofy could not load API key settings. You can continue now and add keys later in Settings."
                : apiSettings?.credential_store.guidance ?? apiSettings?.credential_store.error ?? "You can skip this step and add keys later in Settings."}
            </span>
          </div>
        </div>
      ) : null}

      {message ? (
        <div className={`notice ${message.ok ? "notice--success" : "notice--error"} notice--compact`} role="status">
          {message.ok ? <CheckCircle2 size={18} aria-hidden="true" /> : <AlertCircle size={18} aria-hidden="true" />}
          <div>
            <strong>{message.ok ? "Saved" : "Could not save key"}</strong>
            <span>{message.text}</span>
          </div>
        </div>
      ) : null}

      <div className="onboarding-api-list">
        {API_PROVIDERS.map((provider) => {
          const metadata = apiSettings?.providers[provider.id];
          const busy = savingProvider === provider.id;
          const draft = apiDrafts[provider.id];
          const status = metadata?.configured
            ? metadata.last_four
              ? `Configured, ending in ${metadata.last_four}`
              : "Configured"
            : "Optional";

          return (
            <div className="onboarding-api-row" key={provider.id}>
              <div className="onboarding-api-row__label">
                <label htmlFor={provider.fieldId}>{provider.label}</label>
                <span>{status}</span>
              </div>
              <div className="onboarding-api-row__controls">
                <input
                  id={provider.fieldId}
                  type="password"
                  value={draft}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={metadata?.configured ? "Paste a replacement key" : "Paste API key"}
                  disabled={credentialStoreUnavailable || busy}
                  onChange={(event) => onDraftChange(provider.id, event.target.value)}
                />
                <button
                  className="secondary-button"
                  type="button"
                  disabled={credentialStoreUnavailable || busy || !draft.trim()}
                  onClick={() => onSave(provider.id)}
                >
                  {busy ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <KeyRound size={16} aria-hidden="true" />}
                  Save
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ExistingComfyUIModelsStep({
  choice,
  modelFolders,
  settingsLoadFailed,
  busy,
  message,
  onChoose,
  onNotNow,
}: {
  choice: FolderChoice;
  modelFolders: ModelFolderSettings | null;
  settingsLoadFailed: boolean;
  busy: boolean;
  message: { text: string; ok: boolean } | null;
  onChoose: () => void;
  onNotNow: () => void;
}) {
  return (
    <div className="onboarding-step">
      <p className="onboarding-step__intro">
        If you already have models in ComfyUI, Noofy can reuse them instead of downloading duplicate copies.
      </p>
      <p className="onboarding-step__privacy">
        This folder is read/reuse-only. Noofy will not download new models into your existing ComfyUI folder.
      </p>
      <div className="onboarding-choice-row" role="group" aria-label="Existing ComfyUI models folder">
        <button
          className={`secondary-button ${choice === "yes" ? "secondary-button--selected" : ""}`}
          type="button"
          disabled={busy}
          onClick={onChoose}
        >
          {busy ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <FolderCog size={16} aria-hidden="true" />}
          Choose Folder
        </button>
        <button
          className={`secondary-button ${choice === "not_now" ? "secondary-button--selected" : ""}`}
          type="button"
          disabled={busy}
          onClick={onNotNow}
        >
          Not Now
        </button>
      </div>
      <div className="path-display" title={modelFolders?.external_comfyui_models_dir ?? ""}>
        {settingsLoadFailed
          ? "Noofy could not load the current connected folder"
          : modelFolders?.external_comfyui_models_dir ?? "No existing ComfyUI models folder connected"}
      </div>
      {message ? <FolderMessage message={message} /> : null}
    </div>
  );
}

function NoofyModelsStep({
  modelFolders,
  settingsLoadFailed,
  busy,
  message,
  onChoose,
}: {
  modelFolders: ModelFolderSettings | null;
  settingsLoadFailed: boolean;
  busy: boolean;
  message: { text: string; ok: boolean } | null;
  onChoose: () => void;
}) {
  return (
    <div className="onboarding-step">
      <p className="onboarding-step__intro">This is where Noofy will save models it downloads for you.</p>
      <p className="onboarding-step__privacy">
        You can keep the default folder or choose another location with more space. Existing ComfyUI models stay separate.
      </p>
      <div className="path-display" title={modelFolders?.noofy_models_dir ?? ""}>
        {settingsLoadFailed
          ? "Noofy could not load the current folder"
          : modelFolders?.noofy_models_dir ?? "Loading Noofy Models folder..."}
      </div>
      <div className="button-row">
        <button className="secondary-button" type="button" disabled={busy} onClick={onChoose}>
          {busy ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <FolderCog size={16} aria-hidden="true" />}
          Change Folder
        </button>
      </div>
      {message ? <FolderMessage message={message} /> : null}
    </div>
  );
}

function StartCreatingStep({
  workflows,
  selectedWorkflowId,
  onSelectedWorkflowChange,
}: {
  workflows: WorkflowSummary[];
  selectedWorkflowId: string;
  onSelectedWorkflowChange: (workflowId: string) => void;
}) {
  const selected = workflows.find((workflow) => workflow.id === selectedWorkflowId) ?? workflows[0] ?? null;

  return (
    <div className="onboarding-step">
      <p className="onboarding-step__intro">
        Your setup is ready. Start with a simple image generation workflow and create your first image locally.
      </p>
      {selected ? (
        <article className="onboarding-starter-card">
          <div className="onboarding-starter-card__icon" aria-hidden="true">
            <ImagePlus size={22} />
          </div>
          <div className="onboarding-starter-card__body">
            <span className="category-badge">Starter workflow</span>
            <h3>Text to Image</h3>
            <p>Generate a new image from a simple text prompt.</p>
            {workflows.length > 1 ? (
              <label className="workflow-variant-select">
                <span>Model</span>
                <div className="workflow-variant-select__control">
                  <select
                    aria-label="Text to Image model workflow"
                    value={selected.id}
                    onChange={(event) => onSelectedWorkflowChange(event.target.value)}
                  >
                    {workflows.map((workflow) => (
                      <option key={workflow.id} value={workflow.id}>
                        {starterVariantLabel(workflow)}
                      </option>
                    ))}
                  </select>
                  <ChevronDown size={15} aria-hidden="true" />
                </div>
              </label>
            ) : null}
          </div>
        </article>
      ) : (
        <div className="onboarding-starter-card onboarding-starter-card--empty">
          <div className="onboarding-starter-card__icon" aria-hidden="true">
            <ImagePlus size={22} />
          </div>
          <div className="onboarding-starter-card__body">
            <span className="category-badge">Workflows</span>
            <h3>Browse workflows</h3>
            <p>Noofy could not find the built-in Text to Image workflow right now. You can browse available workflows instead.</p>
          </div>
        </div>
      )}
    </div>
  );
}

function FolderMessage({ message }: { message: { text: string; ok: boolean } }) {
  return (
    <div className={`notice ${message.ok ? "notice--success" : "notice--error"} notice--compact`} role="status">
      {message.ok ? <CheckCircle2 size={18} aria-hidden="true" /> : <AlertCircle size={18} aria-hidden="true" />}
      <div>
        <strong>{message.ok ? "Saved" : "Folder could not be saved"}</strong>
        <span>{message.text}</span>
      </div>
    </div>
  );
}

function stepTitle(stepIndex: number) {
  if (stepIndex === 0) return "Connect model providers";
  if (stepIndex === 1) return "Reuse existing ComfyUI models";
  if (stepIndex === 2) return "Choose your Noofy Models folder";
  return "Start creating";
}

function stepSubtitle(stepIndex: number) {
  if (stepIndex === 0) return "You can add API keys now, or skip them and add keys later in Settings.";
  if (stepIndex === 1) return "Point Noofy at models you already downloaded so it can reuse them.";
  if (stepIndex === 2) return "Confirm where new model downloads should live.";
  return "Open a starter workflow when you are ready.";
}

function providerLabel(provider: ApiKeyProviderId) {
  if (provider === "civitai") return "CivitAI";
  if (provider === "hugging_face") return "Hugging Face";
  return "Provider";
}

function textToImageWorkflows(workflows: WorkflowSummary[]) {
  const candidates = workflows.filter((workflow) => {
    if (!isNativeBundledWorkflow(workflow)) return false;
    if (!isStarterWorkflowOpenable(workflow)) return false;
    if (workflow.id === TEXT_TO_IMAGE_WORKFLOW_ID) return true;
    const normalizedName = normalizeWorkflowName(workflow.name);
    return matchesNativeWorkflowName(normalizedName, "text to image") || workflow.category?.toLowerCase() === "txt2img";
  });
  return candidates.sort((a, b) => {
    if (a.id === TEXT_TO_IMAGE_WORKFLOW_ID) return -1;
    if (b.id === TEXT_TO_IMAGE_WORKFLOW_ID) return 1;
    return a.name.localeCompare(b.name);
  });
}

function isStarterWorkflowOpenable(workflow: WorkflowSummary) {
  if (workflow.status === "needs_input_setup" || workflow.status === "cannot_prepare_automatically") return false;
  if (workflow.dashboard_status === "invalid" || workflow.dashboard_ready === false) return false;
  return true;
}

function isNativeBundledWorkflow(workflow: WorkflowSummary) {
  if (workflow.source_label === "Imported" || workflow.trust_level === "quarantined_community" || workflow.can_remove) {
    return false;
  }
  if (workflow.status === "imported") return false;
  return workflow.source_label === undefined || workflow.source_label === "Native Noofy";
}

function normalizeWorkflowName(value: string) {
  return value
    .replace(/[\u2014\u2013]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function matchesNativeWorkflowName(normalizedName: string, baseName: string) {
  return normalizedName === baseName || normalizedName.startsWith(`${baseName} - `) || normalizedName.startsWith(`${baseName}: `);
}

function starterVariantLabel(workflow: WorkflowSummary) {
  const rawLabel = workflow.name
    .replace(/^Text\s+to\s+Image/i, "")
    .replace(/^[\s:\u2014\u2013-]+/, "")
    .trim();
  if (rawLabel) return rawLabel;
  if (workflow.main_model?.name && workflow.main_model.name !== "No model detected") return workflow.main_model.name;
  return "Default";
}
