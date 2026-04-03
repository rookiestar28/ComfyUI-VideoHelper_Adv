#!/usr/bin/env python3
import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from videohelpersuite.format_validation import (  # noqa: E402
    detect_ffmpeg_capabilities,
    materialize_format_file,
    validate_format_file,
)


VIDEO_CODEC_EXPECTATIONS = {
    "libx264": "h264",
    "h264_nvenc": "h264",
    "libx265": "hevc",
    "hevc_nvenc": "hevc",
    "prores_ks": "prores",
    "ffv1": "ffv1",
    "libsvtav1": "av1",
    "av1_nvenc": "av1",
    "libvpx-vp9": "vp9",
}
AUDIO_CODEC_EXPECTATIONS = {
    "aac": "aac",
    "libopus": "opus",
    "libvorbis": "vorbis",
    "pcm_s16le": "pcm_s16le",
    "flac": "flac",
}


def make_frames(width: int, height: int, frame_count: int, bit_depth: str):
    channels = 3
    if bit_depth == "16bit":
        dtype = np.uint16
        max_value = 65535
        input_pix_fmt = "rgb48"
    else:
        dtype = np.uint8
        max_value = 255
        input_pix_fmt = "rgb24"

    frames = []
    x = np.linspace(0, max_value, width, dtype=np.float64)
    y = np.linspace(0, max_value, height, dtype=np.float64)
    base_r = np.tile(x, (height, 1))
    base_g = np.tile(y[:, None], (1, width))
    for index in range(frame_count):
        base_b = np.full((height, width), max_value * index / max(frame_count - 1, 1), dtype=np.float64)
        frame = np.stack([base_r, base_g, base_b], axis=2).astype(dtype)
        frames.append(frame.tobytes())
    return input_pix_fmt, b"".join(frames)


def ffprobe_streams(ffprobe_path: str, path: Path):
    result = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_name,codec_type,pix_fmt,width,height,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout).get("streams", [])


def build_encode_args(ffmpeg_path: str, output_path: Path, materialized: dict, width: int, height: int, fps: int, input_pix_fmt: str):
    args = [
        ffmpeg_path,
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        input_pix_fmt,
        "-color_range",
        "pc",
        "-colorspace",
        "rgb",
        "-color_primaries",
        "bt709",
        "-color_trc",
        materialized.get("fake_trc", "iec61966-2-1"),
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
    ]
    args += materialized["main_pass"]
    args += [str(output_path)]
    return args


def build_audio_mux_args(ffmpeg_path: str, input_path: Path, output_path: Path, materialized: dict, duration: float):
    audio_pass = materialized.get("audio_pass")
    if not audio_pass:
        return None
    return [
        ffmpeg_path,
        "-v",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-f",
        "lavfi",
        "-t",
        str(duration),
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v",
        "copy",
        *audio_pass,
        "-shortest",
        str(output_path),
    ]


def expected_video_codec(materialized: dict, extension: str):
    codecs = []
    main_pass = materialized.get("main_pass", [])
    for index, item in enumerate(main_pass):
        if item == "-c:v" and index + 1 < len(main_pass):
            codecs.append(main_pass[index + 1])
    if codecs:
        return VIDEO_CODEC_EXPECTATIONS.get(codecs[-1], codecs[-1])
    return {"gif": "gif", "png": "png"}.get(extension)


def expected_audio_codec(materialized: dict):
    audio_pass = materialized.get("audio_pass", [])
    for index, item in enumerate(audio_pass):
        if item == "-c:a" and index + 1 < len(audio_pass):
            return AUDIO_CODEC_EXPECTATIONS.get(audio_pass[index + 1], audio_pass[index + 1])
    return None


def main():
    parser = argparse.ArgumentParser(description="Encode short samples for each video format and verify them with ffprobe.")
    parser.add_argument("--formats-dir", default="video_formats")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=256)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--frames", type=int, default=4)
    args = parser.parse_args()

    capabilities = detect_ffmpeg_capabilities(args.ffmpeg)
    temp_root = Path(tempfile.mkdtemp(prefix="vhs-format-probe-"))
    failures = 0
    skipped = 0
    passed = 0

    try:
        for format_path in sorted(Path(args.formats_dir).glob("*.json")):
            validation = validate_format_file(format_path, capabilities=capabilities)
            materialized = materialize_format_file(format_path)
            extension = materialized["extension"]
            format_name = format_path.name

            if validation.errors:
                print(f"[{format_name}] FAIL static validation: {'; '.join(validation.errors)}")
                failures += 1
                continue

            if "gifski_pass" in materialized and not shutil.which("gifski"):
                print(f"[{format_name}] SKIP gifski not installed")
                skipped += 1
                continue

            if validation.env_warnings:
                print(f"[{format_name}] SKIP {'; '.join(validation.env_warnings)}")
                skipped += 1
                continue

            bit_depth = materialized.get("input_color_depth", "8bit")
            input_pix_fmt, frame_bytes = make_frames(args.width, args.height, args.frames, bit_depth)
            output_stem = temp_root / format_path.stem
            output_path = output_stem.with_suffix(f".{extension.split('.')[-1]}")
            if "%" in extension:
                output_path = temp_root / extension

            encode_args = build_encode_args(
                args.ffmpeg,
                output_path,
                materialized,
                args.width,
                args.height,
                args.fps,
                input_pix_fmt,
            )
            try:
                subprocess.run(encode_args, input=frame_bytes, capture_output=True, check=True)
            except subprocess.CalledProcessError as exc:
                print(f"[{format_name}] FAIL encode: {exc.stderr.decode('utf-8', 'replace')}")
                failures += 1
                continue

            probe_target = output_path
            if "%" in extension:
                probe_target = temp_root / extension.replace("%03d", "001")
                if not probe_target.exists():
                    print(f"[{format_name}] FAIL image-sequence output missing first frame")
                    failures += 1
                    continue

            try:
                streams = ffprobe_streams(args.ffprobe, probe_target)
            except subprocess.CalledProcessError as exc:
                print(f"[{format_name}] FAIL ffprobe: {exc.stderr}")
                failures += 1
                continue

            video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
            if not video_streams:
                print(f"[{format_name}] FAIL no video stream detected")
                failures += 1
                continue

            expected_vcodec = expected_video_codec(materialized, extension.split(".")[-1])
            actual_vcodec = video_streams[0].get("codec_name")
            if expected_vcodec and actual_vcodec != expected_vcodec:
                print(f"[{format_name}] FAIL codec mismatch: expected {expected_vcodec}, got {actual_vcodec}")
                failures += 1
                continue

            mux_args = build_audio_mux_args(
                args.ffmpeg,
                output_path if "%" not in extension else probe_target,
                temp_root / f"{format_path.stem}-audio.{extension.split('.')[-1]}",
                materialized,
                duration=args.frames / args.fps + 0.25,
            )
            if mux_args is not None and "%" not in extension:
                try:
                    subprocess.run(mux_args, capture_output=True, check=True)
                    muxed_streams = ffprobe_streams(args.ffprobe, Path(mux_args[-1]))
                    audio_streams = [stream for stream in muxed_streams if stream.get("codec_type") == "audio"]
                    expected_acodec = expected_audio_codec(materialized)
                    if not audio_streams:
                        print(f"[{format_name}] FAIL no audio stream after mux")
                        failures += 1
                        continue
                    if expected_acodec and audio_streams[0].get("codec_name") != expected_acodec:
                        print(
                            f"[{format_name}] FAIL audio codec mismatch: expected {expected_acodec}, got {audio_streams[0].get('codec_name')}"
                        )
                        failures += 1
                        continue
                except subprocess.CalledProcessError as exc:
                    print(f"[{format_name}] FAIL audio mux: {exc.stderr.decode('utf-8', 'replace')}")
                    failures += 1
                    continue

            print(f"[{format_name}] PASS video={actual_vcodec}")
            passed += 1

        print()
        print(f"Summary: passed={passed} skipped={skipped} failed={failures} output_dir={temp_root}")
        raise SystemExit(1 if failures else 0)
    finally:
        # Keep artifacts for debugging only when there are failures.
        if failures == 0:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
