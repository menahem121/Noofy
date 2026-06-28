import { app } from "../../scripts/app.js";

const NODE_NAME = "OptionalAudioInput";

app.registerExtension({
  name: "OptionalMediaInput.AudioWidget",
  async beforeRegisterNodeDef(_nodeType, nodeData) {
    if (nodeData?.name !== NODE_NAME) {
      return;
    }

    const required = nodeData.input?.required;
    if (!required || required.audioUI) {
      return;
    }

    // ComfyUI's AUDIOUPLOAD widget expects AUDIO_UI to exist first.
    const { upload, ...inputsBeforeUpload } = required;
    nodeData.input.required = {
      ...inputsBeforeUpload,
      audioUI: ["AUDIO_UI", {}],
      ...(upload ? { upload } : {}),
    };
  },
});
