# Music Library Organizer

A local-only, plan-first command-line tool for organizing music files you own or are authorized to manage. It reads MP3, FLAC, and M4A metadata, creates a reviewable deterministic plan, and copies files into an `Artist/Album/NN - Title.ext` layout.

This project does **not** download media or artwork, decrypt media, connect or log in to music services, call private platform APIs, bypass DRM, manage accounts, scrape playlists, or write to network storage. It never deletes or moves source files and includes no media files. You are responsible for confirming that you have the legal right to process every input. Version 0.1.0 is intentionally small and is not a media acquisition tool.

## Safety model

- `scan` and `plan` are read-only.
- `apply` is a dry run unless `--execute` is supplied.
- Existing destination files are never overwritten.
- Every source hash is checked immediately before copying.
- Copies are staged in the destination filesystem and linked into place without overwrite.
- A failed apply removes files created by that invocation; source files remain unchanged.
- Symlinked sources and unsafe plan paths are rejected.
- There are no runtime network calls.

Review the generated plan before applying it. Keep an independent backup: software cannot eliminate filesystem, hardware, or operator risk.

## Requirements and installation

- Python 3.11 or newer
- [Mutagen](https://mutagen.readthedocs.io/) (installed automatically)

```console
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
music-organizer --help
```

## Workflow

Scan to JSON on standard output:

```console
music-organizer scan ~/Music/Incoming
```

Create a plan, preview it, then explicitly apply copies:

```console
music-organizer plan ~/Music/Incoming --output plan.json
music-organizer apply plan.json --destination ~/Music/Organized
music-organizer apply plan.json --destination ~/Music/Organized --execute
```

`plan.json` contains the local source root and should generally remain uncommitted. It is excluded by the supplied `.gitignore`.

## Metadata overrides

Pass either JSON keyed by source-relative path:

```json
{
  "demo.mp3": {
    "artist": "Example Artist",
    "album": "Example Album",
    "title": "Example Track",
    "tracknumber": "1"
  }
}
```

or CSV with a required `source` column and optional `artist`, `album`, `title`, `tracknumber`, and `discnumber` columns:

```console
music-organizer plan ~/Music/Incoming --metadata metadata.csv --output plan.json
```

## Local cover artwork

JPEG and PNG files up to 20 MiB can be embedded into every copied file during apply:

```console
music-organizer apply plan.json --destination ~/Music/Organized --cover ./cover.jpg --execute
music-organizer artwork ~/Music/Organized/example.mp3 --output ./extracted.jpg
```

No cover is fetched from the internet.

## Exit codes

- `0`: success, including a successful dry run
- `2`: rejected input, conflict, or expected safety failure
- `3`: unexpected local I/O or malformed JSON failure

## Limitations

- Only MP3, FLAC, and M4A are supported in v0.1.0.
- Organization copies files; it does not deduplicate by acoustic fingerprint.
- The same optional `--cover` is applied to all files in one invocation.
- Empty metadata uses visible fallback names such as `Unknown Artist`.

## Development

Tests create short synthetic silent media at runtime with FFmpeg. No music fixtures are stored in this repository.

```console
python -m unittest discover -s tests -v
ruff check .
python -m build
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [CHANGELOG.md](CHANGELOG.md).

## License

GPL-2.0-or-later. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
