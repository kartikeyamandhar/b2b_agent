"""Owned by the payments owner (component 3, CODEOWNERS). Placeholder only.

This is the one module allowed to import both stripe and the payouts ledger (03-payments.md section 1). It
creates Connect Express accounts, sends transfers with idempotency_key = payouts.id, and calls the
PayoutLedger methods (recallops.payouts.ledger) as Stripe events arrive. Until it exists, the backend uses
recallops.payouts.fake.FakeLedger.
"""
