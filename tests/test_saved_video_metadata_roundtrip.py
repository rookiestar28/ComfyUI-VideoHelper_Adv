import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from videohelpersuite.video_metadata import (
    ROUNDTRIP_COMMENT_KEY,
    build_roundtrip_comment_payload,
    create_ffmetadata_file,
)


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
PARSER_PATH = Path("web/js/videoMetadataParser.js")


def find_node_18():
    candidates = []
    default_node = shutil.which("node")
    if default_node:
        candidates.append(default_node)
    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if nvm_root.exists():
        candidates.extend(str(path) for path in sorted(nvm_root.glob("*/bin/node")))

    for candidate in candidates:
        try:
            version = subprocess.run(
                [candidate, "-v"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        if version.startswith("v"):
            try:
                major = int(version[1:].split(".", 1)[0])
            except ValueError:
                continue
            if major >= 18:
                return candidate
    return None


NODE = find_node_18()


def ffprobe_format_tags(path: Path):
    result = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format_tags",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout).get("format", {}).get("tags", {})


def run_node_parser(path: Path):
    script = """
import fs from 'node:fs';

(async () => {
    const [parserPath, mediaPath] = process.argv.slice(2);
    const source = fs.readFileSync(parserPath, 'utf8');
    const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(source, 'utf8').toString('base64');
    const mod = await import(moduleUrl);
    const bytes = fs.readFileSync(mediaPath);
    const arrayBuffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    const parsed = mod.parseVideoMetadataBuffer(arrayBuffer);
    process.stdout.write(JSON.stringify(parsed ?? null));
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
"""
    result = subprocess.run(
        [NODE, "--input-type=module", "-", str(PARSER_PATH), str(path)],
        input=script,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe are required for saved-video metadata roundtrip tests")
class SavedVideoMetadataRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="vhs-metadata-roundtrip-")
        self.root = Path(self.tempdir.name)
        self.payload = {
            "workflow": {"nodes": [{"id": 1, "type": "UnitTest"}], "links": []},
            "prompt": {"text": "metadata roundtrip"},
            "extra_pnginfo": {"foo": "bar"},
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_build_roundtrip_comment_payload_keeps_workflow_object(self):
        payload = json.loads(build_roundtrip_comment_payload(self.payload))
        self.assertEqual(payload["workflow"]["nodes"][0]["type"], "UnitTest")
        self.assertEqual(payload["prompt"]["text"], "metadata roundtrip")

    def test_create_ffmetadata_file_writes_comment_and_named_tags(self):
        metadata_path = Path(create_ffmetadata_file(self.payload, self.root))
        text = metadata_path.read_text(encoding="utf-8")
        self.assertIn(f"{ROUNDTRIP_COMMENT_KEY}=", text)
        self.assertIn("workflow=", text)
        self.assertIn("prompt=", text)

    @unittest.skipUnless(NODE and PARSER_PATH.exists(), "node and the standalone parser module are required")
    def test_parser_reads_roundtrip_comment_from_supported_video_containers(self):
        samples = {
            "sample.mp4": ["-c:v", "libx264", "-movflags", "use_metadata_tags"],
            "sample.webm": ["-c:v", "libvpx-vp9"],
            "sample.mkv": ["-c:v", "ffv1"],
        }
        for filename, codec_args in samples.items():
            metadata_path = create_ffmetadata_file(self.payload, self.root)
            output_path = self.root / filename
            subprocess.run(
                [
                    FFMPEG,
                    "-v",
                    "error",
                    "-i",
                    metadata_path,
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=16x16:d=1",
                    "-map_metadata",
                    "0",
                    *codec_args,
                    "-t",
                    "1",
                    "-y",
                    str(output_path),
                ],
                check=True,
            )
            tags = ffprobe_format_tags(output_path)
            comment = tags.get("comment") or tags.get("COMMENT")
            self.assertIsNotNone(comment, filename)
            parsed = run_node_parser(output_path)
            self.assertEqual(parsed["workflow"]["nodes"][0]["type"], "UnitTest", filename)

    @unittest.skipUnless(NODE and PARSER_PATH.exists(), "node and the standalone parser module are required")
    def test_muxed_mp4_keeps_roundtrip_comment_payload(self):
        silent_metadata = create_ffmetadata_file(self.payload, self.root)
        silent_video = self.root / "silent.mp4"
        subprocess.run(
            [
                FFMPEG,
                "-v",
                "error",
                "-i",
                silent_metadata,
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=16x16:d=1",
                "-map_metadata",
                "0",
                "-c:v",
                "libx264",
                "-movflags",
                "use_metadata_tags",
                "-t",
                "1",
                "-y",
                str(silent_video),
            ],
            check=True,
        )

        mux_metadata = create_ffmetadata_file(self.payload, self.root)
        muxed_video = self.root / "muxed.mp4"
        subprocess.run(
            [
                FFMPEG,
                "-v",
                "error",
                "-i",
                str(silent_video),
                "-f",
                "lavfi",
                "-t",
                "1",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-i",
                mux_metadata,
                "-map_metadata",
                "2",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-movflags",
                "use_metadata_tags",
                "-shortest",
                "-y",
                str(muxed_video),
            ],
            check=True,
        )

        tags = ffprobe_format_tags(muxed_video)
        self.assertIn("comment", tags)
        parsed = run_node_parser(muxed_video)
        self.assertEqual(parsed["workflow"]["nodes"][0]["type"], "UnitTest")
