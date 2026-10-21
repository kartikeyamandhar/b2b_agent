# Runtime trace: keyboard navigation in the claims queue and on a claim

Observed on 2026-10-21 in Chrome against the production build of apps/web (`next start`, Next 16.4.2, dev auth as an admin of the demo firm) and the local API. The recorder logged each `keydown` in the capture phase and polled the DOM every 20 ms for the selected row and the open confirmation. Keys were real key presses sent to the browser window, not synthetic events. Ids are shortened. Nothing below is intended behavior; it is what was recorded.

Page: the flagged filter of the demo program's queue (`/app/programs/0874f66f…/claims?status=flagged`, 115 claims), then the first claim's page.

| t (ms) | UTC | event |
|---|---|---|
| 0.3 | 17:36:49.483 | recorder attached; no row selected |
| 2423.7 | 17:36:51.906 | key `j` |
| 2453.2 | 17:36:51.936 | row 1 selected (copper outline, `aria-current`) |
| 2722.5 | 17:36:52.205 | key `j` |
| 2741.7 | 17:36:52.224 | row 1 deselected, row 2 selected |
| 2987.3 | 17:36:52.470 | key `k` |
| 3001.9 | 17:36:52.485 | row 2 deselected, row 1 selected |
| 3256.7 | 17:36:52.739 | key `Enter` |
| 3292.1 | 17:36:52.775 | client route `/app/programs/0874f66f…/claims` → `/app/claims/06161d3e…` (the row's own link was followed) |
| 12936.7 | 17:37:02.419 | key `d` |
| 12996.2 | 17:37:02.479 | deny confirmation open, with the focus in its note field |
| 13396.6 | 17:37:02.879 | key `Escape` (pressed with the focus still in the note field) |
| 13426.8 | 17:37:02.909 | deny confirmation closed |

Each key took effect within 15 to 60 ms. No request was sent by `d` or `Escape`: the claim stayed `flagged` with only its `claim_adjudicated` audit row (checked through the API afterwards). Keys typed into a field or with Ctrl, Cmd, or Alt held are ignored by design (unit-tested in apps/web/tests/shortcuts.test.tsx).

An earlier run the same day found that `Escape` did nothing while the focus was in the note field, because the typing guard swallowed it; it was fixed before this trace (apps/web, commit `b97b09e`).
