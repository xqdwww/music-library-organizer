# Music Library Organizer

A plan-first tool for organizing music files you own or are authorized to manage. It reads local metadata, creates a reviewable deterministic organization plan, and can build a separate public-rating review for already organized albums.

This project does **not** download media or artwork, decrypt media, log in to personal music-library accounts, call private platform APIs, bypass DRM, manage subscriptions, or scrape playlists. Optional album-review commands query documented public metadata and award sources; they never request media URLs. The original organization workflow never deletes or moves source files. The album-pruning workflow can only move explicitly selected albums into a configured quarantine after a hashed preview and a second confirmation. It never treats that first action as permanent deletion.

## Safety model

- `scan` and `plan` are read-only.
- `apply` is a dry run unless `--execute` is supplied.
- Existing destination files are never overwritten.
- Every source hash is checked immediately before copying.
- Copies are staged in the destination filesystem and linked into place without overwrite.
- A failed apply removes files created by that invocation; source files remain unchanged.
- Symlinked sources and unsafe plan paths are rejected.
- The original organization commands make no runtime network calls. `album-prune scan` contacts only enabled public rating adapters and supports cached offline mode.

The `album-prune` command family has a separate safety model:

- Album scanning and candidate generation never modify media.
- `music_score` uses only public album ratings. Popularity, listeners, plays, file size, format, and local activity are excluded.
- Fuzzy or ambiguous matches, soundtracks, compilations, live albums, box sets, and multiple local versions are not selectable.
- Every candidate starts unchecked and selections use stable album IDs.
- A selection produces a hashed, file-level quarantine preview. Apply requires the preview's random confirmation token.
- Apply moves files into a batch quarantine, verifies hashes, and retains a rollback manifest.
- Rollback refuses to overwrite new files. Permanent purge is a separate operation with a separate confirmation phrase.
- Both review controls bind to loopback only and reject non-loopback Host/Origin requests.

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

## Low-rated album review

Create a local config from `config.example.json`. Runtime state, API responses, selections, and cleanup batches are ignored by Git.

```console
music-organizer album-prune --config ~/.config/music-library-organizer/config.local.json scan --professional
music-organizer album-prune --config ~/.config/music-library-organizer/config.local.json candidates --threshold 60
music-organizer album-prune --config ~/.config/music-library-organizer/config.local.json serve
```

The integrated local review control shows the canonical match, raw source ratings, grouped community/critic/professional scores, awards, match features, conversion rules, protection reasons, file formats, size, and local path. MusicBrainz identifies the release group and provides community ratings. Discogs aligns its master and reads the main release's documented community rating. The opt-in `--professional` adapter reads Taiwan Ministry of Culture Golden Indie open data. It does not scrape award or review websites. Discogs `want` and `have`, charts, sales, and popularity are deliberately ignored.

### Network and metadata privacy

`album-prune scan` enables public MusicBrainz and Discogs lookups by default. It sends the local artist, album title, and—when available—year, barcode, catalogue number, or embedded public database ID needed for entity resolution. It does not send audio, cover images, local paths, playlists, or file hashes. Use `--no-ratings` for a local-only rescan that preserves prior evidence, or `--offline` to consume existing cache entries without network access. `--professional` is separately opt-in.

Discogs can read `DISCOGS_TOKEN` from the process environment. The token is sent only in the Discogs authorization header; it is not placed in URLs, reports, SQLite records, or response-cache files. Treat the state directory and generated reports as private because they contain library metadata and local paths.

This application uses Discogs' API but is not affiliated with, sponsored, or endorsed by Discogs. Discogs responses are retained for no more than six hours and are labelled with a direct source link in the local review controls.

Before any new rating adapter is developed or enabled, follow the [Music Rating Ecosystem Audit](docs/music-rating-ecosystem-audit.md). It records reusable matching and review components, classical and jazz identity requirements, licensing constraints, and hard rejection gates. Professional awards and reviews remain independent evidence until a validated conversion rule exists.

CLI cleanup remains explicit:

```console
music-organizer album-prune select <album_id> [<album_id> ...]
music-organizer album-prune plan --selection-id <selection_id>
music-organizer album-prune apply --batch-id <batch_id> --confirm <plan_confirmation_token>
music-organizer album-prune rollback --batch-id <batch_id> --confirm ROLLBACK:<batch_id>
music-organizer album-prune purge --batch-id <batch_id> --confirm PURGE:<batch_id>
```

`scan`, `candidates`, and `serve` do not change music files. `plan` hashes selected album contents but does not move them. Only `apply`, `rollback`, and `purge` mutate the filesystem.

## Personal library calibration

Calibration uses the real curated library to measure whether fixed score thresholds fit one person's collection. It does not change media and never recommends a threshold until enough independent human labels exist.

```console
music-organizer album-prune --config config.local.json calibration-baseline create
music-organizer album-prune --config config.local.json scan
music-organizer album-prune --config config.local.json calibration-baseline verify
music-organizer album-prune --config config.local.json calibration-sample --size 140 --seed 20260718
music-organizer album-prune --config config.local.json calibration-enrich --batch-id <calibration_batch_id>
music-organizer album-prune --config config.local.json calibration-enrich-library --source musicbrainz
music-organizer album-prune --config config.local.json calibration-enrich-library --source discogs
music-organizer album-prune --config config.local.json calibration-enrich-library --source official-awards
music-organizer album-prune --config config.local.json calibration-enrich-library --source musicbrainz --language-status JA_CONFIRMED
music-organizer album-prune --config config.local.json calibration-enrich-library --source musicbrainz --category Jazz
music-organizer album-prune --config config.local.json calibration-stats
music-organizer album-prune --config config.local.json calibration-scope
music-organizer album-prune --config config.local.json calibration-import-beets-scope --beets-db <library.db>
music-organizer album-prune --config config.local.json calibration-report
music-organizer album-prune --config config.local.json calibration-policy
music-organizer album-prune --config config.local.json calibration-serve --batch-id <calibration_batch_id>
music-organizer album-prune --config config.local.json personal-policy-apply --batch-id <completed_batch_id>
music-organizer album-prune --config config.local.json personal-candidates
```

The stratified sample includes high, medium, low, unrated, single-source, multi-source, conflicting, ambiguous, and multi-version records across popular music, jazz, classical, and other categories. The calibration UI stores `KEEP`, `DELETE_CANDIDATE`, or `LATER` separately from `music_score`, along with match and rating feedback. Its server intentionally exposes no apply, quarantine, rollback, or purge endpoint. The generated personal policy keeps `music_score_threshold: null` until the user chooses a policy after reviewing the report.

After a calibration batch is fully reviewed, `personal-policy-apply` verifies every label before enabling separate
strong-score and review-score thresholds. The active policy is stored as JSON in the local SQLite state and mirrored
to an ignored YAML file. Explicit `DELETE_CANDIDATE` labels remain independent candidates even when they have no
score or lie above the thresholds. `KEEP`, `LATER`, ambiguous matches, insufficient score evidence, and professional
protections cannot become automatic score candidates. Candidate groups can overlap, but reclaim size and album
counts are deduplicated. Automatic selection and deletion are always disabled.

`calibration-enrich-library` sends each stored artist, album title, and year to the selected public service. Run it online only with explicit authorization to disclose the full library metadata. `--offline` consumes existing cache entries, records cache misses as unresolved external attempts, and sends nothing.

## Personal Library Curator

Personal Library Curator adds a read-only collection-value analysis above the existing rating and cleanup systems.
It combines optional local Apple Music usage exports, existing public and professional evidence, collector
protection, and release-edition redundancy. Missing personal data is neutral and never interpreted as zero plays.

```console
music-organizer album-prune --state-root <state> curator-analyze --library-root <music-library>
music-organizer album-prune --state-root <state> curator-analyze --library-root <music-library> --apple-source Library.xml
music-organizer album-prune --state-root <state> curator-report
music-organizer album-prune --state-root <state> curator-serve --port 8771
```

The output classes are `KEEP`, `REVIEW`, `LOW_PERSONAL_VALUE`, `DUPLICATE_VALUE`, and `PROTECTED_COLLECTION`.
Every row starts unchecked. The dedicated UI is GET-only and contains no cleanup operation. See
[Personal Library Curator v1](docs/personal-library-curator.md) for accepted import fields, score weights, duplicate
guards, and safety boundaries.

Run `calibration-scope` before enrichment. Explicit Chinese-language records are excluded. Classical and jazz records remain in specialty calibration, Japanese/Korean script is retained as non-Chinese, and unresolved Han-script records are withheld for language review rather than guessed.

Entity resolution is staged and auditable: embedded IDs, barcode/catalogue lookup, exact structured queries,
bounded normalized queries, release-group/master reconciliation, and manual review. Every attempted stage records
its query, candidate count, selected entity, features, confidence, and rejection reason. Edition or disc suffix
normalization generates candidates only; it never establishes a match by itself.

CJK routing uses `ZH_CONFIRMED`, `JA_CONFIRMED`, `KO_CONFIRMED`, `HK_TW_CANTONESE`, `MIXED_CJK`, `NON_CJK`, and
`UNKNOWN_CJK` with retained language/country/script evidence, resolver sources, confidence, and decision trace.
Routing controls source selection and never contributes to
`music_score`. Classical and Jazz albums retain separate recording/session identity fields. CritiqueBrainz has a
fixture-tested wrapper but is not exposed by the production enrichment CLI because its fixed 40-album validation
sample produced no usable ratings or professional-source evidence.

Professional evidence is stored before score conversion. An official album award retains
its publication, category, source URL, recording identity, match features, confidence, raw evidence hash, and
conversion rule. The grouped score model combines available community, critic, and professional groups at
25/30/45 weights after per-source aggregation. An evidence-backed award or historic-recording protection reason
prevents a low community score from creating a deletion candidate. Missing or failed evidence never creates a score.

## Exit codes

- `0`: success, including a successful dry run
- `2`: rejected input, conflict, or expected safety failure
- `3`: unexpected local I/O or malformed JSON failure

## Limitations

- The organization workflow supports MP3, FLAC, and M4A. Album review recognizes common additional audio containers through Mutagen.
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
