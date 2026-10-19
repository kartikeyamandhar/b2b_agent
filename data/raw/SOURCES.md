# Data sources

Provenance for every downloaded file. data/raw/ is gitignored except this file; re-download from the URLs below to reproduce. All downloads used the User-Agent "RecallOps research (student project)". Times are UTC.

## 1. CPSC monthly progress report spreadsheet

- Page: https://www.cpsc.gov/Data/Monthly-Progress-Reports (link text "Download MPR Data - Recalls August 1, 2022 to Present")
- Page access: scripted requests to the page return HTTP 403 (Akamai "Access Denied") for this User-Agent; the link was read with a page-fetch tool on 2026-10-16. The block was not bypassed by imitating a browser.
- File URL: https://www.cpsc.gov/s3fs-public/Monthly-Progress-Report-September-Update.xlsx?VersionId=Je.HWAXA1.tTcx6l2G3ZXV.o60fY9gJe
- Saved as: data/raw/mpr.xlsx
- Downloaded: 2026-10-16T21:40:16Z, HTTP 200, content type application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
- Size: 129,372 bytes
- sha256: a559ef28ebaa4458dd4d6482c174e4c0909ecbac3508c42f83c922763928c3fa
- Contents (inspected read-only with Polars 2.0.0 and fastexcel 0.21.0): three sheets. "MPR": 38 rows, one per recall, cumulative totals as of September 21, 2026. "RecallsBetween 3.1.24 - 9.19.24": 123 rows (122 distinct recalls), recall list without counts, as of September 30, 2024. "MPR 08.1.22 and Post 08.1.2 (2)": 245 rows, one per recall, as of June 20, 2023. No month or period column. Verdict: no per-recall, per-month rows; the completion forecast is killed (CLAUDE.md decisions log).

## 2. CPSC recall API

- Endpoint: https://www.saferproducts.gov/RestWebServices/Recall?format=json&RecallDateStart=<date>&RecallDateEnd=<date>
- Pause between calls: 2 seconds.

### Sample fixture (committed)

- Query: RecallDateStart=2026-09-15, RecallDateEnd=2026-10-16
- Saved as: data/samples/cpsc_sample.json (raw response bytes, unmodified)
- Downloaded: 2026-10-16T21:39:37Z, HTTP 200
- Records: 51; size 159,562 bytes
- sha256: a5a8c2e4f198006c825045212ffaef2c2630fdb479023a877f1d629b744cc758

### Archive (gitignored)

- Range: 2022-08-01 to 2026-10-16 in 30-day windows (inclusive bounds), one file per window: data/raw/cpsc/<start>_<end>.json
- Pulled: 2026-10-16T21:39:40Z to 2026-10-16T21:41:55Z (137 s), every window HTTP 200
- Totals: 52 windows, 1,657 records, 5,242,511 bytes
- sha256 of the sorted per-file sha256 list (`cd data/raw/cpsc && shasum -a 256 *.json | shasum -a 256`): 1af3879a8a7a99f9d860f0b8118c217d2d995e38320ea9227bb9a5b04ecdfaa2

| window | records | bytes |
|---|---|---|
| 2022-08-01 to 2022-08-30 | 15 | 47849 |
| 2022-08-31 to 2022-09-29 | 30 | 85571 |
| 2022-09-30 to 2022-10-29 | 26 | 80925 |
| 2022-10-30 to 2022-11-28 | 19 | 67075 |
| 2022-11-29 to 2022-12-28 | 30 | 91353 |
| 2022-12-29 to 2023-01-27 | 27 | 94147 |
| 2023-01-28 to 2023-02-26 | 31 | 103335 |
| 2023-02-27 to 2023-03-28 | 30 | 104605 |
| 2023-03-29 to 2023-04-27 | 35 | 114512 |
| 2023-04-28 to 2023-05-27 | 25 | 81615 |
| 2023-05-28 to 2023-06-26 | 23 | 72685 |
| 2023-06-27 to 2023-07-26 | 14 | 42597 |
| 2023-07-27 to 2023-08-25 | 23 | 76501 |
| 2023-08-26 to 2023-09-24 | 25 | 81341 |
| 2023-09-25 to 2023-10-24 | 20 | 63773 |
| 2023-10-25 to 2023-11-23 | 28 | 94295 |
| 2023-11-24 to 2023-12-23 | 43 | 127865 |
| 2023-12-24 to 2024-01-22 | 15 | 49000 |
| 2024-01-23 to 2024-02-21 | 31 | 96441 |
| 2024-02-22 to 2024-03-22 | 44 | 142567 |
| 2024-03-23 to 2024-04-21 | 27 | 93908 |
| 2024-04-22 to 2024-05-21 | 28 | 89786 |
| 2024-05-22 to 2024-06-20 | 29 | 96855 |
| 2024-06-21 to 2024-07-20 | 22 | 71878 |
| 2024-07-21 to 2024-08-19 | 29 | 92874 |
| 2024-08-20 to 2024-09-18 | 14 | 45313 |
| 2024-09-19 to 2024-10-18 | 20 | 64213 |
| 2024-10-19 to 2024-11-17 | 24 | 77246 |
| 2024-11-18 to 2024-12-17 | 17 | 53651 |
| 2024-12-18 to 2025-01-16 | 21 | 63363 |
| 2025-01-17 to 2025-02-15 | 30 | 99131 |
| 2025-02-16 to 2025-03-17 | 40 | 134013 |
| 2025-03-18 to 2025-04-16 | 37 | 117983 |
| 2025-04-17 to 2025-05-16 | 38 | 122219 |
| 2025-05-17 to 2025-06-15 | 35 | 117516 |
| 2025-06-16 to 2025-07-15 | 31 | 101272 |
| 2025-07-16 to 2025-08-14 | 44 | 140195 |
| 2025-08-15 to 2025-09-13 | 15 | 47591 |
| 2025-09-14 to 2025-10-13 | 28 | 87961 |
| 2025-10-14 to 2025-11-12 | 49 | 150118 |
| 2025-11-13 to 2025-12-12 | 45 | 136436 |
| 2025-12-13 to 2026-01-11 | 31 | 95620 |
| 2026-01-12 to 2026-02-10 | 54 | 156878 |
| 2026-02-11 to 2026-03-12 | 51 | 148685 |
| 2026-03-13 to 2026-04-11 | 50 | 157613 |
| 2026-04-12 to 2026-05-11 | 55 | 160331 |
| 2026-05-12 to 2026-06-10 | 40 | 121378 |
| 2026-06-11 to 2026-07-10 | 54 | 165789 |
| 2026-07-11 to 2026-08-09 | 53 | 161982 |
| 2026-08-10 to 2026-09-08 | 49 | 155836 |
| 2026-09-09 to 2026-10-08 | 57 | 178820 |
| 2026-10-09 to 2026-10-16 | 6 | 18005 |

## 3. Recall Bench CSV (comparison set)

- URL: https://recallbench.com/data/cpsc-recalls-full.csv (not government-affiliated)
- Saved as: data/raw/recallbench/cpsc-recalls-full.csv
- Downloaded: 2026-10-16T21:40:23Z, HTTP 200, content type text/csv
- Size: 6,847,220 bytes; 8,445 lines including the header
- sha256: 269d75082641c609e81bf2dd45ac6e29a71760450dfeebbbeb0a477687bc3e9f
- Columns: recall_date, recall_number, title, products, hazard, remedy, retailers, units, url
- Comparison against the API archive (2026-10-16, `python -m recallops.ingest.recallbench --csv data/raw/recallbench/cpsc-recalls-full.csv --archive data/raw/cpsc`): 30 rows sampled at seed 20261016 from Recall Bench rows dated inside the archive window (2022-08-04 to 2026-10-15); recall number (digits only), recall date, and canonicalized title compared with the API record. Result: 0 of 30 missing from the API, 0 number, 0 date, 0 title mismatches. Mismatch rate 0.0 percent (stop threshold 2 percent).

## 4. CPSC recall photos (vision block)

- Source: the `Images` URLs of the cached CPSC records in section 2 (www.cpsc.gov), chosen by the seeded rule in evals/cpsc_photos/PREREG.md (`python -m recallops.evals.cpsc_photos select`)
- Saved as: data/raw/cpsc_images/<sha256>.<ext> (gitignored); evals/cpsc_photos/manifest.json commits every URL, caption, sha256, byte size, and the size the model receives
- Downloaded: 2026-10-19T20:45:14+00:00, HTTP 200 for all 64 photos, one request at a time with the project User-Agent; 0 skipped
- Size: 16,271,458 bytes in total (42 PNG, 19 JPEG, 3 GIF)
- Use: images CPSC publishes with its recall notices; the files are not redistributed by this repository
