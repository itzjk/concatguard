import argparse
import json

from . import __version__
from .parser import diagnostic
from .validator import finish, validate


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def human(report):
    counts = report["summary"]
    lines = [f"{report['status']} | {len(report['entries'])} entries | "
             f"{counts['blocker']} blockers | {counts['incomplete']} incomplete | "
             f"{counts['invalid']} invalid | {counts['warning']} warnings"]
    for entry in report["entries"]:
        requested = entry["requested"]
        observed = entry["observed"]
        begin = requested["inpoint"]
        end = requested["outpoint"]
        interval = f"{begin if begin is not None else 'source start'} -> {end if end is not None else 'source end'}"
        lines.append(f"  line {entry['line']}: {entry['path']!r} | range {interval}")
        if requested["duration"] is not None:
            lines.append(f"    declared duration={requested['duration']:g}s (timeline spacing)")
        if observed is not None:
            duration = observed["duration_seconds"]
            duration_text = f"{duration:g}s" if duration is not None else "unknown"
            streams = []
            for position, stream in enumerate(observed["streams"]):
                detail = f"{position}:{stream.get('codec_type', '?')}/{stream.get('codec_name', '?')}"
                if stream.get("codec_type") == "video":
                    detail += f" {stream.get('width', '?')}x{stream.get('height', '?')}"
                if stream.get("codec_type") == "audio":
                    detail += f" {stream.get('sample_rate', '?')}Hz/{stream.get('channels', '?')}ch"
                detail += f" tb={stream.get('time_base', '?')}"
                streams.append(detail)
            lines.append(f"    observed {duration_text}, start={observed['start_time_seconds']:g}s | " + "; ".join(streams))
        for item in entry["diagnostics"]:
            lines.append(format_diagnostic(item))
    for item in report["diagnostics"]:
        lines.append(format_diagnostic(item))
    return "\n".join(lines)


def format_diagnostic(item):
    location = f"line {item['line']}" if item["line"] is not None else "input"
    evidence = json.dumps(item["evidence"], ensure_ascii=True, sort_keys=True) if item["evidence"] else ""
    message = json.dumps(item["message"], ensure_ascii=True)[1:-1]
    return f"    {item['severity'].upper()} {location} [{item['code']}] {message}" + (f" {evidence}" if evidence else "")


def main(argv=None):
    parser = Parser(prog="concatguard", description="Check a local FFmpeg concat playlist before rendering.")
    parser.add_argument("playlist", help="path to an ffconcat playlist")
    parser.add_argument("--json", action="store_true", help="print the versioned JSON report")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    try:
        args = parser.parse_args(argv)
        report = validate(args.playlist)
        as_json = args.json
    except UsageError as error:
        import sys
        as_json = "--json" in (argv if argv is not None else sys.argv[1:])
        report = finish({"schema_version": 1, "playlist": None, "entries": [],
                         "diagnostics": [diagnostic("invalid", "cli-arguments", None, str(error))]})
    print(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) if as_json else human(report))
    return report["exit_code"]
