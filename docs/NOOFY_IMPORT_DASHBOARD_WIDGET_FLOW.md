# Noofy Import, Dashboard Widget Builder, and User Dashboard Flow

Status: product/engineering reference  
Audience: backend, frontend, and agentic coding contributors  
Scope: import flow, dashboard widget creation, dashboard layout creation, and normal workflow usage  
Out of scope: visual design details, styling rules, ComfyUI execution implementation details

---

## 1. Core Idea

Noofy turns a ComfyUI workflow into a simple app-like dashboard.

A ComfyUI workflow is powerful, but it is not a good interface for normal users. It contains nodes, internal values, technical names, engine-specific details, and many parameters that should not be edited casually.

Noofy keeps the ComfyUI graph as execution data, then builds a curated dashboard around it. That dashboard exposes only the values the creator/importer intentionally chooses to make editable.

The important distinction is:

- **ComfyUI graph**: the engine-specific workflow that runs behind the scenes.
- **Noofy dashboard**: the simplified interface that people use to run that workflow.
- **Dashboard widgets**: the user-facing controls or output areas shown in the dashboard.
- **Bindings**: the mapping between a dashboard widget and a specific workflow node value or output.

Noofy is not trying to replace ComfyUI as a workflow authoring tool. Noofy is the layer that makes an existing workflow usable as a clean product experience.

---

## 2. Product Philosophy

### 2.1 The Dashboard Is Curated, Not Generated Blindly

Noofy should not expose every editable workflow value automatically.

A workflow may contain many values, but only a small subset usually matters to the person running it. Examples include prompt, input image, transformation strength, image size, variation ID, LoRA/style choice, and output image.

The creator/importer is responsible for deciding which values belong in the dashboard. Noofy may suggest good defaults, but it should not assume every parameter deserves to become a widget.

The result should be intentional: a focused dashboard with the useful widgets, not a technical control panel.

### 2.2 The Creator/Importer and the Normal User Are Different People

Noofy has two separate experiences:

1. **Creator/importer setup flow**  
   Used to import a workflow and configure its dashboard.

2. **Normal user dashboard**  
   Used to run a workflow after it has already been configured.

The normal user should not see workflow nodes, raw node values, package internals, custom node details, runtime state, Python setup, or ComfyUI-specific concepts unless they explicitly open advanced/developer details.

### 2.3 Widgets Are the Public Interface of a Workflow

Dashboard widgets are the parts of the workflow that the creator chooses to expose.

A widget can represent:

- an input the user can change before running the workflow
- an output/result the user can view after running the workflow
- a higher-level user-friendly wrapper around a technical workflow value

A widget is not just a form field. It is the app-facing representation of a workflow capability.

Examples:

- A `denoise` node input may become a **Transformation** slider.
- A `seed` node input may become a **Variation ID** widget.
- A `LoadImage` input may become an **Input Image** widget.
- A `SaveImage` output may become a **Result Image** widget.

### 2.4 Raw ComfyUI JSON Is Supported, but It Is a Degraded Import

A `.noofy` package is the preferred format because it can carry metadata, model records, custom node records, export observations, and a dashboard schema.

A raw ComfyUI `.json` file may still be imported, but it usually lacks dashboard metadata and package-level context. Noofy should treat it as an incomplete starting point and require dashboard setup before it becomes a normal ready-to-run Noofy workflow.

---

## 3. Key Concepts

### 3.1 Workflow Package

A workflow package is Noofy’s app-owned representation of an imported workflow.

It should include or normalize:

- package identity
- source format
- trust/source metadata
- execution graph
- required models
- custom node records when available
- unresolved runtime inputs when applicable
- dashboard schema
- output mapping
- import/preparation status

The package is the object that the frontend and backend discuss. The frontend should not depend directly on raw ComfyUI files.

### 3.2 Dashboard Schema

The dashboard schema describes what the normal user sees and how those visible widgets connect to the workflow.

It should include:

- dashboard status
- widgets
- widget bindings
- widget display metadata
- widget layout data
- validation rules
- default values
- output mappings
- simple/advanced grouping when needed

The dashboard schema is part of the workflow package contract. It is the bridge between workflow internals and the normal user experience.

### 3.3 Widget

A widget is a user-facing dashboard element.

A widget should include:

- stable widget ID
- widget type
- title
- description/help text
- binding to a workflow input or output
- default value
- validation settings
- display settings
- layout settings
- simple/advanced grouping

Common widget types:

- Slider
- Int field
- String field
- Textarea
- On/Off button
- Load image
- Load image with draw mask option
- Display image with download button
- Seed / Variation ID widget
- LoRA loader
- Select / dropdown

### 3.4 Binding

A binding connects a widget to the workflow graph.

For an input widget, the binding tells the backend which workflow node value should be overwritten before execution.

Example concept:

```json
{
  "widget_id": "prompt",
  "binding": {
    "direction": "input",
    "node_id": "6",
    "input_name": "text"
  }
}
```

For an output widget, the binding tells Noofy which engine output or generated file should be displayed in the dashboard.

Example concept:

```json
{
  "widget_id": "result_image",
  "binding": {
    "direction": "output",
    "node_id": "9",
    "output_name": "images"
  }
}
```

### 3.5 Layout

The layout describes where widgets appear in the dashboard.

The layout should be saved as responsive grid data, not fragile raw pixel positions.

Example concept:

```json
{
  "widget_id": "prompt",
  "layout": {
    "x": 0,
    "y": 0,
    "w": 13,
    "h": 6,
    "min_w": 8,
    "min_h": 4
  }
}
```

---

## 4. End-to-End Flow

## 4.1 Import Workflow

The user imports either:

1. a `.noofy` package
2. a raw ComfyUI `.json` workflow

The backend should normalize the import into a Noofy workflow package record.

Import should inspect files and metadata as data. It should not run arbitrary custom node code in the trusted backend process.

After import, Noofy determines whether the workflow already has a valid dashboard schema.

Possible outcomes:

- dashboard already configured
- dashboard missing
- dashboard marked as not configured
- dashboard has widgets but no layout
- workflow has unresolved runtime inputs
- workflow cannot be prepared automatically

## 4.2 Determine Dashboard Setup State

Noofy should route the workflow according to its dashboard state.

Suggested states:

| State | Meaning | Typical next step |
| --- | --- | --- |
| `imported` | Package was imported and normalized | inspect setup requirements |
| `dashboard_not_configured` | No dashboard widgets exist yet | open Step 1: choose widgets |
| `dashboard_widgets_selected` | Widgets exist but layout is not complete | open Step 2: arrange dashboard |
| `dashboard_draft` | Dashboard exists but is not finalized | continue editing or save final |
| `dashboard_ready` | Dashboard schema is complete | open normal user dashboard |
| `needs_input_setup` | Workflow has unresolved runtime inputs | require widget/binding setup |
| `ready_to_prepare` | Dashboard is ready but runtime/model preparation may still be needed | prepare workflow/runtime |
| `ready_to_run` | Dashboard and runtime requirements are ready | normal user can run |
| `cannot_prepare_automatically` | Noofy cannot resolve required components safely | show graceful unsupported state |

These state names do not need to be the final API enum names, but the backend and frontend should preserve this distinction.

Dashboard readiness and runtime readiness are separate concerns.

A workflow can have a complete dashboard but still require model/runtime preparation before it can run. Likewise, a workflow can be technically preparable but still not have a user-facing dashboard.

---

## 5. Dashboard Builder Step 1 — Choose Widgets

Step 1 is the creator/importer flow for choosing which workflow values become dashboard widgets.

This step is about mapping workflow internals to a user-facing interface.

The creator/importer can:

- inspect workflow nodes and editable values
- select values that should be visible to the normal user
- create widgets from selected values
- choose widget types
- give widgets user-friendly titles and descriptions
- define defaults and validation rules
- group widgets as simple or advanced
- create output widgets for generated results

Noofy may suggest widget types based on the underlying value:

| Workflow value | Suggested widget |
| --- | --- |
| text prompt | Textarea or String field |
| width/height | Int field or select preset |
| denoise/strength | Slider |
| boolean | On/Off button |
| image input | Load image |
| image input plus mask | Load image with draw mask |
| seed | Variation ID / Seed widget |
| LoRA/model style | LoRA loader or dropdown |
| generated image output | Display image |

The creator/importer should be encouraged to expose only meaningful values. Technical values should stay hidden unless the creator explicitly decides otherwise.

Step 1 output:

- a list of selected widgets
- their bindings
- their widget types
- their user-facing metadata
- initial default values and validation rules

Step 1 does not need to finalize visual placement.

---

## 6. Dashboard Builder Step 2 — Arrange Dashboard

Step 2 starts after widgets have already been selected.

This step is only about arranging the chosen widgets into the final dashboard layout.

The creator/importer can:

- see widgets that still need to be placed
- drag widgets into the dashboard layout
- move widgets
- resize widgets if supported
- rotate widgets if supported and if it remains useful
- remove widgets from the layout
- save a draft layout
- save the final dashboard

Important behavior:

- The left-side source list should represent unplaced widgets.
- Once a widget is placed, it is no longer unplaced.
- If a widget is removed from the layout, it becomes unplaced again.
- The final dashboard should not be considered complete until every selected widget has a layout position.

Step 2 output:

- grid layout data for each widget
- widget size constraints
- optional responsive layout metadata
- final dashboard completion status

Step 2 should not change workflow bindings unless the creator explicitly goes back to Step 1.

---

## 7. Saving the Dashboard

There are two useful save modes.

### 7.1 Save as Draft

`Save as draft` stores progress without marking the dashboard as complete.

Use this when:

- not all widgets are placed
- the creator wants to continue later
- the dashboard is not ready for normal users yet

A draft dashboard should not be presented as a finished normal user interface.

### 7.2 Save Dashboard

`Save Dashboard` finalizes the dashboard schema.

It should require:

- widgets exist
- every selected widget is placed
- required bindings are valid
- required output mapping exists when the workflow produces visible output
- basic validation passes

After saving the final dashboard, Noofy can redirect the creator to the normal user workflow dashboard.

Saving the dashboard should update the local Noofy workflow package record. It should not silently mutate the original imported `.noofy` archive unless the user explicitly performs a re-export action.

---

## 8. Normal User Dashboard

After the dashboard is saved, the normal user opens the workflow through the dashboard, not through the builder.

The normal user can:

- fill in input widgets
- choose simple settings
- upload images
- choose variation behavior
- run the workflow
- cancel a running workflow
- see progress
- view outputs
- download or reuse generated results

The normal user should not need to know:

- which ComfyUI node is being modified
- what the raw input name is
- what custom node package provides a node
- where models are stored
- what Python dependencies are installed
- which runner is active
- how the workflow graph is structured

Before running, the backend applies widget values to the workflow graph through the stored bindings.

Execution concept:

1. User enters values in dashboard widgets.
2. Frontend sends widget values to the Noofy backend.
3. Backend validates the widget values against the dashboard schema.
4. Backend applies bindings to the execution graph.
5. Backend validates model/runtime availability.
6. Backend runs the workflow through the active engine adapter.
7. Backend streams progress/results back to the frontend.
8. Frontend displays outputs through output widgets and result views.

The frontend should call only the Noofy backend API. It should never call ComfyUI directly.

---

## 9. Backend Responsibilities

The backend should own the workflow package and dashboard contract.

Backend responsibilities include:

- importing `.noofy` archives and raw JSON workflows
- normalizing imports into workflow package records
- storing original imported files for diagnostics when appropriate
- exposing workflow setup state to the frontend
- exposing candidate workflow values for widget creation
- validating widget bindings
- saving dashboard schemas
- distinguishing draft dashboard state from completed dashboard state
- validating dashboard inputs before execution
- applying widget values to the graph before run
- resolving output mappings after run
- maintaining the boundary between frontend API and engine internals
- preventing the frontend from needing ComfyUI-specific routes or payloads

The backend should treat the ComfyUI graph as engine-specific execution data. UI code should work against Noofy package/dashboard concepts instead of raw ComfyUI objects whenever possible.

---

## 10. Frontend Responsibilities

The frontend should provide the creator/importer setup experience and the normal user dashboard experience.

Frontend responsibilities include:

- showing the correct page based on workflow setup state
- allowing creators to choose widgets from workflow values
- allowing creators to arrange widgets in a layout
- saving draft and final dashboard schemas through the backend
- rendering the normal user dashboard from the saved schema
- collecting widget values from the user
- sending workflow run requests to the Noofy backend
- showing progress, errors, and results using frontend-ready backend responses

The frontend should not:

- call ComfyUI directly
- assume raw node names are good user-facing labels
- decide runtime or runner policy
- modify workflow packages outside backend APIs
- expose technical workflow details to normal users by default

---

## 11. Suggested API Surface

Exact endpoint names may change, but the product needs these capabilities.

### Import and package inspection

- Import `.noofy` package
- Import raw ComfyUI `.json`
- Get workflow package summary
- Get workflow setup status
- Get dashboard schema
- Get import diagnostics/details when needed

### Widget setup

- Get candidate workflow values that can become widgets
- Create widget from workflow value
- Update widget metadata
- Remove widget
- Validate widget binding
- Save widget setup draft

### Layout setup

- Get selected widgets
- Save widget layout draft
- Save final dashboard layout
- Mark dashboard as complete when valid

### Normal dashboard run

- Get renderable dashboard schema
- Validate dashboard input values
- Run workflow with widget values
- Stream progress/events
- Cancel job
- Get result outputs

The API should keep user-facing payloads clean and put technical diagnostics behind explicit details fields.

---

## 12. Example Dashboard Schema Shape

This is a conceptual example, not a final strict schema.

```json
{
  "schema_version": "0.1.0",
  "status": "configured",
  "widgets": [
    {
      "id": "positive_prompt",
      "type": "textarea",
      "title": "Prompt",
      "description": "Describe what you want to generate.",
      "group": "main",
      "binding": {
        "direction": "input",
        "node_id": "6",
        "input_name": "text"
      },
      "default_value": "",
      "validation": {
        "required": true,
        "max_length": 2000
      },
      "display": {
        "advanced": false
      },
      "layout": {
        "x": 0,
        "y": 0,
        "w": 16,
        "h": 6,
        "min_w": 8,
        "min_h": 4
      }
    },
    {
      "id": "transformation_level",
      "type": "slider",
      "title": "Transformation",
      "description": "Lower stays closer to the input. Higher changes more.",
      "group": "main",
      "binding": {
        "direction": "input",
        "node_id": "21",
        "input_name": "denoise"
      },
      "default_value": 0.45,
      "validation": {
        "min": 0,
        "max": 1,
        "step": 0.01
      },
      "layout": {
        "x": 0,
        "y": 6,
        "w": 11,
        "h": 4
      }
    },
    {
      "id": "result_image",
      "type": "display_image",
      "title": "Resulting Image",
      "description": "The final output will appear here.",
      "group": "output",
      "binding": {
        "direction": "output",
        "node_id": "9",
        "output_name": "images"
      },
      "layout": {
        "x": 16,
        "y": 0,
        "w": 16,
        "h": 12,
        "min_w": 11,
        "min_h": 8
      }
    }
  ],
  "layout": {
    "grid_columns": 32,
    "row_height": 32,
    "responsive": true
  }
}
```

---

## 13. Important Invariants

These rules should remain true across backend and frontend work.

1. A normal user should not need to understand ComfyUI.
2. The dashboard is the public workflow interface.
3. Widgets are intentionally selected by the creator/importer.
4. Noofy should not expose all workflow values by default.
5. A widget must have a clear binding or purpose.
6. Dashboard readiness and runtime readiness are separate states.
7. Raw JSON imports require more setup than `.noofy` imports.
8. The frontend should not call ComfyUI directly.
9. The backend applies widget bindings before execution.
10. The original imported package should not be silently mutated during normal use.
11. Local machine-specific observations belong in local app data, not in the portable workflow package.
12. Technical details should remain available for diagnostics but should not define the normal user experience.

---

## 14. Edge Cases to Handle

### Imported workflow has no dashboard

Open Dashboard Builder Step 1.

### Imported workflow has widgets but no layout

Open Dashboard Builder Step 2.

### Imported workflow has valid dashboard but missing models

Open normal dashboard, but show required model/preparation state before run.

### Imported workflow has unresolved image input

Require a Load Image widget or another runtime input setup before it can be ready for normal use.

### Widget binding points to a missing node/input

Mark dashboard invalid and require creator repair.

### Output mapping is missing

The workflow may still run, but Noofy cannot show a meaningful result widget until an output is mapped.

### Raw JSON import has unknown custom nodes

The dashboard setup can still be drafted, but workflow preparation may later fail or require unsupported/community workflow handling.

### Creator saves draft with unplaced widgets

Keep the workflow out of normal ready state until the dashboard is completed.

---

## 15. What This Enables

This flow lets Noofy support several product paths:

- creators can package workflows for their audience
- users can import community workflows without learning ComfyUI
- raw ComfyUI workflows can be converted into Noofy dashboards
- workflows can become reusable app-like tools
- future marketplace workflows can ship with ready-made dashboards
- Noofy can keep execution powerful while keeping usage simple

The long-term goal is that a workflow creator can build something complex in ComfyUI, then use Noofy to expose it as a simple tool that anyone can run.
