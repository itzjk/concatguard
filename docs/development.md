# Development

Python 3.9+ runs the unit suite without external media tools. Install FFmpeg
with the `mpeg4`, `aac`, `libx264`, `libvpx-vp9`, and `libopus` encoders for the integration suite. On
macOS use `brew install ffmpeg`; on Ubuntu use `sudo apt-get install ffmpeg`.
Both `ffmpeg` and `ffprobe` must be on PATH. Runtime has no Python dependencies.

If either binary is missing, the real-media class is explicitly skipped with
an installation hint. Parser, process bounds, mocked metadata, local-file
safety, and missing-ffprobe CLI tests still run. With both binaries present,
encoder or fixture failures remain errors. A mode-000 readability test skips
when run as root.

The README test command checks the source directly, independently of an installed
pipx application:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

CI also checks editable installation with pip 21.2.4 on Python 3.9, matching
older system Python environments. The `setup.py` entry point delegates to
setuptools; package metadata stays in `pyproject.toml`. The build backend stays
below setuptools 80 because its replacement of legacy `develop` invokes pip
inside the isolated build environment, where old pip does not install itself.
The optional `dev` extra
installs pytest for running the same suite through `python -m pytest -q`.

From a fresh clone, install the package and test runner together:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

CI checks that both binaries execute before running the full suite on Linux
and macOS. It also runs pytest with an empty tool PATH to cover environments
without FFmpeg. Skipped integration tests mean those media checks were not
performed; a passing unit-only run is not a complete release check.

The suite generates short synthetic media in temporary directories, compares
accepted playlists with FFmpeg's concat demuxer, checks hashes before and after
inspection, and tests all four exit statuses. A permissive ffprobe is shown
following a disguised ffconcat source; the guarded invocation must reject it.
FFmpeg's successful exit is used only for syntax/demuxer compatibility, not proof
of visible frame accuracy. Probe timeouts and both output channels have cap tests.

To record the demo, install concatguard in a virtual environment, then run these
commands from the repository root with `vhs`, `ttyd`, a Chromium browser, and
FFmpeg available:

```sh
python3 examples/make_media.py
vhs docs/demo.tape
```

Both reports in the tape come from the installed CLI. The synthetic sources are
reproducible and ignored by git. The tape generates `docs/demo.gif`; no media
files or machine-specific paths are needed in the recording.
