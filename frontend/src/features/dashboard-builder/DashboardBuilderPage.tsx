import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  GripVertical,
  ImagePlus,
  LayoutGrid,
  Loader2,
  Plus,
  Save,
  Search,
  Sparkles,
  Trash2,
  Wand2,
  X,
} from "lucide-react";

import { fetchBindableInputs, fetchRuntimeStatus, type RuntimeStatus } from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";
import {
  WIDGET_TYPE_LABELS,
  MOCK_WORKFLOW,
  NODE_ICONS,
  VALUE_KIND_ICONS,
  buildInitialDashboard,
  createDashboardWidgetForValue,
  defaultNumericRangeForValue,
  loadDashboardDraft,
  saveDashboardDraft,
  widgetTypesForKind,
  workflowFromBindableInputs,
  type WidgetType,
  type DashboardWidget,
  type DashboardSchema,
  type MockWorkflow,
  type WorkflowNode,
  type WorkflowNodeValue,
} from "./dashboardBuilderContent";

interface DashboardBuilderPageProps {
  workflowId?: string;
  workflowName?: string;
  initialSchema?: DashboardSchema;
  onBack: () => void;
  onContinue: (schema: DashboardSchema) => void;
  onNavigate: (route: AppRouteId) => void;
}

interface RuntimeState {
  loading: boolean;
  runtime: RuntimeStatus | null;
}

interface WorkflowAuthoringState {
  loading: boolean;
  workflow: MockWorkflow | null;
  error: string | null;
}

function emptyWorkflow(workflowId: string, workflowName: string): MockWorkflow {
  return {
    ...MOCK_WORKFLOW,
    id: workflowId,
    name: workflowName,
    source: "imported_noofy_package",
    nodes: [],
  };
}

export function DashboardBuilderPage({
  workflowId,
  workflowName,
  initialSchema,
  onBack,
  onContinue,
  onNavigate,
}: DashboardBuilderPageProps) {
  const activeWorkflowId = workflowId ?? MOCK_WORKFLOW.id;
  const activeWorkflowName = workflowName ?? (workflowId ? workflowId : MOCK_WORKFLOW.name);
  const scopedInitialSchema = initialSchema?.workflowId === activeWorkflowId ? initialSchema : undefined;
  const loadSequenceRef = useRef(0);
  const [runtimeState, setRuntimeState] = useState<RuntimeState>({ loading: true, runtime: null });
  const [workflowState, setWorkflowState] = useState<WorkflowAuthoringState>(() => {
    if (workflowId) return { loading: true, workflow: null, error: null };
    return {
      loading: false,
      workflow: { ...MOCK_WORKFLOW, id: activeWorkflowId, name: activeWorkflowName },
      error: null,
    };
  });
  const [schema, setSchema] = useState<DashboardSchema>(
    () => scopedInitialSchema ?? loadDashboardDraft(activeWorkflowId) ?? buildInitialDashboard(emptyWorkflow(activeWorkflowId, activeWorkflowName)),
  );
  const [selectedValueId, setSelectedValueId] = useState<string | null>(null);
  const [selectedWidgetId, setSelectedWidgetId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(() => new Set());
  const [savedFlash, setSavedFlash] = useState<"saved" | "draft" | null>(null);

  useEffect(() => {
    let mounted = true;
    fetchRuntimeStatus()
      .then((runtime) => {
        if (mounted) setRuntimeState({ loading: false, runtime });
      })
      .catch(() => {
        if (mounted) setRuntimeState({ loading: false, runtime: null });
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!workflowId) {
      const workflow = { ...MOCK_WORKFLOW, id: activeWorkflowId, name: activeWorkflowName };
      setWorkflowState({ loading: false, workflow, error: null });
      return;
    }
    const loadSequence = ++loadSequenceRef.current;
    setWorkflowState({ loading: true, workflow: null, error: null });
    fetchBindableInputs(workflowId)
      .then((res) => {
        if (loadSequence !== loadSequenceRef.current) return;
        setWorkflowState({
          loading: false,
          workflow: workflowFromBindableInputs(workflowId, activeWorkflowName, res.nodes),
          error: null,
        });
      })
      .catch(() => {
        if (loadSequence !== loadSequenceRef.current) return;
        setWorkflowState({
          loading: false,
          workflow: emptyWorkflow(workflowId, activeWorkflowName),
          error: "Workflow values could not be loaded.",
        });
      });
    return () => {
      loadSequenceRef.current += 1;
    };
  }, [workflowId, activeWorkflowId, activeWorkflowName]);

  const appStatus = runtimeStatusCopy(runtimeState);

  useLayoutEffect(() => {
    setSchema(
      scopedInitialSchema ??
        loadDashboardDraft(activeWorkflowId) ??
        buildInitialDashboard(emptyWorkflow(activeWorkflowId, activeWorkflowName)),
    );
    setSelectedValueId(null);
    setSelectedWidgetId(null);
    setSearch("");
    setExpandedNodes(new Set());
    setSavedFlash(null);
  }, [activeWorkflowId]);

  useEffect(() => {
    const workflow = workflowState.workflow;
    if (workflowState.loading || !workflow || workflow.id !== activeWorkflowId) return;
    const nextSchema = scopedInitialSchema ?? loadDashboardDraft(activeWorkflowId) ?? buildInitialDashboard(workflow);
    setSchema(nextSchema);
    const firstWidget = nextSchema.widgets[0];
    if (firstWidget) {
      setSelectedWidgetId(firstWidget.id);
      setSelectedValueId(firstWidget.valueId);
    } else {
      setSelectedWidgetId(null);
      setSelectedValueId(null);
    }
    setExpandedNodes(new Set([workflow.nodes[0]?.id ?? ""]));
  }, [workflowState, activeWorkflowId, scopedInitialSchema]);

  const workflow = workflowState.workflow;
  const builderReady =
    !workflowState.loading &&
    workflow !== null &&
    workflow.id === activeWorkflowId &&
    schema.workflowId === activeWorkflowId;
  const displayWorkflowName = builderReady ? workflow.name : activeWorkflowName;

  const valueIndex = useMemo(() => {
    const map = new Map<string, { node: WorkflowNode; value: WorkflowNodeValue }>();
    if (!builderReady || !workflow) return map;
    for (const node of workflow.nodes) {
      for (const value of node.values) {
        map.set(value.id, { node, value });
      }
    }
    return map;
  }, [builderReady, workflow]);

  const exposedValueIds = useMemo(
    () => new Set(builderReady ? schema.widgets.map((c) => c.valueId) : []),
    [builderReady, schema.widgets],
  );

  const filteredNodes = useMemo(() => {
    if (!builderReady || !workflow) return [];
    const query = search.trim().toLowerCase();
    if (!query) return workflow.nodes;

    return workflow.nodes
      .map((node) => {
        const filteredValues = node.values.filter((value) => {
          return (
            value.label.toLowerCase().includes(query) ||
            node.title.toLowerCase().includes(query) ||
            node.classType.toLowerCase().includes(query)
          );
        });
        return { ...node, values: filteredValues };
      })
      .filter((node) => node.values.length > 0);
  }, [builderReady, workflow, search]);

  const selectedWidget = useMemo(
    () => (builderReady && selectedWidgetId ? schema.widgets.find((c) => c.id === selectedWidgetId) ?? null : null),
    [builderReady, schema.widgets, selectedWidgetId],
  );

  const selectedValueRecord = selectedValueId ? valueIndex.get(selectedValueId) ?? null : null;
  const hasValidationErrors = builderReady && schema.widgets.some((widget) => validateWidgetForSave(widget).length > 0);

  function handleSelectValue(valueId: string) {
    const record = valueIndex.get(valueId);
    if (!record) return;

    const existing = schema.widgets.find((c) => c.valueId === valueId);
    if (existing) {
      setSelectedValueId(valueId);
      setSelectedWidgetId(existing.id);
      return;
    }

    const newWidget = createDashboardWidgetForValue(record.value, record.node);
    setSchema((current) => ({ ...current, widgets: [...current.widgets, newWidget] }));
    setSelectedValueId(valueId);
    setSelectedWidgetId(newWidget.id);
  }

  function handleSelectWidget(widgetId: string) {
    const widget = schema.widgets.find((c) => c.id === widgetId);
    if (!widget) return;
    setSelectedWidgetId(widgetId);
    setSelectedValueId(widget.valueId);
  }

  function patchWidget(widgetId: string, patch: Partial<DashboardWidget>) {
    setSchema((current) => ({
      ...current,
      widgets: current.widgets.map((c) => (c.id === widgetId ? { ...c, ...patch } : c)),
    }));
  }

  function removeWidget(widgetId: string) {
    setSchema((current) => ({
      ...current,
      widgets: current.widgets.filter((c) => c.id !== widgetId),
    }));
    if (selectedWidgetId === widgetId) {
      setSelectedWidgetId(null);
      setSelectedValueId(null);
    }
  }

  function toggleNode(nodeId: string) {
    setExpandedNodes((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  }

  function handleSaveDraft() {
    if (!builderReady || hasValidationErrors) return;
    saveDashboardDraft(schema);
    setSavedFlash("draft");
    window.setTimeout(() => setSavedFlash(null), 2400);
  }

  function handleContinue() {
    if (!builderReady || schema.widgets.length === 0 || hasValidationErrors) return;
    onContinue(schema);
  }

  const simpleWidgets = builderReady ? schema.widgets.filter((c) => c.group === "simple") : [];
  const advancedWidgets = builderReady ? schema.widgets.filter((c) => c.group === "advanced") : [];

  return (
    <AppLayout activeRoute="workflows" status={appStatus} onNavigate={onNavigate}>
      <div className="builder-page">
        <section className="builder-heading" aria-labelledby="builder-title">
          <div className="builder-heading__text">
            <div className="builder-heading__eyebrow-row">
              <button className="ghost-button ghost-button--back" type="button" onClick={onBack}>
                <ArrowLeft size={15} aria-hidden="true" />
                Back to workflows
              </button>
              <h1 id="builder-title" className="builder-heading__inline-title">Dashboard Builder · {displayWorkflowName}</h1>
            </div>
            <p>Choose which workflow values become simple widgets.</p>
          </div>

          <div className="builder-heading__meta">
            <div className="status-pill status-pill--info">
              <span />
              <span>{savedFlash === "saved" ? "Dashboard saved" : "Draft dashboard"}</span>
            </div>
            <div className="button-row">
              <button className="secondary-button" type="button" onClick={handleSaveDraft} disabled={!builderReady || hasValidationErrors}>
                <Save size={15} aria-hidden="true" />
                Save as draft
              </button>
              <button
                className="primary-button primary-button--compact"
                type="button"
                onClick={handleContinue}
                disabled={!builderReady || schema.widgets.length === 0 || hasValidationErrors}
              >
                <ArrowRight size={16} aria-hidden="true" />
                Continue
              </button>
            </div>
          </div>
        </section>

        {savedFlash ? (
          <div className="notice" role="status">
            <CheckCircle2 size={18} aria-hidden="true" />
            <div>
              <strong>{savedFlash === "saved" ? "Dashboard saved" : "Saved as draft"}</strong>
              <span>
                {savedFlash === "saved"
                  ? "End users can now open this workflow with the simple dashboard."
                  : "You can come back later to finish the dashboard before sharing."}
              </span>
            </div>
          </div>
        ) : null}

        {!builderReady ? (
          <DashboardBuilderLoadingState />
        ) : (
          <div className="builder-grid">
          <aside className="builder-pane builder-values" aria-label="Workflow values">
            <header className="builder-pane__header">
              <div>
                <h2>Workflow values</h2>
                <p>Pick a value to expose as a friendly widget.</p>
              </div>
            </header>
            <div className="builder-pane__toolbar">
              <label className="search-field search-field--builder">
                <Search size={15} aria-hidden="true" />
                <span className="sr-only">Search workflow values</span>
                <input
                  type="search"
                  placeholder="Search values..."
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </label>
            </div>

            <div className="builder-pane__scroll">
              {workflowState.error ? (
                <div className="builder-empty builder-empty--small">
                  <Search size={26} aria-hidden="true" />
                  <p>{workflowState.error}</p>
                </div>
              ) : filteredNodes.length === 0 ? (
                <div className="builder-empty builder-empty--small">
                  <Search size={26} aria-hidden="true" />
                  <p>No values match your search.</p>
                </div>
              ) : (
                <ul className="builder-node-list">
                  {filteredNodes.map((node) => (
                    <NodeListItem
                      key={node.id}
                      node={node}
                      expanded={expandedNodes.has(node.id) || search.trim().length > 0}
                      exposedIds={exposedValueIds}
                      selectedValueId={selectedValueId}
                      onToggle={() => toggleNode(node.id)}
                      onSelectValue={handleSelectValue}
                    />
                  ))}
                </ul>
              )}
            </div>
          </aside>

          <main className="builder-pane builder-config" aria-label="Widget configuration">
            {selectedWidget && selectedValueRecord ? (
              <WidgetEditor
                widget={selectedWidget}
                value={selectedValueRecord.value}
                node={selectedValueRecord.node}
                onPatch={(patch) => patchWidget(selectedWidget.id, patch)}
                onRemove={() => removeWidget(selectedWidget.id)}
              />
            ) : (
              <BuilderEmptyState />
            )}
          </main>

          <aside className="builder-pane builder-preview" aria-label="Dashboard preview">
            <header className="builder-pane__header builder-pane__header--preview">
              <div>
                <h2>Created widgets</h2>
                <p>Contains the widgets that will be added to the dashboard.</p>
              </div>
              <span className="builder-preview__chip">
                <LayoutGrid size={13} aria-hidden="true" />
                Preview
              </span>
            </header>

            <div className="builder-pane__scroll builder-preview__canvas">
              {schema.widgets.length === 0 ? (
                <div className="builder-empty">
                  <div className="builder-empty__icon">
                    <Wand2 size={26} aria-hidden="true" />
                  </div>
                  <h3>Your dashboard is empty</h3>
                  <p>Select a workflow value and turn it into a dashboard widget.</p>
                </div>
              ) : (
                <>
                  {simpleWidgets.length > 0 && (
                    <PreviewSection title="Simple widgets" widgets={simpleWidgets}
                      selectedWidgetId={selectedWidgetId}
                      onSelect={handleSelectWidget}
                      onRemove={removeWidget}
                    />
                  )}

                  {advancedWidgets.length > 0 && (
                    <PreviewSection title="Advanced widgets" widgets={advancedWidgets}
                      selectedWidgetId={selectedWidgetId}
                      onSelect={handleSelectWidget}
                      onRemove={removeWidget}
                      muted
                    />
                  )}
                </>
              )}
            </div>

            <footer className="builder-preview__footer">
              <button className="primary-button primary-button--full" type="button" disabled>
                <Sparkles size={16} aria-hidden="true" />
                Run workflow
              </button>
              <p>Preview only. End-users will see this dashboard when they open the workflow.</p>
            </footer>
          </aside>
          </div>
        )}
      </div>
    </AppLayout>
  );
}

function DashboardBuilderLoadingState() {
  return (
    <div className="builder-loading" aria-live="polite" aria-busy="true">
      <div className="builder-loading__panel" role="status">
        <div className="builder-loading__status">
          <span className="builder-loading__spinner" aria-hidden="true">
            <Loader2 className="spin" size={18} />
          </span>
          <div>
            <strong>Loading dashboard builder</strong>
            <span>Preparing this workflow's values, widgets, and saved dashboard draft.</span>
          </div>
        </div>
        <div className="builder-loading__preview" aria-hidden="true">
          <div className="builder-loading__sidebar">
            <div className="builder-loading-line builder-loading-line--title" />
            <div className="builder-loading-block" />
            <div className="builder-loading-block" />
            <div className="builder-loading-block builder-loading-block--short" />
          </div>
          <div className="builder-loading__main">
            <div className="builder-loading-line builder-loading-line--wide" />
            <div className="builder-loading-card-grid">
              <div className="builder-loading-card" />
              <div className="builder-loading-card" />
            </div>
            <div className="builder-loading-block builder-loading-block--wide" />
          </div>
        </div>
      </div>
    </div>
  );
}

function NodeListItem({
  node,
  expanded,
  exposedIds,
  selectedValueId,
  onToggle,
  onSelectValue,
}: {
  node: WorkflowNode;
  expanded: boolean;
  exposedIds: Set<string>;
  selectedValueId: string | null;
  onToggle: () => void;
  onSelectValue: (id: string) => void;
}) {
  const Icon = NODE_ICONS[node.iconKind];
  const exposedCount = node.values.filter((value) => exposedIds.has(value.id)).length;

  return (
    <li className="builder-node">
      <button
        type="button"
        className={`builder-node__header ${expanded ? "builder-node__header--expanded" : ""}`}
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="builder-node__chevron" aria-hidden="true">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <span className="builder-node__icon" aria-hidden="true">
          <Icon size={14} />
        </span>
        <span className="builder-node__title">{node.title}</span>
        <span className="builder-node__count">{node.values.length}</span>
        {exposedCount > 0 ? <span className="builder-node__exposed-dot" aria-label={`${exposedCount} exposed`} /> : null}
      </button>

      {expanded && node.values.length > 0 ? (
        <ul className="builder-value-list">
          {node.values.map((value) => {
            const isExposed = exposedIds.has(value.id);
            const isSelected = selectedValueId === value.id;
            const ValueIcon = VALUE_KIND_ICONS[value.valueKind];
            return (
              <li key={value.id}>
                <button
                  type="button"
                  className={`builder-value ${isExposed ? "builder-value--exposed" : ""} ${
                    isSelected ? "builder-value--selected" : ""
                  }`}
                  onClick={() => onSelectValue(value.id)}
                >
                  <span className="builder-value__icon" aria-hidden="true">
                    <ValueIcon size={13} />
                  </span>
                  <span className="builder-value__label">{value.label}</span>
                  <span className="builder-value__badge">{isExposed ? "Exposed" : "Hidden"}</span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </li>
  );
}

type SliderValidationField = "defaultValue" | "min" | "max" | "step";

interface SliderValidationError {
  field: SliderValidationField;
  message: string;
}

function validateWidgetForSave(widget: DashboardWidget): SliderValidationError[] {
  return widget.widgetType === "slider" ? validateSliderWidget(widget) : [];
}

function validateSliderWidget(widget: DashboardWidget): SliderValidationError[] {
  if (widget.widgetType !== "slider") return [];

  const errors: SliderValidationError[] = [];
  const defaultValue = finiteNumber(widget.defaultValue);
  const min = finiteNumber(widget.min);
  const max = finiteNumber(widget.max);
  const step = finiteNumber(widget.step);

  if (defaultValue === null) errors.push({ field: "defaultValue", message: "Enter a default value." });
  if (min === null) errors.push({ field: "min", message: "Enter a minimum value." });
  if (max === null) errors.push({ field: "max", message: "Enter a maximum value." });
  if (step === null) {
    errors.push({ field: "step", message: "Enter a step size." });
  } else if (step <= 0) {
    errors.push({ field: "step", message: "Step size must be greater than 0." });
  }

  if (min !== null && max !== null && max <= min) {
    errors.push({ field: "max", message: "Maximum value must be greater than minimum value." });
  }

  if (defaultValue !== null && min !== null && max !== null && max > min) {
    if (defaultValue < min || defaultValue > max) {
      errors.push({ field: "defaultValue", message: "Default value must be between the minimum and maximum." });
    }
  }

  if (min !== null && max !== null && max > min && step !== null && step > 0) {
    if (!alignsWithStep(max, min, step)) {
      errors.push({ field: "max", message: "Maximum value must match the step size from the minimum value." });
    }
    if (defaultValue !== null && defaultValue >= min && defaultValue <= max && !alignsWithStep(defaultValue, min, step)) {
      errors.push({ field: "defaultValue", message: "Default value must match the step size from the minimum value." });
    }
  }

  return errors;
}

function sliderDefaultsForValue(value: WorkflowNodeValue, widget: DashboardWidget): Partial<DashboardWidget> {
  const range = defaultNumericRangeForValue(value) ?? { min: 0, max: 100, step: 1 };
  const min = finiteNumber(widget.min) ?? range.min;
  const max = finiteNumber(widget.max) ?? range.max;
  const step = positiveFiniteNumber(widget.step) ?? range.step;
  const defaultValue = finiteNumber(widget.defaultValue) ?? finiteNumber(value.rawValue) ?? min;

  return { min, max, step, defaultValue };
}

function numericInputValue(value: unknown): number | "" {
  const numeric = finiteNumber(value);
  return numeric ?? "";
}

function parseNumberInput(value: string): number {
  return value.trim() === "" ? Number.NaN : Number(value);
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function positiveFiniteNumber(value: unknown): number | null {
  const numeric = finiteNumber(value);
  return numeric !== null && numeric > 0 ? numeric : null;
}

function alignsWithStep(value: number, min: number, step: number): boolean {
  const stepIndex = (value - min) / step;
  return Math.abs(stepIndex - Math.round(stepIndex)) < 1e-7;
}

function WidgetEditor({
  widget,
  value,
  node,
  onPatch,
  onRemove,
}: {
  widget: DashboardWidget;
  value: WorkflowNodeValue;
  node: WorkflowNode;
  onPatch: (patch: Partial<DashboardWidget>) => void;
  onRemove: () => void;
}) {
  const allowedTypes = widgetTypesForKind(value.valueKind);
  const showSliderSettings = widget.widgetType === "slider";
  const showNumberRange = widget.widgetType === "int_field";
  const showOptions = widget.widgetType === "select" || widget.widgetType === "lora_loader";
  const showImageOptions = widget.widgetType === "load_image" || widget.widgetType === "load_image_mask";
  const sliderErrors = validateSliderWidget(widget);
  const sliderErrorFor = (field: SliderValidationField) => sliderErrors.find((error) => error.field === field)?.message;
  const defaultRange = defaultNumericRangeForValue(value);

  return (
    <div className="builder-config__inner">
      <div className="builder-config__top">
        <div>
          <p className="builder-config__breadcrumb">
            <span>{node.title}</span>
            <ChevronRight size={12} aria-hidden="true" />
            <span>{value.label}</span>
          </p>
          <h2>Configure widget</h2>
          <p className="builder-config__summary">
            This widget is connected to a workflow value. People running this workflow will see your clear label instead of the ComfyUI node name.
          </p>
        </div>
        <button className="icon-button icon-button--danger" type="button" onClick={onRemove} aria-label="Remove widget" title="Remove widget">
          <Trash2 size={16} aria-hidden="true" />
        </button>
      </div>

      <FormCard title="Widget details">
        <FieldRow label="Widget title">
          <input
            type="text"
            className="builder-input"
            value={widget.title}
            onChange={(event) => onPatch({ title: event.target.value })}
          />
        </FieldRow>
        <FieldRow label="Helper description" hint="Shown under the widget. Keep it short and friendly.">
          <textarea
            className="builder-input builder-input--textarea"
            rows={2}
            value={widget.description}
            onChange={(event) => onPatch({ description: event.target.value })}
          />
        </FieldRow>
      </FormCard>

      <FormCard title="Widget behavior">
        <div className="builder-config__grid">
          <FieldRow label="Widget type">
            <select
              className="builder-input"
              value={widget.widgetType}
              onChange={(event) => {
                const widgetType = event.target.value as WidgetType;
                onPatch(widgetType === "slider" ? { widgetType, ...sliderDefaultsForValue(value, widget) } : { widgetType });
              }}
            >
              {allowedTypes.map((type) => (
                <option key={type} value={type}>
                  {WIDGET_TYPE_LABELS[type]}
                </option>
              ))}
            </select>
          </FieldRow>
          <FieldRow label="Group">
            <SegmentedControl
              ariaLabel="Group"
              options={[
                { id: "simple", label: "Simple" },
                { id: "advanced", label: "Advanced" },
              ]}
              value={widget.group}
              onChange={(group) => onPatch({ group })}
            />
          </FieldRow>
        </div>

        <FieldRow label="Orientation">
          <SegmentedControl
            ariaLabel="Orientation"
            options={[
              { id: "vertical", label: "Stacked" },
              { id: "horizontal", label: "Inline" },
            ]}
            value={widget.orientation}
            onChange={(orientation) => onPatch({ orientation })}
          />
        </FieldRow>
      </FormCard>

      {showSliderSettings ? (
        <FormCard title="Slider settings">
          <div className="builder-config__grid">
            <FieldRow label="Default value" error={sliderErrorFor("defaultValue")}>
              <input
                type="number"
                className="builder-input"
                value={numericInputValue(widget.defaultValue)}
                step={positiveFiniteNumber(widget.step) ?? defaultRange?.step ?? 1}
                aria-invalid={Boolean(sliderErrorFor("defaultValue"))}
                onChange={(event) => onPatch({ defaultValue: parseNumberInput(event.target.value) })}
              />
            </FieldRow>
            <FieldRow label="Minimum value" error={sliderErrorFor("min")}>
              <input
                type="number"
                className="builder-input"
                value={numericInputValue(widget.min)}
                step={positiveFiniteNumber(widget.step) ?? defaultRange?.step ?? 1}
                aria-invalid={Boolean(sliderErrorFor("min"))}
                onChange={(event) => onPatch({ min: parseNumberInput(event.target.value) })}
              />
            </FieldRow>
            <FieldRow label="Maximum value" error={sliderErrorFor("max")}>
              <input
                type="number"
                className="builder-input"
                value={numericInputValue(widget.max)}
                step={positiveFiniteNumber(widget.step) ?? defaultRange?.step ?? 1}
                aria-invalid={Boolean(sliderErrorFor("max"))}
                onChange={(event) => onPatch({ max: parseNumberInput(event.target.value) })}
              />
            </FieldRow>
            <FieldRow
              label="Step size"
              hint="Controls how much the value changes each time the slider moves."
              error={sliderErrorFor("step")}
            >
              <input
                type="number"
                className="builder-input"
                value={numericInputValue(widget.step)}
                min={0}
                step="any"
                aria-invalid={Boolean(sliderErrorFor("step"))}
                onChange={(event) => onPatch({ step: parseNumberInput(event.target.value) })}
              />
            </FieldRow>
          </div>
        </FormCard>
      ) : null}

      {showNumberRange && value.numberRange ? (
        <FormCard title="Numeric range">
          <div className="builder-config__grid builder-config__grid--three">
            <FieldRow label="Minimum">
              <input
                type="number"
                className="builder-input"
                value={widget.min ?? value.numberRange.min}
                step={value.numberRange.step ?? 1}
                onChange={(event) => onPatch({ min: Number(event.target.value) })}
              />
            </FieldRow>
            <FieldRow label="Maximum">
              <input
                type="number"
                className="builder-input"
                value={widget.max ?? value.numberRange.max}
                step={value.numberRange.step ?? 1}
                onChange={(event) => onPatch({ max: Number(event.target.value) })}
              />
            </FieldRow>
            <FieldRow label="Step">
              <input
                type="number"
                className="builder-input"
                value={widget.step ?? value.numberRange.step ?? 1}
                step={value.numberRange.step ?? 1}
                onChange={(event) => onPatch({ step: Number(event.target.value) })}
              />
            </FieldRow>
          </div>
        </FormCard>
      ) : null}

      {showOptions ? (
        <FormCard title="Options">
          <DropdownOptionsEditor
            options={widget.options ?? value.options ?? []}
            onChange={(options) => onPatch({ options })}
          />
        </FormCard>
      ) : null}

      {showImageOptions ? (
        <FormCard title="Image input">
          <ToggleRow
            checked={Boolean(widget.drawMask)}
            onChange={(drawMask) =>
              onPatch({ drawMask, widgetType: drawMask ? "load_image_mask" : "load_image" })
            }
            label="Allow drawing a mask"
            hint="Adds a mask brush over the uploaded image."
          />
        </FormCard>
      ) : null}

      {widget.widgetType !== "display_image" ? (
        widget.widgetType === "slider" ? null : (
          <FormCard title="Default value">
            <DefaultValueEditor widget={widget} value={value} onPatch={onPatch} />
          </FormCard>
        )
      ) : null}

      <div className="builder-config__binding">
        <span>Connected to</span>
        <code>node {widget.binding.nodeId}</code>
        <span className="builder-config__binding-arrow">→</span>
        <code>{widget.binding.inputName}</code>
      </div>
    </div>
  );
}

function DropdownOptionsEditor({
  options,
  onChange,
}: {
  options: string[];
  onChange: (options: string[]) => void;
}) {
  const visibleOptions = options.length > 0 ? options : [""];

  function updateOption(index: number, nextValue: string) {
    onChange(visibleOptions.map((option, optionIndex) => (optionIndex === index ? nextValue : option)));
  }

  function addOption() {
    const existingOptions = options.length > 0 ? visibleOptions : [];
    onChange([...existingOptions, `Option ${existingOptions.length + 1}`]);
  }

  function removeOption(index: number) {
    onChange(visibleOptions.filter((_, optionIndex) => optionIndex !== index));
  }

  function moveOption(index: number, direction: -1 | 1) {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= visibleOptions.length) return;
    const nextOptions = [...visibleOptions];
    [nextOptions[index], nextOptions[nextIndex]] = [nextOptions[nextIndex], nextOptions[index]];
    onChange(nextOptions);
  }

  return (
    <div className="builder-options-editor">
      <div className="builder-options-editor__header">
        <div>
          <span className="builder-field__label">Choices</span>
          <span className="builder-field__hint">Each dropdown item gets its own field.</span>
        </div>
        <button className="secondary-button secondary-button--small" type="button" onClick={addOption}>
          <Plus size={14} aria-hidden="true" />
          Add option
        </button>
      </div>

      <div className="builder-options-grid">
        {visibleOptions.map((option, index) => (
          <div className="builder-options-grid__item" key={`${index}-${visibleOptions.length}`}>
            <input
              type="text"
              className="builder-input builder-input--compact"
              aria-label={`Dropdown option ${index + 1}`}
              value={option}
              onChange={(event) => updateOption(index, event.target.value)}
            />
            <div className="builder-options-grid__actions" role="group" aria-label={`Edit dropdown option ${index + 1}`}>
              <button
                className="icon-button icon-button--small"
                type="button"
                onClick={() => moveOption(index, -1)}
                disabled={index === 0}
                aria-label={`Move option ${index + 1} up`}
                title="Move up"
              >
                <ArrowUp size={13} aria-hidden="true" />
              </button>
              <button
                className="icon-button icon-button--small"
                type="button"
                onClick={() => moveOption(index, 1)}
                disabled={index === visibleOptions.length - 1}
                aria-label={`Move option ${index + 1} down`}
                title="Move down"
              >
                <ArrowDown size={13} aria-hidden="true" />
              </button>
              <button
                className="icon-button icon-button--small icon-button--danger"
                type="button"
                onClick={() => removeOption(index)}
                aria-label={`Remove option ${index + 1}`}
                title="Remove option"
              >
                <X size={13} aria-hidden="true" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function DefaultValueEditor({
  widget,
  value,
  onPatch,
}: {
  widget: DashboardWidget;
  value: WorkflowNodeValue;
  onPatch: (patch: Partial<DashboardWidget>) => void;
}) {
  if (widget.widgetType === "textarea") {
    return (
      <textarea
        className="builder-input builder-input--textarea"
        rows={4}
        value={String(widget.defaultValue ?? "")}
        onChange={(event) => onPatch({ defaultValue: event.target.value })}
      />
    );
  }

  if (widget.widgetType === "string_field") {
    return (
      <input
        type="text"
        className="builder-input"
        value={String(widget.defaultValue ?? "")}
        onChange={(event) => onPatch({ defaultValue: event.target.value })}
      />
    );
  }

  if (widget.widgetType === "slider" || widget.widgetType === "int_field" || widget.widgetType === "seed_widget") {
    return (
      <input
        type="number"
        className="builder-input"
        value={Number(widget.defaultValue ?? 0)}
        step={widget.step ?? value.numberRange?.step ?? 1}
        onChange={(event) => onPatch({ defaultValue: Number(event.target.value) })}
      />
    );
  }

  if (widget.widgetType === "toggle") {
    return (
      <ToggleRow
        checked={Boolean(widget.defaultValue)}
        onChange={(checked) => onPatch({ defaultValue: checked })}
        label={widget.defaultValue ? "On" : "Off"}
      />
    );
  }

  if (widget.widgetType === "select" || widget.widgetType === "lora_loader") {
    const options = widget.options ?? value.options ?? [];
    return (
      <select
        className="builder-input"
        value={String(widget.defaultValue ?? options[0] ?? "")}
        onChange={(event) => onPatch({ defaultValue: event.target.value })}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }

  if (widget.widgetType === "load_image" || widget.widgetType === "load_image_mask") {
    return (
      <p className="builder-config__hint">
        End-users will pick an image from their computer when they open the dashboard.
      </p>
    );
  }

  return null;
}

function FormCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="builder-card">
      <h3>{title}</h3>
      <div className="builder-card__body">{children}</div>
    </section>
  );
}

function FieldRow({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <label className="builder-field">
      <span className="builder-field__label">{label}</span>
      {children}
      {error ? <span className="builder-field__error">{error}</span> : null}
      {hint ? <span className="builder-field__hint">{hint}</span> : null}
    </label>
  );
}

function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: Array<{ id: T; label: string }>;
  value: T;
  onChange: (next: T) => void;
  ariaLabel: string;
}) {
  return (
    <div className="builder-segment" role="group" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          className={`builder-segment__option ${value === option.id ? "builder-segment__option--active" : ""}`}
          onClick={() => onChange(option.id)}
          aria-pressed={value === option.id}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function ToggleRow({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <button
      type="button"
      className={`builder-toggle-row ${checked ? "builder-toggle-row--on" : ""}`}
      onClick={() => onChange(!checked)}
      aria-pressed={checked}
    >
      <span className={`builder-toggle-switch ${checked ? "builder-toggle-switch--on" : ""}`} aria-hidden="true">
        <span />
      </span>
      <span className="builder-toggle-row__text">
        <span className="builder-toggle-row__label">{label}</span>
        {hint ? <span className="builder-toggle-row__hint">{hint}</span> : null}
      </span>
    </button>
  );
}

function PreviewSection({
  title,
  widgets,
  selectedWidgetId,
  onSelect,
  onRemove,
  muted,
}: {
  title: string;
  widgets: DashboardWidget[];
  selectedWidgetId: string | null;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
  muted?: boolean;
}) {
  return (
    <section className={`preview-section ${muted ? "preview-section--muted" : ""}`}>
      <header>
        <h4>{title}</h4>
      </header>
      <div className="preview-stack">
        {widgets.map((widget) => (
          <PreviewWidget
            key={widget.id}
            widget={widget}
            isSelected={selectedWidgetId === widget.id}
            onSelect={() => onSelect(widget.id)}
            onRemove={() => onRemove(widget.id)}
          />
        ))}
      </div>
    </section>
  );
}

function PreviewWidget({
  widget,
  isSelected,
  onSelect,
  onRemove,
}: {
  widget: DashboardWidget;
  isSelected: boolean;
  onSelect: () => void;
  onRemove: () => void;
}) {
  return (
    <article
      className={`preview-widget ${isSelected ? "preview-widget--selected" : ""}`}
      onClick={onSelect}
    >
      <div className="preview-widget__handle" aria-hidden="true">
        <GripVertical size={14} />
      </div>

      <div className="preview-widget__body">
        <div className="preview-widget__heading">
          <h5>{widget.title}</h5>
          {widget.description ? <p>{widget.description}</p> : null}
        </div>
        <PreviewWidgetInput widget={widget} />
      </div>

      <div className="preview-widget__actions" onClick={(e) => e.stopPropagation()}>
        <button
          className="icon-button icon-button--card"
          type="button"
          onClick={onRemove}
          aria-label="Remove widget"
          title="Remove from dashboard"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
    </article>
  );
}

function PreviewWidgetInput({ widget }: { widget: DashboardWidget }) {
  if (widget.widgetType === "textarea") {
    return (
      <textarea
        className="preview-input preview-input--textarea"
        readOnly
        rows={3}
        value={String(widget.defaultValue ?? "")}
      />
    );
  }

  if (widget.widgetType === "string_field") {
    return <input className="preview-input" readOnly type="text" value={String(widget.defaultValue ?? "")} />;
  }

  if (widget.widgetType === "slider") {
    const min = widget.min ?? 0;
    const max = widget.max ?? 100;
    const numeric = Number(widget.defaultValue ?? min);
    const percent = max > min ? Math.max(0, Math.min(100, ((numeric - min) / (max - min)) * 100)) : 0;
    return (
      <div className="preview-slider">
        <div className="preview-slider__track">
          <div className="preview-slider__fill" style={{ width: `${percent}%` }} />
          <div className="preview-slider__thumb" style={{ left: `${percent}%` }} />
        </div>
        <div className="preview-slider__values">
          <span>{min}</span>
          <strong>{numeric}</strong>
          <span>{max}</span>
        </div>
      </div>
    );
  }

  if (widget.widgetType === "int_field" || widget.widgetType === "seed_widget") {
    return (
      <div className="preview-int">
        <input className="preview-input" readOnly type="text" value={String(widget.defaultValue ?? 0)} />
        {widget.widgetType === "seed_widget" ? (
          <span className="preview-int__hint">Click to randomize</span>
        ) : null}
      </div>
    );
  }

  if (widget.widgetType === "toggle") {
    const on = Boolean(widget.defaultValue);
    return (
      <div className={`preview-toggle ${on ? "preview-toggle--on" : ""}`}>
        <span />
        <span>{on ? "On" : "Off"}</span>
      </div>
    );
  }

  if (widget.widgetType === "select" || widget.widgetType === "lora_loader") {
    const options = widget.options ?? [];
    return (
      <div className="preview-select">
        <span>{String(widget.defaultValue ?? options[0] ?? "—")}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </div>
    );
  }

  if (widget.widgetType === "load_image") {
    return (
      <div className="preview-image-input">
        <ImagePlus size={20} aria-hidden="true" />
        <span>Drop an image or click to upload</span>
      </div>
    );
  }

  if (widget.widgetType === "load_image_mask") {
    return (
      <div className="preview-image-input">
        <ImagePlus size={20} aria-hidden="true" />
        <span>Upload an image, then draw a mask</span>
      </div>
    );
  }

  if (widget.widgetType === "display_image") {
    return (
      <div className="preview-image-output">
        <Sparkles size={22} aria-hidden="true" />
        <span>Generated image will appear here</span>
      </div>
    );
  }

  return null;
}

function BuilderEmptyState() {
  return (
    <div className="builder-empty builder-empty--center">
      <div className="builder-empty__icon">
        <Plus size={28} aria-hidden="true" />
      </div>
      <h3>Pick a workflow value to start</h3>
      <p>
        Open a node on the left and tap a value. Noofy will turn it into a dashboard widget you can
        rename and configure.
      </p>
    </div>
  );
}
