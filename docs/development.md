# Development

Python 3.9+ and FFmpeg with the `mpeg4`, `aac`, and `libx264` encoders are required
for the integration suite. Runtime has no Python dependencies. The test suite
uses the standard library and fails rather than silently skipping media tests
when FFmpeg is missing. A mode-000 readability test skips when run as root.

The README test command checks the source directly, independently of an installed
pipx application:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

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
