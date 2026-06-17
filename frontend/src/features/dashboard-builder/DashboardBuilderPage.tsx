import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type DragEvent, type ReactNode, type SetStateAction } from "react";
import {
  ArrowLeft,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Box,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  File,
  FileAudio,
  GripVertical,
  ImagePlus,
  LayoutGrid,
  Loader2,
  Plus,
  Save,
  Search,
  Sparkles,
  Trash2,
  Type,
  Video,
  Wand2,
  X,
} from "lucide-react";

import {
  fetchBindableInputs,
  uploadDashboardAsset,
  uploadDashboardAudioAsset,
  uploadDashboardFileAsset,
  uploadDashboardThreeDAsset,
  uploadDashboardVideoAsset,
} from "../../lib/api/noofyApi";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import {
  WIDGET_TYPE_LABELS,
  DEFAULT_FILE_ACCEPTED_EXTENSIONS,
  MOCK_WORKFLOW,
  NODE_ICONS,
  VALUE_KIND_ICONS,
  addAutomaticDashboardWidgets,
  buildInitialDashboard,
  canPreserveWidgetAsHiddenInput,
  clearDashboardDraft,
  createDashboardWidgetForValue,
  defaultNumericRangeForValue,
  isOutputWidgetType,
  normalizeDashboardSchema,
  removeDashboardWidgetsFromSchema,
  resolveBuilderSchemaSource,
  saveDashboardDraft,
  suggestTitle,
  suggestWidgetType,
  topLevelDashboardItems,
  widgetGroupIdMap,
  widgetTypesForKind,
  workflowFromBindableInputs,
  type WidgetType,
  type DashboardWidgetGroup,
  type DashboardWidget,
  type DashboardSchema,
  type DashboardTopLevelItem,
  type MockWorkflow,
  type WorkflowNode,
  type WorkflowNodeValue,
} from "./dashboardBuilderContent";
import { DEFAULT_SEED_MODE, SEED_MODES, SEED_MODE_LABELS, type SeedMode } from "../../lib/seedControl";
import { SEED_MODE_ICONS } from "../../lib/seedModeIcon";

interface DashboardBuilderPageProps {
  workflowId?: string;
  workflowName?: string;
  initialSchema?: DashboardSchema;
  onBack: () => void;
  onCancelEdit?: () => void;
  onContinue: (schema: DashboardSchema) => void;
  onNavigate: (route: AppRouteId) => void;
}

interface WorkflowAuthoringState {
  loading: boolean;
  workflow: MockWorkflow | null;
  error: string | null;
}

type CreatedDragState =
  | { kind: "widget"; widgetId: string; sourceGroupId: string | null }
  | { kind: "group"; groupId: string };

type CreatedDropPreview =
  | { kind: "group"; targetWidgetId: string }
  | { kind: "group-add"; targetGroupId: string; targetWidgetId?: string }
  | { kind: "insert"; targetGroupId: string | null; beforeId: string | null }
  | null;

type PendingWidgetRemoval = { widget: DashboardWidget } | null;
type WidgetScopedStatus = { widgetId: string; message: string };
type HoveredValuePreview = {
  node: WorkflowNode;
  value: WorkflowNodeValue;
  position: { top: number; left: number };
  side: "left" | "right";
} | null;

function emptyWorkflow(workflowId: string, workflowName: string): MockWorkflow {
  return {
    ...MOCK_WORKFLOW,
    id: workflowId,
    name: workflowName,
    source: "imported_noofy_package",
    nodes: [],
  };
}

function reconcileDashboardSchemaForWorkflow(schema: DashboardSchema, workflow: MockWorkflow): DashboardSchema {
  let changed = false;
  const widgets = schema.widgets.map((widget) => {
    const currentValue = workflow.nodes
      .flatMap((node) => node.values)
      .find((value) => value.id === widget.valueId);
    if (currentValue) return widget;

    const boundValue = workflow.nodes
      .flatMap((node) => node.values)
      .find((value) => {
        if (value.nodeId !== widget.binding.nodeId) return false;
        if (value.inputName === widget.binding.inputName) return true;
        return isOutputWidgetType(widget.widgetType) && value.valueKind === "image_output";
      });
    if (!boundValue) return widget;

    changed = true;
    return {
      ...widget,
      valueId: boundValue.id,
      binding: { nodeId: boundValue.nodeId, inputName: boundValue.inputName },
    };
  });

  return changed ? { ...schema, widgets } : schema;
}

export function DashboardBuilderPage({
  workflowId,
  workflowName,
  initialSchema,
  onBack,
  onCancelEdit,
  onContinue,
  onNavigate,
}: DashboardBuilderPageProps) {
  const activeWorkflowId = workflowId ?? MOCK_WORKFLOW.id;
  const activeWorkflowName = workflowName ?? (workflowId ? workflowId : MOCK_WORKFLOW.name);
  const scopedInitialSchema = initialSchema?.workflowId === activeWorkflowId ? initialSchema : undefined;
  const loadSequenceRef = useRef(0);
  const draftBaseKeyRef = useRef("");
  const draftActiveRef = useRef(false);
  const [workflowState, setWorkflowState] = useState<WorkflowAuthoringState>(() => {
    if (workflowId) return { loading: true, workflow: null, error: null };
    return {
      loading: false,
      workflow: { ...MOCK_WORKFLOW, id: activeWorkflowId, name: activeWorkflowName },
      error: null,
    };
  });
  const [schema, setSchema] = useState<DashboardSchema>(
    () => {
      const source = resolveBuilderSchemaSource(activeWorkflowId, scopedInitialSchema);
      draftBaseKeyRef.current = source.baseKey;
      draftActiveRef.current = source.fromDraft;
      return normalizeDashboardSchema(
        source.schema ?? buildInitialDashboard(emptyWorkflow(activeWorkflowId, activeWorkflowName)),
      );
    },
  );
  const [selectedValueId, setSelectedValueId] = useState<string | null>(null);
  const [selectedWidgetId, setSelectedWidgetId] = useState<string | null>(null);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [createdDrag, setCreatedDrag] = useState<CreatedDragState | null>(null);
  const [createdDropPreview, setCreatedDropPreview] = useState<CreatedDropPreview>(null);
  const [search, setSearch] = useState("");
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(() => new Set());
  const [savedFlash, setSavedFlash] = useState<"saved" | "draft" | null>(null);
  const [widgetSaveStatus, setWidgetSaveStatus] = useState<WidgetScopedStatus | null>(null);
  const [pendingWidgetRemoval, setPendingWidgetRemoval] = useState<PendingWidgetRemoval>(null);
  const [hoveredValuePreview, setHoveredValuePreview] = useState<HoveredValuePreview>(null);

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
          error: "This workflow's controls could not be loaded.",
        });
      });
    return () => {
      loadSequenceRef.current += 1;
    };
  }, [workflowId, activeWorkflowId, activeWorkflowName]);

  useLayoutEffect(() => {
    const source = resolveBuilderSchemaSource(activeWorkflowId, scopedInitialSchema);
    draftBaseKeyRef.current = source.baseKey;
    draftActiveRef.current = source.fromDraft;
    setSchema(
      normalizeDashboardSchema(
        source.schema ??
          buildInitialDashboard(emptyWorkflow(activeWorkflowId, activeWorkflowName)),
      ),
    );
    setSelectedValueId(null);
    setSelectedWidgetId(null);
    setSelectedGroupId(null);
    setCreatedDrag(null);
    setCreatedDropPreview(null);
    setSearch("");
    setExpandedNodes(new Set());
    setSavedFlash(null);
    setWidgetSaveStatus(null);
    setHoveredValuePreview(null);
  }, [activeWorkflowId]);

  useEffect(() => {
    const workflow = workflowState.workflow;
    if (workflowState.loading || !workflow || workflow.id !== activeWorkflowId) return;
    const source = resolveBuilderSchemaSource(activeWorkflowId, scopedInitialSchema);
    draftBaseKeyRef.current = source.baseKey;
    draftActiveRef.current = source.fromDraft;
    const sourceSchema = source.schema ?? buildInitialDashboard(workflow);
    const nextSchema = normalizeDashboardSchema(
      source.fromDraft
        ? reconcileDashboardSchemaForWorkflow(sourceSchema, workflow)
        : addAutomaticDashboardWidgets(reconcileDashboardSchemaForWorkflow(sourceSchema, workflow), workflow),
    );
    setSchema(nextSchema);
    const firstWidget = nextSchema.widgets[0];
    if (firstWidget) {
      setSelectedWidgetId(firstWidget.id);
      setSelectedValueId(firstWidget.valueId);
      setSelectedGroupId(null);
    } else {
      setSelectedWidgetId(null);
      setSelectedValueId(null);
      setSelectedGroupId(null);
    }
    setExpandedNodes(new Set([sortWorkflowNodesByName(workflow.nodes)[0]?.id ?? ""]));
  }, [workflowState, activeWorkflowId, scopedInitialSchema]);

  const workflow = workflowState.workflow;
  const builderReady =
    !workflowState.loading &&
    workflow !== null &&
    workflow.id === activeWorkflowId &&
    schema.workflowId === activeWorkflowId;
  const displayWorkflowName = builderReady ? workflow.name : activeWorkflowName;

  useEffect(() => {
    if (!builderReady || !draftActiveRef.current) return;
    saveDashboardDraft(schema, draftBaseKeyRef.current);
  }, [builderReady, schema]);

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
  const dashboardTitlesByValueId = useMemo(() => {
    const map = new Map<string, string[]>();
    if (!builderReady) return map;

    for (const widget of [...schema.widgets, ...(schema.hiddenWidgets ?? [])]) {
      const title = widget.title.trim();
      if (!title) continue;
      const titles = map.get(widget.valueId);
      if (titles) {
        titles.push(title);
      } else {
        map.set(widget.valueId, [title]);
      }
    }

    return map;
  }, [builderReady, schema.widgets, schema.hiddenWidgets]);

  const filteredNodes = useMemo(() => {
    if (!builderReady || !workflow) return [];
    const queryTerms = normalizeSearchText(search).split(/\s+/).filter(Boolean);
    if (queryTerms.length === 0) return sortWorkflowNodesByName(workflow.nodes);

    const matchingNodes = workflow.nodes
      .map((node) => {
        const filteredValues = node.values.filter((value) =>
          valueMatchesSearch(node, value, queryTerms, dashboardTitlesByValueId.get(value.id) ?? []),
        );
        return { ...node, values: filteredValues };
      })
      .filter((node) => node.values.length > 0);
    return sortWorkflowNodesByName(matchingNodes);
  }, [builderReady, workflow, search, dashboardTitlesByValueId]);

  const selectedWidget = useMemo(
    () => (builderReady && selectedWidgetId ? schema.widgets.find((c) => c.id === selectedWidgetId) ?? null : null),
    [builderReady, schema.widgets, selectedWidgetId],
  );
  const selectedGroup = useMemo(
    () => (builderReady && selectedGroupId ? schema.groups.find((group) => group.id === selectedGroupId) ?? null : null),
    [builderReady, schema.groups, selectedGroupId],
  );
  const groupIdByWidgetId = useMemo(() => widgetGroupIdMap(schema), [schema]);
  const createdItems = useMemo(() => (builderReady ? topLevelDashboardItems(schema) : []), [builderReady, schema]);

  const selectedValueRecord = selectedValueId ? valueIndex.get(selectedValueId) ?? null : null;
  const hasValidationErrors = builderReady && schema.widgets.some((widget) => validateWidgetForSave(widget).length > 0);

  function updateSchemaFromUser(updater: SetStateAction<DashboardSchema>) {
    draftActiveRef.current = true;
    setSchema(updater);
  }

  function handleSelectValue(valueId: string) {
    const record = valueIndex.get(valueId);
    if (!record) return;

    const existing = schema.widgets.find((c) => c.valueId === valueId);
    if (existing) {
      setSelectedValueId(valueId);
      setSelectedWidgetId(existing.id);
      setSelectedGroupId(null);
      setWidgetSaveStatus(null);
      return;
    }

    const newWidget = createDashboardWidgetForValue(record.value, record.node);
    updateSchemaFromUser((current) => ({ ...current, widgets: [...current.widgets, newWidget] }));
    setSelectedValueId(valueId);
    setSelectedWidgetId(newWidget.id);
    setSelectedGroupId(null);
    setWidgetSaveStatus(null);
  }

  function handleShowValuePreview(node: WorkflowNode, value: WorkflowNodeValue, event: { currentTarget: HTMLButtonElement }) {
    setHoveredValuePreview({
      node,
      value,
      ...previewPositionForElement(event.currentTarget),
    });
  }

  function handleAddNote() {
    const id = nextDashboardNoteId(schema);
    const note: DashboardWidget = {
      id,
      valueId: `note:${id}`,
      binding: { nodeId: "", inputName: "" },
      widgetType: "note",
      title: "Note",
      description: "",
      defaultValue: null,
    };
    updateSchemaFromUser((current) => ({ ...current, widgets: [...current.widgets, note] }));
    setSelectedValueId(note.valueId);
    setSelectedWidgetId(note.id);
    setSelectedGroupId(null);
    setWidgetSaveStatus(null);
  }

  function handleSelectWidget(widgetId: string) {
    const widget = schema.widgets.find((c) => c.id === widgetId);
    if (!widget) return;
    setSelectedWidgetId(widgetId);
    setSelectedValueId(widget.valueId);
    setSelectedGroupId(null);
    setWidgetSaveStatus(null);
  }

  function handleSelectGroup(groupId: string) {
    const group = schema.groups.find((candidate) => candidate.id === groupId);
    if (!group) return;
    setSelectedGroupId(groupId);
    setSelectedWidgetId(null);
    setSelectedValueId(null);
    setWidgetSaveStatus(null);
  }

  function patchWidget(widgetId: string, patch: Partial<DashboardWidget>) {
    updateSchemaFromUser((current) => ({
      ...current,
      widgets: current.widgets.map((c) => (c.id === widgetId ? { ...c, ...patch } : c)),
    }));
  }

  function patchGroup(groupId: string, patch: Partial<DashboardWidgetGroup>) {
    updateSchemaFromUser((current) => ({
      ...current,
      groups: current.groups.map((group) => (group.id === groupId ? { ...group, ...patch } : group)),
    }));
  }

  function removeWidget(widgetId: string) {
    const widget = schema.widgets.find((item) => item.id === widgetId);
    if (widget && widgetHasSavedDefault(widget)) {
      setPendingWidgetRemoval({ widget });
      return;
    }
    commitRemoveWidget(widgetId, false);
  }

  function commitRemoveWidget(widgetId: string, keepHiddenDefault: boolean) {
    updateSchemaFromUser((current) => removeDashboardWidgetsFromSchema(current, [widgetId], keepHiddenDefault));
    if (selectedWidgetId === widgetId) {
      setSelectedWidgetId(null);
      setSelectedValueId(null);
    }
    setPendingWidgetRemoval(null);
  }

  function handleCreatedDragStart(widgetId: string) {
    const sourceGroupId = groupIdByWidgetId.get(widgetId) ?? null;
    setCreatedDrag({ kind: "widget", widgetId, sourceGroupId });
    setCreatedDropPreview(null);
  }

  function handleCreatedGroupDragStart(groupId: string) {
    setCreatedDrag({ kind: "group", groupId });
    setCreatedDropPreview(null);
  }

  function handleCreatedDragEnd() {
    setCreatedDrag(null);
    setCreatedDropPreview(null);
  }

  function handleWidgetDragOver(event: DragEvent<HTMLElement>, targetWidgetId: string) {
    if (!createdDrag || createdDrag.kind !== "widget") return;
    if (createdDrag.widgetId === targetWidgetId) {
      event.stopPropagation();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const targetGroupId = groupIdByWidgetId.get(targetWidgetId) ?? null;
    if (targetGroupId && createdDrag.sourceGroupId === targetGroupId) {
      setCreatedDropPreview(null);
      return;
    }
    setCreatedDropPreview(
      targetGroupId
        ? { kind: "group-add", targetGroupId, targetWidgetId }
        : { kind: "group", targetWidgetId },
    );
  }

  function handleWidgetDrop(event: DragEvent<HTMLElement>, targetWidgetId: string) {
    event.preventDefault();
    event.stopPropagation();
    if (!createdDrag || createdDrag.kind !== "widget" || createdDrag.widgetId === targetWidgetId) return;
    updateSchemaFromUser((current) => groupWidgetOnTarget(current, createdDrag.widgetId, targetWidgetId));
    handleSelectWidget(createdDrag.widgetId);
    handleCreatedDragEnd();
  }

  function handleGroupDragOver(event: DragEvent<HTMLElement>, targetGroupId: string) {
    if (!createdDrag || createdDrag.kind !== "widget") return;
    if (createdDrag.sourceGroupId === targetGroupId) {
      event.stopPropagation();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setCreatedDropPreview({ kind: "group-add", targetGroupId });
  }

  function handleGroupDrop(event: DragEvent<HTMLElement>, targetGroupId: string) {
    event.preventDefault();
    event.stopPropagation();
    if (!createdDrag || createdDrag.kind !== "widget") return;
    updateSchemaFromUser((current) => moveWidgetToGroup(current, createdDrag.widgetId, targetGroupId));
    handleSelectWidget(createdDrag.widgetId);
    handleCreatedDragEnd();
  }

  function handleTopLevelInsertDragOver(event: DragEvent<HTMLElement>, beforeId: string | null) {
    if (!createdDrag) return;
    if (
      (createdDrag.kind === "widget" && beforeId === createdDrag.widgetId) ||
      (createdDrag.kind === "group" && beforeId === createdDrag.groupId)
    ) {
      event.stopPropagation();
      setCreatedDropPreview(null);
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setCreatedDropPreview({ kind: "insert", targetGroupId: null, beforeId });
  }

  function handleTopLevelInsertDrop(event: DragEvent<HTMLElement>, beforeId: string | null) {
    event.preventDefault();
    event.stopPropagation();
    if (!createdDrag) return;
    const dropped = createdDrag;
    updateSchemaFromUser((current) => moveCreatedItemToTopLevelPosition(current, dropped, beforeId));
    if (dropped.kind === "widget") handleSelectWidget(dropped.widgetId);
    else handleSelectGroup(dropped.groupId);
    handleCreatedDragEnd();
  }

  function handleGroupInsertDragOver(event: DragEvent<HTMLElement>, targetGroupId: string, beforeWidgetId: string | null) {
    if (!createdDrag || createdDrag.kind !== "widget") return;
    if (beforeWidgetId === createdDrag.widgetId) {
      event.stopPropagation();
      setCreatedDropPreview(null);
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setCreatedDropPreview({ kind: "insert", targetGroupId, beforeId: beforeWidgetId });
  }

  function handleGroupInsertDrop(event: DragEvent<HTMLElement>, targetGroupId: string, beforeWidgetId: string | null) {
    event.preventDefault();
    event.stopPropagation();
    if (!createdDrag || createdDrag.kind !== "widget") return;
    updateSchemaFromUser((current) => moveWidgetToGroupPosition(current, createdDrag.widgetId, targetGroupId, beforeWidgetId));
    handleSelectWidget(createdDrag.widgetId);
    handleCreatedDragEnd();
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
    if (!builderReady) return;
    draftActiveRef.current = true;
    saveDashboardDraft(schema, draftBaseKeyRef.current);
    setSavedFlash("draft");
    window.setTimeout(() => setSavedFlash(null), 2400);
  }

  function handleCancelEdit() {
    if (!builderReady) return;
    clearDashboardDraft(activeWorkflowId);
    draftActiveRef.current = false;
    onCancelEdit?.();
  }

  function handleContinue() {
    if (!builderReady || schema.widgets.length === 0 || hasValidationErrors) return;
    if (draftActiveRef.current) saveDashboardDraft(schema, draftBaseKeyRef.current);
    onContinue(schema);
  }

  return (
    <AppLayout activeRoute="workflows" onNavigate={onNavigate}>
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
            <p>Choose which workflow inputs and outputs should appear on the dashboard.</p>
          </div>

          <div className="builder-heading__meta">
            <div className="status-pill status-pill--info">
              <span />
              <span>{savedFlash === "saved" ? "Dashboard saved" : "Draft dashboard"}</span>
            </div>
            <div className="button-row">
              {onCancelEdit ? (
                <button className="secondary-button" type="button" onClick={handleCancelEdit} disabled={!builderReady}>
                  <X size={15} aria-hidden="true" />
                  Cancel
                </button>
              ) : (
                <button className="secondary-button" type="button" onClick={handleSaveDraft} disabled={!builderReady}>
                  <Save size={15} aria-hidden="true" />
                  Save as draft
                </button>
              )}
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
          <aside className="builder-pane builder-values" aria-label="Workflow controls">
            <header className="builder-pane__header">
              <div>
                <h2>Workflow controls</h2>
                <p>Pick an item to turn it into a simple dashboard control.</p>
              </div>
            </header>
            <div className="builder-pane__toolbar">
              <label className="search-field search-field--builder">
                <Search size={15} aria-hidden="true" />
                <span className="sr-only">Search workflow controls</span>
                <input
                  type="search"
                  placeholder="Search controls..."
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </label>
              <button className="secondary-button secondary-button--small builder-add-note" type="button" onClick={handleAddNote}>
                <Plus size={14} aria-hidden="true" />
                Add note
              </button>
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
                  <p>No controls match your search.</p>
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
                      onPreviewValue={handleShowValuePreview}
                      onPreviewLeave={() => setHoveredValuePreview(null)}
                    />
                  ))}
                </ul>
              )}
            </div>
          </aside>

          <main className="builder-pane builder-config" aria-label="Widget configuration">
            {selectedGroup ? (
              <GroupEditor
                group={selectedGroup}
                workflowId={activeWorkflowId}
                widgets={selectedGroup.widgetIds
                  .map((widgetId) => schema.widgets.find((widget) => widget.id === widgetId))
                  .filter((widget): widget is DashboardWidget => Boolean(widget))}
                valueIndex={valueIndex}
                onPatchGroup={(patch) => patchGroup(selectedGroup.id, patch)}
                onPatchWidget={patchWidget}
                onSelectWidget={handleSelectWidget}
              />
            ) : selectedWidget?.widgetType === "note" ? (
              <NoteWidgetEditor
                widget={selectedWidget}
                statusMessage={widgetSaveStatus?.widgetId === selectedWidget.id ? widgetSaveStatus.message : null}
                onStatusChange={(message) => setWidgetSaveStatus(message ? { widgetId: selectedWidget.id, message } : null)}
                onPatch={(patch) => patchWidget(selectedWidget.id, patch)}
                onRemove={() => removeWidget(selectedWidget.id)}
                onSaveDefault={() => patchWidget(selectedWidget.id, { defaultPinned: true })}
              />
            ) : selectedWidget && selectedValueRecord ? (
              <WidgetEditor
                widget={selectedWidget}
                value={selectedValueRecord.value}
                node={selectedValueRecord.node}
                workflowId={activeWorkflowId}
                statusMessage={widgetSaveStatus?.widgetId === selectedWidget.id ? widgetSaveStatus.message : null}
                onStatusChange={(message) => setWidgetSaveStatus(message ? { widgetId: selectedWidget.id, message } : null)}
                onPatch={(patch) => patchWidget(selectedWidget.id, patch)}
                onRemove={() => removeWidget(selectedWidget.id)}
                onSaveDefault={() => patchWidget(selectedWidget.id, { defaultPinned: true })}
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
                  <p>Select a workflow value or add a note for the people running it.</p>
                </div>
              ) : (
                <CreatedWidgetsList
                  items={createdItems}
                  selectedWidgetId={selectedWidgetId}
                  selectedGroupId={selectedGroupId}
                  draggingWidgetId={createdDrag?.kind === "widget" ? createdDrag.widgetId : null}
                  draggingGroupId={createdDrag?.kind === "group" ? createdDrag.groupId : null}
                  dropPreview={createdDropPreview}
                  onSelectWidget={handleSelectWidget}
                  onSelectGroup={handleSelectGroup}
                  onRenameWidget={(widgetId, title) => patchWidget(widgetId, { title })}
                  onRemoveWidget={removeWidget}
                  onDragStart={handleCreatedDragStart}
                  onGroupDragStart={handleCreatedGroupDragStart}
                  onDragEnd={handleCreatedDragEnd}
                  onWidgetDragOver={handleWidgetDragOver}
                  onWidgetDrop={handleWidgetDrop}
                  onGroupDragOver={handleGroupDragOver}
                  onGroupDrop={handleGroupDrop}
                  onTopLevelInsertDragOver={handleTopLevelInsertDragOver}
                  onTopLevelInsertDrop={handleTopLevelInsertDrop}
                  onGroupInsertDragOver={handleGroupInsertDragOver}
                  onGroupInsertDrop={handleGroupInsertDrop}
                />
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
      <RemoveDefaultDialog
        removal={pendingWidgetRemoval}
        onCancel={() => setPendingWidgetRemoval(null)}
        onKeep={(widgetId) => commitRemoveWidget(widgetId, true)}
        onDelete={(widgetId) => commitRemoveWidget(widgetId, false)}
      />
      <WorkflowValuePreview preview={hoveredValuePreview} />
    </AppLayout>
  );
}

function groupWidgetOnTarget(schema: DashboardSchema, widgetId: string, targetWidgetId: string): DashboardSchema {
  const groupMap = widgetGroupIdMap(schema);
  const targetGroupId = groupMap.get(targetWidgetId);
  const sourceGroupId = groupMap.get(widgetId);
  if (targetGroupId && sourceGroupId === targetGroupId) {
    return schema;
  }
  if (targetGroupId) {
    return moveWidgetToGroup(schema, widgetId, targetGroupId, targetWidgetId);
  }

  const current = ungroupWidget(schema, widgetId);
  const widgetOrder = new Map(current.widgets.map((widget, index) => [widget.id, index]));
  const widgetIds = [widgetId, targetWidgetId].sort((a, b) => (widgetOrder.get(a) ?? 0) - (widgetOrder.get(b) ?? 0));
  const targetWidget = current.widgets.find((widget) => widget.id === targetWidgetId);
  const draggedWidget = current.widgets.find((widget) => widget.id === widgetId);
  if (!targetWidget || !draggedWidget) return current;
  const layout = targetWidget.layout ?? draggedWidget.layout;
  const groupWidgets = widgetIds
    .map((id) => current.widgets.find((widget) => widget.id === id))
    .filter((widget): widget is DashboardWidget => Boolean(widget));
  const groups = [
    ...current.groups,
    {
      id: nextGroupId(current),
      title: groupTitleForWidgets(groupWidgets),
      description: "",
      widgetIds,
      layout,
    },
  ];
  return normalizeDashboardSchema({
    ...current,
    widgets: current.widgets.map((widget) =>
      widgetIds.includes(widget.id) ? withoutWidgetLayout(widget) : widget,
    ),
    groups,
  });
}

function moveCreatedItemToTopLevelPosition(
  schema: DashboardSchema,
  drag: CreatedDragState,
  beforeTopLevelId: string | null,
): DashboardSchema {
  if (drag.kind === "group") {
    return moveGroupToTopLevelPosition(schema, drag.groupId, beforeTopLevelId);
  }
  return moveWidgetToTopLevelPosition(schema, drag.widgetId, beforeTopLevelId);
}

function moveWidgetToTopLevelPosition(
  schema: DashboardSchema,
  widgetId: string,
  beforeTopLevelId: string | null,
): DashboardSchema {
  if (beforeTopLevelId === widgetId) return schema;
  if (!schema.widgets.some((widget) => widget.id === widgetId)) return schema;
  const anchorWidgetId = topLevelAnchorWidgetId(schema, beforeTopLevelId, new Set([widgetId]));
  const current = ungroupWidget(schema, widgetId);
  return normalizeDashboardSchema({
    ...current,
    widgets: moveWidgetBlockBefore(current.widgets, [widgetId], anchorWidgetId),
  });
}

function moveGroupToTopLevelPosition(
  schema: DashboardSchema,
  groupId: string,
  beforeTopLevelId: string | null,
): DashboardSchema {
  if (beforeTopLevelId === groupId) return schema;
  const group = schema.groups.find((candidate) => candidate.id === groupId);
  if (!group) return schema;
  const movingWidgetIds = group.widgetIds.filter((widgetId) =>
    schema.widgets.some((widget) => widget.id === widgetId),
  );
  if (movingWidgetIds.length === 0) return schema;
  const anchorWidgetId = topLevelAnchorWidgetId(schema, beforeTopLevelId, new Set(movingWidgetIds));
  return normalizeDashboardSchema({
    ...schema,
    widgets: moveWidgetBlockBefore(schema.widgets, movingWidgetIds, anchorWidgetId),
  });
}

function topLevelAnchorWidgetId(
  schema: DashboardSchema,
  beforeTopLevelId: string | null,
  movingWidgetIds: Set<string>,
): string | null {
  if (!beforeTopLevelId) return null;
  if (schema.widgets.some((widget) => widget.id === beforeTopLevelId && !movingWidgetIds.has(widget.id))) {
    return beforeTopLevelId;
  }

  const targetGroup = schema.groups.find((group) => group.id === beforeTopLevelId);
  return targetGroup?.widgetIds.find((widgetId) => !movingWidgetIds.has(widgetId)) ?? null;
}

function moveWidgetBlockBefore(
  widgets: DashboardWidget[],
  movingWidgetIds: string[],
  beforeWidgetId: string | null,
): DashboardWidget[] {
  const movingSet = new Set(movingWidgetIds);
  const movingById = new Map(widgets.filter((widget) => movingSet.has(widget.id)).map((widget) => [widget.id, widget]));
  const movingWidgets = movingWidgetIds
    .map((widgetId) => movingById.get(widgetId))
    .filter((widget): widget is DashboardWidget => Boolean(widget));
  if (movingWidgets.length === 0) return widgets;

  const remainingWidgets = widgets.filter((widget) => !movingSet.has(widget.id));
  const insertIndex = beforeWidgetId
    ? remainingWidgets.findIndex((widget) => widget.id === beforeWidgetId)
    : remainingWidgets.length;
  const resolvedInsertIndex = insertIndex >= 0 ? insertIndex : remainingWidgets.length;
  return [
    ...remainingWidgets.slice(0, resolvedInsertIndex),
    ...movingWidgets,
    ...remainingWidgets.slice(resolvedInsertIndex),
  ];
}

function moveWidgetToGroupPosition(
  schema: DashboardSchema,
  widgetId: string,
  targetGroupId: string,
  beforeWidgetId: string | null,
): DashboardSchema {
  const sourceGroupId = widgetGroupIdMap(schema).get(widgetId);
  if (sourceGroupId === targetGroupId) {
    return reorderWidgetInGroup(schema, widgetId, beforeWidgetId);
  }
  return moveWidgetToGroup(schema, widgetId, targetGroupId, beforeWidgetId ?? undefined);
}

function reorderWidgetInGroup(schema: DashboardSchema, widgetId: string, beforeWidgetId: string | null): DashboardSchema {
  const groupId = widgetGroupIdMap(schema).get(widgetId);
  if (!groupId) return schema;
  if (beforeWidgetId === widgetId) return schema;
  return normalizeDashboardSchema({
    ...schema,
    groups: schema.groups.map((group) => {
      if (group.id !== groupId) return group;
      const nextWidgetIds = group.widgetIds.filter((id) => id !== widgetId);
      const insertIndex = beforeWidgetId && nextWidgetIds.includes(beforeWidgetId)
        ? nextWidgetIds.indexOf(beforeWidgetId)
        : nextWidgetIds.length;
      nextWidgetIds.splice(insertIndex, 0, widgetId);
      return { ...group, widgetIds: nextWidgetIds };
    }),
  });
}

function moveWidgetToGroup(
  schema: DashboardSchema,
  widgetId: string,
  targetGroupId: string,
  beforeWidgetId?: string,
): DashboardSchema {
  const current = ungroupWidget(schema, widgetId);
  const targetGroup = current.groups.find((group) => group.id === targetGroupId);
  if (!targetGroup) return current;
  const nextWidgetIds = targetGroup.widgetIds.filter((id) => id !== widgetId);
  const insertIndex = beforeWidgetId && nextWidgetIds.includes(beforeWidgetId)
    ? nextWidgetIds.indexOf(beforeWidgetId)
    : nextWidgetIds.length;
  nextWidgetIds.splice(insertIndex, 0, widgetId);
  return normalizeDashboardSchema({
    ...current,
    widgets: current.widgets.map((widget) => (widget.id === widgetId ? withoutWidgetLayout(widget) : widget)),
    groups: current.groups.map((group) =>
      group.id === targetGroupId ? { ...group, widgetIds: nextWidgetIds } : group,
    ),
  });
}

function ungroupWidget(schema: DashboardSchema, widgetId: string): DashboardSchema {
  const groupId = widgetGroupIdMap(schema).get(widgetId);
  if (!groupId) return schema;
  const sourceGroup = schema.groups.find((group) => group.id === groupId);
  if (!sourceGroup) return schema;
  const remainingWidgetIds = sourceGroup.widgetIds.filter((id) => id !== widgetId);
  let widgets = schema.widgets.map((widget) => (widget.id === widgetId ? withoutWidgetLayout(widget) : widget));
  let groups = schema.groups.filter((group) => group.id !== groupId);

  if (remainingWidgetIds.length >= 2) {
    groups = [...groups, { ...sourceGroup, widgetIds: remainingWidgetIds }];
  } else if (remainingWidgetIds.length === 1 && sourceGroup.layout) {
    widgets = widgets.map((widget) =>
      widget.id === remainingWidgetIds[0] ? { ...widget, layout: sourceGroup.layout } : widget,
    );
  }

  return normalizeDashboardSchema({ ...schema, widgets, groups });
}

function widgetHasSavedDefault(widget: DashboardWidget): boolean {
  return widget.defaultPinned === true && canPreserveWidgetAsHiddenInput(widget);
}

function RemoveDefaultDialog({
  removal,
  onCancel,
  onKeep,
  onDelete,
}: {
  removal: PendingWidgetRemoval;
  onCancel: () => void;
  onKeep: (widgetId: string) => void;
  onDelete: (widgetId: string) => void;
}) {
  if (!removal) return null;
  return (
    <div className="builder-remove-dialog" role="dialog" aria-modal="true" aria-label="Remove saved default">
      <div className="builder-remove-dialog__panel">
        <h2>Remove widget?</h2>
        <p>
          This widget has a saved default. Keep it as a hidden default, or delete it to restore the original workflow default.
        </p>
        <div className="builder-remove-dialog__actions">
          <button className="secondary-button" type="button" onClick={onCancel}>
            Cancel
          </button>
          <button className="secondary-button" type="button" onClick={() => onDelete(removal.widget.id)}>
            Delete default
          </button>
          <button className="primary-button" type="button" onClick={() => onKeep(removal.widget.id)}>
            Keep hidden default
          </button>
        </div>
      </div>
    </div>
  );
}

function withoutWidgetLayout(widget: DashboardWidget): DashboardWidget {
  const { layout: _layout, ...withoutLayout } = widget;
  return withoutLayout;
}

function groupTitleForWidgets(widgets: DashboardWidget[]): string {
  const titles = widgets.map((widget) => widget.title.trim()).filter(Boolean);
  if (titles.length >= 2) return `${titles[0]} + ${titles[1]}`;
  return "Widget group";
}

function nextGroupId(schema: DashboardSchema): string {
  const existing = new Set(schema.groups.map((group) => group.id));
  let index = schema.groups.length + 1;
  let id = `group-${index}`;
  while (existing.has(id)) {
    index += 1;
    id = `group-${index}`;
  }
  return id;
}

function nextDashboardNoteId(schema: DashboardSchema): string {
  const existing = new Set(schema.widgets.map((widget) => widget.id));
  let index = 1;
  let id = `note-${index}`;
  while (existing.has(id)) {
    index += 1;
    id = `note-${index}`;
  }
  return id;
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
            <span>Loading this workflow's controls and saved dashboard draft.</span>
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

function normalizeSearchText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.toLocaleLowerCase();
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value).toLocaleLowerCase();
  }
  try {
    return JSON.stringify(value).toLocaleLowerCase();
  } catch {
    return String(value).toLocaleLowerCase();
  }
}

const VALUE_PREVIEW_WIDTH = 320;
const VALUE_PREVIEW_MAX_HEIGHT = 420;
const VALUE_PREVIEW_GAP = 12;
const VALUE_PREVIEW_MARGIN = 14;

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function previewPositionForElement(element: HTMLElement): { position: { top: number; left: number }; side: "left" | "right" } {
  const rect = element.getBoundingClientRect();
  const viewportWidth = typeof window !== "undefined" ? window.innerWidth : 1280;
  const viewportHeight = typeof window !== "undefined" ? window.innerHeight : 720;
  const rightLeft = rect.right + VALUE_PREVIEW_GAP;
  const fitsRight = rightLeft + VALUE_PREVIEW_WIDTH <= viewportWidth - VALUE_PREVIEW_MARGIN;
  const side = fitsRight ? "right" : "left";
  const fallbackLeft = rect.left - VALUE_PREVIEW_WIDTH - VALUE_PREVIEW_GAP;
  const rawLeft = fitsRight ? rightLeft : fallbackLeft;
  const maxTop = Math.max(VALUE_PREVIEW_MARGIN, viewportHeight - VALUE_PREVIEW_MAX_HEIGHT - VALUE_PREVIEW_MARGIN);

  return {
    side,
    position: {
      left: clampNumber(rawLeft, VALUE_PREVIEW_MARGIN, Math.max(VALUE_PREVIEW_MARGIN, viewportWidth - VALUE_PREVIEW_WIDTH - VALUE_PREVIEW_MARGIN)),
      top: clampNumber(rect.top, VALUE_PREVIEW_MARGIN, maxTop),
    },
  };
}

const WORKFLOW_NODE_COLLATOR = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

function sortWorkflowNodesByName(nodes: WorkflowNode[]): WorkflowNode[] {
  return nodes
    .map((node) => ({ ...node, values: [...node.values] }))
    .sort(
      (a, b) =>
        WORKFLOW_NODE_COLLATOR.compare(nodeSortName(a), nodeSortName(b)) ||
        WORKFLOW_NODE_COLLATOR.compare(a.classType, b.classType) ||
        WORKFLOW_NODE_COLLATOR.compare(a.id, b.id),
    );
}

function nodeSortName(node: Pick<WorkflowNode, "id" | "title" | "classType">): string {
  return node.title.trim() || node.classType.trim() || node.id;
}

function valueMatchesSearch(
  node: WorkflowNode,
  value: WorkflowNodeValue,
  queryTerms: string[],
  dashboardTitles: string[] = [],
): boolean {
  const searchableText = [
    node.id,
    node.title,
    node.classType,
    value.id,
    value.nodeId,
    value.inputName,
    value.label,
    value.valueKind,
    value.hint,
    value.rawValue,
    ...dashboardTitles,
    ...(value.options ?? []),
  ]
    .map(normalizeSearchText)
    .filter(Boolean)
    .join(" ");

  return queryTerms.every((term) => searchableText.includes(term));
}

function NodeListItem({
  node,
  expanded,
  exposedIds,
  selectedValueId,
  onToggle,
  onSelectValue,
  onPreviewValue,
  onPreviewLeave,
}: {
  node: WorkflowNode;
  expanded: boolean;
  exposedIds: Set<string>;
  selectedValueId: string | null;
  onToggle: () => void;
  onSelectValue: (id: string) => void;
  onPreviewValue: (node: WorkflowNode, value: WorkflowNodeValue, event: { currentTarget: HTMLButtonElement }) => void;
  onPreviewLeave: () => void;
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
        <span className="builder-node__text">
          <span className="builder-node__title">{node.title}</span>
          <span className="builder-node__class">{node.classType}</span>
        </span>
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
                  onMouseEnter={(event) => onPreviewValue(node, value, event)}
                  onMouseLeave={onPreviewLeave}
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

interface ValuePreviewDetail {
  label: string;
  value: string;
  multiline?: boolean;
}

function WorkflowValuePreview({ preview }: { preview: HoveredValuePreview }) {
  if (!preview) return null;

  const summary = workflowValuePreviewSummary(preview.node, preview.value);
  const Icon = VALUE_KIND_ICONS[preview.value.valueKind];

  return (
    <aside
      className={`builder-value-preview builder-value-preview--${preview.side}`}
      role="tooltip"
      style={{ top: preview.position.top, left: preview.position.left }}
    >
      <div className="builder-value-preview__header">
        <span className="builder-value-preview__icon" aria-hidden="true">
          <Icon size={16} />
        </span>
        <div>
          <span className="builder-value-preview__eyebrow">Workflow value preview</span>
          <h3>{summary.title}</h3>
        </div>
      </div>

      <dl className="builder-value-preview__details">
        <div className="builder-value-preview__row">
          <dt>Widget type</dt>
          <dd>{WIDGET_TYPE_LABELS[summary.widgetType]}</dd>
        </div>
        {summary.details.map((detail) => (
          <div className={`builder-value-preview__row ${detail.multiline ? "builder-value-preview__row--stacked" : ""}`} key={detail.label}>
            <dt>{detail.label}</dt>
            <dd>{detail.value}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}

function workflowValuePreviewSummary(node: WorkflowNode, value: WorkflowNodeValue): {
  title: string;
  widgetType: WidgetType;
  details: ValuePreviewDetail[];
} {
  const widgetType = suggestWidgetType(value);
  const currentValue = formatPreviewValue(value.rawValue);
  const details: ValuePreviewDetail[] = [
    { label: "Dashboard label", value: suggestTitle(value, node.title) },
    { label: "Value kind", value: valueKindPreviewLabel(value.valueKind) },
    ...widgetSpecificPreviewDetails(value, widgetType),
  ];

  if (currentValue) {
    details.splice(2, 0, { label: "Current/default", value: currentValue, multiline: currentValue.includes("\n") || currentValue.length > 64 });
  }
  if (value.hint) {
    details.push({ label: "Hint", value: value.hint, multiline: value.hint.length > 64 });
  }

  return {
    title: `${node.title}: ${value.label}`,
    widgetType,
    details,
  };
}

function widgetSpecificPreviewDetails(value: WorkflowNodeValue, widgetType: WidgetType): ValuePreviewDetail[] {
  if (widgetType === "slider" || widgetType === "int_field" || widgetType === "seed_widget") {
    const range = defaultNumericRangeForValue(value);
    const details: ValuePreviewDetail[] = [];
    if (range) {
      details.push({
        label: widgetType === "slider" ? "Slider range" : "Number range",
        value: `${range.min} to ${range.max}${range.step !== undefined ? `, step ${range.step}` : ""}`,
      });
    }
    if (widgetType === "seed_widget") {
      details.push({ label: "Behavior", value: "Integer seed value with randomize control" });
    }
    return details;
  }

  if (widgetType === "select" || widgetType === "lora_loader") {
    const options = value.options ?? [];
    return [
      {
        label: `Options${options.length > 0 ? ` (${options.length})` : ""}`,
        value: options.length > 0 ? truncatePreviewText(options.join(", "), 260) : "No choices discovered yet",
        multiline: options.join(", ").length > 64,
      },
    ];
  }

  if (widgetType === "load_image" || widgetType === "load_image_mask") {
    return [
      {
        label: "Accepts",
        value: widgetType === "load_image_mask" ? "Image files, with mask drawing available" : "Image files (image/*)",
      },
    ];
  }

  if (widgetType === "load_audio") return [{ label: "Accepts", value: "Audio files (audio/*)" }];
  if (widgetType === "load_video") return [{ label: "Accepts", value: "Video files (video/*)" }];
  if (widgetType === "load_3d") {
    return [
      {
        label: "Accepts",
        value: ".glb, .gltf, .obj, .stl, .fbx, .ply, .usdz, .dae, .spz, .splat, .ksplat",
        multiline: true,
      },
    ];
  }
  if (widgetType === "load_file") {
    return [{ label: "Accepts", value: DEFAULT_FILE_ACCEPTED_EXTENSIONS.join(", "), multiline: true }];
  }

  if (widgetType === "display_image") return [{ label: "Output", value: "Generated image result" }];
  if (widgetType === "display_audio") return [{ label: "Output", value: "Generated audio result" }];
  if (widgetType === "display_text") return [{ label: "Output", value: "Generated text result" }];
  if (widgetType === "display_video") return [{ label: "Output", value: "Generated video result" }];
  if (widgetType === "display_file") return [{ label: "Output", value: "Generated file result" }];
  if (widgetType === "display_3d") return [{ label: "Output", value: "Generated 3D model result" }];

  if (widgetType === "note") return [{ label: "Contains", value: "Creator guidance shown on the dashboard" }];
  if (widgetType === "textarea") return [{ label: "Text", value: "Multi-line text input" }];
  if (widgetType === "string_field") return [{ label: "Text", value: "Single-line text input" }];
  if (widgetType === "toggle") return [{ label: "Value", value: "Boolean on/off value" }];

  return [];
}

function valueKindPreviewLabel(kind: WorkflowNodeValue["valueKind"]): string {
  const labels: Record<WorkflowNodeValue["valueKind"], string> = {
    string: "Text",
    number: "Number",
    boolean: "Boolean",
    note: "Note",
    image_input: "Image input",
    image_output: "Image output",
    audio_input: "Audio input",
    audio_output: "Audio output",
    text_output: "Text output",
    video_input: "Video input",
    video_output: "Video output",
    file_input: "File input",
    file_output: "File output",
    three_d_input: "3D model input",
    three_d_output: "3D model output",
    seed: "Seed",
    lora: "LoRA",
    select: "Dropdown choice",
  };
  return labels[kind];
}

function formatPreviewValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return truncatePreviewText(value.length > 0 ? value : "(empty text)", 520);
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") return String(value);
  try {
    return truncatePreviewText(JSON.stringify(value, null, 2), 520);
  } catch {
    return truncatePreviewText(String(value), 520);
  }
}

function truncatePreviewText(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, maxLength - 3)}...`;
}

type NumericValidationField = "defaultValue" | "min" | "max" | "step";

interface NumericValidationError {
  field: NumericValidationField;
  message: string;
}

function validateWidgetForSave(widget: DashboardWidget): NumericValidationError[] {
  if (widget.widgetType === "slider") return validateSliderWidget(widget);
  if (widget.widgetType === "int_field") return validateNumberFieldWidget(widget);
  return [];
}

function validateSliderWidget(widget: DashboardWidget): NumericValidationError[] {
  if (widget.widgetType !== "slider") return [];

  const errors: NumericValidationError[] = [];
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
    errors.push({ field: "step", message: "Step size must be positive. Decimals such as 0.01 are allowed." });
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

function validateNumberFieldWidget(widget: DashboardWidget): NumericValidationError[] {
  if (widget.widgetType !== "int_field") return [];
  const min = finiteNumber(widget.min);
  const max = finiteNumber(widget.max);
  if (min !== null && max !== null && max < min) {
    return [{ field: "max", message: "Maximum value must be greater than or equal to minimum value." }];
  }
  return [];
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

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseFiniteDecimal(value: string): number | null {
  const trimmed = value.trim();
  if (!/^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(trimmed)) return null;
  return finiteNumber(Number(trimmed));
}

function parseFiniteDecimalOnBlur(value: string): number | null {
  const trimmed = value.trim();
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(trimmed)) return null;
  return finiteNumber(Number(trimmed));
}

function positiveFiniteNumber(value: unknown): number | null {
  const numeric = finiteNumber(value);
  return numeric !== null && numeric > 0 ? numeric : null;
}

function alignsWithStep(value: number, min: number, step: number): boolean {
  const stepIndex = (value - min) / step;
  return Math.abs(stepIndex - Math.round(stepIndex)) < 1e-7;
}

function SliderNumberInput({
  label,
  value,
  ariaInvalid,
  onChange,
}: {
  label: string;
  value: unknown;
  ariaInvalid: boolean;
  onChange: (value: number) => void;
}) {
  const externalValue = numericInputValue(value);
  const [draft, setDraft] = useState(() => (externalValue === "" ? "" : String(externalValue)));

  useEffect(() => {
    setDraft((current) => {
      const parsedCurrent = parseFiniteDecimal(current);
      if (externalValue !== "" && parsedCurrent === externalValue) return current;
      if (externalValue === "" && current === "") return current;
      return externalValue === "" ? "" : String(externalValue);
    });
  }, [externalValue]);

  return (
    <input
      type="text"
      inputMode="decimal"
      autoComplete="off"
      spellCheck={false}
      className="builder-input"
      value={draft}
      aria-label={label}
      aria-invalid={ariaInvalid}
      onChange={(event) => {
        const nextDraft = event.target.value;
        setDraft(nextDraft);
        const nextValue = parseFiniteDecimal(nextDraft);
        if (nextValue !== null) onChange(nextValue);
      }}
      onBlur={() => {
        const parsedDraft = parseFiniteDecimalOnBlur(draft);
        setDraft(parsedDraft === null ? (externalValue === "" ? "" : String(externalValue)) : String(parsedDraft));
      }}
    />
  );
}

function OptionalNumberInput({
  label,
  value,
  placeholder,
  onChange,
}: {
  label: string;
  value: unknown;
  placeholder: string;
  onChange: (value: number | undefined) => void;
}) {
  const numericValue = numericInputValue(value);
  return (
    <input
      type="number"
      inputMode="decimal"
      className="builder-input"
      value={numericValue}
      step="any"
      placeholder={placeholder}
      aria-label={label}
      onChange={(event) => {
        if (event.target.value === "") {
          onChange(undefined);
          return;
        }
        const nextValue = Number(event.target.value);
        if (Number.isFinite(nextValue)) onChange(nextValue);
      }}
    />
  );
}

function WidgetEditor({
  widget,
  value,
  node,
  workflowId,
  statusMessage,
  onStatusChange,
  onPatch,
  onRemove,
  onSaveDefault,
}: {
  widget: DashboardWidget;
  value: WorkflowNodeValue;
  node: WorkflowNode;
  workflowId: string;
  statusMessage: string | null;
  onStatusChange: (message: string | null) => void;
  onPatch: (patch: Partial<DashboardWidget>) => void;
  onRemove: () => void;
  onSaveDefault: () => void;
}) {
  function handleSaveDefault() {
    onSaveDefault();
    onStatusChange("Default saved.");
  }

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
        <div className="builder-config__header-actions">
          <button className="secondary-button secondary-button--small" type="button" onClick={handleSaveDefault}>
            <Save size={14} aria-hidden="true" />
            Save as default
          </button>
          <button className="icon-button icon-button--danger" type="button" onClick={onRemove} aria-label="Remove widget" title="Remove widget">
            <Trash2 size={16} aria-hidden="true" />
          </button>
        </div>
      </div>

      {statusMessage ? <p className="builder-config__hint">{statusMessage}</p> : null}
      <WidgetDetailsCard widget={widget} onPatch={onPatch} />
      <WidgetBehaviorCard widget={widget} value={value} workflowId={workflowId} onPatch={onPatch} />
      <WidgetBinding widget={widget} />
    </div>
  );
}

function NoteWidgetEditor({
  widget,
  statusMessage,
  onStatusChange,
  onPatch,
  onRemove,
  onSaveDefault,
}: {
  widget: DashboardWidget;
  statusMessage: string | null;
  onStatusChange: (message: string | null) => void;
  onPatch: (patch: Partial<DashboardWidget>) => void;
  onRemove: () => void;
  onSaveDefault: () => void;
}) {
  function handleSaveDefault() {
    onSaveDefault();
    onStatusChange("Default saved.");
  }

  return (
    <div className="builder-config__inner">
      <div className="builder-config__top">
        <div>
          <p className="builder-config__breadcrumb">Dashboard note</p>
          <h2>Configure note</h2>
          <p className="builder-config__summary">
            Notes give people instructions, explanations, or warnings without changing the workflow.
          </p>
        </div>
        <div className="builder-config__header-actions">
          {widget.hasExecutableBinding ? (
            <button className="secondary-button secondary-button--small" type="button" onClick={handleSaveDefault}>
              <Save size={14} aria-hidden="true" />
              Save as default
            </button>
          ) : null}
          <button className="icon-button icon-button--danger" type="button" onClick={onRemove} aria-label="Remove widget" title="Remove widget">
            <Trash2 size={16} aria-hidden="true" />
          </button>
        </div>
      </div>

      {statusMessage ? <p className="builder-config__hint">{statusMessage}</p> : null}
      <WidgetDetailsCard widget={widget} onPatch={onPatch} />
      <WidgetBinding widget={widget} />
    </div>
  );
}

function WidgetDetailsCard({
  widget,
  onPatch,
}: {
  widget: DashboardWidget;
  onPatch: (patch: Partial<DashboardWidget>) => void;
}) {
  return (
    <FormCard title="Widget details">
      <WidgetDetailsFields widget={widget} onPatch={onPatch} />
    </FormCard>
  );
}

function WidgetDetailsFields({
  widget,
  onPatch,
}: {
  widget: DashboardWidget;
  onPatch: (patch: Partial<DashboardWidget>) => void;
}) {
  return (
    <>
      <FieldRow label={widget.widgetType === "note" ? "Note title" : "Widget title"}>
        <input
          type="text"
          className="builder-input"
          value={widget.title}
          onChange={(event) => onPatch({ title: event.target.value })}
        />
      </FieldRow>
      <FieldRow
        label={widget.widgetType === "note" ? "Note body" : "Helper description"}
        hint={widget.widgetType === "note" ? "Supports multiple lines of creator guidance." : "Shown under the widget. Keep it short and friendly."}
      >
        <textarea
          className="builder-input builder-input--textarea"
          rows={2}
          value={widget.description}
          onChange={(event) => onPatch({ description: event.target.value })}
        />
      </FieldRow>
    </>
  );
}

function WidgetBehaviorCard({
  widget,
  value,
  workflowId,
  onPatch,
}: {
  widget: DashboardWidget;
  value: WorkflowNodeValue;
  workflowId: string;
  onPatch: (patch: Partial<DashboardWidget>) => void;
}) {
  return (
    <FormCard title="Widget behavior">
      <WidgetBehaviorFields widget={widget} value={value} workflowId={workflowId} onPatch={onPatch} />
    </FormCard>
  );
}

function WidgetBehaviorFields({
  widget,
  value,
  workflowId,
  onPatch,
}: {
  widget: DashboardWidget;
  value: WorkflowNodeValue;
  workflowId: string;
  onPatch: (patch: Partial<DashboardWidget>) => void;
}) {
  const allowedTypes = widgetTypesForKind(value.valueKind);
  const showSliderSettings = widget.widgetType === "slider";
  const showNumberRange = widget.widgetType === "int_field";
  const showSeedSettings = widget.widgetType === "seed_widget";
  const showOptions = widget.widgetType === "select" || widget.widgetType === "lora_loader";
  const showImageOptions = widget.widgetType === "load_image" || widget.widgetType === "load_image_mask";
  const showFileOptions = widget.widgetType === "load_file";
  const sliderErrors = validateSliderWidget(widget);
  const sliderErrorFor = (field: NumericValidationField) => sliderErrors.find((error) => error.field === field)?.message;
  const numberFieldErrors = validateNumberFieldWidget(widget);
  const numberFieldErrorFor = (field: NumericValidationField) => numberFieldErrors.find((error) => error.field === field)?.message;

  return (
    <>
      <FieldRow label="Widget type">
        <select
          className="builder-input"
          value={widget.widgetType}
          onChange={(event) => {
            const widgetType = event.target.value as WidgetType;
            if (widgetType === "slider") {
              onPatch({ widgetType, ...sliderDefaultsForValue(value, widget) });
            } else if (widgetType === "load_file") {
              onPatch({ widgetType, acceptedExtensions: widget.acceptedExtensions ?? DEFAULT_FILE_ACCEPTED_EXTENSIONS });
            } else {
              onPatch({ widgetType });
            }
          }}
        >
          {allowedTypes.map((type) => (
            <option key={type} value={type}>
              {WIDGET_TYPE_LABELS[type]}
            </option>
          ))}
        </select>
      </FieldRow>

      {showSliderSettings ? (
        <div className="builder-config__grid">
          <FieldRow label="Default value" error={sliderErrorFor("defaultValue")}>
            <SliderNumberInput
              label="Default value"
              value={widget.defaultValue}
              ariaInvalid={Boolean(sliderErrorFor("defaultValue"))}
              onChange={(defaultValue) => onPatch({ defaultValue })}
            />
          </FieldRow>
          <FieldRow label="Minimum value" error={sliderErrorFor("min")}>
            <SliderNumberInput
              label="Minimum value"
              value={widget.min}
              ariaInvalid={Boolean(sliderErrorFor("min"))}
              onChange={(min) => onPatch({ min })}
            />
          </FieldRow>
          <FieldRow label="Maximum value" error={sliderErrorFor("max")}>
            <SliderNumberInput
              label="Maximum value"
              value={widget.max}
              ariaInvalid={Boolean(sliderErrorFor("max"))}
              onChange={(max) => onPatch({ max })}
            />
          </FieldRow>
          <FieldRow
            label="Step size"
            hint="Controls how much the value changes each time the slider moves."
            error={sliderErrorFor("step")}
          >
            <SliderNumberInput
              label="Step size"
              value={widget.step}
              ariaInvalid={Boolean(sliderErrorFor("step"))}
              onChange={(step) => onPatch({ step })}
            />
          </FieldRow>
        </div>
      ) : null}

      {showNumberRange ? (
        <div className="builder-config__grid">
          <FieldRow label="Minimum value" hint="Optional. Leave empty when the number has no lower limit.">
            <OptionalNumberInput
              label="Minimum value"
              value={widget.min}
              placeholder="No minimum"
              onChange={(min) => onPatch({ min })}
            />
          </FieldRow>
          <FieldRow
            label="Maximum value"
            hint="Optional. Leave empty when the number has no upper limit."
            error={numberFieldErrorFor("max")}
          >
            <OptionalNumberInput
              label="Maximum value"
              value={widget.max}
              placeholder="No maximum"
              onChange={(max) => onPatch({ max })}
            />
          </FieldRow>
          {value.numberRange ? (
            <FieldRow label="Step">
              <input
                type="number"
                className="builder-input"
                value={widget.step ?? value.numberRange.step ?? 1}
                step={value.numberRange.step ?? 1}
                onChange={(event) => onPatch({ step: Number(event.target.value) })}
              />
            </FieldRow>
          ) : null}
        </div>
      ) : null}

      {showSeedSettings ? (
        <FieldRow
          label="After each generation"
          hint="Controls how the seed changes after every run when people use this dashboard."
        >
          <select
            className="builder-input"
            value={widget.seedMode ?? DEFAULT_SEED_MODE}
            onChange={(event) => onPatch({ seedMode: event.target.value as SeedMode })}
          >
            {SEED_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {SEED_MODE_LABELS[mode]}
              </option>
            ))}
          </select>
        </FieldRow>
      ) : null}

      {showOptions ? (
        <DropdownOptionsEditor
          options={widget.options ?? value.options ?? []}
          onChange={(options) => onPatch({ options })}
        />
      ) : null}

      {showImageOptions ? (
        <ToggleRow
          checked={Boolean(widget.drawMask)}
          onChange={(drawMask) =>
            onPatch({ drawMask, widgetType: drawMask ? "load_image_mask" : "load_image" })
          }
          label="Allow drawing a mask"
          hint="Adds a mask brush over the uploaded image."
        />
      ) : null}

      {showFileOptions ? (
        <div className="builder-config__grid">
          <FieldRow label="Accepted extensions" hint="Comma-separated, for example .txt, .json, .zip">
            <input
              className="builder-input"
              type="text"
              value={(widget.acceptedExtensions ?? []).join(", ")}
              onChange={(event) => onPatch({ acceptedExtensions: splitCsvList(event.target.value) })}
            />
          </FieldRow>
          <FieldRow label="Accepted MIME types" hint="Optional comma-separated MIME allow-list.">
            <input
              className="builder-input"
              type="text"
              value={(widget.acceptedMimeTypes ?? []).join(", ")}
              onChange={(event) => onPatch({ acceptedMimeTypes: splitCsvList(event.target.value) })}
            />
          </FieldRow>
        </div>
      ) : null}

      {widget.widgetType !== "display_image" && widget.widgetType !== "slider" ? (
        <DefaultValueEditor widget={widget} value={value} workflowId={workflowId} onPatch={onPatch} />
      ) : null}
    </>
  );
}

function WidgetBinding({ widget }: { widget: DashboardWidget }) {
  if (widget.widgetType === "note" && !widget.hasExecutableBinding) {
    return (
      <div className="builder-config__binding">
        <span>Dashboard-only information</span>
      </div>
    );
  }

  return (
    <div className="builder-config__binding">
      <span>Connected to</span>
      <code>node {widget.binding.nodeId}</code>
      <span className="builder-config__binding-arrow">→</span>
      <code>{widget.binding.inputName}</code>
    </div>
  );
}

function GroupEditor({
  group,
  workflowId,
  widgets,
  valueIndex,
  onPatchGroup,
  onPatchWidget,
  onSelectWidget,
}: {
  group: DashboardWidgetGroup;
  workflowId: string;
  widgets: DashboardWidget[];
  valueIndex: Map<string, { node: WorkflowNode; value: WorkflowNodeValue }>;
  onPatchGroup: (patch: Partial<DashboardWidgetGroup>) => void;
  onPatchWidget: (widgetId: string, patch: Partial<DashboardWidget>) => void;
  onSelectWidget: (widgetId: string) => void;
}) {
  return (
    <div className="builder-config__inner">
      <div className="builder-config__top">
        <div>
          <p className="builder-config__breadcrumb">
            <span>Created widgets</span>
            <ChevronRight size={12} aria-hidden="true" />
            <span>{group.title}</span>
          </p>
          <h2>Configure group</h2>
          <p className="builder-config__summary">
            You are editing the whole group. Each widget inside keeps its own binding, value, validation, and default.
          </p>
        </div>
      </div>

      <FormCard title="Group details">
        <FieldRow label="Group title">
          <input
            type="text"
            className="builder-input"
            value={group.title}
            onChange={(event) => onPatchGroup({ title: event.target.value })}
          />
        </FieldRow>
        <FieldRow label="Group helper description" hint="Shown under the group title on the dashboard.">
          <textarea
            className="builder-input builder-input--textarea"
            rows={2}
            value={group.description}
            onChange={(event) => onPatchGroup({ description: event.target.value })}
          />
        </FieldRow>
      </FormCard>

      <div className="builder-group-editor__children">
        {widgets.map((widget, index) => {
          const record = valueIndex.get(widget.valueId);
          if (!record && widget.widgetType !== "note") return null;
          return (
            <section className="builder-card builder-card--child-widget" key={widget.id}>
              <header className="builder-card__child-header">
                <div>
                  <h3>{widget.title || `Widget ${index + 1}`}</h3>
                  <p>
                    {widget.widgetType === "note"
                      ? WIDGET_TYPE_LABELS[widget.widgetType]
                      : `${WIDGET_TYPE_LABELS[widget.widgetType]} · node ${widget.binding.nodeId} → ${widget.binding.inputName}`}
                  </p>
                </div>
                <button className="secondary-button secondary-button--small" type="button" onClick={() => onSelectWidget(widget.id)}>
                  Edit only this widget
                </button>
              </header>
              <div className="builder-card__body">
                <WidgetDetailsFields widget={widget} onPatch={(patch) => onPatchWidget(widget.id, patch)} />
                <div className="builder-card__divider" />
                {record && widget.widgetType !== "note" ? (
                  <WidgetBehaviorFields widget={widget} value={record.value} workflowId={workflowId} onPatch={(patch) => onPatchWidget(widget.id, patch)} />
                ) : null}
                <WidgetBinding widget={widget} />
              </div>
            </section>
          );
        })}
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
  workflowId,
  onPatch,
}: {
  widget: DashboardWidget;
  value: WorkflowNodeValue;
  workflowId: string;
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
        min={widget.widgetType === "int_field" ? widget.min : undefined}
        max={widget.widgetType === "int_field" ? widget.max : undefined}
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
      <DefaultAssetUploader widget={widget} workflowId={workflowId} onPatch={onPatch} />
    );
  }

  if (widget.widgetType === "load_audio" || widget.widgetType === "load_video" || widget.widgetType === "load_3d" || widget.widgetType === "load_file") {
    return (
      <DefaultAssetUploader widget={widget} workflowId={workflowId} onPatch={onPatch} />
    );
  }

  return null;
}

function DefaultAssetUploader({
  widget,
  workflowId,
  onPatch,
}: {
  widget: DashboardWidget;
  workflowId: string;
  onPatch: (patch: Partial<DashboardWidget>) => void;
}) {
  const [status, setStatus] = useState<WidgetScopedStatus | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const accept = uploadAcceptForWidget(widget);
  const statusMessage = status?.widgetId === widget.id ? status.message : null;

  useEffect(() => {
    setStatus(null);
  }, [widget.id]);

  async function handleFile(file: File | null) {
    if (!file) return;
    setStatus(null);
    try {
      const result = await uploadDefaultAssetForWidget(workflowId, widget, file);
      onPatch({ defaultValue: result.asset_id, defaultPinned: true });
      setStatus({ widgetId: widget.id, message: `${file.name} saved as default.` });
    } catch (error) {
      setStatus({ widgetId: widget.id, message: error instanceof Error ? error.message : "Default upload failed." });
    }
  }
  return (
    <div className="builder-default-asset" data-noofy-workflow-import-drop-ignore>
      <button className="secondary-button secondary-button--small" type="button" onClick={() => fileInputRef.current?.click()}>
        <File size={14} aria-hidden="true" />
        Upload default
      </button>
      <input
        ref={fileInputRef}
        type="file"
        accept={accept}
        style={{ display: "none" }}
        onChange={(event) => {
          void handleFile(event.target.files?.[0] ?? null);
          event.currentTarget.value = "";
        }}
      />
      {statusMessage ? <p className="builder-config__hint">{statusMessage}</p> : null}
    </div>
  );
}

async function uploadDefaultAssetForWidget(workflowId: string, widget: DashboardWidget, file: File) {
  if (widget.widgetType === "load_image" || widget.widgetType === "load_image_mask") {
    return uploadDashboardAsset(workflowId, file);
  }
  if (widget.widgetType === "load_audio") {
    return uploadDashboardAudioAsset(workflowId, file);
  }
  if (widget.widgetType === "load_video") {
    return uploadDashboardVideoAsset(workflowId, file);
  }
  if (widget.widgetType === "load_3d") {
    return uploadDashboardThreeDAsset(workflowId, file);
  }
  return uploadDashboardFileAsset(workflowId, widget.id, file);
}

function uploadAcceptForWidget(widget: DashboardWidget): string | undefined {
  if (widget.widgetType === "load_image" || widget.widgetType === "load_image_mask") return "image/*";
  if (widget.widgetType === "load_audio") return "audio/*";
  if (widget.widgetType === "load_video") return "video/*";
  if (widget.widgetType === "load_3d") return ".glb,.gltf,.obj,.stl,.fbx,.ply,.usdz,.dae,.spz,.splat,.ksplat";
  if (widget.widgetType === "load_file") return (widget.acceptedExtensions ?? DEFAULT_FILE_ACCEPTED_EXTENSIONS).join(",");
  return undefined;
}

function splitCsvList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
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

function CreatedWidgetsList({
  items,
  selectedWidgetId,
  selectedGroupId,
  draggingWidgetId,
  draggingGroupId,
  dropPreview,
  onSelectWidget,
  onSelectGroup,
  onRenameWidget,
  onRemoveWidget,
  onDragStart,
  onGroupDragStart,
  onDragEnd,
  onWidgetDragOver,
  onWidgetDrop,
  onGroupDragOver,
  onGroupDrop,
  onTopLevelInsertDragOver,
  onTopLevelInsertDrop,
  onGroupInsertDragOver,
  onGroupInsertDrop,
}: {
  items: DashboardTopLevelItem[];
  selectedWidgetId: string | null;
  selectedGroupId: string | null;
  draggingWidgetId: string | null;
  draggingGroupId: string | null;
  dropPreview: CreatedDropPreview;
  onSelectWidget: (id: string) => void;
  onSelectGroup: (id: string) => void;
  onRenameWidget: (id: string, title: string) => void;
  onRemoveWidget: (id: string) => void;
  onDragStart: (id: string) => void;
  onGroupDragStart: (id: string) => void;
  onDragEnd: () => void;
  onWidgetDragOver: (event: DragEvent<HTMLElement>, targetWidgetId: string) => void;
  onWidgetDrop: (event: DragEvent<HTMLElement>, targetWidgetId: string) => void;
  onGroupDragOver: (event: DragEvent<HTMLElement>, targetGroupId: string) => void;
  onGroupDrop: (event: DragEvent<HTMLElement>, targetGroupId: string) => void;
  onTopLevelInsertDragOver: (event: DragEvent<HTMLElement>, beforeId: string | null) => void;
  onTopLevelInsertDrop: (event: DragEvent<HTMLElement>, beforeId: string | null) => void;
  onGroupInsertDragOver: (event: DragEvent<HTMLElement>, targetGroupId: string, beforeWidgetId: string | null) => void;
  onGroupInsertDrop: (event: DragEvent<HTMLElement>, targetGroupId: string, beforeWidgetId: string | null) => void;
}) {
  return (
    <section className="preview-section">
      <header>
        <h4>Created widgets</h4>
      </header>
      <div className="preview-stack">
        {items.map((item) => (
          <Fragment key={item.id}>
            <PreviewInsertDropZone
              active={isInsertPreview(dropPreview, null, item.id)}
              testId={`created-insert-top-${item.id}`}
              onDragOver={(event) => onTopLevelInsertDragOver(event, item.id)}
              onDrop={(event) => onTopLevelInsertDrop(event, item.id)}
            />
            {item.kind === "group" ? (
              <PreviewGroup
                item={item}
                selectedGroupId={selectedGroupId}
                selectedWidgetId={selectedWidgetId}
                draggingWidgetId={draggingWidgetId}
                draggingGroupId={draggingGroupId}
                dropPreview={dropPreview}
                onSelectGroup={() => onSelectGroup(item.id)}
                onSelectWidget={onSelectWidget}
                onRenameWidget={onRenameWidget}
                onRemoveWidget={onRemoveWidget}
                onDragStart={onDragStart}
                onGroupDragStart={() => onGroupDragStart(item.id)}
                onDragEnd={onDragEnd}
                onWidgetDragOver={onWidgetDragOver}
                onWidgetDrop={onWidgetDrop}
                onGroupDragOver={onGroupDragOver}
                onGroupDrop={onGroupDrop}
                onGroupInsertDragOver={onGroupInsertDragOver}
                onGroupInsertDrop={onGroupInsertDrop}
              />
            ) : (
              <PreviewWidget
                widget={item.widget}
                isSelected={selectedWidgetId === item.id}
                dragging={draggingWidgetId === item.id}
                groupPreview={dropPreview?.kind === "group" && dropPreview.targetWidgetId === item.id}
                onSelect={() => onSelectWidget(item.id)}
                onRename={(title) => onRenameWidget(item.id, title)}
                onRemove={() => onRemoveWidget(item.id)}
                onDragStart={() => onDragStart(item.id)}
                onDragEnd={onDragEnd}
                onDragOver={(event) => onWidgetDragOver(event, item.id)}
                onDrop={(event) => onWidgetDrop(event, item.id)}
              />
            )}
          </Fragment>
        ))}
        <PreviewInsertDropZone
          active={isInsertPreview(dropPreview, null, null)}
          testId="created-insert-top-end"
          onDragOver={(event) => onTopLevelInsertDragOver(event, null)}
          onDrop={(event) => onTopLevelInsertDrop(event, null)}
        />
      </div>
    </section>
  );
}

function isInsertPreview(dropPreview: CreatedDropPreview, targetGroupId: string | null, beforeId: string | null): boolean {
  return dropPreview?.kind === "insert" && dropPreview.targetGroupId === targetGroupId && dropPreview.beforeId === beforeId;
}

function PreviewInsertDropZone({
  active,
  compact = false,
  testId,
  onDragOver,
  onDrop,
}: {
  active: boolean;
  compact?: boolean;
  testId: string;
  onDragOver: (event: DragEvent<HTMLDivElement>) => void;
  onDrop: (event: DragEvent<HTMLDivElement>) => void;
}) {
  return (
    <div
      className={`preview-insert-zone ${compact ? "preview-insert-zone--compact" : ""} ${
        active ? "preview-insert-zone--active" : ""
      }`}
      data-testid={testId}
      onDragOver={onDragOver}
      onDrop={onDrop}
    />
  );
}

function PreviewGroup({
  item,
  selectedGroupId,
  selectedWidgetId,
  draggingWidgetId,
  draggingGroupId,
  dropPreview,
  onSelectGroup,
  onSelectWidget,
  onRenameWidget,
  onRemoveWidget,
  onDragStart,
  onGroupDragStart,
  onDragEnd,
  onWidgetDragOver,
  onWidgetDrop,
  onGroupDragOver,
  onGroupDrop,
  onGroupInsertDragOver,
  onGroupInsertDrop,
}: {
  item: Extract<DashboardTopLevelItem, { kind: "group" }>;
  selectedGroupId: string | null;
  selectedWidgetId: string | null;
  draggingWidgetId: string | null;
  draggingGroupId: string | null;
  dropPreview: CreatedDropPreview;
  onSelectGroup: () => void;
  onSelectWidget: (id: string) => void;
  onRenameWidget: (id: string, title: string) => void;
  onRemoveWidget: (id: string) => void;
  onDragStart: (id: string) => void;
  onGroupDragStart: () => void;
  onDragEnd: () => void;
  onWidgetDragOver: (event: DragEvent<HTMLElement>, targetWidgetId: string) => void;
  onWidgetDrop: (event: DragEvent<HTMLElement>, targetWidgetId: string) => void;
  onGroupDragOver: (event: DragEvent<HTMLElement>, targetGroupId: string) => void;
  onGroupDrop: (event: DragEvent<HTMLElement>, targetGroupId: string) => void;
  onGroupInsertDragOver: (event: DragEvent<HTMLElement>, targetGroupId: string, beforeWidgetId: string | null) => void;
  onGroupInsertDrop: (event: DragEvent<HTMLElement>, targetGroupId: string, beforeWidgetId: string | null) => void;
}) {
  const isSelected = selectedGroupId === item.id;
  const isGroupTarget = dropPreview?.kind === "group-add" && dropPreview.targetGroupId === item.id && !dropPreview.targetWidgetId;
  return (
    <article
      className={`preview-group ${isSelected ? "preview-group--selected" : ""} ${isGroupTarget ? "preview-group--drop-target" : ""} ${
        draggingGroupId === item.id ? "preview-group--dragging" : ""
      }`}
      draggable
      onClick={onSelectGroup}
      onDragStart={(event) => {
        event.stopPropagation();
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", item.id);
        onGroupDragStart();
      }}
      onDragEnd={onDragEnd}
      onDragOver={(event) => onGroupDragOver(event, item.id)}
      onDrop={(event) => onGroupDrop(event, item.id)}
      data-testid={`created-group-${item.id}`}
    >
      <div className="preview-group__header">
        <div className="preview-group__title">
          <span aria-hidden="true">
            <LayoutGrid size={15} />
          </span>
          <div>
            <h5>{item.group.title}</h5>
            {item.group.description ? <p>{item.group.description}</p> : <p>{item.widgets.length} widgets grouped together</p>}
          </div>
        </div>
      </div>
      <div className="preview-group__children">
        {item.widgets.map((widget) => (
          <Fragment key={widget.id}>
            <PreviewInsertDropZone
              compact
              active={isInsertPreview(dropPreview, item.id, widget.id)}
              testId={`created-insert-group-${item.id}-${widget.id}`}
              onDragOver={(event) => onGroupInsertDragOver(event, item.id, widget.id)}
              onDrop={(event) => onGroupInsertDrop(event, item.id, widget.id)}
            />
            <PreviewWidget
              widget={widget}
              compact
              isSelected={selectedWidgetId === widget.id}
              dragging={draggingWidgetId === widget.id}
              groupPreview={
                dropPreview?.kind === "group-add" &&
                dropPreview.targetGroupId === item.id &&
                dropPreview.targetWidgetId === widget.id
              }
              onSelect={() => onSelectWidget(widget.id)}
              onRename={(title) => onRenameWidget(widget.id, title)}
              onRemove={() => onRemoveWidget(widget.id)}
              onDragStart={() => onDragStart(widget.id)}
              onDragEnd={onDragEnd}
              onDragOver={(event) => onWidgetDragOver(event, widget.id)}
              onDrop={(event) => onWidgetDrop(event, widget.id)}
            />
          </Fragment>
        ))}
        <PreviewInsertDropZone
          compact
          active={isInsertPreview(dropPreview, item.id, null)}
          testId={`created-insert-group-${item.id}-end`}
          onDragOver={(event) => onGroupInsertDragOver(event, item.id, null)}
          onDrop={(event) => onGroupInsertDrop(event, item.id, null)}
        />
      </div>
    </article>
  );
}

function PreviewWidget({
  widget,
  isSelected,
  dragging = false,
  compact = false,
  groupPreview = false,
  onSelect,
  onRename,
  onRemove,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
}: {
  widget: DashboardWidget;
  isSelected: boolean;
  dragging?: boolean;
  compact?: boolean;
  groupPreview?: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onRemove: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOver: (event: DragEvent<HTMLElement>) => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
}) {
  const [editingTitle, setEditingTitle] = useState(false);

  return (
    <article
      className={`preview-widget ${isSelected ? "preview-widget--selected" : ""} ${compact ? "preview-widget--compact" : ""} ${
        dragging ? "preview-widget--dragging" : ""
      } ${groupPreview ? "preview-widget--group-preview" : ""}`}
      draggable={!editingTitle}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
      onDragStart={(event) => {
        event.stopPropagation();
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", widget.id);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onDragOver={onDragOver}
      onDrop={onDrop}
      data-testid={`created-widget-${widget.id}`}
    >
      <div className="preview-widget__handle" aria-hidden="true">
        <GripVertical size={14} />
      </div>

      <div className="preview-widget__body">
        <div className="preview-widget__heading">
          {editingTitle ? (
            <input
              className="preview-widget__title-input"
              type="text"
              value={widget.title}
              aria-label="Edit widget name"
              autoFocus
              draggable={false}
              onFocus={(event) => event.currentTarget.select()}
              onMouseDown={(event) => event.stopPropagation()}
              onClick={(event) => event.stopPropagation()}
              onDoubleClick={(event) => event.stopPropagation()}
              onDragStart={(event) => {
                event.preventDefault();
                event.stopPropagation();
              }}
              onChange={(event) => onRename(event.target.value)}
              onBlur={() => setEditingTitle(false)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === "Escape") {
                  event.currentTarget.blur();
                }
              }}
            />
          ) : (
            <h5
              className="preview-widget__title"
              title="Double-click to edit widget name"
              onDoubleClick={(event) => {
                event.stopPropagation();
                setEditingTitle(true);
              }}
            >
              {widget.title}
            </h5>
          )}
          {widget.widgetType !== "note" && widget.description ? <p>{widget.description}</p> : null}
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
  if (widget.widgetType === "note") {
    return <p className="preview-note-card">{widget.description}</p>;
  }

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
    const seedMode = widget.seedMode ?? DEFAULT_SEED_MODE;
    const SeedModeIcon = SEED_MODE_ICONS[seedMode];
    return (
      <div className="preview-int">
        <input className="preview-input" readOnly type="text" value={String(widget.defaultValue ?? 0)} />
        {widget.widgetType === "seed_widget" ? (
          <span className="preview-int__seed-mode" title={`After each run: ${SEED_MODE_LABELS[seedMode]}`}>
            <SeedModeIcon size={14} aria-hidden="true" />
          </span>
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
        <span>Click here to upload an image</span>
      </div>
    );
  }

  if (widget.widgetType === "load_image_mask") {
    return (
      <div className="preview-image-input">
        <ImagePlus size={20} aria-hidden="true" />
        <span>Click here to upload an image</span>
      </div>
    );
  }

  if (widget.widgetType === "load_audio") {
    return (
      <div className="preview-image-input">
        <FileAudio size={20} aria-hidden="true" />
        <span>Click here to upload audio</span>
      </div>
    );
  }

  if (widget.widgetType === "load_video") {
    return (
      <div className="preview-image-input">
        <Video size={20} aria-hidden="true" />
        <span>Click here to upload video</span>
      </div>
    );
  }
  if (widget.widgetType === "load_file") {
    return (
      <div className="preview-image-input">
        <File size={24} aria-hidden="true" />
        <span>File upload</span>
      </div>
    );
  }
  if (widget.widgetType === "load_3d") {
    return (
      <div className="preview-image-input">
        <Box size={24} aria-hidden="true" />
        <span>3D model upload</span>
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

  if (widget.widgetType === "display_audio") {
    return (
      <div className="preview-image-output">
        <FileAudio size={22} aria-hidden="true" />
        <span>Generated audio will appear here</span>
      </div>
    );
  }

  if (widget.widgetType === "display_text") {
    return (
      <div className="preview-image-output">
        <Type size={22} aria-hidden="true" />
        <span>Generated text will appear here</span>
      </div>
    );
  }

  if (widget.widgetType === "display_video") {
    return (
      <div className="preview-image-output">
        <Video size={22} aria-hidden="true" />
        <span>Generated video will appear here</span>
      </div>
    );
  }
  if (widget.widgetType === "display_file") {
    return (
      <div className="preview-image-output">
        <File size={24} aria-hidden="true" />
        <span>File output</span>
      </div>
    );
  }
  if (widget.widgetType === "display_3d") {
    return (
      <div className="preview-image-output">
        <Box size={24} aria-hidden="true" />
        <span>Generated 3D model will appear here</span>
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
