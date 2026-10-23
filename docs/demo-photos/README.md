# Demo photos

Synthetic photos for filing a claim against the demo product on the live site (the Scotch thermal laminator recall, CPSC 27024). Each kit is one claim: upload its label, destruction, and receipt photos, and enter:

| Kit | Model | Retailer | Purchase date |
|---|---|---|---|
| 1 | TL1302Z | Amazon.com | 2025-02-08 |
| 2 | TL1302Z | Amazon.com | 2025-09-30 |
| 3 | TL1302Z | Amazon.com | 2026-01-12 |

Make up the name, an `example.com` email, and the street address. A kit can be approved once: a second claim with the same photos, email, or address is flagged as a likely duplicate.

The labels and receipts come from the project's renderer (`apps/api/src/recallops/synth/render.py`). The destruction photos were drawn for the live demo with a wider cut and bare copper at both ends, because the vision check rejects many of the renderer's schematic cut cords ([evals/adjudication/PREREG.md](../../evals/adjudication/PREREG.md)).
