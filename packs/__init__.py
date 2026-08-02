"""Domain packs built on ``nosilo_core``.

A pack declares its own entity types, its own field classifications and its own
capabilities, and gets identity, policy, canonical storage, the safe-staging
write plane, the QA veto and the never-unprotected outcome contract from the
core without reimplementing any of them.

``packs/source_to_pay``
    Accounts payable: three-way match of invoices against purchase orders and
    goods receipts.

The supply-chain pack lives in ``src/`` rather than ``packs/supply_chain/``
because it predates this convention and is the larger half of the repository.
Renaming it would touch every import in ~170 files for no functional change, so
it stays put; ``src/entities.py`` is its pack declaration and reads exactly like
``packs/source_to_pay/entities.py``.
"""
