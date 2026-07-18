# Privacy and Secret Audit

Audit date: 2026-07-19

Scopes: current source, tests, fixtures, examples, documentation, GitHub configuration, package manifest, generated distribution inventory, and the seven local commits not present on `origin/main`.

## Current publication tree

- No private absolute path, home-directory identity, Hermes/NAS/SMB path, private IP, SSH material, credential value, cookie, API key, password, subscription URL, or authorization value is present.
- `DISCOGS_TOKEN` is only an environment-variable name. The value is placed only in an HTTPS request header and is not logged, cached, serialized, or included in URLs.
- No audio, artwork, database, generated media, or file larger than 1 MiB is included.
- Fixtures are synthetic or minimal public open-data shapes. Personal library reports, inventory counts, directory names, paths, ratings, and review decisions are excluded.
- Runtime SQLite, cache, reports, selections, plans, and quarantine state are ignored and documented as private.
- Apple/NetEase curator inputs are read only from user-supplied local exports; personal usage, source paths, and local library paths remain in mode-restricted ignored state and are never sent to a service.

## History disposition

The seven unpublished local commits contain private-library aggregate reports and a removed website scraper. They must not be pushed. Publication is constructed as one clean fast-forward commit directly on the public `origin/main` ancestry. The local commits remain preserved locally and are not rewritten or deleted.

Verdict: `PRIVACY_AND_SECRET_AUDIT_PASS_FOR_CLEAN_PUBLICATION_HISTORY`
