import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Eye,
  EyeOff,
  GripVertical,
  ImagePlus,
  LayoutGrid,
  Plus,
  Save,
  Search,
  Sparkles,
  Trash2,
  Wand2,
  X,
} from "lucide-react";

import { fetchRuntimeStatus, type RuntimeStatus } from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { runtimeStatusCopy } from "../app/status";
import {
  CONTROL_TYPE_LABELS,
  MOCK_WORKFLOW,
  NODE_ICONS,
  VALUE_KIND_ICONS,
  buildInitialDashboard,
  controlTypesForKind,
  defaultGroupFor,
  suggestControlType,
  suggestDescription,
  suggestTitle,
  type ControlType,
  type DashboardControl,
  type DashboardSchema,
  type MockWorkflow,
  type WorkflowNode,
  type WorkflowNodeValue,
} from "./dashboardBuilderContent";

interface DashboardBuilderPageProps {
  workflowId?: string;
  workflowName?: string;
  onBack: () => void;
  onNavigate: (route: AppRouteId) => void;
}

interface RuntimeState {
  loading: boolean;
  runtime: RuntimeStatus | null;
}

export function DashboardBuilderPage({
  workflowId,
  workflowName,
  onBack,
  onNavigate,
}: DashboardBuilderPageProps) {
  const [runtimeState, setRuntimeState] = useState<RuntimeState>({ loading: true, runtime: null });

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

  const appStatus = runtimeStatusCopy(runtimeState);

  const workflow: MockWorkflow = useMemo(() => {
    return {
      ...MOCK_WORKFLOW,
      id: workflowId ?? MOCK_WORKFLOW.id,
      name: workflowName ?? MOCK_WORKFLOW.name,
    };
  }, [workflowId, workflowName]);

  const [schema, setSchema] = useState<DashboardSchema>(() => buildInitialDashboard(workflow));
  const [selectedValueId, setSelectedValueId] = useState<string | null>(null);
  const [selectedControlId, setSelectedControlId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(() => new Set([workflow.nodes[0]?.id ?? ""]));
  const [showTechnical, setShowTechnical] = useState(false);
  const [savedFlash, setSavedFlash] = useState<"saved" | "draft" | null>(null);

  useEffect(() => {
    setSchema(buildInitialDashboard(workflow));
    const firstControl = buildInitialDashboard(workflow).controls[0];
    if (firstControl) {
      setSelectedControlId(firstControl.id);
      setSelectedValueId(firstControl.valueId);
    }
  }, [workflow]);

  const valueIndex = useMemo(() => {
    const map = new Map<string, { node: WorkflowNode; value: WorkflowNodeValue }>();
    for (const node of workflow.nodes) {
      for (const value of node.values) {
        map.set(value.id, { node, value });
      }
    }
    return map;
  }, [workflow]);

  const exposedValueIds = useMemo(() => new Set(schema.controls.map((c) => c.valueId)), [schema.controls]);

  const filteredNodes = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query && showTechnical) return workflow.nodes;

    return workflow.nodes
      .map((node) => {
        const filteredValues = node.values.filter((value) => {
          if (!showTechnical && value.technical) return false;
          if (!query) return true;
          return (
            value.label.toLowerCase().includes(query) ||
            node.title.toLowerCase().includes(query) ||
            node.classType.toLowerCase().includes(query)
          );
        });
        return { ...node, values: filteredValues };
      })
      .filter((node) => node.values.length > 0 || (!query && node.values.length === 0));
  }, [workflow.nodes, search, showTechnical]);

  const selectedControl = useMemo(
    () => (selectedControlId ? schema.controls.find((c) => c.id === selectedControlId) ?? null : null),
    [schema.controls, selectedControlId],
  );

  const selectedValueRecord = selectedValueId ? valueIndex.get(selectedValueId) ?? null : null;

  function handleSelectValue(valueId: string) {
    const record = valueIndex.get(valueId);
    if (!record) return;

    const existing = schema.controls.find((c) => c.valueId === valueId);
    if (existing) {
      setSelectedValueId(valueId);
      setSelectedControlId(existing.id);
      return;
    }

    const newControl = createControlForValue(record.value, record.node);
    setSchema((current) => ({ ...current, controls: [...current.controls, newControl] }));
    setSelectedValueId(valueId);
    setSelectedControlId(newControl.id);
  }

  function handleSelectControl(controlId: string) {
    const control = schema.controls.find((c) => c.id === controlId);
    if (!control) return;
    setSelectedControlId(controlId);
    setSelectedValueId(control.valueId);
  }

  function patchControl(controlId: string, patch: Partial<DashboardControl>) {
    setSchema((current) => ({
      ...current,
      controls: current.controls.map((c) => (c.id === controlId ? { ...c, ...patch } : c)),
    }));
  }

  function removeControl(controlId: string) {
    setSchema((current) => ({
      ...current,
      controls: current.controls.filter((c) => c.id !== controlId),
    }));
    if (selectedControlId === controlId) {
      setSelectedControlId(null);
      setSelectedValueId(null);
    }
  }

  function moveControl(controlId: string, direction: "up" | "down") {
    setSchema((current) => {
      const index = current.controls.findIndex((c) => c.id === controlId);
      if (index === -1) return current;
      const target = direction === "up" ? index - 1 : index + 1;
      if (target < 0 || target >= current.controls.length) return current;
      const next = [...current.controls];
      const [moved] = next.splice(index, 1);
      next.splice(target, 0, moved);
      return { ...current, controls: next };
    });
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
    setSavedFlash("draft");
    window.setTimeout(() => setSavedFlash(null), 2400);
  }

  function handleSaveAndContinue() {
    setSavedFlash("saved");
    window.setTimeout(() => setSavedFlash(null), 2400);
  }

  const simpleControls = schema.controls.filter((c) => c.group === "simple");
  const advancedControls = schema.controls.filter((c) => c.group === "advanced");

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
              <p className="eyebrow">Creator setup · Dashboard builder</p>
            </div>
            <h1 id="builder-title">Dashboard Builder · {workflow.name}</h1>
            <p>Choose which workflow values become simple controls.</p>
          </div>

          <div className="builder-heading__meta">
            <div className="status-pill status-pill--info">
              <span />
              <span>{savedFlash === "saved" ? "Dashboard saved" : "Draft dashboard"}</span>
            </div>
            <div className="button-row">
              <button className="secondary-button" type="button" onClick={handleSaveDraft}>
                <Save size={15} aria-hidden="true" />
                Save as draft
              </button>
              <button className="primary-button primary-button--compact" type="button" onClick={handleSaveAndContinue}>
                <CheckCircle2 size={16} aria-hidden="true" />
                Save dashboard
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

        <div className="builder-grid">
          <aside className="builder-pane builder-values" aria-label="Workflow values">
            <header className="builder-pane__header">
              <div>
                <h2>Workflow values</h2>
                <p>Pick a value to expose as a friendly control.</p>
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
              <button
                className={`builder-toggle ${showTechnical ? "builder-toggle--active" : ""}`}
                type="button"
                onClick={() => setShowTechnical((current) => !current)}
                aria-pressed={showTechnical}
              >
                {showTechnical ? <Eye size={14} aria-hidden="true" /> : <EyeOff size={14} aria-hidden="true" />}
                {showTechnical ? "Hide technical" : "Show technical"}
              </button>
            </div>

            <div className="builder-pane__scroll">
              {filteredNodes.length === 0 ? (
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

          <main className="builder-pane builder-config" aria-label="Control configuration">
            {selectedControl && selectedValueRecord ? (
              <ControlEditor
                control={selectedControl}
                value={selectedValueRecord.value}
                node={selectedValueRecord.node}
                onPatch={(patch) => patchControl(selectedControl.id, patch)}
                onRemove={() => removeControl(selectedControl.id)}
              />
            ) : (
              <BuilderEmptyState />
            )}
          </main>

          <aside className="builder-pane builder-preview" aria-label="Dashboard preview">
            <header className="builder-pane__header builder-pane__header--preview">
              <div>
                <h2>End-user dashboard</h2>
                <p>How beginners will see this workflow.</p>
              </div>
              <span className="builder-preview__chip">
                <LayoutGrid size={13} aria-hidden="true" />
                Preview
              </span>
            </header>

            <div className="builder-pane__scroll builder-preview__canvas">
              {schema.controls.length === 0 ? (
                <div className="builder-empty">
                  <div className="builder-empty__icon">
                    <Wand2 size={26} aria-hidden="true" />
                  </div>
                  <h3>Your dashboard is empty</h3>
                  <p>Select a workflow value and turn it into a dashboard control.</p>
                </div>
              ) : (
                <>
                  {simpleControls.length > 0 && (
                    <PreviewSection title="Simple controls" controls={simpleControls}
                      selectedControlId={selectedControlId}
                      onSelect={handleSelectControl}
                      onRemove={removeControl}
                      onMove={moveControl}
                    />
                  )}

                  {advancedControls.length > 0 && (
                    <PreviewSection title="Advanced controls" controls={advancedControls}
                      selectedControlId={selectedControlId}
                      onSelect={handleSelectControl}
                      onRemove={removeControl}
                      onMove={moveControl}
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
      </div>
    </AppLayout>
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

function ControlEditor({
  control,
  value,
  node,
  onPatch,
  onRemove,
}: {
  control: DashboardControl;
  value: WorkflowNodeValue;
  node: WorkflowNode;
  onPatch: (patch: Partial<DashboardControl>) => void;
  onRemove: () => void;
}) {
  const allowedTypes = controlTypesForKind(value.valueKind);
  const showSlider = control.controlType === "slider" || control.controlType === "int_field";
  const showOptions = control.controlType === "select" || control.controlType === "lora_loader";
  const showImageOptions = control.controlType === "load_image" || control.controlType === "load_image_mask";

  return (
    <div className="builder-config__inner">
      <div className="builder-config__top">
        <div>
          <p className="builder-config__breadcrumb">
            <span>{node.title}</span>
            <ChevronRight size={12} aria-hidden="true" />
            <span>{value.label}</span>
          </p>
          <h2>Configure control</h2>
          <p className="builder-config__summary">
            This control is connected to a workflow value. People running this workflow will see your clear label instead of the ComfyUI node name.
          </p>
        </div>
        <button className="secondary-button secondary-button--small secondary-button--danger" type="button" onClick={onRemove}>
          <Trash2 size={13} aria-hidden="true" />
          Remove control
        </button>
      </div>

      <FormCard title="Control details">
        <FieldRow label="Display title">
          <input
            type="text"
            className="builder-input"
            value={control.title}
            onChange={(event) => onPatch({ title: event.target.value })}
          />
        </FieldRow>
        <FieldRow label="Helper description" hint="Shown under the control. Keep it short and friendly.">
          <textarea
            className="builder-input builder-input--textarea"
            rows={2}
            value={control.description}
            onChange={(event) => onPatch({ description: event.target.value })}
          />
        </FieldRow>
      </FormCard>

      <FormCard title="Control behavior">
        <div className="builder-config__grid">
          <FieldRow label="Control type">
            <select
              className="builder-input"
              value={control.controlType}
              onChange={(event) => onPatch({ controlType: event.target.value as ControlType })}
            >
              {allowedTypes.map((type) => (
                <option key={type} value={type}>
                  {CONTROL_TYPE_LABELS[type]}
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
              value={control.group}
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
            value={control.orientation}
            onChange={(orientation) => onPatch({ orientation })}
          />
        </FieldRow>
      </FormCard>

      {showSlider && value.numberRange ? (
        <FormCard title="Numeric range">
          <div className="builder-config__grid builder-config__grid--three">
            <FieldRow label="Minimum">
              <input
                type="number"
                className="builder-input"
                value={control.min ?? value.numberRange.min}
                step={value.numberRange.step ?? 1}
                onChange={(event) => onPatch({ min: Number(event.target.value) })}
              />
            </FieldRow>
            <FieldRow label="Maximum">
              <input
                type="number"
                className="builder-input"
                value={control.max ?? value.numberRange.max}
                step={value.numberRange.step ?? 1}
                onChange={(event) => onPatch({ max: Number(event.target.value) })}
              />
            </FieldRow>
            <FieldRow label="Step">
              <input
                type="number"
                className="builder-input"
                value={control.step ?? value.numberRange.step ?? 1}
                step={value.numberRange.step ?? 1}
                onChange={(event) => onPatch({ step: Number(event.target.value) })}
              />
            </FieldRow>
          </div>
        </FormCard>
      ) : null}

      {showOptions ? (
        <FormCard title="Options">
          <FieldRow label="Choices" hint="One per line.">
            <textarea
              className="builder-input builder-input--textarea"
              rows={4}
              value={(control.options ?? value.options ?? []).join("\n")}
              onChange={(event) =>
                onPatch({
                  options: event.target.value.split("\n").map((line) => line.trim()).filter(Boolean),
                })
              }
            />
          </FieldRow>
        </FormCard>
      ) : null}

      {showImageOptions ? (
        <FormCard title="Image input">
          <ToggleRow
            checked={Boolean(control.drawMask)}
            onChange={(drawMask) =>
              onPatch({ drawMask, controlType: drawMask ? "load_image_mask" : "load_image" })
            }
            label="Allow drawing a mask"
            hint="Adds a mask brush over the uploaded image."
          />
        </FormCard>
      ) : null}

      {control.controlType !== "display_image" ? (
        <FormCard title="Default value">
          <DefaultValueEditor control={control} value={value} onPatch={onPatch} />
        </FormCard>
      ) : null}

      <div className="builder-config__binding">
        <span>Connected to</span>
        <code>node {control.binding.nodeId}</code>
        <span className="builder-config__binding-arrow">→</span>
        <code>{control.binding.inputName}</code>
      </div>
    </div>
  );
}

function DefaultValueEditor({
  control,
  value,
  onPatch,
}: {
  control: DashboardControl;
  value: WorkflowNodeValue;
  onPatch: (patch: Partial<DashboardControl>) => void;
}) {
  if (control.controlType === "textarea") {
    return (
      <textarea
        className="builder-input builder-input--textarea"
        rows={4}
        value={String(control.defaultValue ?? "")}
        onChange={(event) => onPatch({ defaultValue: event.target.value })}
      />
    );
  }

  if (control.controlType === "string_field") {
    return (
      <input
        type="text"
        className="builder-input"
        value={String(control.defaultValue ?? "")}
        onChange={(event) => onPatch({ defaultValue: event.target.value })}
      />
    );
  }

  if (control.controlType === "slider" || control.controlType === "int_field" || control.controlType === "seed_control") {
    return (
      <input
        type="number"
        className="builder-input"
        value={Number(control.defaultValue ?? 0)}
        step={control.step ?? value.numberRange?.step ?? 1}
        onChange={(event) => onPatch({ defaultValue: Number(event.target.value) })}
      />
    );
  }

  if (control.controlType === "toggle") {
    return (
      <ToggleRow
        checked={Boolean(control.defaultValue)}
        onChange={(checked) => onPatch({ defaultValue: checked })}
        label={control.defaultValue ? "On" : "Off"}
      />
    );
  }

  if (control.controlType === "select" || control.controlType === "lora_loader") {
    const options = control.options ?? value.options ?? [];
    return (
      <select
        className="builder-input"
        value={String(control.defaultValue ?? options[0] ?? "")}
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

  if (control.controlType === "load_image" || control.controlType === "load_image_mask") {
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

function FieldRow({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="builder-field">
      <span className="builder-field__label">{label}</span>
      {children}
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
  controls,
  selectedControlId,
  onSelect,
  onRemove,
  onMove,
  muted,
}: {
  title: string;
  controls: DashboardControl[];
  selectedControlId: string | null;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
  onMove: (id: string, direction: "up" | "down") => void;
  muted?: boolean;
}) {
  return (
    <section className={`preview-section ${muted ? "preview-section--muted" : ""}`}>
      <header>
        <h4>{title}</h4>
      </header>
      <div className="preview-stack">
        {controls.map((control, index) => (
          <PreviewControl
            key={control.id}
            control={control}
            isSelected={selectedControlId === control.id}
            isFirst={index === 0}
            isLast={index === controls.length - 1}
            onSelect={() => onSelect(control.id)}
            onRemove={() => onRemove(control.id)}
            onMove={(direction) => onMove(control.id, direction)}
          />
        ))}
      </div>
    </section>
  );
}

function PreviewControl({
  control,
  isSelected,
  isFirst,
  isLast,
  onSelect,
  onRemove,
  onMove,
}: {
  control: DashboardControl;
  isSelected: boolean;
  isFirst: boolean;
  isLast: boolean;
  onSelect: () => void;
  onRemove: () => void;
  onMove: (direction: "up" | "down") => void;
}) {
  return (
    <article
      className={`preview-control ${isSelected ? "preview-control--selected" : ""}`}
      onClick={onSelect}
    >
      <div className="preview-control__handle" aria-hidden="true">
        <GripVertical size={14} />
      </div>

      <div className="preview-control__body">
        <div className="preview-control__heading">
          <h5>{control.title}</h5>
          {control.description ? <p>{control.description}</p> : null}
        </div>
        <PreviewControlInput control={control} />
      </div>

      <div className="preview-control__actions" onClick={(e) => e.stopPropagation()}>
        <button
          className="icon-button icon-button--card"
          type="button"
          onClick={() => onMove("up")}
          disabled={isFirst}
          aria-label="Move up"
          title="Move up"
        >
          <ChevronUp size={14} aria-hidden="true" />
        </button>
        <button
          className="icon-button icon-button--card"
          type="button"
          onClick={() => onMove("down")}
          disabled={isLast}
          aria-label="Move down"
          title="Move down"
        >
          <ChevronDown size={14} aria-hidden="true" />
        </button>
        <button
          className="icon-button icon-button--card"
          type="button"
          onClick={onRemove}
          aria-label="Remove control"
          title="Remove from dashboard"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
    </article>
  );
}

function PreviewControlInput({ control }: { control: DashboardControl }) {
  if (control.controlType === "textarea") {
    return (
      <textarea
        className="preview-input preview-input--textarea"
        readOnly
        rows={3}
        value={String(control.defaultValue ?? "")}
      />
    );
  }

  if (control.controlType === "string_field") {
    return <input className="preview-input" readOnly type="text" value={String(control.defaultValue ?? "")} />;
  }

  if (control.controlType === "slider") {
    const min = control.min ?? 0;
    const max = control.max ?? 100;
    const numeric = Number(control.defaultValue ?? min);
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

  if (control.controlType === "int_field" || control.controlType === "seed_control") {
    return (
      <div className="preview-int">
        <input className="preview-input" readOnly type="text" value={String(control.defaultValue ?? 0)} />
        {control.controlType === "seed_control" ? (
          <span className="preview-int__hint">Click to randomize</span>
        ) : null}
      </div>
    );
  }

  if (control.controlType === "toggle") {
    const on = Boolean(control.defaultValue);
    return (
      <div className={`preview-toggle ${on ? "preview-toggle--on" : ""}`}>
        <span />
        <span>{on ? "On" : "Off"}</span>
      </div>
    );
  }

  if (control.controlType === "select" || control.controlType === "lora_loader") {
    const options = control.options ?? [];
    return (
      <div className="preview-select">
        <span>{String(control.defaultValue ?? options[0] ?? "—")}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </div>
    );
  }

  if (control.controlType === "load_image") {
    return (
      <div className="preview-image-input">
        <ImagePlus size={20} aria-hidden="true" />
        <span>Drop an image or click to upload</span>
      </div>
    );
  }

  if (control.controlType === "load_image_mask") {
    return (
      <div className="preview-image-input">
        <ImagePlus size={20} aria-hidden="true" />
        <span>Upload an image, then draw a mask</span>
      </div>
    );
  }

  if (control.controlType === "display_image") {
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
        Open a node on the left and tap a value. Noofy will turn it into a dashboard control you can
        rename and configure.
      </p>
    </div>
  );
}

function createControlForValue(value: WorkflowNodeValue, node: WorkflowNode): DashboardControl {
  const controlType = suggestControlType(value);
  return {
    id: `ctrl-${value.id}`,
    valueId: value.id,
    binding: { nodeId: value.nodeId, inputName: value.inputName },
    controlType,
    title: suggestTitle(value, node.title),
    description: suggestDescription(value),
    orientation: "vertical",
    group: defaultGroupFor(value),
    defaultValue: value.rawValue,
    options: value.options,
    min: value.numberRange?.min,
    max: value.numberRange?.max,
    step: value.numberRange?.step,
    showDownload: controlType === "display_image" ? true : undefined,
    drawMask: controlType === "load_image_mask" ? true : undefined,
  };
}
