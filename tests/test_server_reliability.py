import asyncio
import os
import types
import unittest
from pathlib import Path

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
        clip_path = sample_dir / "clip.mp4"
        audio_path = sample_dir / "audio.wav"
        clip_path.write_bytes(b"x")
        audio_path.write_bytes(b"y")
        os.utime(clip_path, (1_700_000_000, 1_700_000_000))
        os.utime(audio_path, (1_700_000_100, 1_700_000_100))
        request = types.SimpleNamespace(rel_url=types.SimpleNamespace(query={
            "path": str(sample_dir) + "/",
            "extensions": "mp4,wav",
        }))
        response = self._run(self.server_mod.get_path(request))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, ["clip.mp4", "audio.wav"])

    def test_cleanup_preview_process_closes_transport_after_kill(self):
        class DummyTransport:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class DummyProcess:
            def __init__(self):
                self.returncode = None
                self.killed = False
                self.waited = False
                self._transport = DummyTransport()

            def kill(self):
                self.killed = True
                self.returncode = -9

            async def wait(self):
                self.waited = True
                return self.returncode

        proc = DummyProcess()
        self._run(self.server_mod.cleanup_preview_process(proc, kill=True, label="unit-test"))
        self.assertTrue(proc.killed)
        self.assertTrue(proc.waited)
        self.assertTrue(proc._transport.closed)

    def test_view_video_returns_500_when_prepass_fails(self):
        sample_file = self.paths["output_dir"] / "clip.mp4"
        sample_file.write_bytes(b"x")

        class DummyProcess:
            def __init__(self):
                self.returncode = 1
                self._transport = types.SimpleNamespace(close=lambda: None)

            async def communicate(self):
                return b"", b"ffmpeg failed"

            async def wait(self):
                return self.returncode

        async def fake_create_subprocess_exec(*_args, **_kwargs):
            return DummyProcess()

        self.server_mod.ffmpeg_path = "ffmpeg"
        self.server_mod.asyncio.create_subprocess_exec = fake_create_subprocess_exec
        request = types.SimpleNamespace(
            rel_url=types.SimpleNamespace(query={
                "filename": "clip.mp4",
                "type": "output",
            })
        )

        response = self._run(self.server_mod.view_video(request))
        self.assertEqual(response.status, 500)
        self.assertIn("Failed to inspect media for preview", response.text)

    def test_view_video_folder_preview_uses_unique_concat_file_per_request(self):
        sample_dir = self.paths["output_dir"] / "frames"
        sample_dir.mkdir(parents=True, exist_ok=True)
        (sample_dir / "0001.png").write_bytes(b"x")
        (sample_dir / "0002.png").write_bytes(b"y")
        captured = []

        async def fake_stream_preview_response(request, *, args, filename, content_type, debug_event, cleanup_paths=None):
            captured.append(
                {
                    "args": list(args),
                    "cleanup_paths": list(cleanup_paths or []),
                    "filename": filename,
                    "content_type": content_type,
                    "debug_event": debug_event,
                }
            )
            return types.SimpleNamespace(status=200)

        self.server_mod.ffmpeg_path = "ffmpeg"
        self.server_mod.run_preview_prepass = mock_async(return_value=(0, b"", b"Stream #0:0: Video: h264, 1 fps,"))
        self.server_mod.stream_preview_response = fake_stream_preview_response

        request = types.SimpleNamespace(
            rel_url=types.SimpleNamespace(query={
                "filename": "frames",
                "type": "output",
                "format": "folder",
            })
        )

        self._run(self.server_mod.view_video(request))
        self._run(self.server_mod.view_video(request))

        self.assertEqual(len(captured), 2)
        first_path = captured[0]["args"][captured[0]["args"].index("-i") + 1]
        second_path = captured[1]["args"][captured[1]["args"].index("-i") + 1]
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(captured[0]["cleanup_paths"], [first_path])
        self.assertEqual(captured[1]["cleanup_paths"], [second_path])
        self.assertTrue(os.path.exists(first_path))
        self.assertTrue(os.path.exists(second_path))

    def test_stream_preview_response_removes_cleanup_paths(self):
        temp_file = Path(self.workspace.path) / "temp-preview.txt"
        temp_file.write_text("preview", encoding="utf-8")

        class DummyStdout:
            async def read(self, _size):
                return b""

        class DummyProcess:
            def __init__(self):
                self.stdout = DummyStdout()
                self.returncode = 0
                self._transport = types.SimpleNamespace(close=lambda: None)

            async def wait(self):
                return self.returncode

        async def fake_create_subprocess_exec(*_args, **_kwargs):
            return DummyProcess()

        self.server_mod.asyncio.create_subprocess_exec = fake_create_subprocess_exec
        request = types.SimpleNamespace(rel_url=types.SimpleNamespace(query={}))

        response = self._run(
            self.server_mod.stream_preview_response(
                request,
                args=["ffmpeg", "-f", "null", "-"],
                filename="preview.webm",
                content_type="video/webm",
                debug_event="unit-test",
                cleanup_paths=[str(temp_file)],
            )
        )

        self.assertEqual(response.status, 200)
        self.assertFalse(temp_file.exists())


def mock_async(return_value):
    async def _mock(*_args, **_kwargs):
        return return_value

    return _mock


if __name__ == "__main__":
    unittest.main()
