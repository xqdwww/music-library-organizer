# Dependency and License Audit

Audit date: 2026-07-16

## Runtime dependency

The only runtime dependency is `mutagen>=1.47,<2`. Version 1.48.1 was installed and exercised against generated MP3, FLAC, and M4A media. Its package metadata declares `GPL-2.0-or-later`.

No source from Mutagen is vendored. Because the application imports Mutagen at runtime, this project is licensed `GPL-2.0-or-later` rather than MIT. The full license text and third-party notice are included.

## Security audit

`pip-audit 2.10.1 --local --skip-editable` initially reported `PYSEC-2026-196` in the isolated environment's pip 26.1.1, not in a project runtime dependency. The audit environment was upgraded to the fixed pip 26.1.2 and the audit was rerun.

Final result: `No known vulnerabilities found`.

The historical platform/npm dependency chain is absent. The application has no JavaScript or npm dependency and no runtime dependency on FFmpeg. FFmpeg is an external test-only tool used to create synthetic silence.

Verdict: `DEPENDENCY_SECURITY_PASS_LICENSE_COMPATIBLE`
