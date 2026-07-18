# Personal Library Curator v1

Personal Library Curator estimates whether a local release still has value in one person's long-term collection. It
does not replace `music_score`, label music as objectively bad, or create a cleanup selection.

## Reused interfaces

- Apple documents Music library and playlist export as XML. Curator parses that plist with Python's standard
  `plistlib`, preserving play count, last-played date, rating, loved/favorite state, date added, and playlist
  membership. See [Apple's Music export documentation](https://support.apple.com/guide/music/mus27cd5060f/mac).
- The maintained `iTunesLibrary` Python package confirms the exported XML entity model, but v2 currently requires
  Python 3.14 while this project supports Python 3.11+. It is reference-only rather than a runtime dependency.
- `Music Library.musiclibrary/Library.musicdb` is a private Apple database format. Its presence is not treated as
  usable personal data. Exported XML, CSV, TSV, or JSON is required until a tested native Apple framework helper is
  available.
- MusicBrainz release groups and Discogs masters from the existing review state are reused as the strongest edition
  grouping keys. Normalized title grouping is only a review hint. Classical and Jazz fallback grouping is disabled
  unless edition wording is present, because the same work or session title can identify different recordings.
- Netease input is optional imported JSON/CSV/TSV. The existing Netease adapter repository is not called or changed.
  A community score is accepted only when at least two thirds of local album tracks match; 8 of 12 tracks is the
  minimum for a 12-track album. Comment counts are retained as context and never converted to quality.

## Score model

`personal_value_score` is transparent and bounded to 0-100:

| Component | Weight | Missing-data behavior |
| --- | ---: | --- |
| Personal usage | 55% | Neutral 50, never zero |
| Existing public quality | 20% | Neutral 50 |
| Collector value | 25% | Neutral 50 |
| Non-preferred duplicate penalty | -15 points | Applied only inside an evidence-backed group |

Personal usage uses play count, recency, personal rating, favorite state, and playlist membership. An album can be
`LOW_PERSONAL_VALUE` only when Apple data reliably covers at least half its tracks, confirms zero plays, has no
rating/favorite/playlist signal, was added more than five years ago (or has no date), and has no collector
protection. Missing Apple data can produce `REVIEW`, but never `LOW_PERSONAL_VALUE`.

When personal data is unavailable, the largest 10% of otherwise unprotected, non-duplicate releases are placed in a
storage-priority `REVIEW` cohort. File size does not change `personal_value_score` and cannot create
`LOW_PERSONAL_VALUE`; it only limits and orders the albums presented for human review.

Collector protection retains existing permanent protection, evidence-backed awards and recommendations, reference
recording status, and historical/catalog significance. Classical recording identity and Jazz session identity are
visible evidence but do not invent an award or automatically convert to a public score.

## Commands

```console
music-organizer album-prune --state-root <state> curator-analyze \
  --library-root <music-library> \
  --apple-source ~/Music/Music/Library.xml \
  --netease-source netease-albums.json

music-organizer album-prune --state-root <state> curator-report
music-organizer album-prune --state-root <state> curator-serve --port 8771
```

Both external sources are optional. `curator-analyze --library-root` performs a fresh read-only media scan and
reuses prior canonical/rating/professional evidence by path, filesystem fingerprint, or a unique normalized
artist/album/year identity. It writes only the SQLite state and ignored report output. The Curator server accepts
GET requests only; all POST requests return HTTP 405. It exposes no selection, plan, apply, quarantine, rollback, or
purge route.
