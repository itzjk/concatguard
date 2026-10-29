# concatguard

Find broken cuts and incompatible sources before starting an FFmpeg concat render.

[![CI](https://github.com/itzjk/concatguard/actions/workflows/ci.yml/badge.svg)](https://github.com/itzjk/concatguard/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

```text
$ concatguard examples/valid.ffconcat
no-static-blockers-found | 2 entries | 0 blockers | 0 incomplete | 0 invalid | 0 warnings
  line 2: 'media/blue.mp4' | range source start -> source end
    observed 2s, start=0s | 0:video/mpeg4 320x180 tb=1/12800; 1:audio/aac 48000Hz/1ch tb=1/48000
  line 3: 'media/green.mp4' | range source start -> source end
    observed 2s, start=0s | 0:video/mpeg4 320x180 tb=1/12800; 1:audio/aac 48000Hz/1ch tb=1/48000
```

## Quick start

Requires Python 3.9+, [pipx](https://pipx.pypa.io/stable/installation/), and FFmpeg's `ffprobe` on PATH. Supports Linux and macOS.

```sh
pipx install git+https://github.com/itzjk/concatguard.git
```

```sh
concatguard playlist.ffconcat
```

## Features

- Get the source path, requested range, observed metadata, and diagnosis for each entry.
- Find missing files and impossible ranges with the exact playlist line number.
- Compare stream count, order, codecs, time bases, video dimensions, and audio parameters.
- Distinguish blocking findings, incomplete checks, invalid input, and warnings in CI.
- Inspect local sources without changing the playlist or media, rendering, or calling a model.

## Why

[A discussion about agent-assisted editing](https://x.com/alexgoughcooper/status/2115076988327076296) highlights interest in faster editing workflows; it does not measure demand for this checker or prove productivity gains. When software assembles a cut list, a missing source or impossible outpoint is useful to diagnose before the render starts. [FFmpeg requires compatible streams](https://ffmpeg.org/ffmpeg-formats.html#concat), while [Editly](https://github.com/mifi/editly) handles declarative editing: concatguard checks an existing FFmpeg playlist.

## How it works

concatguard reads the supported ffconcat directives, resolves sources relative to the playlist's directory, and runs a bounded `ffprobe` process for each source. It compares observed streams against the first successfully inspected source, preserving their order. Temporal checks use source timestamps and available container metadata; a declared `duration` controls timeline spacing and never supplies a missing media endpoint. The result `no-static-blockers-found` means the static checks found no blockers. Playback and visible cut boundaries still need verification in your editing workflow.

## Usage

| Command | Result |
| --- | --- |
| `concatguard playlist.ffconcat` | Brief human report |
| `concatguard --json playlist.ffconcat` | Versioned JSON report |
| `concatguard --help` | Command reference |
| `concatguard --version` | Installed version |

Exit codes: **0** no static blockers found, **1** blockers observed, **2** incomplete check, **3** invalid input. If findings overlap, invalid input takes precedence, then blockers, then incomplete checks. Warnings alone keep exit code 0. JSON includes every finding, including those below the winning status; see the [report contract](docs/report.md) for field definitions and precedence.

Generate the synthetic demo inputs and check both lists:

```sh
python3 examples/make_media.py
concatguard examples/valid.ffconcat
concatguard examples/broken.ffconcat
```

The last command exits 1 and points to the missing file on line 2 and the outpoint on line 4. It compares 9 requested seconds against a 2-second source.

## FAQ

**Which playlists and formats work?**
The subset accepts the optional exact header, whole-line comments, `file`, `inpoint`, `outpoint`, and `duration`. Sources may be MP4/MOV, Matroska/WebM, AVI, WAV, FLAC, Ogg, or elementary H.264. Other directives produce scope errors. See [format and safety details](docs/scope.md).

**What about spaces and apostrophes?**
FFmpeg's single quotes and backslash escapes are supported, including `file 'director'\''s cut.mp4'`. Paths are relative to the playlist, regardless of your working directory.

**Does a successful check promise an exact cut?**
No. Inpoints may seek before the requested frame, and outpoints use decoding timestamps. Unknown duration produces an incomplete result even if the playlist declares a duration.

**Can I install from a clone?**

```sh
git clone https://github.com/itzjk/concatguard.git
cd concatguard
pipx install .
```

## Limitations

- Metadata can be wrong. Complete corruption checks, audiovisual drift, and frame accuracy require decoding.
- URLs, nested playlists, devices, pipes, subtitle checks, and attachment checks are outside scope.
- Probing is sequential: at most 256 entries, 10 seconds per source, 60 seconds total, and 1 MiB of combined output per probe. A limit produces an incomplete result.
- Use stable files during inspection. Files can change between checking and rendering.

## Contributing

Install from a clone, make FFmpeg available on PATH, and run the real-media suite with one command:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

See [development notes](docs/development.md) to regenerate the terminal demo.

## License

[MIT](LICENSE).
