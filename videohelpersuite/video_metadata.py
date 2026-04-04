import json
import os
import tempfile


FFMETADATA_HEADER = ";FFMETADATA1\n"
ROUNDTRIP_COMMENT_KEY = "comment"


def normalize_video_metadata(video_metadata):
    normalized = {}
    for key, value in (video_metadata or {}).items():
        if value is None:
            continue
        normalized[str(key)] = value
    return normalized


def serialize_metadata_value(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def build_roundtrip_comment_payload(video_metadata):
    normalized = normalize_video_metadata(video_metadata)
    if not normalized:
        return None
    return serialize_metadata_value(normalized)


def iter_ffmpeg_metadata(video_metadata):
    normalized = normalize_video_metadata(video_metadata)
    if not normalized:
        return

    comment_payload = build_roundtrip_comment_payload(normalized)
    if comment_payload is not None:
        yield ROUNDTRIP_COMMENT_KEY, comment_payload

    for key, value in normalized.items():
        if key.lower() == ROUNDTRIP_COMMENT_KEY:
            continue
        yield key, serialize_metadata_value(value)


def escape_ffmetadata_value(value):
    value = str(value)
    value = value.replace("\\", "\\\\")
    value = value.replace(";", "\\;")
    value = value.replace("#", "\\#")
    value = value.replace("=", "\\=")
    value = value.replace("\n", "\\\n")
    return value


def create_ffmetadata_file(video_metadata, directory):
    metadata_items = list(iter_ffmpeg_metadata(video_metadata) or [])
    if not metadata_items:
        return None

    os.makedirs(directory, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=directory,
        prefix="vhs-metadata-",
        suffix=".txt",
        delete=False,
    ) as handle:
        handle.write(FFMETADATA_HEADER)
        for key, value in metadata_items:
            handle.write(f"{key}={escape_ffmetadata_value(value)}\n")
        return handle.name
