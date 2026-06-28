import hashlib
import os

import folder_paths
from comfy_extras.nodes_audio import load as load_audio_file
from nodes import LoadImage as ComfyLoadImage


_MODE_AUTO = "auto"
_MODE_FORCE_ON = "force_on"
_MODE_FORCE_OFF = "force_off"
_MODE_LEGACY_TOGGLE = "legacy_toggle"
_MODES = [_MODE_AUTO, _MODE_FORCE_ON, _MODE_FORCE_OFF, _MODE_LEGACY_TOGGLE]
_DEFAULT_VIDEO_MAX_FRAMES = 32
_DEFAULT_VIDEO_FRAME_STRIDE = 1
_DEFAULT_VIDEO_MAX_SIDE = 768


def _input_files(content_types):
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    files = [
        filename
        for filename in os.listdir(input_dir)
        if os.path.isfile(os.path.join(input_dir, filename))
    ]
    return sorted(folder_paths.filter_files_content_types(files, content_types))


def _resolve_media_path(name):
    stripped_name = name.strip()
    virtual_dirs = {
        "/input/": folder_paths.get_input_directory,
        "input/": folder_paths.get_input_directory,
        "/output/": folder_paths.get_output_directory,
        "output/": folder_paths.get_output_directory,
        "/temp/": folder_paths.get_temp_directory,
        "temp/": folder_paths.get_temp_directory,
    }

    for prefix, directory_getter in virtual_dirs.items():
        if stripped_name.startswith(prefix):
            return os.path.join(directory_getter(), stripped_name[len(prefix):])

    return folder_paths.get_annotated_filepath(stripped_name)


def _effective_enabled(mode=_MODE_AUTO, enabled=False, image=""):
    normalized_mode = mode if mode in _MODES else _MODE_AUTO
    if normalized_mode == _MODE_FORCE_ON:
        return True
    if normalized_mode == _MODE_FORCE_OFF:
        return False
    if normalized_mode == _MODE_LEGACY_TOGGLE:
        return bool(enabled)
    return bool(str(image or "").strip())


def _audio_payload(audio_path):
    waveform, sample_rate = load_audio_file(audio_path)
    return {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate}


def _resize_video_frame(frame, max_side):
    import numpy as np
    from PIL import Image

    image = Image.fromarray(frame).convert("RGB")
    if max_side > 0:
        width, height = image.size
        longest_side = max(width, height)
        if longest_side > max_side:
            scale = max_side / longest_side
            new_size = (
                max(1, int(round(width * scale))),
                max(1, int(round(height * scale))),
            )
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            image = image.resize(new_size, resampling)

    return np.asarray(image)


def _video_payload(
    video_path,
    max_frames=_DEFAULT_VIDEO_MAX_FRAMES,
    frame_stride=_DEFAULT_VIDEO_FRAME_STRIDE,
    max_side=_DEFAULT_VIDEO_MAX_SIDE,
):
    try:
        import imageio.v3 as iio
        import numpy as np
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "Optional Video Input requires imageio[ffmpeg], Pillow, and torch "
            "to load video files."
        ) from exc

    max_frames = max(1, int(max_frames))
    frame_stride = max(1, int(frame_stride))
    max_side = max(0, int(max_side))

    frames = []
    for frame_index, frame in enumerate(iio.imiter(video_path)):
        if frame_index % frame_stride != 0:
            continue

        frames.append(_resize_video_frame(frame, max_side))
        if len(frames) >= max_frames:
            break

    if not frames:
        raise RuntimeError(f"Could not decode video frames from: {video_path}")

    stacked_frames = np.stack(frames, axis=0)
    images = torch.from_numpy(stacked_frames).to(dtype=torch.float32).div_(255.0)
    masks = torch.zeros(
        (images.shape[0], images.shape[1], images.shape[2]),
        dtype=torch.float32,
        device=images.device,
    )
    return images, masks


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as media_file:
        digest.update(media_file.read())
    return digest.hexdigest()


class OptionalImageInput:
    """
    Optional image input for ComfyUI workflows.

    When disabled, this intentionally returns None for IMAGE and MASK. Use it
    only with downstream nodes whose corresponding inputs are optional.
    """

    @classmethod
    def INPUT_TYPES(cls):
        files = _input_files(["image"])
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": False}),
                "image": (["", *files], {"image_upload": True}),
            },
            "optional": {
                "mode": (_MODES, {"default": _MODE_AUTO}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "load_image"
    CATEGORY = "optional media input"

    @classmethod
    def VALIDATE_INPUTS(cls, enabled=False, image="", mode=_MODE_AUTO):
        if not _effective_enabled(mode=mode, enabled=enabled, image=image):
            return True

        if not image:
            return "Image is enabled, but no image filename was provided."

        return ComfyLoadImage.VALIDATE_INPUTS(image)

    def load_image(self, enabled=False, image="", mode=_MODE_AUTO):
        if not _effective_enabled(mode=mode, enabled=enabled, image=image):
            return (None, None)

        return ComfyLoadImage().load_image(image)

    @classmethod
    def IS_CHANGED(cls, enabled=False, image="", mode=_MODE_AUTO):
        if not _effective_enabled(mode=mode, enabled=enabled, image=image):
            return f"{mode}:disabled"
        if not image:
            return f"{mode}:enabled-no-image"

        try:
            return f"{mode}:{ComfyLoadImage.IS_CHANGED(image)}"
        except Exception:
            return f"{mode}:{image}"


class OptionalAudioInput:
    """
    Optional audio input for ComfyUI workflows.

    When disabled, this intentionally returns None for AUDIO. Use it only with
    downstream nodes whose corresponding inputs are optional.
    """

    @classmethod
    def INPUT_TYPES(cls):
        files = _input_files(["audio", "video"])
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": False}),
                "audio": (["", *files], {"audio_upload": True}),
            },
            "optional": {
                "mode": (_MODES, {"default": _MODE_AUTO}),
            },
        }

    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("audio",)
    FUNCTION = "load_audio"
    CATEGORY = "optional media input"

    @classmethod
    def VALIDATE_INPUTS(cls, enabled=False, audio="", mode=_MODE_AUTO):
        if not _effective_enabled(mode=mode, enabled=enabled, image=audio):
            return True

        if not audio:
            return "Audio is enabled, but no audio filename was provided."

        try:
            audio_path = _resolve_media_path(audio)
        except Exception as exc:
            return f"Could not resolve audio path: {exc}"

        if not os.path.exists(audio_path):
            return f"Audio file does not exist: {audio}"

        return True

    def load_audio(self, enabled=False, audio="", mode=_MODE_AUTO):
        if not _effective_enabled(mode=mode, enabled=enabled, image=audio):
            return (None,)

        audio_path = _resolve_media_path(audio)
        return (_audio_payload(audio_path),)

    @classmethod
    def IS_CHANGED(cls, enabled=False, audio="", mode=_MODE_AUTO):
        if not _effective_enabled(mode=mode, enabled=enabled, image=audio):
            return f"{mode}:disabled"
        if not audio:
            return f"{mode}:enabled-no-audio"

        try:
            audio_path = _resolve_media_path(audio)
            return f"{mode}:{audio}:{_file_digest(audio_path)}"
        except Exception:
            return f"{mode}:{audio}"


class OptionalVideoInput:
    """
    Optional video input for ComfyUI workflows.

    When disabled, this intentionally returns None for IMAGE and MASK. Use it
    only with downstream nodes whose corresponding inputs are optional.
    """

    @classmethod
    def INPUT_TYPES(cls):
        files = _input_files(["video"])
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": False}),
                "video": (["", *files], {"video_upload": True}),
            },
            "optional": {
                "mode": (_MODES, {"default": _MODE_AUTO}),
                "max_frames": (
                    "INT",
                    {"default": _DEFAULT_VIDEO_MAX_FRAMES, "min": 1, "max": 4096},
                ),
                "frame_stride": (
                    "INT",
                    {"default": _DEFAULT_VIDEO_FRAME_STRIDE, "min": 1, "max": 4096},
                ),
                "max_side": (
                    "INT",
                    {"default": _DEFAULT_VIDEO_MAX_SIDE, "min": 0, "max": 8192},
                ),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("video", "mask")
    FUNCTION = "load_video"
    CATEGORY = "optional media input"

    @classmethod
    def VALIDATE_INPUTS(
        cls,
        enabled=False,
        video="",
        mode=_MODE_AUTO,
        max_frames=_DEFAULT_VIDEO_MAX_FRAMES,
        frame_stride=_DEFAULT_VIDEO_FRAME_STRIDE,
        max_side=_DEFAULT_VIDEO_MAX_SIDE,
    ):
        if int(max_frames) < 1:
            return "Video max_frames must be at least 1."
        if int(frame_stride) < 1:
            return "Video frame_stride must be at least 1."
        if int(max_side) < 0:
            return "Video max_side must be at least 0."

        if not _effective_enabled(mode=mode, enabled=enabled, image=video):
            return True

        if not video:
            return "Video is enabled, but no video filename was provided."

        try:
            video_path = _resolve_media_path(video)
        except Exception as exc:
            return f"Could not resolve video path: {exc}"

        if not os.path.exists(video_path):
            return f"Video file does not exist: {video}"

        return True

    def load_video(
        self,
        enabled=False,
        video="",
        mode=_MODE_AUTO,
        max_frames=_DEFAULT_VIDEO_MAX_FRAMES,
        frame_stride=_DEFAULT_VIDEO_FRAME_STRIDE,
        max_side=_DEFAULT_VIDEO_MAX_SIDE,
    ):
        if not _effective_enabled(mode=mode, enabled=enabled, image=video):
            return (None, None)

        video_path = _resolve_media_path(video)
        return _video_payload(
            video_path,
            max_frames=max_frames,
            frame_stride=frame_stride,
            max_side=max_side,
        )

    @classmethod
    def IS_CHANGED(
        cls,
        enabled=False,
        video="",
        mode=_MODE_AUTO,
        max_frames=_DEFAULT_VIDEO_MAX_FRAMES,
        frame_stride=_DEFAULT_VIDEO_FRAME_STRIDE,
        max_side=_DEFAULT_VIDEO_MAX_SIDE,
    ):
        if not _effective_enabled(mode=mode, enabled=enabled, image=video):
            return f"{mode}:disabled"
        if not video:
            return f"{mode}:enabled-no-video"

        try:
            video_path = _resolve_media_path(video)
            return (
                f"{mode}:{video}:{max_frames}:{frame_stride}:{max_side}:"
                f"{_file_digest(video_path)}"
            )
        except Exception:
            return f"{mode}:{video}"
