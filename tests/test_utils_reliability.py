import unittest

from tests._support import TempWorkspace, install_base_stubs, import_fresh, purge_modules


class UtilsReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.workspace = TempWorkspace()
        purge_modules("videohelpersuite.utils", "videohelpersuite.logger", "server", "folder_paths", "comfy", "torch")
        self.paths = install_base_stubs(self.workspace.path)
        self.utils = import_fresh("videohelpersuite.utils")

    def tearDown(self):
        self.workspace.cleanup()

    def test_validate_path_allows_supported_urls(self):
        self.assertTrue(self.utils.validate_path("https://example.com/video.mp4"))

    def test_validate_path_rejects_urls_when_disabled(self):
        self.assertEqual(
            self.utils.validate_path("https://example.com/video.mp4", allow_url=False),
            "URLs are unsupported for this path",
        )

    def test_try_download_video_reuses_existing_cached_file(self):
        cached_file = self.paths["temp_dir"] / "cached.mp4"
        cached_file.write_bytes(b"video")
        self.utils.ytdl_path = "yt-dlp"
        self.utils.download_history["https://example.com/video.mp4"] = str(cached_file)

        def unexpected_run(*_args, **_kwargs):
            raise AssertionError("yt-dlp should not run when cached file still exists")

        self.utils.subprocess.run = unexpected_run

        result = self.utils.try_download_video("https://example.com/video.mp4")

        self.assertEqual(result, str(cached_file))

    def test_try_download_video_invalidates_missing_cached_file_and_redownloads(self):
        stale_file = self.paths["temp_dir"] / "stale.mp4"
        fresh_file = self.paths["temp_dir"] / "fresh.mp4"
        fresh_file.write_bytes(b"video")
        url = "https://example.com/video.mp4"
        self.utils.ytdl_path = "yt-dlp"
        self.utils.download_history[url] = str(stale_file)
        calls = []

        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))
            return type("Result", (), {"stdout": f"{fresh_file}\n".encode("utf-8")})()

        self.utils.subprocess.run = fake_run

        result = self.utils.try_download_video(url)

        self.assertEqual(result, str(fresh_file))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.utils.download_history[url], str(fresh_file))

    def test_try_download_video_rejects_missing_fresh_download_path(self):
        missing_file = self.paths["temp_dir"] / "missing.mp4"
        url = "https://example.com/video.mp4"
        self.utils.ytdl_path = "yt-dlp"

        def fake_run(*args, **kwargs):
            return type("Result", (), {"stdout": f"{missing_file}\n".encode("utf-8")})()

        self.utils.subprocess.run = fake_run

        with self.assertRaisesRegex(Exception, "yt-dl did not produce a reusable downloaded file path"):
            self.utils.try_download_video(url)

        self.assertNotIn(url, self.utils.download_history)


if __name__ == "__main__":
    unittest.main()
