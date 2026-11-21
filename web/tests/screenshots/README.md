# Screenshot sets

Each subfolder (`m3/`, later the MC3 set and `final/`) holds one set of reference captures approved by the USER, one PNG per state of `web/tests/e2e/screenshots.spec.ts` (`home.png`, `shape-empty.png`, `shape-image.png`, `keys-sheet.png`, `export-menu.png`, `mode-guard.png`, `fatal.png`, `study-placeholder.png`). Only the lead writes into a set folder, and only after the USER's approval; no test or tool writes here.

## How captures are made

`npm run test:shots` runs the `shots` Playwright project: headless Chromium at 1440 × 900, device pixel ratio 1, reduced motion, animations off, fonts loaded (`document.fonts.ready`), the build identity in the status line (version and hashes) masked with a solid box, so captures do not change between builds of the same app. A state the build cannot reach yet is skipped with the reason; `SARCOSTAT_SHOTS_REQUIRE=1` turns such a skip into a failure.

## Modes

- Dev machine, reference present: the capture must equal `web/tests/screenshots/<set>/<name>.png` with 0 differing pixels (set from `SARCOSTAT_SHOTS_SET`, default `m3`); a difference writes `<name>.diff.png` (differing pixels in red) next to the capture in the Playwright output folder.
- No reference, or `CI` set: the capture is written to the Playwright output folder (`test-results/<lane>/artifacts/`) and attached to the report; a missing reference never fails the run.
- Candidate run: `SARCOSTAT_SHOTS_CANDIDATE=<folder>` also writes every capture into `<folder>` and compares nothing; a folder under `web/tests/screenshots/` is refused.

## Approval procedure

1. Capture candidates twice and check that the two runs agree (plan M3-6 acceptance 1), from `web/` with the usual environment: `LANE=<lane> PORT=0 SITE_DIR=<site root> node tests/screenshots/shots.mjs twice --out <scratch>/shots-<set>`; it runs `npm run test:shots` in candidate mode into `run1/` and `run2/` and passes only when every PNG is byte-identical or differs by 0 pixels (`node tests/screenshots/shots.mjs compare <run1> <run2>` repeats the comparison).
2. The lead shows the candidates of `run1/` to the USER, who approves or rejects each state.
3. After the approval, the lead copies the approved PNGs into `web/tests/screenshots/<set>/` and records the DECISIONS id of the approval in the commit path list.
4. From then on `npm run test:shots` (with `SARCOSTAT_SHOTS_SET=<set>` for a set other than `m3`) compares every capture with the set on the dev machine (V-SHOTS); a new approval replaces the set the same way.
