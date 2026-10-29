import math
import os
import re
import stat
import time
from fractions import Fraction
from pathlib import Path

from .parser import MAX_PLAYLIST_BYTES, diagnostic, parse
from .probe import PROBE_TIMEOUT, TOTAL_TIMEOUT, ProbeError, probe


EXIT_CODES = {"no-static-blockers-found": 0, "blocked": 1, "incomplete": 2, "invalid-input": 3}
COMMON = ("codec_type", "codec_name", "time_base")
VIDEO = ("width", "height", "pix_fmt")
AUDIO = ("sample_rate", "channels", "sample_fmt")
OPTIONAL = ("sample_aspect_ratio", "channel_layout", "bits_per_sample")
WARN_FIELDS = ("profile", "level", "codec_tag_string", "extradata_hash", "r_frame_rate", "avg_frame_rate")


def local_path(value):
    return not (re.match(r"^[A-Za-z][A-Za-z0-9+.-]*[:,]", value) or value.startswith("//"))


def read_regular(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("Only local regular files are supported.")
        data = source.read(limit + 1)
    return data


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def known(value):
    return value not in (None, "", "unknown", "N/A", "0/0", "0:1")


def required_known(key, value):
    if not known(value):
        return False
    if key in {"width", "height", "sample_rate", "channels"}:
        return number(value) is not None and number(value) > 0
    if key == "time_base":
        try:
            return Fraction(str(value)) > 0
        except (ValueError, ZeroDivisionError):
            return False
    return True


def canonical(key, value):
    if key in {"time_base", "sample_aspect_ratio", "r_frame_rate", "avg_frame_rate"}:
        try:
            return Fraction(str(value).replace(":", "/"))
        except (ValueError, ZeroDivisionError):
            return value
    return str(value)


def add(entry, severity, code, message, directive=None, **evidence):
    line = entry["directive_lines"].get(directive, entry["line"])
    entry["diagnostics"].append(diagnostic(severity, code, line, message, **evidence))


def inspect_range(entry):
    observed = entry["observed"]
    requested = entry["requested"]
    start = observed["start_time_seconds"]
    duration = observed["duration_seconds"]
    inpoint = requested["inpoint"] if requested["inpoint"] is not None else start
    outpoint = requested["outpoint"]
    entry["range_check"] = "unknown"
    if outpoint is not None and outpoint <= inpoint:
        add(entry, "blocker", "incoherent-range", "outpoint must be greater than the effective inpoint.",
            "outpoint", inpoint=inpoint, outpoint=outpoint)
    if duration is None:
        add(entry, "incomplete", "duration-unknown", "Source duration is unknown; temporal availability cannot be checked.")
    else:
        end = start + duration
        if requested["inpoint"] is not None and (inpoint < start - 0.000001 or inpoint >= end):
            add(entry, "blocker", "inpoint-outside-media", "inpoint is outside the observed media interval.",
                "inpoint", requested=inpoint, observed_start=start, observed_end=end)
        if outpoint is not None and outpoint > end + 0.000001:
            add(entry, "blocker", "outpoint-outside-media", "outpoint exceeds the observed media end.",
                "outpoint", requested=outpoint, observed_end=end)
        for position, stream in enumerate(observed["streams"]):
            stream_start = number(stream.get("start_time"))
            stream_duration = number(stream.get("duration"))
            if stream_start is None or stream_duration is None or stream_duration <= 0:
                continue
            stream_end = stream_start + stream_duration
            if outpoint is not None and outpoint <= end + 0.000001 and outpoint > stream_end + 0.000001:
                add(entry, "blocker", "outpoint-outside-stream", "outpoint exceeds an observed stream end.",
                    "outpoint", stream=position, requested=outpoint, observed_end=stream_end)
            if requested["inpoint"] is not None and inpoint < end and inpoint >= stream_end:
                add(entry, "blocker", "inpoint-outside-stream", "inpoint is beyond an observed stream end.",
                    "inpoint", stream=position, requested=inpoint, observed_end=stream_end)
            if abs(stream_end - end) > 0.05 or abs(stream_start - start) > 0.05:
                add(entry, "warning", "stream-interval-differs",
                    "Stream timing differs from the container interval; gaps or overlaps may occur.",
                    stream=position, stream_start=stream_start, stream_end=stream_end,
                    container_start=start, container_end=end)
        entry["range_check"] = ("outside-observed-bounds" if any(
            item["severity"] == "blocker" for item in entry["diagnostics"]
        ) else "within-observed-bounds")
        effective_end = end if outpoint is None else outpoint
        declared = requested["duration"]
        if declared is not None and abs(declared - (effective_end - inpoint)) > 0.000001:
            add(entry, "warning", "duration-timeline-override",
                "duration overrides timeline spacing; it does not trim the source.", "duration",
                declared_duration=declared, observed_range_length=effective_end - inpoint)
    if requested["inpoint"] is not None or outpoint is not None:
        add(entry, "warning", "cut-not-frame-verified",
            "Cut bounds use metadata; keyframes, decoding timestamps and visible frames were not checked.")


def inspect_streams(entry):
    streams = entry["observed"]["streams"]
    if not streams:
        add(entry, "blocker", "no-streams", "No media streams were observed.")
    for position, stream in enumerate(streams):
        kind = stream.get("codec_type")
        required = COMMON + (VIDEO if kind == "video" else AUDIO if kind == "audio" else ())
        missing = [key for key in required if not required_known(key, stream.get(key))]
        if missing:
            add(entry, "incomplete", "stream-metadata-unknown",
                f"Stream {position} lacks required compatibility metadata.", stream=position, fields=missing)
        if kind not in {"video", "audio"}:
            add(entry, "incomplete", "stream-type-outside-scope",
                f"Stream {position} is outside audio/video compatibility checks.", stream=position, codec_type=kind)


def compare(entry, reference):
    streams = entry["observed"]["streams"]
    base = reference["observed"]["streams"]
    entry["compared_with_line"] = reference["line"]
    if len(streams) != len(base):
        add(entry, "blocker", "stream-count-mismatch", "Stream count differs from the reference source.",
            reference_line=reference["line"], expected=len(base), observed=len(streams))
    for position, (expected, actual) in enumerate(zip(base, streams)):
        kind = expected.get("codec_type")
        fields = COMMON + (VIDEO if kind == "video" else AUDIO if kind == "audio" else ()) + OPTIONAL
        for key in fields + WARN_FIELDS:
            left, right = expected.get(key), actual.get(key)
            if not known(left) or not known(right):
                continue
            if canonical(key, left) != canonical(key, right):
                severity = "warning" if key in WARN_FIELDS else "blocker"
                add(entry, severity, "stream-property-differs",
                    f"Stream {position} {key} differs from line {reference['line']}.",
                    stream=position, field=key, expected=left, observed=right,
                    reference_line=reference["line"])


def finish(report):
    issues = report["diagnostics"] + [item for entry in report["entries"] for item in entry["diagnostics"]]
    severities = {item["severity"] for item in issues}
    report["status"] = next((status for severity, status in [
        ("invalid", "invalid-input"), ("blocker", "blocked"), ("incomplete", "incomplete")
    ] if severity in severities), "no-static-blockers-found")
    report["exit_code"] = EXIT_CODES[report["status"]]
    report["summary"] = {severity: sum(item["severity"] == severity for item in issues)
                         for severity in ("invalid", "blocker", "incomplete", "warning")}
    return report


def validate(playlist):
    report = {"schema_version": 1, "playlist": str(playlist), "entries": [], "diagnostics": []}
    try:
        if not local_path(str(playlist)):
            raise ValueError("Playlist must be a local regular file, not a URL or protocol path.")
        path = Path(playlist).absolute()
        raw = read_regular(path, MAX_PLAYLIST_BYTES)
        if len(raw) > MAX_PLAYLIST_BYTES:
            raise ValueError(f"Playlist exceeds {MAX_PLAYLIST_BYTES} bytes.")
        if b"\x00" in raw:
            raise ValueError("Playlist contains a NUL byte.")
        entries, issues = parse(raw.decode("utf-8"))
    except (OSError, ValueError, RuntimeError) as error:
        report["diagnostics"].append(diagnostic("invalid", "playlist-unreadable", None, str(error)))
        return finish(report)
    report["diagnostics"] = issues
    deadline = time.monotonic() + TOTAL_TIMEOUT
    reference = None
    for source in entries:
        entry = {"line": source.line, "path": source.path, "resolved_path": None,
                 "requested": source.requested, "directive_lines": source.directive_lines,
                 "observed": None, "range_check": "unknown", "compared_with_line": None,
                 "diagnostics": source.diagnostics}
        report["entries"].append(entry)
        if not local_path(source.path):
            add(entry, "invalid", "nonlocal-source", "URLs and protocol paths are outside the supported subset.")
            continue
        if any(item["severity"] == "invalid" for item in entry["diagnostics"]):
            continue
        try:
            media = (path.parent / source.path).resolve()
            entry["resolved_path"] = str(media)
            prefix = read_regular(media, 4096)
        except (OSError, ValueError, RuntimeError) as error:
            detail = error.strerror if isinstance(error, OSError) else str(error)
            add(entry, "blocker", "source-unreadable", f"Cannot read source: {detail}")
            continue
        if prefix.lstrip().startswith((b"ffconcat", b"#EXTM3U", b"<MPD", b"<?xml", b"[playlist]")):
            add(entry, "invalid", "nested-playlist", "Nested playlists are outside the supported subset.")
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            add(entry, "incomplete", "total-time-limit", "Total probing time limit reached; source was not probed.")
            continue
        try:
            data = probe(media, min(PROBE_TIMEOUT, remaining))
        except ProbeError as error:
            add(entry, "incomplete" if error.incomplete else "blocker", error.code, str(error))
            continue
        fmt = data["format"]
        duration = number(fmt.get("duration"))
        warning = data.get("probe_warning")
        if duration is not None and (duration <= 0 or fmt.get("format_name") == "h264"
                                     or (warning and "estimating duration" in warning.lower())):
            duration = None
        entry["observed"] = {
            "format_name": fmt.get("format_name"), "duration_seconds": duration,
            "duration_source": "container-metadata" if duration is not None else "unknown",
            "start_time_seconds": number(fmt.get("start_time")) or 0.0,
            "streams": data["streams"],
        }
        if warning:
            add(entry, "warning", "probe-warning", warning[:1500])
        inspect_range(entry)
        inspect_streams(entry)
        if reference is None and data["streams"]:
            reference = entry
        elif reference is not None:
            compare(entry, reference)
    return finish(report)
