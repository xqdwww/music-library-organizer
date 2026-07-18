# Release Audit

Audit date: 2026-07-19
Candidate version: 0.2.0

## Release decision

`READY_TO_PUBLISH`

The v0.2 candidate adds explainable public-rating review, personal calibration, a read-only personal curator, and explicit quarantine/rollback/purge workflows without changing the original plan-first organization boundary. No operation selects albums automatically. Scan, calibration, curator analysis, candidate generation, and preview remain non-destructive; mutation requires a signed persisted plan and separate explicit confirmation.

## Security and correctness

- Loopback review servers validate Host, same-origin Origin, CSRF tokens, JSON object shape, and a 1 MiB request limit.
- Remote strings and URLs are escaped or restricted to HTTP(S); covers and public responses are size-bounded.
- Delete plans are integrity checked; apply, rollback, recovery, and purge reconstruct trusted paths from the signed plan rather than mutable SQLite or journal values.
- Quarantine moves never overwrite a concurrently created target and verify file hashes.
- MusicBrainz and Discogs use fixed HTTPS API origins, bounded responses, rate limits, and local caches. Discogs cache entries expire and are deleted after six hours.
- The prohibited GRAMMY HTML scraper and its fixture were removed. The opt-in award adapter uses only Taiwan Ministry of Culture open data.
- Curator imports are local files only. Its GET-only UI validates loopback Host headers, bounds cover responses, and exposes no cleanup endpoint; private state uses directory mode 0700 and report/database mode 0600 on POSIX.

## Verification

- Unit and CLI tests: 117 passed with `ResourceWarning` promoted to errors.
- Ruff: PASS.
- Python bytecode compilation: PASS.
- Isolated sdist and wheel build: PASS.
- Fresh wheel installation with runtime dependency: PASS (`music-organizer 0.2.0`, Mutagen 1.48.1).
- Tests executed from the final unpacked sdist: 117 passed. CI rebuilds and reinstalls the final distribution.
- CLI help/version, local scan, `--no-ratings`, and `--offline` smoke: PASS.
- `pip-audit 2.10.1 --local --skip-editable`: no known vulnerabilities.
- Privacy, secret, private-path, media, large-file, license, provenance, README, and GitHub Actions static checks: PASS.
- Git whitespace validation: PASS on the publication tree.

The host prevented nesting `sandbox-exec`; offline/no-rating network boundaries are instead covered by explicit CLI modes and mocked source-call regression tests. CI performs the complete test/build path on Python 3.11.

Remaining P0/P1 issues: none.
