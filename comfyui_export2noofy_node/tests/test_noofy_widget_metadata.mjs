import assert from "node:assert/strict";
import test from "node:test";

import { collectComfyUIWidgetMetadata } from "../web/noofy_widget_metadata.mjs";

test("collects static, getter-backed, async, object, and select dropdown options", async () => {
  const dynamicOptions = {};
  Object.defineProperty(dynamicOptions, "values", {
    enumerable: true,
    get: () => ["dynamic-a", "dynamic-b"],
  });
  const metadata = await collectComfyUIWidgetMetadata([
    {
      id: 12,
      widgets: [
        {
          name: "style",
          label: "Rendering style",
          tooltip: "Choose a style.",
          options: { values: ["cinematic", "illustration", "cinematic"] },
        },
        { name: "dynamic", options: dynamicOptions },
        { name: "remote", options: { values: async () => ["fast", "quality"] } },
        { name: "objects", options: { values: [{ value: "one" }, { id: "two" }] } },
        { name: "direct", options: ["direct-a", "direct-b"] },
        { name: "set_values", options: { values: new Set(["set-a", "set-b"]) } },
        {
          name: "select_fallback",
          element: { options: [{ value: "first" }, { value: "second" }] },
        },
      ],
    },
  ]);

  assert.deepEqual(metadata, {
    schema_version: "0.1.0",
    nodes: {
      "12": {
        inputs: {
          style: {
            options: ["cinematic", "illustration"],
            display_name: "Rendering style",
            tooltip: "Choose a style.",
          },
          dynamic: { options: ["dynamic-a", "dynamic-b"] },
          remote: { options: ["fast", "quality"] },
          objects: { options: ["one", "two"] },
          direct: { options: ["direct-a", "direct-b"] },
          set_values: { options: ["set-a", "set-b"] },
          select_fallback: { options: ["first", "second"] },
        },
      },
    },
  });
});

test("ignores widgets without usable dropdown choices", async () => {
  const partiallyBrokenOptions = { options: ["fallback-a", "fallback-b"] };
  Object.defineProperty(partiallyBrokenOptions, "values", {
    get: () => {
      throw new Error("disconnected");
    },
  });
  const metadata = await collectComfyUIWidgetMetadata([
    {
      id: 1,
      widgets: [
        { name: "text", options: {} },
        { name: "broken", options: { values: () => { throw new Error("no values"); } } },
        { name: "partial", options: partiallyBrokenOptions },
        { options: { values: ["missing-name"] } },
      ],
    },
  ]);

  assert.deepEqual(metadata, {
    schema_version: "0.1.0",
    nodes: {
      "1": {
        inputs: {
          partial: { options: ["fallback-a", "fallback-b"] },
        },
      },
    },
  });
});

test("ignores frontend-only widgets that are not execution graph inputs", async () => {
  const metadata = await collectComfyUIWidgetMetadata(
    [
      {
        id: 4,
        widgets: [
          { name: "style", options: { values: ["cinematic", "illustration"] } },
          { name: "frontend_setting", options: { values: ["compact", "expanded"] } },
        ],
      },
    ],
    {
      "4": {
        class_type: "CustomSelector",
        inputs: { style: "cinematic" },
      },
    },
  );

  assert.deepEqual(Object.keys(metadata.nodes["4"].inputs), ["style"]);
});

test("captures declared upload and helper semantics without relying on node names", async () => {
  const metadata = await collectComfyUIWidgetMetadata(
    [
      {
        id: 8,
        constructor: {
          nodeData: {
            output: ["AUDIO"],
            input: {
              required: {
                portrait_source: [
                  ["", "private.png"],
                  { image_upload: true },
                ],
                sound_source: [
                  ["", "private.wav"],
                  { audio_upload: true },
                ],
                audio_preview: ["AUDIO_UI", {}],
              },
            },
          },
        },
        widgets: [
          { name: "portrait_source", type: "combo", options: { values: ["", "private.png"] } },
          { name: "sound_source", type: "combo", options: { values: ["", "private.wav"] } },
          { name: "audio_preview", type: "AUDIO_UI" },
        ],
      },
    ],
    {
      "8": {
        class_type: "ArbitraryFutureNode",
        inputs: {
          portrait_source: "",
          sound_source: "",
          audio_preview: "",
        },
      },
    },
  );

  assert.deepEqual(metadata.nodes["8"].inputs, {
    portrait_source: {
      input_type: "COMBO",
      input_group: "required",
      image_upload: true,
      options: ["", "private.png"],
    },
    sound_source: {
      input_type: "COMBO",
      input_group: "required",
      audio_upload: true,
      options: ["", "private.wav"],
    },
    audio_preview: { input_type: "AUDIO_UI", input_group: "required" },
  });
  assert.deepEqual(metadata.nodes["8"].outputs, ["AUDIO"]);
});
