import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


MODULE_PATH = Path(__file__).parents[1] / "nodes.py"
PACKAGE_PATH = MODULE_PATH.parent

folder_paths_stub = types.ModuleType("folder_paths")
folder_paths_stub.get_input_directory = lambda: tempfile.gettempdir()
folder_paths_stub.get_output_directory = lambda: tempfile.gettempdir()
folder_paths_stub.get_temp_directory = lambda: tempfile.gettempdir()
folder_paths_stub.get_annotated_filepath = lambda name: name


def _filter_files_content_types(files, content_types):
    extensions = {
        "audio": {".flac", ".m4a", ".mp3", ".ogg", ".wav"},
        "image": {".bmp", ".jpeg", ".jpg", ".png", ".webp"},
        "video": {".avi", ".mkv", ".mov", ".mp4", ".webm"},
    }
    allowed_extensions = set()
    for content_type in content_types:
        allowed_extensions.update(extensions[content_type])

    return [
        filename
        for filename in files
        if Path(filename).suffix.lower() in allowed_extensions
    ]


folder_paths_stub.filter_files_content_types = _filter_files_content_types

nodes_stub = types.ModuleType("nodes")


class LoadImage:
    @staticmethod
    def VALIDATE_INPUTS(_image):
        return True

    @staticmethod
    def IS_CHANGED(image):
        return image

    def load_image(self, image):
        return (image, None)


nodes_stub.LoadImage = LoadImage


class _FakeWaveform:
    def unsqueeze(self, _axis):
        return self


audio_stub = types.ModuleType("comfy_extras.nodes_audio")
audio_stub.load = lambda _path: (_FakeWaveform(), 44100)

comfy_extras_stub = types.ModuleType("comfy_extras")
comfy_extras_stub.nodes_audio = audio_stub

sys.modules.setdefault("folder_paths", folder_paths_stub)
sys.modules.setdefault("nodes", nodes_stub)
sys.modules.setdefault("comfy_extras", comfy_extras_stub)
sys.modules.setdefault("comfy_extras.nodes_audio", audio_stub)
SPEC = importlib.util.spec_from_file_location("optional_media_input_nodes", MODULE_PATH)
optional_media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(optional_media)


class OptionalMediaInputTests(unittest.TestCase):
    def test_image_input_uses_native_upload_picker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            (input_dir / "example.png").touch()
            (input_dir / "ignore.txt").touch()

            with patch.object(
                optional_media.folder_paths,
                "get_input_directory",
                return_value=temp_dir,
            ):
                image_input = optional_media.OptionalImageInput.INPUT_TYPES()["required"]["image"]

        self.assertEqual(image_input[0], ["", "example.png"])
        self.assertEqual(image_input[1], {"image_upload": True})

    def test_audio_input_uses_native_upload_picker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            (input_dir / "clip.mp4").touch()
            (input_dir / "speech.wav").touch()
            (input_dir / "ignore.png").touch()

            with patch.object(
                optional_media.folder_paths,
                "get_input_directory",
                return_value=temp_dir,
            ):
                audio_input = optional_media.OptionalAudioInput.INPUT_TYPES()["required"]["audio"]

        self.assertEqual(audio_input[0], ["", "clip.mp4", "speech.wav"])
        self.assertEqual(audio_input[1], {"audio_upload": True})

    def test_video_input_uses_native_upload_picker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            (input_dir / "clip.mp4").touch()
            (input_dir / "movie.webm").touch()
            (input_dir / "ignore.png").touch()

            with patch.object(
                optional_media.folder_paths,
                "get_input_directory",
                return_value=temp_dir,
            ):
                input_types = optional_media.OptionalVideoInput.INPUT_TYPES()
                video_input = input_types["required"]["video"]
                video_options = input_types["optional"]

        self.assertEqual(video_input[0], ["", "clip.mp4", "movie.webm"])
        self.assertEqual(video_input[1], {"video_upload": True})
        self.assertEqual(video_options["max_frames"][1]["default"], 32)
        self.assertEqual(video_options["frame_stride"][1]["default"], 1)
        self.assertEqual(video_options["max_side"][1]["default"], 768)

    def test_empty_picker_value_disables_auto_mode(self):
        image_node = optional_media.OptionalImageInput()
        audio_node = optional_media.OptionalAudioInput()
        video_node = optional_media.OptionalVideoInput()

        self.assertEqual(
            image_node.load_image(enabled=False, image="", mode="auto"),
            (None, None),
        )
        self.assertEqual(
            audio_node.load_audio(enabled=False, audio="", mode="auto"),
            (None,),
        )
        self.assertEqual(
            video_node.load_video(enabled=False, video="", mode="auto"),
            (None, None),
        )

    def test_video_payload_stops_after_max_frames(self):
        yielded_frames = []

        def imiter(_path):
            for frame_index in range(100):
                yielded_frames.append(frame_index)
                yield np.full((20, 10, 3), frame_index, dtype=np.uint8)

        imageio_package = types.ModuleType("imageio")
        imageio_package.__path__ = []
        imageio_v3_stub = types.ModuleType("imageio.v3")
        imageio_v3_stub.imiter = imiter
        imageio_package.v3 = imageio_v3_stub

        class _FakeTensor:
            def __init__(self, shape):
                self.shape = shape
                self.device = "cpu"

            def to(self, dtype):
                self.dtype = dtype
                return self

            def div_(self, _value):
                return self

        torch_stub = types.ModuleType("torch")
        torch_stub.float32 = "float32"
        torch_stub.from_numpy = lambda array: _FakeTensor(array.shape)
        torch_stub.zeros = lambda shape, **_kwargs: {"shape": shape}

        with patch.dict(
            sys.modules,
            {
                "imageio": imageio_package,
                "imageio.v3": imageio_v3_stub,
                "torch": torch_stub,
            },
        ):
            images, masks = optional_media._video_payload(
                "example.mp4",
                max_frames=5,
                frame_stride=2,
                max_side=8,
            )

        self.assertEqual(yielded_frames, list(range(9)))
        self.assertEqual(images.shape, (5, 8, 4, 3))
        self.assertEqual(masks["shape"], (5, 8, 4))

    def test_audio_frontend_hook_registers_preview_before_upload(self):
        source = (PACKAGE_PATH / "web" / "optional_audio.js").read_text()

        self.assertIn('const NODE_NAME = "OptionalAudioInput"', source)
        self.assertIn('audioUI: ["AUDIO_UI", {}]', source)
        self.assertLess(source.index("audioUI:"), source.index("...(upload"))


if __name__ == "__main__":
    unittest.main()
