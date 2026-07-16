# Code Provenance

Audit date: 2026-07-16

Each public implementation file was newly written for this candidate. “Private source” below identifies only the classification evidence; it does not mean code was copied.

| file | feature | source | author | original_or_third_party | original_commit_evidence | third_party_license | safe_for_publication | resolution |
|---|---|---|---|---|---|---|---|---|
| `src/music_library_organizer/__init__.py` | package version | clean-room candidate | project maintainer | original | new root history | none | yes | retain |
| `src/music_library_organizer/__main__.py` | module entry point | clean-room candidate | project maintainer | original | new root history | none | yes | retain |
| `src/music_library_organizer/errors.py` | user-facing error type | clean-room candidate | project maintainer | original | new root history | none | yes | retain |
| `src/music_library_organizer/cli.py` | scan/plan/apply/artwork CLI | clean-room candidate | project maintainer | original | new root history | none | yes | retain |
| `src/music_library_organizer/planner.py` | scan, JSON/CSV import, deterministic plan | clean-room candidate | project maintainer | original | new root history | none | yes | retain |
| `src/music_library_organizer/applier.py` | dry-run, preflight, staged copy, rollback | clean-room candidate | project maintainer | original | new root history | none | yes | retain |
| `src/music_library_organizer/media.py` | MP3/FLAC/M4A tags and local artwork | clean-room candidate using Mutagen API | project maintainer | original plus declared dependency | new root history | Mutagen GPL-2.0-or-later | yes | retain under GPL-2.0-or-later |
| `tests/test_cli.py` | synthetic media and safety tests | clean-room candidate | project maintainer | original | new root history | FFmpeg used externally for generation | yes | retain; no generated media committed |

## Private-source file resolution

| file | feature | source | author | original_or_third_party | original_commit_evidence | third_party_license | safe_for_publication | resolution |
|---|---|---|---|---|---|---|---|---|
| `adapter.py` | provider orchestration | private source | private project | mixed platform workflow | `13ee902`, `27cdda9` | internal-only repository | no | excluded |
| `provider.py` | account/provider subprocess | private source | private project | platform-specific | `13ee902` | internal-only repository | no | excluded |
| `downloader.py` | media download | private source | private project | platform-dangerous | `13ee902`, `27cdda9` | internal-only repository | no | excluded |
| `ncm.py` | encrypted-container conversion | private source | private project | decryption integration | `13ee902` | external tool separately MIT; wrapper internal-only | no | excluded |
| `nas.py` | remote/NAS delivery | private source | private project | private infrastructure | `13ee902` | internal-only repository | no | excluded |
| `tagging.py` | tags plus network artwork | private source | private project | mixed safe/unsafe | `13ee902` | internal-only repository | no | excluded and independently reimplemented |
| `validation.py` | media inspection and validation | private source | private project | mixed generic/private runtime | `13ee902`, `27cdda9` | internal-only repository | no | excluded and independently reimplemented |
| `pipeline.py` | download/decrypt/deliver pipeline | private source | private project | platform-dangerous | `13ee902` | internal-only repository | no | excluded |
| `batch.py` | resumable platform batch | private source | private project | platform-dangerous | `13ee902` | internal-only repository | no | excluded |
| `config.py` | credential and runtime config | private source | private project | credentials/private runtime | `13ee902` | internal-only repository | no | excluded |
| `evidence.py` | provider evidence and URL redaction | private source | private project | mixed with signed URLs | `13ee902` | internal-only repository | no | excluded |
| `manifest.py` | private batch state | private source | private project | workflow-specific | `13ee902` | internal-only repository | no | excluded |
| `album_compare.py` | local/provider comparison | private source | private project | mixed safe/unsafe | `13ee902`, `27cdda9` | internal-only repository | no | excluded |
| `models.py` | provider response envelopes | private source | private project | workflow-specific | `13ee902` | internal-only repository | no | excluded |
| `hashing.py` | hashing helper | private source | private project | generic but internal-only | `13ee902` | internal-only repository | no | excluded and independently reimplemented |
| `errors.py` | adapter errors | private source | private project | workflow-specific | `13ee902` | internal-only repository | no | excluded and independently reimplemented |

Dependency source is installed through package management; no Mutagen or FFmpeg source or binary is vendored.

Verdict: `PROVENANCE_COMPLETE_NO_PRIVATE_CODE_COPIED`
