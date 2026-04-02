import unittest

from tests._support import TempWorkspace, install_base_stubs, import_fresh, purge_modules


class UtilsReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.workspace = TempWorkspace()
        purge_modules("videohelpersuite.utils", "videohelpersuite.logger", "server", "folder_paths", "comfy", "torch")
        install_base_stubs(self.workspace.path)
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


if __name__ == "__main__":
    unittest.main()
