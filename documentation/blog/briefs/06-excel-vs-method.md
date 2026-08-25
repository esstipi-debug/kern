# Brief 06 — pair `excel-vs-method`

**Claude writes:** `documentation/blog/es/excel-vs-metodo.md` and
`documentation/blog/en/excel-inventory-method.md`.

Calendar C1 / bible §2.1. TOFU. Do not turn it into a 3,000-word methodology dump
(that is pair 4). This is **why the spreadsheet fails as the system of record
for the policy**, not a second safety-stock lecture.

## Queries

| | ES | EN |
|---|---|---|
| Primary | inventario en Excel | inventory planning in Excel |
| Secondary | min max en la planilla | spreadsheet reorder point |
| Not this article | full (s,Q) derivation (pair 4); Stocky (pair 2) | |

## Persona

Owner-operator whose "ERP" is a shared workbook. They are not searching "Vandeput".

## Core claim

Excel is a fine **export and review surface**. It is a bad place to **keep** the
policy: no memory of last cycle's error, no σ_e, no simulation-before-recommend,
one person who understands the formulas, breaks at SKU #4,000 / second warehouse
(bible §1–2).

Kern can **write back to the client's workbook** via the Excel connector
(`src/connectors/excel.py`): drift check, atomic replace, backup, cell-exact
rollback, `.xlsm` macros preserved but **never executed**. That is a selling
point for EN/ES "we are not asking you to leave Excel on day one" — only if you
read the connector docstring and do not claim features that are not there.

Public `/demo` accepts CSV/Excel upload for the scan — read-only analysis, not
the writeback connector. Do not conflate demo upload with writeback.

## Required sections

1. What Excel is good at (review, sharing a deliverable).
2. Failure modes: the cell someone can delete (C1); σ of sales vs σ_e; no audit
   of who changed min/max; second location.
3. Compared-to table (short): Excel alone / ERP min-max / SaaS dashboard / Kern
   as method-running-on-your-file. No rival $ except labelled market anchors
   from bible §2 if you mention SaaS at all.
4. What Kern does not do: "rip out Excel next week"; execute VBA; become your
   WMS.
5. FAQ:
   - Do I have to migrate off Excel to start?
   - Can Kern edit my workbook?
   - Is a CSV enough for the free scan?
   - Will my macros run? (**No** — preserved, not executed.)
   - What breaks at a second warehouse? (point to multi-echelon as a **later**
     package/tool, do not pretend the demo runs DRP.)

## Sources

- `kind: code` `src/connectors/excel.py` (only for the writeback paragraph;
  skip if you decide not to mention writeback — then cite `src/forecasting.py`
  or `src/excess_obsolete.py` for the method contrast)
- Book: optional; a light Chopra/Christopher cite only with a graph hit. This
  piece can be code + bible framing.

## CTA

`cta: demo` · `offer: diagnostico-arranque` · `/demo`
"Send the export you already have."

## Landmines

- Do not mock the reader for using Excel (they all do).
- Do not claim Kern "never uses Excel" (deliverables **are** Excel + report +
  chart).
- SKU 4,000 is a bible motif — illustrative scale, not a measured client.

## Transcreation notes

ES: "la planilla del dueño". EN: "the Google Sheet the 3PL cannot see". Same
failure modes.
