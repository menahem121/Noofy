# Noofy Design System

## 1. Product Context

Noofy is a desktop interface for running local AI workflows on macOS and Windows. The main user is not a technical ComfyUI expert. The interface must make AI workflows feel simple, safe, private, and approachable.

The user should feel:

- "I understand what this screen is asking me."
- "I know what button to press next."
- "The app feels powerful, but not complicated."
- "This looks like a serious desktop tool, not a toy."

The UI should hide complexity by default and progressively reveal advanced controls only when needed.

## 2. Visual Direction

Build a modern dark desktop UI with warm charcoal surfaces, disciplined purple accents, rounded components, and calm professional spacing.

The app should feel like a creative/professional desktop tool, not a developer dashboard, node editor, mobile app, or marketing site.

Do:

- Use warm dark greys instead of pure black.
- Layer surfaces with subtle contrast.
- Keep controls large enough for beginners.
- Use rounded panels, cards, inputs, and dialogs.
- Use purple for primary actions, active states, and focus.
- Make status and progress visible without making the app feel alarming.
- Keep desktop structure clear: sidebar, top bar, workspace, optional inspector.

Avoid:

- Pure black backgrounds.
- Aggressive cyberpunk or neon styling.
- Excessive glow, transparency, blur, or animation.
- Dense power-user controls on the default screen.
- Overloaded panels and too many visible technical settings.
- UI that looks like ComfyUI, a filesystem, or a stretched mobile app.
- Purple everywhere.

## 3. Design Principles

### Beginner-First Clarity

Every screen should quickly answer:

1. What is this screen for?
2. What should I do next?
3. What is currently happening?

Beginners should not need to understand ComfyUI, nodes, Python, model folders, checkpoints, workflow graphs, or engine internals.

### One Main Action

Each major screen should have one obvious primary action.

Examples:

- "Run Workflow"
- "Choose Workflow"
- "Download Required Models"
- "Open Result"
- "Create New Workflow Package"

Secondary actions must be visually quieter than the main action.

### Progressive Disclosure

Use three levels of controls:

1. Simple mode: main inputs only.
2. Advanced settings: optional parameters such as seed, steps, strength, or style options.
3. Developer details: logs, engine status, raw workflow JSON, model paths, and adapter details.

The default experience must stay in simple mode.

### Friendly Confidence

Use plain language that explains what happened and what the user can do next.

Avoid:

- "Execution failed: missing dependency."
- "Invalid graph node reference."
- "Inference backend unavailable."

Prefer:

- "This workflow needs one missing model before it can run."
- "The ComfyUI engine could not start. Open details to see the technical error."
- "The app is preparing the local ComfyUI engine."

### Clean Workspace

The normal user sees workflow cards, inputs, preview, run controls, progress, output history, and clear requirement messages.

The full node graph and raw engine details belong only in creator or developer mode.

## 4. Color System

Use a warm dark grey base with purple as the main brand/accent color.

| Role | Color | Usage |
|---|---:|---|
| App background | `#242323` | Main app background |
| Deep background | `#1C1B1D` | Modals, sidebars, darker areas |
| Surface 1 | `#2E2D31` | Main panels |
| Surface 2 | `#37363B` | Cards, elevated blocks |
| Surface 3 | `#424047` | Hovered cards, selected rows |
| Border soft | `#4B4852` | Subtle borders |
| Border strong | `#686371` | Focused or active borders |
| Primary purple | `#8B5CF6` | Main action, selected navigation |
| Primary purple hover | `#9F7AEA` | Hover state |
| Primary purple pressed | `#6D42D8` | Pressed state |
| Purple soft surface | `#352A4A` | Soft selected backgrounds |
| Text primary | `#F4F0FA` | Main text |
| Text secondary | `#C9C2D4` | Supporting text |
| Text muted | `#8F879B` | Metadata, disabled hints |
| Success | `#4ADE80` | Completed, ready |
| Warning | `#FBBF24` | Needs attention |
| Error | `#F87171` | Failed, destructive |
| Info blue | `#60A5FA` | Neutral progress/info |

Rules:

- Do not use pure black (`#000000`, `#050505`, `#0A0A0A`) as the app background.
- Keep the darkest regular surface near `#1C1B1D`.
- Use purple for primary buttons, active navigation, active tabs, focus rings, selected workflows, and important progress states.
- Do not use purple for every accent, icon, border, and badge.
- Use gradients rarely, mostly for primary CTA treatment, selected workflow highlights, or special creator features.
- Avoid rainbow gradients and harsh neon gradients.

Suggested purple gradient:

```css
background: linear-gradient(135deg, #8B5CF6 0%, #6D42D8 100%);
```

## 5. Typography

Use a modern, readable sans-serif.

Preferred stack:

```css
font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
```

| Role | Size | Weight | Usage |
|---|---:|---:|---|
| Display | 40-48 px | 500 | Rare landing or empty-state headline |
| Page title | 28-32 px | 600 | Main screen title |
| Section title | 20-24 px | 600 | Panel title |
| Card title | 16-18 px | 600 | Workflow/card title |
| Body | 14-16 px | 400 | Normal UI text |
| Small | 12-13 px | 400/500 | Metadata, labels |
| Micro | 11 px | 500 | Badges, technical IDs |

Copy rules:

- Write direct, human labels.
- Prefer verbs users understand: generate, edit, remove, choose, download, save.
- Keep technical terms out of default UI.
- Show technical terms only in advanced or developer mode.
- Rewrite engine and ComfyUI labels before they reach beginner UI.
- Treat raw workflow labels as implementation details, not product copy.

Avoid beginner-hostile terms by default:

- Inference
- Checkpoint
- Latent
- Sampler
- Scheduler
- CLIP
- VAE
- Denoise
- Seed

Suggested translations:

| Technical label | Beginner label |
|---|---|
| Denoise strength | Transformation level |
| Low denoise | Stay close to original |
| High denoise | Change more |
| Seed | Variation ID |
| Checkpoint/model | AI model |
| Sampler | Generation method |
| Steps | Detail passes |
| CFG scale | Prompt strength |
| Latent preview | Live preview |

Be ruthless about terminology. If a label came directly from ComfyUI, assume it needs rewriting unless it is inside developer details.

## 6. Layout System

Use a classic desktop layout:

```text
+---------------------------------------------------------+
| Top bar: app name, current status, actions              |
+---------------+-----------------------------------------+
| Sidebar       | Main workspace                          |
| Navigation    | Workflow content / preview / settings   |
| Library       |                                         |
+---------------+-----------------------------------------+
```

Core regions:

- Sidebar: workflow library, recent workflows, categories, settings, engine status.
- Top bar: app name, current workspace, search, engine status, settings.
- Main workspace: workflow grid, input form, preview/result area, progress state.
- Optional right inspector: advanced settings, workflow details, model requirements, output metadata.

Spacing:

| Token | Value |
|---|---:|
| `space-1` | 4 px |
| `space-2` | 8 px |
| `space-3` | 12 px |
| `space-4` | 16 px |
| `space-5` | 24 px |
| `space-6` | 32 px |
| `space-7` | 48 px |
| `space-8` | 64 px |

Density targets:

- Sidebar width: 240-280 px.
- Right inspector: 320-380 px.
- Top bar height: 56-72 px.
- Main card gaps: 16-24 px.
- Main content padding: 24-32 px.
- Default density should be comfortable, not compact.

## 7. Shape, Borders, And Elevation

Rounded corners are part of the visual identity.

| Element | Radius |
|---|---:|
| Small inputs | 8-10 px |
| Buttons | 10-12 px |
| Cards | 16-20 px |
| Panels | 20-24 px |
| Dialogs | 24 px |
| Large feature panels | 28-32 px |

Rules:

- Use pill shapes only for badges, filters, and small status labels.
- Use soft 1 px borders and surface contrast more than heavy shadows.
- Use stronger borders and a soft purple focus ring for active/focused states.
- Use shadows only for dialogs, floating panels, active cards, and dropdown menus.

Default border:

```css
border: 1px solid rgba(255, 255, 255, 0.08);
```

Focused or selected:

```css
border: 1px solid rgba(139, 92, 246, 0.75);
box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.18);
```

Floating surface:

```css
box-shadow: 0 16px 40px rgba(0, 0, 0, 0.28);
```

## 8. Component Rules

### Buttons

Primary buttons:

- Use only for the main action.
- Use purple background and off-white text.
- Use 10-12 px radius.
- Use 44-48 px height and 18-24 px horizontal padding.

Secondary buttons:

- Use for normal actions.
- Use dark grey surface, soft border, and primary or secondary text.
- Lighten the surface on hover.

Ghost buttons:

- Use for low-priority actions.
- Keep background transparent until hover.
- Use muted text.

Destructive buttons:

- Use only for dangerous actions.
- Prefer red text or red border.
- Use filled red only for final confirmation.

### Workflow Cards

Workflow cards should be simple, scannable, and friendly.

Include:

- Workflow name.
- Short plain-English description.
- Category or complexity badge.
- Required model status.
- Small preview thumbnail if available.
- Primary action on hover, or always visible when important.

States:

| State | Visual |
|---|---|
| Default | Surface 2, soft border |
| Hover | Surface 3, slight lift |
| Selected | Purple border with soft purple glow |
| Missing models | Warning badge |
| Ready | Green "Ready" badge |
| Running | Progress indicator |
| Failed | Small error badge, not a scary panel |

Do not overload cards with technical details. Put technical details in the inspector panel.

### Sidebar

The sidebar should feel like a friendly library.

Recommended sections:

- Home
- Workflows
- My Projects
- History
- Downloads / Models
- Settings

Rules:

- Active item uses purple surface or purple left indicator.
- Icons use simple rounded line style.
- Labels stay readable at 14-15 px.
- Keep nesting shallow.
- Avoid deep folder trees.

### Top Bar

The top bar should make app state clear.

Recommended items:

- App logo/name.
- Current page/workspace.
- Search field.
- AI engine status.
- Settings icon.

Engine status examples:

- "Engine ready"
- "Starting engine..."
- "Downloading model"
- "Engine offline"

Use badges instead of large alerts unless the user must act immediately.

### Search

Search should be prominent enough to use comfortably.

Rules:

- Use 12-16 px radius.
- Use Surface 1 or Surface 2.
- Use a soft border.
- Keep placeholder text muted.

Placeholder examples:

- "Search workflows..."
- "Search projects or outputs..."
- "Find a workflow..."

### Tabs And Segmented Controls

Use tabs for major sections inside one screen.

Examples:

- Simple
- Advanced
- History

Rules:

- Active tab uses purple underline or purple soft fill.
- Keep labels short.
- Avoid too many tabs.

### Forms And Inputs

Forms must feel approachable, not technical.

Rules:

- Put clear labels above inputs.
- Add helper text when needed.
- Use friendly placeholders.
- Keep advanced parameters collapsed by default.
- Use sliders for values beginners can tune visually.
- Use numeric fields only where precision matters.
- Translate technical labels into human labels in simple mode.
- Explain transformed labels with helper text when the setting affects output quality or waiting time.

Example:

```text
Creativity
[ slider ]
Lower = closer to your image. Higher = more imaginative.
```

For image-to-image workflows:

```text
Faithfulness to original
[ slider ]
Lower keeps more of your image. Higher allows bigger changes.
```

## 9. Workflow Experience

The core workflow screen should follow a simple left-to-right mental model:

```text
+----------------------------------------------------------+
| Workflow name                              [Run Workflow] |
+-----------------------+----------------------------------+
| Inputs                | Preview / Output                 |
| - Prompt              |                                  |
| - Image upload        |                                  |
| - Style               |                                  |
| - Simple settings     |                                  |
|                       |                                  |
| [Advanced settings]   |                                  |
+-----------------------+----------------------------------+
```

Rules:

- Put user inputs on the left.
- Put preview and outputs in the main/right area.
- Keep output history nearby but secondary.
- Keep advanced settings in an accordion or inspector.
- Keep the run button obvious and easy to find.
- Show model requirements before the user starts a workflow.
- Always provide a clear cancel path while a workflow is running.

## 10. Progress, Empty, And Error States

### Progress

Running AI workflows can take time. The UI must always show what is happening.

Show:

- Current step in simple language.
- Percent progress when the engine can provide it.
- Estimated state or time remaining when possible.
- Cancel button.
- Live preview if the engine can provide intermediate output.
- Logs behind "Show details".

Good messages:

- "Preparing workflow..."
- "Loading model into memory..."
- "Loading model into VRAM (40%)..."
- "Generating image..."
- "Refining preview (12 of 30 passes)..."
- "Saving result..."

Avoid default messages like:

- "Queue prompt submitted"
- "KSampler executing"
- "Node 47 failed"

Rules:

- Do not show only a vague "Generation in progress" state for long-running work.
- Break waiting into concrete phases: preparing workflow, checking resources, loading model, generating, saving.
- Keep the last successful preview visible while a new run starts, then replace it when new preview data arrives.
- If live preview is unavailable, show a determinate progress bar when possible and a clear current phase when not.
- If progress stalls, update the copy to explain that the app is still waiting on the local engine.

Live preview:

- Show progressive image previews when the active engine exposes them.
- Label previews as in-progress when quality is still changing.
- Avoid flashing or rapid layout changes as preview frames update.
- Keep the final output visually distinct from intermediate previews.

### Empty States

Empty states should guide users toward the next action.

Example:

```text
No workflows yet

Start with a ready-made workflow or import one from ComfyUI.

[Browse Starter Workflows] [Import Workflow]
```

Use a friendly illustration or soft icon. Avoid huge blank screens.

### Errors

Errors should explain:

1. What happened.
2. Why it matters.
3. What the user can do.
4. Where to find optional technical details.

Example:

```text
This workflow needs a missing model

The app cannot run this workflow until the model is available locally.

[Download Model] [Choose Existing File] [Show details]
```

Rules:

- Do not use full-screen red error treatments for normal recoverable problems.
- Reserve red for the specific error label, destructive action, or failed status.
- Keep technical stack traces and raw logs collapsed by default.

### Storage And Resources

Model downloads can be several GB. The UI must show resource requirements before downloads or workflow runs that may fail because of missing disk space, memory, or offline state.

Use a resource requirement component for models and large workflow assets.

Show:

- Required disk space.
- Available disk space.
- Download size.
- Installed/missing status.
- Offline or unavailable source status.
- Action to download, choose an existing file, free space, or retry.

Example:

```text
Required storage

Model: Dream image model
Download size: 4 GB
Available disk space: 12 GB

[Download Model] [Choose Existing File]
```

Warning example:

```text
Not enough disk space

This model needs 4 GB. Your selected drive has 2 GB available.

[Free Up Space] [Choose Another Location]
```

Rules:

- Never let a model download fail silently because storage was not checked or explained.
- Use warning styling for low disk space before it becomes an error.
- Show offline state near the blocked action: "You are offline. Connect to the internet to download this model."
- Keep raw paths and technical storage details behind "Show details" unless the user is choosing a file location.

## 11. Workflow Library

The workflow library should look like a polished app library, not a filesystem.

Recommended categories:

- Image Generation
- Image Editing
- Background Removal
- Inpainting / Erase
- Upscaling
- Utilities
- Creator Workflows

Each card should show:

- Name.
- Short plain-English description.
- Complexity badge: Easy / Intermediate / Advanced.
- Model status: Ready / Needs download.
- Estimated time if known.

Example:

```text
Background Remover

Remove the background from an image automatically.

Easy - Ready
[Open]
```

## 12. Iconography

Use simple rounded line icons.

Rules:

- Use 1.5-2 px stroke.
- Use rounded caps.
- Keep detail minimal.
- Keep icon style consistent.
- Prefer icon buttons for common tool actions when the icon is familiar.
- Add tooltips for icon-only controls.

Sizes:

- Sidebar icons: 18-20 px.
- Button icons: 16-18 px.
- Card icons: 24-32 px.
- Empty-state icons: 48-80 px.

Avoid:

- Overly sharp icons.
- Randomly mixing filled icons with line icons.
- Decorative icon clutter.

## 13. Motion And Interaction

Animations should be subtle and useful.

Use motion for:

- Hover lift.
- Button press.
- Panel open/close.
- Progress changes.
- Toast notifications.

Timing:

- Fast UI feedback: 120-180 ms.
- Panel transitions: 180-240 ms.
- Large modal transitions: 220-280 ms.

Avoid:

- Bouncy animation.
- Slow transitions.
- Excessive glowing or pulsing.
- Motion that distracts from workflow status.

## 14. Accessibility And Readability

Rules:

- Minimum body text: 14 px.
- Important labels: 15-16 px.
- Avoid very low contrast text.
- Do not rely only on color to communicate status.
- Pair status color with labels, icons, or badges.
- Focus states must be visible.
- Buttons must have clear hover, focus, and pressed states.
- Text must fit inside controls at supported desktop sizes.

## 15. First Prototype Screens

Prototype these screens first:

1. Home / Workflow Library: starter workflows and a clear choose-workflow path.
2. Workflow Run Screen: simple inputs, preview/output, main run CTA, hidden advanced settings.
3. Missing Models And Storage Screen: required models, disk space, download/choose actions, friendly explanation.
4. Running Workflow State: granular progress, live preview when available, current step, cancel action, hidden logs.
5. Result / History Screen: generated outputs with open, save, copy, and regenerate actions.
6. Settings / Engine Status: backend/ComfyUI health with developer details collapsed.

## 16. Do And Avoid Summary

Do:

- Use warm dark grey backgrounds.
- Use purple as the primary action color.
- Keep screens spacious.
- Make the main action obvious.
- Use rounded cards and panels.
- Hide technical complexity.
- Write simple, human UI copy.
- Show progress clearly.
- Use granular waiting states and live previews when available.
- Show disk space and download requirements before large model actions.
- Use friendly warnings.
- Preserve the backend API boundary in frontend flows.

Avoid:

- Pure black app backgrounds.
- Node-editor visuals in normal user flows.
- ComfyUI technical terms in beginner UI.
- Purple overuse.
- Too many panels on first screen.
- Aggressive neon or cyberpunk effects.
- Tiny dense controls.
- Frightening error screens.
- Mobile layouts stretched to desktop.

## 17. Design Target

A calm, modern, rounded dark desktop app that makes local AI workflows feel simple and friendly for beginners, while still feeling powerful enough for serious creative work.
