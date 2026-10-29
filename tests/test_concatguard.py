import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from concatguard.cli import main
from concatguard.parser import MAX_ENTRIES, parse, timestamp, token
from concatguard.probe import FORMATS, ProbeError, bounded_run, command, probe
from concatguard.validator import validate


def run_ffmpeg(*arguments):
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, arguments)],
                          check=True, capture_output=True, timeout=20)


def quote(path):
    return "'" + str(path).replace("'", "'\\''") + "'"


class ParserTests(unittest.TestCase):
    def test_ffmpeg_escaping(self):
        for source, expected in [
            ("'a b.mp4'", "a b.mp4"), (r"'director'\''s cut.mp4'", "director's cut.mp4"),
            (r"a\ b.mp4", "a b.mp4"), (r"'a\b.mp4'", "a\\b.mp4"),
            ('\'"double quotes".mp4\'', '"double quotes".mp4'),
            ("' tail '.mp4", " tail .mp4"),
        ]:
            with self.subTest(source=source):
                self.assertEqual(token(source), expected)

    def test_bad_arguments_and_headers(self):
        for text in ["file 'open", "file a b", "file x\\", "file ''", "inpoint 1", "stream 0",
                     "ffconcat version 2.0\nfile a", "\nffconcat version 1.0\nfile a",
                     "file a\nduration 0", "file a\ninpoint nan", "file a\noutpoint inf",
                     "file a\ninpoint -1", "file a\ninpoint 1\ninpoint 2", "file a\noption key value"]:
            with self.subTest(text=text):
                entries, issues = parse(text)
                self.assertTrue(issues or any(entry.diagnostics for entry in entries))

    def test_times(self):
        for source, expected in [("1.25", 1.25), ("1250ms", 1.25), ("1250000us", 1.25),
                                 ("01:02:03.5", 3723.5), ("02:03", 123), ("0.0000019", 0.000001),
                                 ("1.9999999999999999999999999999999", 1.999999)]:
            self.assertEqual(timestamp(source), expected)
        for value in ["NaN", "Infinity", "1e3", "1e309", "-1", "01:99:00", "00:60", str(2 ** 63), "١", "9" * 100000]:
            with self.assertRaises(ValueError):
                timestamp(value)

    def test_comments_line_numbers_and_limit(self):
        entries, issues = parse("ffconcat version 1.0\n# comment\n\n  file 'clip.mp4'\noutpoint 3\n")
        self.assertFalse(issues)
        self.assertEqual(entries[0].line, 4)
        self.assertEqual(entries[0].directive_lines["outpoint"], 5)
        _, issues = parse("file a\n" * (MAX_ENTRIES + 1))
        self.assertEqual(issues[0]["code"], "entry-limit")


class ProcessTests(unittest.TestCase):
    def test_security_arguments(self):
        argv = command(Path("/tmp/clip.mp4"))
        self.assertEqual(argv[argv.index("-protocol_whitelist") + 1], "file")
        self.assertEqual(argv[argv.index("-format_whitelist") + 1], FORMATS)
        self.assertNotIn("concat", FORMATS.split(","))
        self.assertNotIn("hls", FORMATS.split(","))
        self.assertEqual(argv[argv.index("-enable_drefs") + 1], "0")

    def test_timeout_and_output_caps(self):
        cases = [("import time; time.sleep(30)", 0.1, "probe-timeout"),
                 ("import sys; sys.stdout.buffer.write(b'x'*200000)", 2, "probe-output-limit"),
                 ("import sys; sys.stderr.buffer.write(b'x'*200000)", 2, "probe-output-limit")]
        for script, timeout, code in cases:
            start = time.monotonic()
            with self.assertRaises(ProbeError) as caught:
                bounded_run([sys.executable, "-c", script], timeout, max_output=1024)
            self.assertEqual(caught.exception.code, code)
            self.assertLess(time.monotonic() - start, 3)

    def test_missing_executable(self):
        with self.assertRaises(ProbeError) as caught:
            bounded_run(["/nonexistent/concatguard-ffprobe"], 1)
        self.assertTrue(caught.exception.incomplete)

    def test_malformed_probe_output_and_allocation_failure(self):
        for payload in [b"invalid", b"[]", b'{}', b'{"format":{},"streams":[null]}']:
            with patch("concatguard.probe.bounded_run", return_value=(0, payload, b"")):
                with self.assertRaises(ProbeError) as caught:
                    probe(Path("unused.mp4"), 1)
            self.assertEqual(caught.exception.code, "probe-invalid-output")
            self.assertTrue(caught.exception.incomplete)
        with patch("concatguard.probe.bounded_run", return_value=(1, b"", b"Cannot allocate memory")):
            with self.assertRaises(ProbeError) as caught:
                probe(Path("unused.mp4"), 1)
        self.assertTrue(caught.exception.incomplete)

    def test_cli_json_invalid_arguments(self):
        for args in [["--json"], ["--json", "--bad"]]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(args)
            self.assertEqual(code, 3)
            self.assertEqual(json.loads(output.getvalue())["status"], "invalid-input")


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("Integration tests require ffmpeg and ffprobe on PATH.")
        cls.temporary = tempfile.TemporaryDirectory(prefix="concatguard-tests-")
        cls.root = Path(cls.temporary.name)
        cls.base = cls.root / "base.mp4"
        cls.variant = cls.root / "variant.mp4"
        for path, size, rate, channels in [(cls.base, "160x90", 48000, 1),
                                           (cls.variant, "320x180", 44100, 2)]:
            run_ffmpeg("-f", "lavfi", "-i", f"color=c=blue:s={size}:r=25:d=2",
                       "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration=2",
                       "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-ac", channels, "-shortest", path)
        cls.raw = cls.root / "raw.h264"
        run_ffmpeg("-f", "lavfi", "-i", "color=s=160x90:r=25:d=1", "-c:v", "libx264", "-threads", 1, cls.raw)
        cls.observed = validate(cls.playlist("file base.mp4\n"))["entries"][0]["observed"]

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def playlist(cls, body, name="test.ffconcat"):
        path = cls.root / name
        path.write_text("ffconcat version 1.0\n" + body, encoding="utf-8")
        return path

    def issues(self, report):
        return report["diagnostics"] + [issue for entry in report["entries"] for issue in entry["diagnostics"]]

    def metadata(self):
        observed = self.observed
        return {"format": {"format_name": observed["format_name"], "start_time": "0", "duration": "2"},
                "streams": copy.deepcopy(observed["streams"]), "probe_warning": None}

    def test_valid_real_media_ffmpeg_and_unchanged_hashes(self):
        unusual = ["clip with spaces.mp4", "director's cut.mp4", "back\\slash.mp4", '"double".mp4', "café.mp4"]
        for name in unusual:
            shutil.copyfile(self.base, self.root / name)
        playlist = self.playlist("# quoted paths\n" + "".join(f"file {quote(name)}\n" for name in unusual))
        inputs = [playlist, self.base, *[self.root / name for name in unusual]]
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        result = validate(playlist)
        self.assertEqual(result["status"], "no-static-blockers-found", result)
        self.assertEqual(result["entries"][0]["line"], 3)
        run_ffmpeg("-f", "concat", "-safe", 0, "-i", playlist, "-map", 0, "-c", "copy", "-f", "null", "-")
        self.assertEqual(hashes, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs})

    def test_both_blockers_and_exact_directive_line(self):
        result = validate(self.playlist("file missing.mp4\nfile base.mp4\noutpoint 9\n"))
        self.assertEqual(result["exit_code"], 1)
        issues = self.issues(result)
        self.assertIn(("source-unreadable", 2), [(item["code"], item["line"]) for item in issues])
        self.assertIn(("outpoint-outside-media", 4), [(item["code"], item["line"]) for item in issues])
        self.assertEqual(result["entries"][1]["observed"]["duration_seconds"], 2)

    def test_unreadable_source(self):
        source = self.root / "unreadable.mp4"
        shutil.copyfile(self.base, source)
        source.chmod(0)
        try:
            result = validate(self.playlist("file unreadable.mp4\n"))
            if os.geteuid() == 0:
                self.skipTest("Root can read mode-000 files.")
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["entries"][0]["diagnostics"][0]["code"], "source-unreadable")
        finally:
            source.chmod(0o600)

    def test_ranges_and_duration_is_not_a_cut(self):
        for directives, code in [("inpoint 1\noutpoint 0.5", "incoherent-range"),
                                 ("inpoint 2", "inpoint-outside-media"),
                                 ("outpoint 3", "outpoint-outside-media")]:
            result = validate(self.playlist("file base.mp4\n" + directives + "\n"))
            self.assertEqual(result["exit_code"], 1)
            self.assertIn(code, [issue["code"] for issue in self.issues(result)])
        result = validate(self.playlist("file base.mp4\nduration 30\n"))
        self.assertEqual(result["exit_code"], 0)
        self.assertIsNone(result["entries"][0]["requested"]["outpoint"])
        self.assertIn("duration-timeline-override", [issue["code"] for issue in self.issues(result)])

    def test_valid_range_and_seconds_suffixes_ffmpeg(self):
        playlist = self.playlist("file base.mp4\ninpoint 500ms\noutpoint 00:01.5\nfile base.mp4\nduration 2s\n")
        result = validate(playlist)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["entries"][0]["range_check"], "within-observed-bounds")
        run_ffmpeg("-f", "concat", "-safe", 0, "-i", playlist, "-map", 0, "-c", "copy", "-f", "null", "-")

    def test_real_unknown_duration_is_incomplete_even_with_duration(self):
        result = validate(self.playlist("file raw.h264\nduration 1\noutpoint 99\n"))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertIsNone(result["entries"][0]["observed"]["duration_seconds"])
        self.assertEqual(result["entries"][0]["range_check"], "unknown")
        self.assertIn("duration-unknown", [issue["code"] for issue in self.issues(result)])

    def test_real_stream_mismatch(self):
        result = validate(self.playlist("file base.mp4\nfile variant.mp4\n"))
        self.assertEqual(result["exit_code"], 1)
        differences = {issue["evidence"].get("field") for issue in self.issues(result)}
        self.assertTrue({"width", "height", "time_base", "sample_rate", "channels"} <= differences)

    def test_real_stream_count_order_and_codec(self):
        fixtures = [
            ("video-only.mp4", ["-map", "0:v", "-c", "copy"], "stream-count-mismatch", None),
            ("reordered.mp4", ["-map", "0:a", "-map", "0:v", "-c", "copy"], "stream-property-differs", "codec_type"),
            ("h264.mp4", ["-c:v", "libx264", "-threads", "1", "-c:a", "copy"], "stream-property-differs", "codec_name"),
        ]
        for name, options, expected_code, expected_field in fixtures:
            with self.subTest(name=name):
                run_ffmpeg("-i", self.base, *options, self.root / name)
                result = validate(self.playlist(f"file base.mp4\nfile {name}\n"))
                self.assertEqual(result["exit_code"], 1, result)
                findings = self.issues(result)
                self.assertIn(expected_code, [issue["code"] for issue in findings])
                if expected_field:
                    self.assertIn(expected_field, [issue["evidence"].get("field") for issue in findings])

    def test_supported_container_families(self):
        fixtures = [
            ("sample.wav", ["-vn", "-c:a", "pcm_s16le"]),
            ("sample.flac", ["-vn", "-c:a", "flac"]),
            ("sample.ogg", ["-vn", "-c:a", "flac", "-strict", "-2"]),
            ("sample.mkv", ["-c", "copy"]),
            ("sample.avi", ["-c:v", "copy", "-c:a", "pcm_s16le"]),
        ]
        for name, options in fixtures:
            with self.subTest(name=name):
                run_ffmpeg("-i", self.base, *options, self.root / name)
                result = validate(self.playlist(f"file {name}\n"))
                self.assertEqual(result["exit_code"], 0, result)

    def test_unknown_or_estimated_metadata_is_incomplete(self):
        for duration in [None, "N/A", "nan", "inf", "0"]:
            data = self.metadata()
            data["format"]["duration"] = duration
            with patch("concatguard.validator.probe", return_value=data):
                result = validate(self.playlist("file base.mp4\nduration 2\n"))
            self.assertEqual(result["exit_code"], 2)
            self.assertIsNone(result["entries"][0]["observed"]["duration_seconds"])
        data = self.metadata()
        data["probe_warning"] = "Estimating duration from bitrate, this may be inaccurate"
        with patch("concatguard.validator.probe", return_value=data):
            self.assertEqual(validate(self.playlist("file base.mp4\n"))["exit_code"], 2)

    def test_zero_or_invalid_stream_metadata(self):
        for field, value in [("width", 0), ("time_base", "0/1"), ("time_base", "bad")]:
            data = self.metadata()
            data["streams"][0][field] = value
            with patch("concatguard.validator.probe", return_value=data):
                result = validate(self.playlist("file base.mp4\n"))
            self.assertEqual(result["exit_code"], 2)

    def test_stream_count_order_codec_and_missing_fields(self):
        playlist = self.playlist("file base.mp4\nfile base.mp4\n")
        for change, expected in [
            (lambda data: data["streams"].pop(), "stream-count-mismatch"),
            (lambda data: data["streams"].reverse(), "stream-property-differs"),
            (lambda data: data["streams"][0].update(codec_name="h264"), "stream-property-differs"),
            (lambda data: data["streams"][0].pop("time_base"), "stream-metadata-unknown"),
        ]:
            base = self.metadata()
            changed = copy.deepcopy(base)
            change(changed)
            with patch("concatguard.validator.probe", side_effect=[base, changed]):
                result = validate(playlist)
            self.assertNotEqual(result["exit_code"], 0)
            self.assertIn(expected, [issue["code"] for issue in self.issues(result)])

    def test_rational_equivalence_and_warnings(self):
        base = self.metadata()
        changed = copy.deepcopy(base)
        changed["streams"][0]["time_base"] = "2/25600"
        changed["streams"][0]["extradata_hash"] = "SHA256:different"
        with patch("concatguard.validator.probe", side_effect=[base, changed]):
            result = validate(self.playlist("file base.mp4\nfile base.mp4\n"))
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["summary"]["blocker"], 0)
        self.assertEqual(result["summary"]["warning"], 1)

    def test_short_audio_bounds(self):
        data = self.metadata()
        data["streams"][1]["duration"] = "1"
        with patch("concatguard.validator.probe", return_value=data):
            result = validate(self.playlist("file base.mp4\noutpoint 1.5\n"))
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("outpoint-outside-stream", [issue["code"] for issue in self.issues(result)])

    def test_nested_playlist_disguised_as_media(self):
        nested = self.root / "nested.mp4"
        nested.write_text("ffconcat version 1.0\nfile 'base.mp4'\n")
        permissive = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(nested)],
                                    capture_output=True, check=True, timeout=10)
        self.assertEqual(len(json.loads(permissive.stdout)["streams"]), 2)
        with patch("concatguard.validator.probe") as mocked:
            result = validate(self.playlist("file nested.mp4\n"))
        mocked.assert_not_called()
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["entries"][0]["diagnostics"][0]["code"], "nested-playlist")
        restricted = subprocess.run(command(nested), capture_output=True, timeout=10)
        self.assertNotEqual(restricted.returncode, 0)
        self.assertIn(b"whitelist", restricted.stderr)

    def test_hls_and_nonlocal_sources(self):
        nested = self.root / "hls.mp4"
        nested.write_text("#EXTM3U\n#EXTINF:2,\nhttp://127.0.0.1:1/never-request\n")
        for source in ["hls.mp4", "https://example.invalid/video.mp4", "file:/tmp/a", "concat:a|b"]:
            with patch("concatguard.validator.probe") as mocked:
                result = validate(self.playlist(f"file {quote(source)}\n"))
            mocked.assert_not_called()
            self.assertEqual(result["exit_code"], 3)

    def test_directory_and_fifo_do_not_block(self):
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        playlist = self.playlist("file pipe\nfile '.'\n")
        process = subprocess.run([sys.executable, "-m", "concatguard", "--json", str(playlist)],
                                 capture_output=True, text=True, timeout=10)
        result = json.loads(process.stdout)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["summary"]["blocker"], 2)
        process = subprocess.run([sys.executable, "-m", "concatguard", "--json", str(fifo)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 3)

    def test_unsupported_invalid_and_oversize_inputs(self):
        result = validate(self.playlist("file base.mp4\nstream\n"))
        self.assertEqual(result["exit_code"], 3)
        self.assertIn(("unsupported-directive", 3), [(i["code"], i["line"]) for i in self.issues(result)])
        for raw in [b"\xff", b"file a\x00", b"#" * (1024 * 1024 + 1)]:
            path = self.root / "invalid.ffconcat"
            path.write_bytes(raw)
            self.assertEqual(validate(path)["exit_code"], 3)

    def test_missing_probe_and_total_deadline(self):
        playlist = self.playlist("file base.mp4\nfile base.mp4\n")
        with patch("concatguard.validator.probe", side_effect=ProbeError("probe-unavailable", "Missing", True)):
            self.assertEqual(validate(playlist)["exit_code"], 2)
        with patch("concatguard.validator.TOTAL_TIMEOUT", 0), patch("concatguard.validator.probe") as mocked:
            result = validate(playlist)
        mocked.assert_not_called()
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["summary"]["incomplete"], 2)

    def test_nonzero_timestamp_origin(self):
        offset = self.root / "offset.mp4"
        run_ffmpeg("-i", self.base, "-c", "copy", "-output_ts_offset", 5, offset)
        result = validate(self.playlist("file offset.mp4\ninpoint 5.5\noutpoint 6\n"))
        self.assertEqual(result["exit_code"], 0, result)
        self.assertGreater(result["entries"][0]["observed"]["start_time_seconds"], 4)

    def test_cli_report_and_exit_codes(self):
        for body, expected in [("file base.mp4\n", 0), ("file missing.mp4\n", 1),
                               ("file raw.h264\n", 2), ("file base.mp4\nstream\n", 3)]:
            playlist = self.playlist(body)
            process = subprocess.run([sys.executable, "-m", "concatguard", "--json", str(playlist)],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, expected, process.stderr)
            report = json.loads(process.stdout)
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["exit_code"], expected)
            self.assertFalse(process.stderr)


if __name__ == "__main__":
    unittest.main()
