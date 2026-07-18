# Third-Party Notices

## Mutagen

- Package: `mutagen`
- Supported version range: `>=1.47,<2`
- Version audited for v0.2.0: `1.48.1`
- Project: https://github.com/quodlibet/mutagen
- License: GPL-2.0-or-later
- Use: runtime metadata and embedded-artwork parsing/writing for MP3, FLAC, and M4A.
- Distribution: installed separately by the Python package installer; its source is not copied into this repository.

The project itself is licensed GPL-2.0-or-later to keep its license compatible with this runtime dependency. Mutagen retains its own copyright and license notices.

## FFmpeg

FFmpeg is used only by the test suite and CI to generate short synthetic silent media. It is not a runtime dependency and no FFmpeg binary or media output is distributed by this repository. License terms for FFmpeg depend on the installed build configuration.

## Optional public metadata and award sources

The album-pruning workflow can fetch factual records at runtime from MusicBrainz, Discogs, and Taiwan's Ministry of
Culture open-data portal. Responses remain in the user's ignored local cache and are not distributed with this
repository.

- MusicBrainz core data is CC0; supplementary data has separate MusicBrainz licensing terms:
  https://musicbrainz.org/doc/MusicBrainz_Database/License
- Discogs data remains subject to the Discogs API terms:
  https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use
- Discogs responses are cached for at most six hours. The review controls display source attribution through a direct
  link next to each Discogs rating; the README contains the required non-affiliation notice.
- Taiwan Ministry of Culture dataset 58040 is published under Taiwan Open Government Data License 1.0:
  https://data.gov.tw/license
