# Labeling guide: identification and remedy rules

You label one recall notice at a time. You see only the notice's source_text (sections TITLE, DESCRIPTION, PRODUCTS, SOLD AT, REMEDY, HAZARD, INCIDENTS, FIRMS, CONTACT, UPC). Label what the notice says, not what seems likely. Every value you give comes with a quote: an exact, character-for-character substring of the notice that supports it. Copy quotes; never retype or paraphrase them. Keep quotes short (the smallest span that supports the value).

Answer every field. Use null or an empty list when the notice does not state it. Put anything you are unsure about in notes.

## Identification

- models: every model number, model name code, or item number the notice says identifies the recalled units, exactly as printed (keep hyphens, spaces, letters). Do not include brand names, product names without a code, UPCs, or SKUs that the notice does not present as model or item numbers. Quote: the code itself.
- model_label_location: where the consumer finds the model number ("on a sticker on the bottom of the laminator"), as printed, or null.
- serial_ranges: each serial-number range or prefix rule. prefix: the required leading characters if the notice says "beginning with"; start and end: the inclusive bounds if given; raw: the quote. Example: "serial numbers 2101 through 2412" gives {prefix: null, start: "2101", end: "2412"}.
- date_codes: each manufacture-date or date-code rule. format: the format the notice describes ("MMYY", "YYYY-MM", "date code"); start and end as printed in the notice's own format, or null; raw: the quote.
- sold_at: each retailer or channel named as a place of sale, one entry per retailer ("Amazon.com", "Walmart stores nationwide" gives "Walmart"; "Target.com" stays "Target.com"). Quote: the retailer's name as printed.
- sold_from, sold_to: the sale window as YYYY-MM-DD. When only a month is given, sold_from is the first day of that month and sold_to the last day of that month. When only a year is given, use January 1 and December 31. Quote: the date words ("June 2024").
- price_range_cents: {min_cents, max_cents} from "for between $44 and $56" (4400, 5600) or "for about $30" (3000, 3000). Quote: the price phrase.
- units_affected: the number of units sold in the United States as an integer (exclude Canada and Mexico; "About 1.2 million" is 1200000). null if the notice gives several product quantities without a US total. Quote: the number as printed.

## Remedy

- remedy_type: refund, repair, replacement, or multiple (more than one of these offered). Quote: the phrase that states it.
- options: one entry per remedy offered. type (refund, repair, replacement); steps (short imperative strings in the notice's order, for example "register online", "confirm model", "cut the power cord", "email a photo"); proof_required (any of: label_photo, destruction_photo, receipt, product_return, registration, none); payout_method (check, original_payment, store_credit, or null); amount_rule (for example "full refund", "purchase price", "prorated", or null).
- destruction_instruction: the instruction for disabling or destroying the product before the remedy ("cut the cord", "write RECALLED on the product"), quoted as printed, or null. A photo-only requirement without destruction is not a destruction instruction.
- registration_url: the recall registration or claim URL as printed, or null.
- contact: the firm's consumer contact phone number or email as printed, or null.

## Notes

One or two sentences on anything ambiguous: values you left out on purpose, values that might be wrong, or fields the notice contradicts itself on.
