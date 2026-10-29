# Supported input and inspection boundaries

## Playlist syntax

One directive per line, in a UTF-8 file of at most 1 MiB. An optional header must
be exactly `ffconcat version 1.0` on the first line, without a byte order mark.
Blank lines and lines whose first non-space character is `#` are ignored.
The supported directives are `file`, `inpoint`, `outpoint`, and `duration`.
All others, including `option`, `stream`, and `chapter`, are explicit scope errors.
Repeated time directives, trailing arguments, inline comments, unclosed quotes,
and dangling escapes are rejected. This is a strict subset of FFmpeg's parser.

Single quotes preserve literal characters; outside quotes a backslash escapes the
following character. Double quotes are literal filename characters. For example:

```text
ffconcat version 1.0
file 'director'\''s cut.mp4'
inpoint 500ms
outpoint 00:01.5
duration 1s
```

Times accept nonnegative decimal seconds with optional `s`, `ms`, or `us`, or
`MM:SS` / `HH:MM:SS` with fractional seconds. Colon components have one or two
digits; minutes within an hours form and seconds must be below 60. Values are
truncated to FFmpeg's microsecond resolution. Scientific notation, negative
timestamps, nonfinite values, and values outside signed 64-bit microseconds are
outside scope. Declared duration must be positive.

Paths resolve against the directory containing the playlist as invoked, including
when the playlist itself is a symlink. Media symlinks may point to regular local
files. Absolute paths and relative `..` paths are allowed. URLs, explicit
protocols (including `file:`), and network-style `//` paths are rejected.
Untrusted local media should still be handled with an updated FFmpeg build.

## Media and streams

Allowlisted demuxers: `mov`, `matroska`, `avi`, `wav`, `flac`, `ogg`, `h264`.
These cover MP4/MOV/M4A, Matroska/WebM, AVI, WAV, FLAC, Ogg, and elementary H.264.
Extension alone does not choose a demuxer. Unsupported formats are rejected by
ffprobe's format allowlist. Nested playlists recognized by their text signature
are rejected before probing, even when named `.mp4`. The allowlist also excludes
concat, HLS, DASH, image sequences, and other indirection demuxers.

Streams are compared by array position, without language or ID remapping.
Differences in codec type/name, time base, dimensions, pixel format, sample rate,
sample format, and channel count are blockers. Known aspect ratios, channel
layouts, and bit depths are compared too. Missing required fields make inspection
incomplete. Missing optional fields are not treated as differences. Profile,
level, codec tag, codec extradata hash, and frame rate differences are warnings:
these can vary within a usable concatenation. Equal values do not prove equal
bitstreams. Subtitle/data/attachment streams produce an incomplete result.

## Time evidence

A positive, finite container duration is an observed metadata bound. The media
interval is `format.start_time` through `format.start_time + format.duration`.
Absent start time defaults to zero, as in the concat demuxer. Explicit bounds
are checked against the interval, plus individual stream endpoints when present.
Stream intervals that differ substantially from the container produce warnings.
No duration is inferred from playlist `duration`, frame counts, or bitrate.
Raw H.264 and probes reporting bitrate-based duration estimation remain unknown.
Container metadata may itself be stale or inaccurate. No packet or full-frame
scan is performed, and passing does not certify the cut's visible endpoints.

## Process boundaries

Only regular local files are opened; nonblocking inspection prevents FIFOs from
hanging the initial file check. ffprobe runs without a shell, with stdin closed,
only the `file` protocol, the fixed demuxer allowlist, and MOV external data
references disabled. It receives a 5 MB probe budget, 5 seconds of analyzed media,
one decoder thread, and a 64 MiB limit per FFmpeg allocation. The allocation limit
is not a whole-process memory cap. One probe runs at a time, up to 256 probes;
each has a 10-second wall-clock deadline and a 1 MiB combined stdout/stderr cap.
All probing shares a 60-second deadline. Limit failures are incomplete findings.
At the 257th file entry, inspection stops with an `entry-limit` finding on that
line. The report contains the first 256 entries; the remaining playlist is
unchecked. Findings already observed keep their severities and exit precedence.
There are no retries or paid calls. Process groups are killed and reaped on exit.

MOV safety options remain enabled for every probe, regardless of filename.
On successful non-MOV probes, the two exact notices saying these options are
unknown to that demuxer are omitted. Other probe warnings and errors are retained.

This is not an OS sandbox. Mounted network storage looks like a local file to
the OS, and concurrent file replacement is outside the stable-input assumption.
Normal operation never invokes ffmpeg or writes media, playlists, or reports to
disk. JSON goes to stdout; redirect it yourself when needed.

Technical references, reviewed 2026-10-28:
[concat demuxer](https://ffmpeg.org/ffmpeg-formats.html#concat),
[quoting and duration syntax](https://ffmpeg.org/ffmpeg-utils.html#Syntax),
[ffprobe](https://ffmpeg.org/ffprobe.html), and
[concat timestamp implementation](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/concatdec.c).
