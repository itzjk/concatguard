import json
import os
import selectors
import signal
import subprocess
import time


FORMATS = "mov,matroska,avi,wav,flac,ogg,h264"
MAX_OUTPUT_BYTES = 1024 * 1024
PROBE_TIMEOUT = 10.0
TOTAL_TIMEOUT = 60.0
MOV_OPTION_NOTICES = {
    "Option enable_drefs skipped - not known to demuxer.",
    "Option use_absolute_path skipped - not known to demuxer.",
}
STREAM_FIELDS = (
    "index", "codec_type", "codec_name", "codec_tag_string", "profile", "level",
    "time_base", "width", "height", "pix_fmt", "sample_aspect_ratio",
    "sample_fmt", "sample_rate", "channels", "channel_layout", "bits_per_sample",
    "r_frame_rate", "avg_frame_rate", "start_time", "duration", "extradata_hash",
)


class ProbeError(Exception):
    def __init__(self, code, message, incomplete=False):
        super().__init__(message)
        self.code = code
        self.incomplete = incomplete


def command(path):
    return [
        "ffprobe", "-v", "warning", "-max_alloc", "67108864", "-threads", "1",
        "-protocol_whitelist", "file", "-format_whitelist", FORMATS,
        "-enable_drefs", "0", "-use_absolute_path", "0",
        "-probesize", "5000000", "-analyzeduration", "5000000",
        "-show_entries", "format=format_name,start_time,duration:stream=" + ",".join(STREAM_FIELDS),
        "-show_data_hash", "sha256", "-of", "json", "-i", str(path),
    ]


def bounded_run(argv, timeout, max_output=MAX_OUTPUT_BYTES):
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
    except OSError as error:
        raise ProbeError("probe-unavailable", f"Cannot start ffprobe: {error.strerror}.", True) from None
    output = {"stdout": bytearray(), "stderr": bytearray()}
    size = 0
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProbeError("probe-timeout", "ffprobe exceeded its time limit.", True)
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(chunk)
                    if size > max_output:
                        raise ProbeError("probe-output-limit", "ffprobe exceeded its output limit.", True)
                    output[key.data].extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError("probe-timeout", "ffprobe exceeded its time limit.", True)
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise ProbeError("probe-timeout", "ffprobe exceeded its time limit.", True) from None
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
        process.stderr.close()
    return process.returncode, bytes(output["stdout"]), bytes(output["stderr"])


def probe(path, timeout):
    code, stdout, stderr = bounded_run(command(path), timeout)
    detail = stderr.decode("utf-8", errors="replace").strip()
    if code:
        if "cannot allocate memory" in detail.lower() or "allocation failed" in detail.lower():
            raise ProbeError("probe-resource-limit", "ffprobe could not allocate resources: " + detail[:1500], True)
        raise ProbeError("probe-failed", "ffprobe rejected the source: " + (detail[:1500] or f"exit {code}"))
    try:
        data = json.loads(stdout)
        if not isinstance(data, dict) or not isinstance(data.get("format"), dict):
            raise ValueError()
        streams = data.get("streams")
        if not isinstance(streams, list) or not all(isinstance(item, dict) for item in streams):
            raise ValueError()
    except (ValueError, UnicodeDecodeError):
        raise ProbeError("probe-invalid-output", "ffprobe returned invalid metadata.", True) from None
    format_name = data["format"].get("format_name")
    if isinstance(format_name, str) and format_name and "mov" not in format_name.split(","):
        detail = "\n".join(line for line in detail.splitlines() if line not in MOV_OPTION_NOTICES)
    data["probe_warning"] = detail or None
    return data
