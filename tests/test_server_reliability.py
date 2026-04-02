import asyncio
import os
import types
import unittest

from tests._support import TempWorkspace, install_base_stubs, import_fresh, purge_modules


class ServerReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.workspace = TempWorkspace()
        purge_modules("videohelpersuite.server", "videohelpersuite.utils", "videohelpersuite.logger", "server", "folder_paths", "comfy", "torch")
        self.paths = install_base_stubs(self.workspace.path)
        self.server_mod = import_fresh("videohelpersuite.server")

    def tearDown(self):
        self.workspace.cleanup()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_resolve_path_requires_filename(self):
        response = self._run(self.server_mod.resolve_path({}))
        self.assertEqual(response.status, 400)
        self.assertIn("filename", response.text)

    def test_resolve_path_handles_url_download_errors(self):
        self.server_mod.try_download_video = lambda _url: (_ for _ in ()).throw(RuntimeError("boom"))
        response = self._run(self.server_mod.resolve_path({"filename": "https://example.com/video.mp4"}))
        self.assertEqual(response.status, 502)
        self.assertIn("Failed to download media from URL", response.text)

    def test_resolve_path_rejects_missing_local_file(self):
        response = self._run(
            self.server_mod.resolve_path({"filename": "missing.mp4", "type": "output"})
        )
        self.assertEqual(response.status, 404)
        self.assertIn("Media file not found", response.text)

    def test_get_path_respects_comma_separated_extensions(self):
        sample_dir = self.paths["output_dir"] / "browse"
        sample_dir.mkdir(parents=True, exist_ok=True)
        (sample_dir / "clip.mp4").write_bytes(b"x")
        (sample_dir / "audio.wav").write_bytes(b"y")
        request = types.SimpleNamespace(rel_url=types.SimpleNamespace(query={
            "path": str(sample_dir) + "/",
            "extensions": "mp4,wav",
        }))
        response = self._run(self.server_mod.get_path(request))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, ["clip.mp4", "audio.wav"])


if __name__ == "__main__":
    unittest.main()
