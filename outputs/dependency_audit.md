# Dependency and License Audit

Audit date: 2026-07-19

## Code dependencies

- Runtime: `mutagen>=1.47,<2`; installed version 1.48.1 declares GPL-2.0-or-later.
- Project license: GPL-2.0-or-later, compatible with the runtime dependency.
- Test-only: FFmpeg generates synthetic silent media; no binary or generated media is distributed.
- No vendored third-party source, JavaScript package, or copied Clash/OpenClash code is present.
- `pip-audit 2.10.1 --local --skip-editable`: no known vulnerabilities.

## Optional public data sources

- MusicBrainz: documented WS/2 endpoint, meaningful project User-Agent, 1.1 second minimum interval; core data is CC0 and supplementary data has separate MusicBrainz terms.
- Discogs: documented API only, fixed origin, 2.5 second interval, required non-affiliation/source notices, and six-hour cache expiry/deletion. User/marketplace popularity fields are not used.
- Taiwan Ministry of Culture dataset 58040: Open Government Data License 1.0; the source is attributed in evidence and notices.
- GRAMMY website automation: rejected and removed because current terms prohibit unauthorized automated access.

No third-party data snapshot is distributed other than a minimal Taiwan open-data parser fixture.

Verdict: `DEPENDENCY_SECURITY_PASS_LICENSE_AND_SOURCE_TERMS_COMPATIBLE`
