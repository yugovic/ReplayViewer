# Project instructions

## Chronological work log (mandatory)

This rule applies to the repository root and every nested project, including
`track-creator/` and `tracktools-kit/`.

1. Read `WORKLOG.md` before substantial investigation or implementation.
2. Record substantial work in `WORKLOG.md` with local time
   (`YYYY-MM-DD HH:MM`, Asia/Tokyo).
3. Keep the file newest-first, matching its existing convention. Within each
   entry, list milestones in actual chronological order.
4. A completed entry must state: request/purpose, decisions and evidence,
   changed or generated files, verification performed, and unresolved work.
5. Never claim a verification that was not run. Mark partial, failed, or
   cancelled work explicitly.
6. Do not write secrets, API keys, tokens, personal data, or raw credentials
   into the log.
7. Update the same entry when the task changes state; do not create misleading
   duplicate completion entries.

For an unrelated repository, copy
`docs/Codex-worklog-policy-template.md` to that repository's
`AGENTS.md`. A repository-local instruction cannot automatically govern
directories outside this repository.

## Imagery processing safety

- Google Map Tiles are display-only. Never download, cache, stitch, modify, or
  use them as super-resolution input.
- Only process imagery whose provenance and derivative-work licence are
  recorded in its metadata.
- AI-enhanced imagery is a visualization layer, not surveyed truth. Do not use
  it for geometry extraction, measurement, or validation ground truth.
