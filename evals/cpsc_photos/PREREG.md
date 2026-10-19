# Pre-registration: the vision tools on CPSC's own recall photos

Written 2026-10-19, before any image in this block was downloaded or looked at, and before labels or model calls.

## Why

Every photo the adjudication evals use is drawn by the project's renderer. On probe60 the vision check rejected the renderer's compliant destruction photos as "an abstract illustration" (evals/adjudication/PREREG.md, outcome section), so the evals cannot say whether `check_destruction_photo` is too strict on real photos or whether the drawings are unconvincing. This block runs the two vision tools, unchanged (claims/vision.py, VISION_VERSION 1.0.0, frozen with the ablation), on real photos that CPSC publishes with its recall notices.

It measures the tools one photo at a time. It is not an adjudication block, it decides no claim, and it is never kill-criterion evidence.

## Source and selection

Source: the `Images` (URL and caption) of the 1,657 cached CPSC recall records in data/raw/cpsc (2022-08-01 to 2026-10-16), downloaded from www.cpsc.gov (an approved source). The image files stay in data/raw/cpsc_images, which is gitignored; evals/cpsc_photos/manifest.json commits each URL, sha256, and the size the model receives, and data/raw/SOURCES.md records the download. Replay is keyed by sha256 and size, so CI needs no image file.

Three pools, built by code (recallops.evals.cpsc_photos), with at most one photo per recall in the first two:

- **label** (40 photos): per recall, the first image whose caption matches `label|labels|model number|model no.|model #|serial|date code` and does not match the destruction pattern below. Task: `read_label_photo`.
- **product** (20 photos): recalls whose notice yields a destruction instruction from the production regex extractor (rules/baseline.py); per recall, the first image whose caption starts with "Recalled" and matches neither pattern. Task: `check_destruction_photo` with that notice's instruction. These are expected to show an intact product.
- **destruction** (every match, 4 expected): images whose caption matches `cut|destroy|destroyed|destruction|disposal|dispose`, with the instruction from their notice (or "destroy the product", the production default, when the extractor finds none). Task: `check_destruction_photo`.

Order: candidates sorted by recall number and image index, then shuffled with `random.Random("20261019:<pool>")`. Photos are taken in that order until the pool is full. A photo is skipped, with the reason recorded in the manifest, when the download fails, the host is not CPSC's, it is larger than the portal's limits (MAX_PHOTO_BYTES, MAX_PHOTO_PIXELS), or Pillow cannot open it.

## Labels

Two labeling sub-agents work independently. Each sees only the image as the model receives it (after the production downscale in prepare_image), never the caption (captions often state the model number) and never the other labeler's answers. For product and destruction photos they also see the instruction.

- label pool: `legible` (a person can read a model number with confidence at this resolution) and `model_texts`, every string printed as a model number (marked MODEL, MOD., M/N, Model No., or clearly the model designation), copied exactly. An image with no readable model number gets `legible: false` and no texts.
- product and destruction pools: `shows_product` and `destruction_shown` (the photo clearly shows the instruction carried out).

The main session adjudicates every disagreement by looking at the image and checks caption model numbers against the labels. Agreement between the two labelers is reported. Labels are committed before any model call. Status: assistant-labeled; owner audit pending.

## Scoring

Codes are compared after `norm_code` (claims/tools/checks.py), the normalization the model check uses in production; exact string matches are reported alongside.

- **Readable labels** (adjudicated `legible` with at least one text): *read* when the model returns `legible` and a model text matching one of the texts; *misread* when it returns a text that matches none; *abstained* when it returns no text or `legible: false`.
- **No readable model number**: *hallucinated* when the model returns a model text; otherwise correct.
- **Product and destruction photos**: correct when the model's `complies` equals the adjudicated `destruction_shown`.

## Expectations

- E1: read rate on readable labels at least 0.80.
- E2: misread rate on readable labels at most 0.10 (a misread can deny a covered claim).
- E3: hallucination rate on photos with no readable model number at most 0.20.
- E4: `complies` false on at least 18 of the 20 product photos labeled as not showing destruction.
- E5: `complies` true on at least 3 of the destruction photos labeled as showing the destruction (counts only; the pool is too small for a rate).
- Spend: at most 0.05 USD for the live run, through the metered client with the per-run caps of invariant 4.

Misses are reported as misses, and neither the prompts nor the selection change after the run. A change to the vision tools would be a new VISION_VERSION with its own revision here.

## Revision 1 (2026-10-19, after labeling, before any model call)

- Selection: 64 photos, none skipped, from pools of 557 label, 466 product, and 4 destruction candidates. L03 and L08 are the same image (one product, two recall notices); the rule takes one photo per recall, not one per image, so both stay.
- Labels: the two blind labelers agreed on all 64 photos. The main session viewed every photo whose caption names a model number the labels do not, and every photo a labeler marked as doubtful, and changed one label: L09's only marker is the CPSC callout "Style/VPN", not a model marker, so it joins L05 (a SKU) as a photo with no readable model number. Final counts: 20 readable labels, 20 with no readable model number, 20 intact products, 3 destruction photos that show the instruction carried out, and 1 (D03, the seal being pried out, not yet cut) that does not. E5 therefore needs all 3.
- Scoring, diagnostic only: labels.json now records the other codes printed in each label photo (serials, SKUs, batch numbers), and each wrong model text is reported as either one of those codes in the wrong field or text printed nowhere in the photo. E2 and E3 keep their pre-registered definitions: a SKU returned as the model still counts against E3.

## Outcome (2026-10-19)

One live run: 64 calls (L08 replayed L03's recording, so 63 were recorded), 0.009425 USD charged and 0.009542 nominal, through the metered client and traced in agent_steps; the ledger went from 1.620841 to 1.630266 USD. Replay with every image file removed reproduces all 64 readings, so CI needs no image.

| expectation | result | |
|---|---|---|
| E1: read rate on readable labels at least 0.80 | 0.900 (18 of 20) | met |
| E2: misread rate on readable labels at most 0.10 | 0.050 (1 of 20) | met |
| E3: hallucination rate on photos with no readable model at most 0.20 | 0.100 (2 of 20) | met |
| E4: complies false on at least 18 intact product photos | 20 of 20 | met |
| E5: complies true on at least 3 of the 3 destruction photos showing it | 1 of 3 | missed |

What the photos show:

- **Labels.** The one misread (L39) is the right model copied with the angle brackets the label prints, "<PUA-US>". `norm_code` strips spaces, hyphens, and dots only, so in production `check_model` would call it a mismatch and deny a covered claim. The two model texts returned where no model number is readable are codes printed in the photo: the style number 177296 (L09) and "ITEM 61970" (L29). No answer was text printed nowhere in the photo. L40 abstained on a small FR730V badge.
- **Destruction.** The check rejected all 20 intact product photos and D03 (the seal pried out, not cut), but accepted only D02 of CPSC's three published examples of compliant destruction. It called D01's cut cord "intact", and said D04's straps were "still attached and intact" with "no trash can visible" while the helmet sits in a lined trash can with its cut straps inside. Three photos are weak evidence, but they agree with probe60: the check errs strict on real photos too, not only on the renders. Strict means needs_info, a request for another photo, never a payment.
- **A diagnostic change after the run, disclosed.** The split of wrong model texts first matched whole codes only and classed "ITEM 61970" as printed nowhere. It now also recognizes a printed code inside the answer, and the right model with extra characters. E1 to E5 are unaffected.

Next: a revised destruction prompt (VISION_VERSION 1.1.0), measured on this block and on probe60 before it is adopted; and `norm_code` stripping bracket and quote delimiters, which changes the frozen `check_model` and needs its own revision of the ablation freeze.
