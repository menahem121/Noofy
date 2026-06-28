# ComfyUI_optional_media_input

Minimal ComfyUI custom nodes for optional image, audio, and video inputs.

## Structure

```text
ComfyUI_optional_media_input/
  __init__.py
  nodes.py
  pyproject.toml
  web/
    optional_audio.js
```

## Image Node

`Optional Image Input`

Inputs:
- `mode`: `auto`, `force_on`, `force_off`, or `legacy_toggle`
- `enabled`: legacy boolean used only by `legacy_toggle`
- `image`: the same upload-enabled file picker used by ComfyUI's built-in
  `Load Image` node

Behavior:
- `mode = auto` is the default. Selecting or uploading an `image` enables
  loading; the empty picker option disables loading and returns `None`.
- `mode = force_on` always requires a valid `image`.
- `mode = force_off` always returns `None`.
- `mode = legacy_toggle` uses the `enabled` boolean.
- Image decoding, validation, and change detection reuse ComfyUI's built-in
  `Load Image` implementation.

Disabled mode should only be connected to downstream inputs that support an
unset image value. This is intended for `TextGenerate.image`, which is optional.

The picker lists supported images from ComfyUI's `input/` directory. Uploaded
files are stored there through ComfyUI's normal upload endpoint.

## Install

Copy this folder into:

```bash
~/ComfyUI/custom_nodes/ComfyUI_optional_media_input
```

Restart ComfyUI.

## Workflow usage

Connect:

```text
Optional Image Input.image -> TextGenerate.image
```

Default disabled state:

```json
"inputs": {
  "enabled": false,
  "image": "",
  "mode": "auto"
}
```

When an external workflow or API request provides an image, it only needs to
set the `image` filename. In default `auto` mode the node enables itself from
the non-empty value.

## Audio Node

`Optional Audio Input`

Inputs:
- `mode`: `auto`, `force_on`, `force_off`, or `legacy_toggle`
- `enabled`: legacy boolean used only by `legacy_toggle`
- `audio`: the same upload-enabled file picker used by ComfyUI's built-in
  `Load Audio` node

Behavior:
- `mode = auto` is the default. Selecting or uploading `audio` enables loading;
  the empty picker option disables loading and returns `None`.
- `mode = force_on` always requires a valid `audio`.
- `mode = force_off` always returns `None`.
- `mode = legacy_toggle` uses the `enabled` boolean.

The audio loader reuses ComfyUI's native audio decode helper and returns the
native `AUDIO` payload shape:

```python
{"waveform": waveform, "sample_rate": sample_rate}
```

Disabled mode should only be connected to downstream inputs that support an
unset audio value. This is intended for optional audio inputs such as
`TextGenerate.audio`.

The picker lists supported audio and video files from ComfyUI's `input/`
directory, matching the built-in `Load Audio` node. Uploaded files are stored
there through ComfyUI's normal upload endpoint.

The package also registers ComfyUI's native `AUDIO_UI` preview widget for the
custom audio node. This is required because the frontend only adds that widget
automatically for its built-in audio node class names.

## Video Node

`Optional Video Input`

Inputs:
- `mode`: `auto`, `force_on`, `force_off`, or `legacy_toggle`
- `enabled`: legacy boolean used only by `legacy_toggle`
- `video`: an upload-enabled file picker for supported video files from
  ComfyUI's `input/` directory
- `max_frames`: maximum decoded frames; default `32`
- `frame_stride`: use every Nth decoded frame; default `1`
- `max_side`: resize frames so the longest side is at most this size before
  tensor conversion; default `768`, set `0` to disable resizing

Behavior:
- `mode = auto` is the default. Selecting or uploading `video` enables loading;
  the empty picker option disables loading and returns `None`.
- `mode = force_on` always requires a valid `video`.
- `mode = force_off` always returns `None`.
- `mode = legacy_toggle` uses the `enabled` boolean.

The video loader decodes the selected file into ComfyUI `IMAGE` frames and a
blank `MASK`, so it can be connected to optional downstream video inputs that
accept image batches. Frames are streamed from the decoder and capped before
conversion to a float tensor to avoid loading large videos fully into memory.

Disabled mode should only be connected to downstream inputs that support an
unset video value. This is intended for optional video inputs such as
`TextGenerate.video`.

Video decoding uses `imageio[ffmpeg]` and resizing uses `pillow`; both are
declared in `pyproject.toml`.
