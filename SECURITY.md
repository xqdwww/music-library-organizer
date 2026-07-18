# Security Policy

Security fixes are supported for the latest release. Report vulnerabilities privately with GitHub's **Report a vulnerability** feature. Do not include private music, credentials, or personal filesystem paths in a public issue.

The organization commands are local-only. Optional `album-prune` rating commands contact documented public metadata and award sources unless `--no-ratings` or `--offline` is used. They send album identity metadata, not media content or local paths. An optional `DISCOGS_TOKEN` is read from the process environment and must never be committed or included in an issue.

Treat plan files, the album-review SQLite database, response caches, and reports as private because they contain library metadata and local paths. Review plans before any apply operation, work from backups, and do not run the tool with elevated privileges.

The review web controls must remain bound to loopback. They validate Host, Origin, CSRF tokens, request sizes, and untrusted public-source values. Do not place them behind a proxy or expose their ports to another device.
