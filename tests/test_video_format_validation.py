import unittest
from pathlib import Path

from videohelpersuite.format_validation import validate_format_directory


class VideoFormatValidationTests(unittest.TestCase):
    def test_all_format_json_files_pass_static_validation(self):
        results = validate_format_directory(Path("video_formats"), capabilities=None)
        failures = {
            result.name: {
                "errors": result.errors,
                "warnings": result.warnings,
            }
            for result in results
            if result.errors or result.warnings
        }
        self.assertEqual(failures, {})


if __name__ == "__main__":
    unittest.main()
