#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from videohelpersuite.format_validation import detect_ffmpeg_capabilities, validate_format_directory


def main():
    parser = argparse.ArgumentParser(description="Validate video_formats/*.json structure and ffmpeg compatibility.")
    parser.add_argument("--formats-dir", default="video_formats", help="Directory containing video format JSON files.")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg binary to inspect for capabilities.")
    parser.add_argument("--strict-warnings", action="store_true", help="Treat general warnings as failures.")
    parser.add_argument("--strict-env", action="store_true", help="Treat environment capability warnings as failures.")
    args = parser.parse_args()

    capabilities = detect_ffmpeg_capabilities(args.ffmpeg)
    results = validate_format_directory(Path(args.formats_dir), capabilities=capabilities)

    error_count = 0
    warning_count = 0
    env_warning_count = 0

    for result in results:
        print(f"[{result.name}]")
        if not result.errors and not result.warnings and not result.env_warnings:
            print("  OK")
            continue
        for item in result.errors:
            print(f"  ERROR: {item}")
            error_count += 1
        for item in result.warnings:
            print(f"  WARNING: {item}")
            warning_count += 1
        for item in result.env_warnings:
            print(f"  ENV: {item}")
            env_warning_count += 1

    print()
    print(
        "Summary:",
        f"errors={error_count}",
        f"warnings={warning_count}",
        f"env_warnings={env_warning_count}",
        f"ffmpeg_capabilities={'yes' if capabilities else 'no'}",
    )

    if error_count:
        raise SystemExit(1)
    if args.strict_warnings and warning_count:
        raise SystemExit(2)
    if args.strict_env and env_warning_count:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
