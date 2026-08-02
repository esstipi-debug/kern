# External data sources for Kern — verified research (2026-07-24)

Research across 6 lanes, 65 candidate sources, each accessible candidate
verified by actually fetching its URL / pricing / licence page. Ranked by the
**revenue** priority (land or serve a client), not technical interest.

## Top 8 to pursue

| # | Source | Feeds | Access / cost / licence | Value |
|---|--------|-------|--------------------------|-------|
| 1 | **GDELT DOC 2.0 API** | `risk` | Free, no key, multilingual (ES/PT). Metadata only — clean to resell insight with attribution. | **LAND** |
| 2 | **Google Merchant Center Price Competitiveness** (Merchant API + BigQuery) | `price_intelligence` / `repricing` | Free; pulled from the client's OWN account → zero scraping, zero legal risk. Per-client only, don't pool. | **LAND** |
| 3 | **USITC HTS REST API** | `landed_cost` | Free, US public-domain, JSON/CSV of HS codes + duty rates. True duty = base line + Chapter 99 (Section 301/232) overlay JOIN. | **SERVE** (+LAND US importers) |
| 4 | **Australian Business Register (ABN Bulk Extract)** | `supplier_management` / `sourcing` | Free, CC BY 3.0 AU (commercial OK), weekly. | **LAND — beachhead geo** |
| 5 | **GLEIF LEI data + API** | `supplier_management` / `sourcing` | Free, **CC0** (cleanest licence available), global. | **SERVE** (entity-resolution plumbing) |
| 6 | **Open Icecat** + **MercadoLibre official API** | homologation / `price_intelligence` | Both free. Icecat: 18M GTIN/EAN datasheets, commercial-clean → attacks SKU matching. MELI: register OAuth for all calls; price endpoints are seller-scoped. | **SERVE** |
| 7 | **IMF PortWatch** | `risk` / `network_design` / `landed_cost` | Free open API, global chokepoints, **weekly**. LICENCE LANDMINE: IMF terms = all-rights-reserved; commercial embed needs `copyright@imf.org` permission OR cite-only framing. | **SERVE + LAND (attributed)** |
| 8 | **openFDA + CPSC Recalls APIs** | `supplier_management` / `risk` | Both free, US public-domain, commercial-clean. US-only; UPC often empty → match on manufacturer/brand text. | **LAND + SERVE** |

**Supporting free layer** (wire opportunistically, not headline): NY Fed GSCPI
(macro-pressure one-number garnish; legacy `.xls`, parse with xlrd), World Bank
Pink Sheet + Indicators API (CC-BY, input-cost + macro), FRED (public-agency
series only — filter out copyrighted ISM/PMI), GDACS + USGS (free real-time
hazard geofencing around supplier locations).

## Highest-leverage single move

**GDELT DOC 2.0 → the `risk` tool, packaged as a free "Supplier Disruption
Exposure Scan."**

Pitch: *"Send me your top-20 suppliers and their locations. In 24h I'll show you
every disruption event — strikes, plant fires, port closures, regulatory
actions, unrest — detected around them in the last 90 days, in local-language
news your team never sees."*

Why this over the (arguably stronger) pricing demo:
- **Closes Kern's most glaring engineering gap.** The `risk` tool reads only a
  client-authored register with no external feed; the just-ingested resilience
  book added a whole supplier-risk-tower concept layer with nothing feeding it.
  This wires the gap AND creates the hook in one move.
- **Zero cost / key / legal friction.** Metadata not article text → clean to
  surface with attribution.
- **Only hook that works before the client trusts you.** The Google Merchant
  hook needs account access first; GDELT needs only a supplier list.
- **Multilingual = real moat for the ICP.** ES/PT disruption coverage is exactly
  where a US-centric incumbent, a spreadsheet, or Stocky-tier tools are blind.
- **The free 70% of a ~$12k/yr Resilinc EventWatch.**

Pair with GDACS + USGS geofencing as the paid "live upgrade" (red-alert cyclone
within 50km of a Tier-1 supplier), so the same tool has a LAND signal and a
SERVE signal.

## Do NOT bother

- **ACLED** — EULA forbids monetizing secondary products. GDELT covers most of
  the unrest signal free-to-resell.
- **OpenSanctions** — CC BY-NC; commercial surfacing needs the paid Reseller
  License. Defer until a paying compliance client.
- **UN Comtrade / WITS / Eurostat Comext** — redistribution-restricted (internal
  use only; Comext commercial carve-out excludes US/China/Japan slices). Insight
  generation only, never re-display raw values. Redundant with USITC/Census/ABR
  for the ICP geos anyway.
- **ImportGenius / Panjiva / OpenCorporates** — enterprise/paid; BOL vendors
  inherit US CBP manifest confidentiality + resale limits. Wrong stage at 0
  clients. Free ABR (AU) + Companies House (UK) cover the priority geos.
- **Resilinc EventWatch** — not a source, the buy-vs-build benchmark. Undercut
  with GDELT+PortWatch+GDACS.
- **Freightos FBX / Drewry WCI / ISM & S&P PMI** — proprietary,
  redistribution-restricted. Headline figures directionally in narrative only;
  never resell the series.
- **ImportYeti BOL** — free tier to WIN a demo ("here's who your competitor
  imports from"); do NOT build a redistributed data product on it.
- **Kaggle Corporación Favorita / H&M** — real and rich but licence-blocked for
  commercial client-facing use; internal-capability-proof only.

## New demo datasets that ARE commercially usable

- **dunnhumby "The Complete Journey"** — free bulk CSV, household transactions +
  campaigns/coupons/promotions → showcases promo-aware forecasting.
- **Rohlik Sales Forecasting v2 (Kaggle)** — European e-grocery, perishable/FEFO
  territory (confirm the competition's data licence before shipping).
- **Open Food Facts** — nightly bulk export, CC — product/barcode reference for
  SKU enrichment (grocery).

## L3 knowledge additions worth ingesting (vs. redundant)

Worth it (non-redundant, citable, legally ingestible free):
- **GS1 System of Standards** (GTIN/GLN/SSCC/EPCIS) — grounds traceability;
  free PDFs, IP policy permits reference use.
- **GHG Protocol Scope 3** (WRI/WBCSD) — grounds a future carbon/Scope-3
  capability; free PDFs.
- **CSCMP SCM Terms & Glossary** — free PDF; canonical terminology layer.
- **UNSPSC** — commercial spend taxonomy for sourcing/spend-analysis.

Redundant — skip: another classical inventory/forecasting/pricing/ops text
(Vandeput, Hyndman, Nagle, Chopra, Heizer, Vollmann already saturate these).
