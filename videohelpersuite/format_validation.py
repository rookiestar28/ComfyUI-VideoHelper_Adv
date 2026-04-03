import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


ALLOWED_TOP_LEVEL_KEYS = {
    "audio_pass",
    "bitrate",
    "dim_alignment",
    "environment",
    "extension",
    "extra_widgets",
    "fake_trc",
    "gifski_pass",
    "input_color_depth",
    "inputs_main_pass",
    "main_pass",
    "megabit",
    "pre_pass",
    "save_metadata",
    "trim_to_audio",
}

PASS_KEYS = {"main_pass", "audio_pass", "pre_pass", "inputs_main_pass", "gifski_pass"}
KNOWN_DEPTHS = {"8bit", "16bit"}
MUXER_BY_EXTENSION = {
    "gif": "gif",
    "mkv": "matroska",
    "mov": "mov",
    "mp4": "mp4",
    "png": "image2",
    "webm": "webm",
}
IMAGE_SEQUENCE_EXTENSIONS = {"png"}
PIX_FMT_ALIASES = {
    "rgba64": {"rgba64le", "rgba64be"},
}


@dataclass
class CapabilityReport:
    encoders: set[str]
    pix_fmts: set[str]
    muxers: set[str]


@dataclass
class FormatValidationResult:
    name: str
    errors: list[str]
    warnings: list[str]
    env_warnings: list[str]


def _parse_encoder_names(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        match = re.match(r"^\s*[A-Z\.]{6}\s+([^\s]+)\s", line)
        if match:
            names.add(match.group(1))
    return names


def _parse_pix_fmt_names(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        match = re.match(r"^\s*[IOHP\.]{5}\s+([^\s]+)\s", line)
        if match:
            names.add(match.group(1))
    return names


def _parse_muxer_names(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        match = re.match(r"^\s*[D E\.]{2}\s+([^\s]+)\s", line)
        if match:
            names.add(match.group(1))
    return names


def detect_ffmpeg_capabilities(ffmpeg_path: str | None = "ffmpeg") -> CapabilityReport | None:
    if not ffmpeg_path:
        return None
    try:
        encoders = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=True,
        )
        pix_fmts = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-pix_fmts"],
            capture_output=True,
            text=True,
            check=True,
        )
        muxers = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-muxers"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return CapabilityReport(
        encoders=_parse_encoder_names(encoders.stdout),
        pix_fmts=_parse_pix_fmt_names(pix_fmts.stdout),
        muxers=_parse_muxer_names(muxers.stdout),
    )


def _extract_literal_values(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        if len(value) > 1 and isinstance(value[1], list):
            return [str(item) for item in value[1] if isinstance(item, (str, int, float))]
        if len(value) == 1 and isinstance(value[0], list):
            return [str(item) for item in value[0] if isinstance(item, (str, int, float))]
    return []


def _scan_flag_values(node, flag: str) -> list[str]:
    values: list[str] = []
    if isinstance(node, list):
        for index, item in enumerate(node):
            if item == flag and index + 1 < len(node):
                values.extend(_extract_literal_values(node[index + 1]))
            values.extend(_scan_flag_values(item, flag))
    elif isinstance(node, dict):
        for value in node.values():
            values.extend(_scan_flag_values(value, flag))
    return values


def _is_widget_value(value) -> bool:
    return isinstance(value, list)


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_format_data(name: str, data: dict, capabilities: CapabilityReport | None = None) -> FormatValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    env_warnings: list[str] = []

    unknown_keys = sorted(set(data) - ALLOWED_TOP_LEVEL_KEYS)
    if unknown_keys:
        errors.append(f"unknown keys: {', '.join(unknown_keys)}")

    if "extension" not in data or not isinstance(data["extension"], str) or not data["extension"]:
        errors.append("missing or invalid 'extension'")

    if "main_pass" not in data or not isinstance(data["main_pass"], list):
        errors.append("missing or invalid 'main_pass'")

    for key in PASS_KEYS & set(data):
        if not isinstance(data[key], list):
            errors.append(f"'{key}' must be a list")

    depth = data.get("input_color_depth")
    if isinstance(depth, str) and depth not in KNOWN_DEPTHS:
        errors.append(f"unsupported input_color_depth literal: {depth}")

    extension = data.get("extension", "")
    extension_suffix = extension.split(".")[-1] if "." in extension else extension
    expected_muxer = MUXER_BY_EXTENSION.get(extension_suffix)
    if expected_muxer and capabilities is not None and expected_muxer not in capabilities.muxers:
        env_warnings.append(f"current ffmpeg does not advertise muxer '{expected_muxer}' for extension '{extension}'")

    video_codecs = sorted(set(_scan_flag_values(data, "-c:v")))
    audio_codecs = sorted(set(_scan_flag_values(data, "-c:a")))
    pixel_formats = sorted(set(_scan_flag_values(data, "-pix_fmt")))

    is_image_sequence = any(extension.endswith(f".{ext}") for ext in IMAGE_SEQUENCE_EXTENSIONS) and "%" in extension
    is_gif_output = extension_suffix == "gif"
    if not is_image_sequence and not is_gif_output and not video_codecs:
        warnings.append("no explicit '-c:v' found; output depends on ffmpeg/container defaults")

    if "audio_pass" in data and not audio_codecs:
        warnings.append("'audio_pass' is present but no explicit '-c:a' was found")

    if capabilities is not None:
        for codec in video_codecs:
            if codec not in capabilities.encoders:
                env_warnings.append(f"current ffmpeg does not advertise video encoder '{codec}'")
        for codec in audio_codecs:
            if codec not in capabilities.encoders:
                env_warnings.append(f"current ffmpeg does not advertise audio encoder '{codec}'")
        for pix_fmt in pixel_formats:
            accepted_names = {pix_fmt} | PIX_FMT_ALIASES.get(pix_fmt, set())
            if not accepted_names & capabilities.pix_fmts:
                env_warnings.append(f"current ffmpeg does not advertise pixel format '{pix_fmt}'")

    return FormatValidationResult(name=name, errors=errors, warnings=warnings, env_warnings=env_warnings)


def validate_format_file(path: str | Path, capabilities: CapabilityReport | None = None) -> FormatValidationResult:
    path = Path(path)
    return validate_format_data(path.name, _load_json(path), capabilities=capabilities)


def validate_format_directory(path: str | Path, capabilities: CapabilityReport | None = None) -> list[FormatValidationResult]:
    path = Path(path)
    return [
        validate_format_file(item, capabilities=capabilities)
        for item in sorted(path.glob("*.json"))
    ]
