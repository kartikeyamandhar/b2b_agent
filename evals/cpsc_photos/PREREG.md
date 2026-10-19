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

## Outcome

(Filled in after the run.)
