# JSON report contract, version 1

`concatguard --json playlist.ffconcat` emits one JSON object to stdout for valid
invocations and usage errors. Help and version print text and exit 0.
Paths, messages, and ffprobe metadata vary with the input, platform, and FFmpeg
version. Consumers should branch on codes and fields, not message text.

Top-level fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `playlist` | Supplied playlist path, or null on CLI argument errors |
| `status` | `no-static-blockers-found`, `blocked`, `incomplete`, `invalid-input` |
| `exit_code` | Respectively 0, 1, 2, 3 |
| `summary` | Counts keyed by `invalid`, `blocker`, `incomplete`, `warning` |
| `diagnostics` | Findings belonging to the playlist rather than an entry |
| `entries` | Source entries in original order |

An entry always has `line` (the one-based `file` line), decoded `path`,
`resolved_path` (absolute local path or null), `requested` (`inpoint`, `outpoint`,
`duration`, each in seconds or null), `directive_lines`, `observed`, `range_check`,
`compared_with_line`, and `diagnostics`. Null requested values mean omitted, not
zero. `range_check` is `unknown` until enough metadata exists, then
`within-observed-bounds` or `outside-observed-bounds` according to temporal findings. It is a statement
about observed bounds, not a certificate of frame accuracy or stream compatibility.

`observed` is null if the source could not be probed. Otherwise it includes
`format_name`, `duration_seconds` (number or null), `duration_source`
(`container-metadata` or `unknown`), `start_time_seconds`, and `streams` in their
observed order. Stream fields are selected ffprobe metadata; absent values remain
absent. `compared_with_line` identifies the reference file line, or is null when
no comparison was made. Even a blocked range may supply stream comparison data.

Every diagnostic has `severity`, a machine-readable `code`, one-based `line`
(null for errors without a line), `message`, and an `evidence` object. For
`outpoint-outside-media`, evidence includes `requested` and `observed_end` in
seconds. Stream differences include `stream` (zero-based position), `field`,
`expected`, `observed`, and `reference_line`.

Mixed findings retain their individual severities. Exit precedence is invalid
input (3), blocker (1), incomplete (2), then no static blockers found (0).
An unavailable executable, timeout, output cap, exhausted total budget, missing
required stream metadata, unsupported stream type, or unknown duration is
incomplete. An unreadable source, failed media probe, impossible range, or
observed stream incompatibility is blocking. Unsupported directives and
nonlocal/nested inputs are invalid. Warning-only reports exit 0.

Schema changes that remove fields or change their meaning require a new schema
version. Additional diagnostic codes and evidence fields may be added in version
1; consumers should preserve unknown codes.
