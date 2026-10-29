# Prior-art check

Reviewed 2026-10-28 before implementation. Searches were bounded to `ffconcat
validator`, `ffconcat preflight validator`, and `ffmpeg concat playlist validation
tool github`. This does not establish that no equivalent exists.

- [FFmpeg concat](https://ffmpeg.org/ffmpeg-formats.html#concat) executes playlist
  concatenation, documents stream compatibility, escaping, duration, and cut
  timestamp behavior. It does not provide this tool's standalone per-entry JSON
  preflight report that collects multiple local-source errors without rendering.
- [ffprobe](https://ffmpeg.org/ffprobe.html) supplies source metadata. concatguard
  uses it rather than implementing media parsing.
- [Editly](https://github.com/mifi/editly) is a declarative editor with its own edit
  specification and rendering workflow, rather than this narrow playlist checker.
- [ffplayout](https://github.com/ffplayout/ffplayout) validates imported media in a
  broadcast scheduling system. Its scope is operating playout channels, rather
  than a standalone CLI for this ffconcat subset and line-level JSON contract.
- [Polyemesis](https://github.com/rainmanjam/polyemesis) has internal source
  verification for a broadcast workflow. It is adjacent safety work, not an
  equivalent standalone ffconcat checker.

No equivalent standalone CLI meeting the brief was identified in these searches.
The supplied [editing discussion](https://x.com/alexgoughcooper/status/2115076988327076296)
is motivation, not measured demand. X returned HTTP 403 during this review, so
the supplied interaction counts and productivity testimony were not independently
verified here and are not used as product performance claims. No estimates of
commercial savings are made.
