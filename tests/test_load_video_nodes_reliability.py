import unittest
from unittest import mock

from tests._support import (
    TempWorkspace,
    import_fresh,
    install_base_stubs,
    install_load_video_dependency_stubs,
    purge_modules,
)


class LoadVideoNodesReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.workspace = TempWorkspace()
        purge_modules(
            "videohelpersuite.load_video_nodes",
            "videohelpersuite.utils",
            "videohelpersuite.logger",
            "server",
            "folder_paths",
            "comfy",
            "torch",
            "nodes",
            "PIL",
            "cv2",
            "psutil",
        )
        install_base_stubs(self.workspace.path)
        install_load_video_dependency_stubs()
        self.load_video_nodes = import_fresh("videohelpersuite.load_video_nodes")

    def tearDown(self):
        self.workspace.cleanup()

    def test_load_video_ffmpeg_upload_handles_latent_dict_when_vae_is_connected(self):
        latent = {"samples": object()}
        audio = {"waveform": object(), "sample_rate": 44100}
        video_info = {"loaded_frame_count": 4}

        with mock.patch.object(
            self.load_video_nodes,
            "load_video",
            return_value=(latent, 4, audio, video_info),
        ):
            result = self.load_video_nodes.LoadVideoFFmpegUpload().load_video(
                video="clip.mp4",
                vae=object(),
            )

        self.assertIs(result[0], latent)
        self.assertIsNone(result[1])
        self.assertIs(result[2], audio)
        self.assertIs(result[3], video_info)


if __name__ == "__main__":
    unittest.main()
