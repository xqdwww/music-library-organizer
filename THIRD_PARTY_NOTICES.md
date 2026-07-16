# Third-Party Notices

## Mutagen

- Package: `mutagen`
- Supported version range: `>=1.47,<2`
- Version audited for v0.1.0: `1.48.1`
- Project: https://github.com/quodlibet/mutagen
- License: GPL-2.0-or-later
- Use: runtime metadata and embedded-artwork parsing/writing for MP3, FLAC, and M4A.
- Distribution: installed separately by the Python package installer; its source is not copied into this repository.

The project itself is licensed GPL-2.0-or-later to keep its license compatible with this runtime dependency. Mutagen retains its own copyright and license notices.

## FFmpeg

FFmpeg is used only by the test suite and CI to generate short synthetic silent media. It is not a runtime dependency and no FFmpeg binary or media output is distributed by this repository. License terms for FFmpeg depend on the installed build configuration.
