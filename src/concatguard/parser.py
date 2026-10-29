import re
from dataclasses import dataclass, field
from typing import Optional


MAX_PLAYLIST_BYTES = 1024 * 1024
MAX_ENTRIES = 256
WHITESPACE = " \t\r\n"


@dataclass
class Entry:
    line: int
    path: str
    requested: dict = field(default_factory=lambda: {
        "inpoint": None, "outpoint": None, "duration": None,
    })
    directive_lines: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)


def diagnostic(severity, code, line, message, **evidence):
    return {"severity": severity, "code": code, "line": line,
            "message": message, "evidence": evidence}


def token(text):
    text = text.lstrip(WHITESPACE)
    result = []
    index = 0
    while index < len(text) and text[index] not in WHITESPACE:
        char = text[index]
        if char == "\\":
            index += 1
            if index == len(text):
                raise ValueError("Trailing backslash has no character to escape.")
            result.append(text[index])
            index += 1
        elif char == "'":
            end = text.find("'", index + 1)
            if end < 0:
                raise ValueError("Unclosed single quote.")
            result.append(text[index + 1:end])
            index = end + 1
        else:
            result.append(char)
            index += 1
    if text[index:].strip(WHITESPACE):
        raise ValueError("Expected one argument; escape spaces or use single quotes.")
    value = "".join(result)
    if not value:
        raise ValueError("Missing argument.")
    return value


def timestamp(value):
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:s|ms|us)?", value):
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(s|ms|us)?", value)
        integral, _, fraction = match[1].partition(".")
        integral = integral.lstrip("0") or "0"
        if len(integral) > 19:
            raise ValueError("Time is outside FFmpeg's range.")
        multiplier = {None: 1000000, "s": 1000000, "ms": 1000, "us": 1}[match[2]]
        micros = int(integral) * multiplier + int((fraction + "000000")[:6]) * multiplier // 1000000
    elif re.fullmatch(r"(?:[0-9]{1,2}:)?[0-9]{1,2}:[0-9]{1,2}(?:\.[0-9]+)?", value):
        whole, _, fraction = value.partition(".")
        parts = [int(part) for part in whole.split(":")]
        if parts[-1] >= 60 or (len(parts) == 3 and parts[-2] >= 60):
            raise ValueError("Minutes and seconds must be below 60.")
        micros = sum(part * 60 ** index for index, part in enumerate(reversed(parts))) * 1000000
        micros += int((fraction + "000000")[:6])
    else:
        raise ValueError("Expected a nonnegative finite FFmpeg time (seconds, ms, us, or HH:MM:SS).")
    if micros >= 2 ** 63 - 1:
        raise ValueError("Time is outside FFmpeg's range.")
    return micros / 1000000


def parse(text):
    entries = []
    issues = []
    current: Optional[Entry] = None
    for line, raw in enumerate(text.split("\n"), 1):
        raw = raw.rstrip("\r")
        content = raw.lstrip(WHITESPACE)
        if not content or content.startswith("#"):
            continue
        words = re.split(r"[ \t\r\n]+", content, maxsplit=1)
        name = words[0]
        rest = words[1] if len(words) == 2 else ""
        if name == "ffconcat":
            if line != 1 or raw != "ffconcat version 1.0":
                issues.append(diagnostic("invalid", "invalid-header", line,
                                         "Header must be exactly 'ffconcat version 1.0' on line 1."))
            continue
        if name not in {"file", "inpoint", "outpoint", "duration"}:
            issues.append(diagnostic("invalid", "unsupported-directive", line,
                                     f"Directive {name!r} is outside the supported ffconcat subset."))
            continue
        if name == "file":
            current = None
        try:
            value = token(rest)
            if name == "file":
                if len(entries) >= MAX_ENTRIES:
                    issues.append(diagnostic("incomplete", "entry-limit", line,
                                             f"Inspection is limited to the first {MAX_ENTRIES} entries; "
                                             "this entry and the remaining playlist were not checked.",
                                             max_entries=MAX_ENTRIES))
                    break
                current = Entry(line, value)
                entries.append(current)
            else:
                if current is None:
                    raise ValueError(f"{name} must follow a file directive.")
                if name in current.directive_lines:
                    raise ValueError(f"Duplicate {name} directive.")
                number = timestamp(value)
                if name == "duration" and number <= 0:
                    raise ValueError("duration must be greater than zero.")
                current.requested[name] = number
                current.directive_lines[name] = line
        except ValueError as error:
            issue = diagnostic("invalid", "invalid-directive", line, str(error))
            (current.diagnostics if current else issues).append(issue)
    if not entries:
        issues.append(diagnostic("invalid", "empty-playlist", None, "Playlist contains no file entries."))
    return entries, issues
