import os
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from tests._support import (
    TempWorkspace,
    import_fresh,
    install_base_stubs,
    install_nodes_dependency_stubs,
    purge_modules,
)


class _FakeWaveformArray:
    def __init__(self, array):
        self.array = array

    def transpose(self, *axes):
        return _FakeWaveformArray(self.array.transpose(*axes))

    def numpy(self):
        return self.array


class _FakeWaveform:
    def __init__(self, channels=2, samples=8):
        self.array = np.zeros((1, channels, samples), dtype=np.float32)

    def size(self, dim):
        return self.array.shape[dim]

    def squeeze(self, axis):
        return _FakeWaveformArray(np.squeeze(self.array, axis=axis))


class _FakeImageTensor:
    def __init__(self, array):
        self.array = array
        self.shape = array.shape

    def cpu(self):
        return self

    def numpy(self):
        return self.array


class NodesReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.workspace = TempWorkspace()
        purge_modules(
            "videohelpersuite.nodes",
            "videohelpersuite.utils",
            "videohelpersuite.logger",
            "videohelpersuite.image_latent_nodes",
            "videohelpersuite.load_video_nodes",
            "videohelpersuite.load_images_nodes",
            "videohelpersuite.batched_nodes",
            "server",
            "folder_paths",
            "comfy",
            "torch",
            "nodes",
        )
        self.paths = install_base_stubs(self.workspace.path)
        install_nodes_dependency_stubs()
        self.nodes_mod = import_fresh("videohelpersuite.nodes")

    def tearDown(self):
        self.workspace.cleanup()

    def test_build_audio_mux_args_injects_default_audio_codec(self):
        video_format = {"extension": "webm"}
        mux_args, channels = self.nodes_mod.build_audio_mux_args(
            video_format,
            "silent.webm",
            "with-audio.webm",
            {"waveform": _FakeWaveform(), "sample_rate": 44100},
            total_frames_output=8,
            frame_rate=8,
        )
        self.assertEqual(channels, 2)
        self.assertIn("-c:a", mux_args)
        self.assertIn("libopus", mux_args)
        self.assertEqual(video_format["audio_pass"], ["-c:a", "libopus"])

    def test_build_audio_mux_args_adds_ffmetadata_input_for_roundtrip_payload(self):
        video_format = {"extension": "mp4", "audio_pass": ["-c:a", "aac"]}
        mux_args, _channels = self.nodes_mod.build_audio_mux_args(
            video_format,
            "silent.mp4",
            "with-audio.mp4",
            {"waveform": _FakeWaveform(), "sample_rate": 48000},
            total_frames_output=12,
            frame_rate=12,
            metadata_path="metadata.txt",
        )
        self.assertIn("metadata.txt", mux_args)
        self.assertIn("-map_metadata", mux_args)
        self.assertEqual(mux_args[mux_args.index("-map_metadata") + 1], "2")
        self.assertIn("-movflags", mux_args)
        self.assertEqual(mux_args[mux_args.index("-movflags") + 1], "use_metadata_tags")

    def test_prune_outputs_all_option_deletes_all_selected_outputs(self):
        prune = self.nodes_mod.PruneOutputs()
        files = []
        for name in ["meta.png", "silent.mp4", "final-audio.mp4"]:
            path = self.paths["output_dir"] / name
            path.write_bytes(b"x")
            files.append(str(path))
        prune.prune_outputs((True, files), "All")
        for path in files:
            self.assertFalse(os.path.exists(path))

    def test_video_combine_returns_only_muxed_video_when_audio_present(self):
        combine = self.nodes_mod.VideoCombine()
        images = [_FakeImageTensor(np.zeros((2, 2, 3), dtype=np.float32))]

        def fake_ffmpeg_process(_args, _video_format, _metadata, file_path, _env):
            frame_data = yield
            total = 0
            while frame_data is not None:
                total += 1
                frame_data = yield
            Path(file_path).write_bytes(b"silent-video")
            yield total

        def fake_subprocess_run(args, input=None, env=None, capture_output=None, check=None):
            Path(args[-1]).write_bytes(b"muxed-video")
            return types.SimpleNamespace(stderr=b"")

        audio = {"waveform": _FakeWaveform(), "sample_rate": 44100}

        with mock.patch.object(self.nodes_mod, "ffmpeg_path", "/usr/bin/ffmpeg"), \
             mock.patch.object(
                 self.nodes_mod,
                 "apply_format_widgets",
                 lambda _ext, _kwargs: {"extension": "mp4", "main_pass": [], "audio_pass": ["-c:a", "aac"]},
             ), \
             mock.patch.object(self.nodes_mod, "ffmpeg_process", fake_ffmpeg_process), \
             mock.patch.object(self.nodes_mod.subprocess, "run", side_effect=fake_subprocess_run):
            result = combine.combine_video(
                images=images,
                frame_rate=8,
                loop_count=0,
                filename_prefix="Test",
                format="video/fake-format",
                save_output=True,
                audio=audio,
                extra_pnginfo={"workflow": {"extra": {"VHS_MetadataImage": True}}},
            )

        output_files = result["result"][0][1]
        self.assertEqual(len(output_files), 2)
        self.assertTrue(output_files[0].endswith(".png"))
        self.assertTrue(output_files[1].endswith("-audio.mp4"))
        self.assertTrue(os.path.exists(output_files[1]))
        self.assertFalse(os.path.exists(output_files[1].replace("-audio.mp4", ".mp4")))


if __name__ == "__main__":
    unittest.main()
