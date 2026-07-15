---
type: "query"
date: "2026-07-09T00:26:46.957129+00:00"
question: "Where does audit-grade lineage/traceability precedent live in Linchpin?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Changeset", "Approval", "Deliverable", "PolicyResult"]
---

# Q: Where does audit-grade lineage/traceability precedent live in Linchpin?

## Answer

Only in the writeback plane: Changeset.content_hash (sha256), HMAC-signed Approval with approver+TTL, SqliteAuditLedger with applied_at. The calc engine (eoq/safety_stock/policies/classification) is deliberately metadata-free (frozen, clock-free, no hashes/versions) and Deliverable.data_sources/prepared are hand-typed strings. Audit evidence needs a read-path sibling of writeback primitives (EvidenceRecord), not a Changeset extension - see documentation/AUDIT_EVIDENCE_DESIGN.md

## Outcome

- Signal: useful

## Source Nodes

- Changeset
- Approval
- Deliverable
- PolicyResult