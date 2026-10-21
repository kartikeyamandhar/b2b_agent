# Runtime trace: program creation, rule edit, open, and close in the console

Observed on 2026-10-21 in Chrome against the production build of apps/web (`next start`, Next 16.4.2, dev auth as an admin of the demo firm) and the local API (uvicorn, `LLM_MODE=replay`, `ADJUDICATOR=rules_only`, Postgres 16.15). Browser times come from an in-page recorder that wrapped `fetch` for `/api/*` and polled the DOM every 20 ms for the states named below; API times are the API's own `http_request` and decision log lines, joined to the browser by the `x-request-id` the web proxy sets. Both clocks are the same machine, in UTC. Program and claim ids are shortened to 8 characters; no request or response body was recorded. Nothing below is intended behavior; it is what was recorded.

The program is a test program created for this trace (`7d91d93d`, Hayward pool heaters, CPSC 26777) in the demo firm. It never held a submitted claim and was closed at the end.

## Create a draft from a recall

| t (ms) | UTC | where | event |
|---|---|---|---|
| 0.3 | 17:32:34.500 | browser | recorder attached on `/app/programs/new` |
| 7604.9 | 17:32:42.104 | browser | → `GET /api/v1/recalls?q=…&limit=25` |
| | 17:32:42.296 | API | `http_request GET /v1/recalls` 200, 83.4 ms (`660f886d`) |
| 7802.8 | 17:32:42.302 | browser | ← 200; results listed at 7821.0 |
| 11228.6 | 17:32:45.728 | browser | recall selected (Choose) |
| 17103.5 | 17:32:51.603 | browser | → `POST /api/v1/programs` |
| 17111.8 | 17:32:51.611 | browser | Create button disabled, label "Reading the notice…" |
| | 17:32:51.772 | API | `program_created`, extractor `baseline 1.0.0` (regex; no model call) |
| | 17:32:51.792 | API | `http_request POST /v1/programs` 201, 131.2 ms (`b16180cd`) |
| 17299.7 | 17:32:51.799 | browser | ← 201 |
| 17361.5 | 17:32:51.861 | browser | client route `/app/programs/new` → `/app/programs/7d91d93d…/settings` |
| 17686.4 | 17:32:52.186 | browser | "Draft program created" banner and the Open control rendered |

The draft is created by the API before the route changes; the settings page is server-rendered from a fresh `GET /v1/programs/{id}` (a server request, not in the browser's fetch log), 325 ms after the client route change.

## Edit the rules and open the program

| t (ms) | UTC | where | event |
|---|---|---|---|
| 31518.1 | 17:33:06.017 | browser | a model number typed: Save enabled (unsaved edit) |
| 36158.6 | 17:33:10.658 | browser | → `PATCH /api/v1/programs/7d91d93d…` (identification and remedy rules, threshold) |
| 36182.0 | 17:33:10.681 | browser | Save disabled while busy |
| | 17:33:10.807 | API | `http_request PATCH` 200, 80.3 ms (`32fd6ef4`) |
| 36311.4 | 17:33:10.810 | browser | ← 200; form rebased on the answer ("Rules saved.") |
| 40461.1 | 17:33:14.960 | browser | "Open this program now?" confirmation shown |
| 46257.0 | 17:33:20.756 | browser | → `PATCH /api/v1/programs/7d91d93d…` `{status: "open"}` |
| | 17:33:20.866 | API | `entitlement_checked` (`open_program`, enterprise plan) |
| | 17:33:20.905 | API | `http_request PATCH` 200, 84.4 ms (`771ec8a4`) |
| 46411.0 | 17:33:20.910 | browser | ← 200 |
| 46423.6 | 17:33:20.922 | browser | draft banner, Open control, and confirmation removed; Close control and "Program opened" notice shown |

The open is one request, and the page changes only after the API answered: there is no optimistic state. The plan check happens inside the PATCH, before the status changes (on a Free-plan firm the same click answered 402 and the page showed the plan message with a link to Billing; observed earlier the same day).

## Close the program, then try every link path

| t (ms) | UTC | where | event |
|---|---|---|---|
| — | before attach | browser | just before closing: `GET /api/portal/<token>` for the program's issued claim answered **200** |
| 0.1 | 17:35:59.150 | browser | recorder attached on the settings page (Close control, editable rules) |
| 5210.5 | 17:36:04.360 | browser | red "Close this program? This cannot be undone." confirmation shown |
| 14878.2 | 17:36:14.028 | browser | → `PATCH /api/v1/programs/7d91d93d…` `{status: "closed"}` |
| 14890.5 | 17:36:14.040 | browser | "Close program permanently" disabled while busy |
| | 17:36:14.161 | API | `http_request PATCH` 200, 54.6 ms (`39788053`) |
| 15017.7 | 17:36:14.167 | browser | ← 200 |
| 15031.0 | 17:36:14.181 | browser | confirmation and Close control removed; "Program closed." shown; Save bar removed; every rule field disabled |
| 17399.6 | 17:36:16.549 | browser | → `GET /api/portal/<token>` (the same link) |
| | 17:36:16.609 | API | `http_request GET /portal/<token>` **404**, 27.0 ms (`150e3ee8`) |
| 17464.8 | 17:36:16.614 | browser | → `POST /api/v1/programs/7d91d93d…/claims/issue` |
| | 17:36:16.636 | API | **409** "open the program before issuing claim links", 13.9 ms (`9390dbf7`) |
| 17492.7 | 17:36:16.642 | browser | → `POST /api/v1/claims/364f65a6…/link` |
| | 17:36:16.665 | API | **409** "the program is closed: its claim links stay dead", 16.9 ms (`dde75089`) |

The three requests after the close were sent by hand from the page to prove the backend refuses them. The console itself no longer offers them: the program overview shows "The program is closed: its claim links no longer work." with no issue control, and the claim page reads the claim's program and shows "The program is closed: its claim links no longer work and none can be issued." instead of a reissue button. The claim page gained that check in this verification: before it, the button was offered and the API's 409 was the first sign (fixed in apps/web, commit `efc921c`).

Database after the close: the program's `status` is `closed`, with `closed_at` set; its 3 draft claims are unchanged. The seeded demo program (`0874f66f`) was not touched.
