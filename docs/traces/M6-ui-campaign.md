# Runtime trace: owner notice campaign in the console (upload, match, draft, simulated send)

Observed on 2026-10-21 in Chrome against the production build of apps/web (`next start`, Next 16.4.2, dev auth as an admin) and the local API (uvicorn, Postgres 16.15). Browser times come from an in-page recorder that wrapped `fetch` for `/api/*` and polled the DOM every 20 ms; API times are the API's own log lines, joined by `x-request-id`. Same machine, UTC. Ids are shortened; no request or response body was recorded, and the registration rows (four synthetic owners: example.com and example.net emails, 555-01xx phones, one row with a 555-9999 phone) are not reproduced here. Sending is simulated: no message left the system. Nothing below is intended behavior; it is what was recorded.

The program is the open test program `7d91d93d` (models HDFS400 and a test model). The steps were driven by clicks on the page's own buttons with a pause between them.

| t (ms) | UTC | where | event |
|---|---|---|---|
| 0.2 | 17:34:58.411 | browser | recorder attached on `/app/programs/7d91d93d…/campaign`; current step: upload |
| 515.7 | 17:34:58.927 | browser | → `POST /api/v1/programs/7d91d93d…/registrations` (multipart, one CSV) |
| 528.5 | 17:34:58.939 | browser | every step button disabled (busy) |
| | 17:34:59.079 | API | `http_request POST …/registrations` 200, 108.0 ms (`c22190ae`) |
| 672.5 | 17:34:59.083 | browser | ← 200: 3 accepted, 1 rejected |
| 676.3 | 17:34:59.087 | browser | → `GET …/campaign` (summary re-read) |
| 691.0 | 17:34:59.102 | browser | upload result and the rejected-rows table shown (row 5: phone not synthetic) |
| | 17:34:59.112 | API | `http_request GET …/campaign` 200, 16.1 ms (`6285dd39`) |
| 709.6 | 17:34:59.120 | browser | ← 200 |
| 720.7 | 17:34:59.132 | browser | busy cleared; current step moves upload → match |
| 3022.9 | 17:35:01.434 | browser | → `POST …/campaign/match` |
| | 17:35:01.482 | API | `campaign_matched` matched 2, unmatched 1 |
| | 17:35:01.502 | API | `http_request POST …/campaign/match` 200, 50.1 ms (`ef16e507`) |
| 3094.8 | 17:35:01.506 | browser | ← 200 |
| 3108.5 | 17:35:01.519 | browser | "2 matched, 1 not matched." shown |
| | 17:35:01.558 | API | `http_request GET …/campaign` 200, 41.0 ms (`8fd1279e`) |
| 3176.5 | 17:35:01.587 | browser | busy cleared; current step match → draft |
| 5532.9 | 17:35:03.944 | browser | → `POST …/campaign/drafts` (email) |
| | 17:35:04.045 | API | `claim_links_issued` n 2 (one claim link per matched owner) |
| | 17:35:04.068 | API | `http_request POST …/campaign/drafts` 200, 89.3 ms (`c3437339`) |
| 5669.6 | 17:35:04.080 | browser | ← 200 |
| 5700.4 | 17:35:04.111 | browser | "New drafts: Email 1." shown |
| | 17:35:04.126 | API | `http_request GET …/campaign` 200, 16.7 ms (`bd41205f`) |
| 5723.0 | 17:35:04.134 | browser | busy cleared; current step draft → send; sample notice shown with a "claim link" marker in place of `{claim_link}` |
| 8720.2 | 17:35:07.131 | browser | "Mark 1 notice sent" clicked: confirmation (no message delivered; counts toward the monthly report) |
| 10130.6 | 17:35:08.541 | browser | → `POST …/campaign/send` (all channels) |
| | 17:35:08.975 | API | `campaign_sent` simulated, email 1 |
| | 17:35:09.102 | API | `http_request POST …/campaign/send` 200, 317.9 ms (`46c795da`) |
| 10704.2 | 17:35:09.115 | browser | ← 200 |
| 10766.1 | 17:35:09.177 | browser | confirmation removed; "Marked sent: Email 1." shown |
| | 17:35:09.491 | API | `http_request GET …/campaign` 200, 348.9 ms (`8bf6d4d3`) |
| 11092.0 | 17:35:09.503 | browser | ← 200 |
| 11195.1 | 17:35:09.606 | browser | busy cleared; no step is current (campaign done) |

Every step follows the same order: the POST's own answer is shown first (14 to 62 ms after it arrives), then the summary is read again, and only then are the buttons enabled and the current step moved. A second click cannot start while a summary read is in flight.

Only one of the two matched owners got an email draft because only one had an email address; the other had a phone, and text messages were not selected. The raw `{claim_link}` placeholder never appears on the page, and no notice holds a link.
