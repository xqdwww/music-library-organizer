# Personal Pruning Policy

Status: `PASS_PERSONAL_PRUNING_POLICY_APPLIED_READY_FOR_FINAL_USER_SELECTION`

The active personal policy is an evidence-preserving overlay on the full-library review state. It does not modify
`music_score`, the original candidate classification, media tags, paths, or files. It is enabled only after every
album in a named calibration batch has a non-`UNREVIEWED` decision.

## Candidate groups

- `STRONG_PERSONAL_CANDIDATE`: score at or below the strong threshold, at least the configured number of independent
  sources, controlled album match, no insufficient-data state, no professional protection, and no source conflict.
- `PERSONAL_REVIEW_CANDIDATE`: score above the strong threshold and at or below the review threshold, with the same
  evidence and controlled-match requirements. Source conflict is visible here for human review.
- `USER_SELECTED_CANDIDATE`: an explicit calibration `DELETE_CANDIDATE`. This group is independent of score, so a
  high-scoring or unscored user choice is retained.
- `LATER`: a deferred calibration decision. It is visible but cannot be selected for a cleanup plan.

`KEEP` always suppresses score-derived candidacy. Professional or permanent protection suppresses every candidate
group, including an older explicit delete label. Candidate groups may overlap; summaries deduplicate by local album
ID. Every returned row has `checked: false`. Applying a policy creates neither a selection nor a delete/quarantine
batch. The user must explicitly check albums in the review UI before the existing selection and preview workflow can
begin.

## Commands

```console
music-organizer album-prune --config config.local.json personal-policy-apply \
  --batch-id <completed_batch_id> --strong-threshold 65 --review-threshold 70
music-organizer album-prune --config config.local.json personal-candidates
music-organizer album-prune --config config.local.json serve
```

Runtime policy YAML, candidate JSON, feedback, paths, scores, and the SQLite database remain ignored local state and
are never committed.
