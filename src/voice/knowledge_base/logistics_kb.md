# Kern Voice Agent — Logistics Knowledge Base (RAG corpus)

Curated, authoritative reference the voice agent retrieves from during a call. Keep it
tight — RAG returns only the relevant chunks per turn. General, slow-changing knowledge
lives here; volatile shipment facts (container #, ETA, last free day) come from tools /
dynamic variables, never from this file.

Sources: ICC Incoterms® 2020; CSCMP Glossary; ASCM Supply Chain Dictionary; US CBP (ISF 10+2, Form 7501); IATA Resolution 600a.

---

## Incoterms 2020 (11 rules — who bears cost/risk, and the duty base)

**Any mode:** EXW, FCA, CPT, CIP, DAP, DPU, DDP. **Sea/inland waterway only:** FAS, FOB, CFR, CIF.

- **EXW** — Ex Works. Buyer bears almost everything from the seller's door. Duty base: goods.
- **FCA** — Free Carrier. Seller delivers to the named carrier/place; risk passes there. Duty base: goods.
- **FAS** — Free Alongside Ship. Seller delivers alongside the vessel at the port.
- **FOB** — Free On Board. Risk passes when goods are on board at the load port. Duty base: goods.
- **CFR** — Cost and Freight. Seller pays freight to destination port; risk passes at load port.
- **CIF** — Cost, Insurance & Freight. CFR + seller buys (minimum) marine insurance. **Duty base: goods + freight + insurance.**
- **CPT / CIP** — Carriage (and Insurance) Paid To. Multimodal equivalents of CFR/CIF; CIP requires higher (Institute Cargo Clauses A) insurance.
- **DAP** — Delivered At Place. Seller bears risk to the named destination (not unloaded).
- **DPU** — Delivered at Place Unloaded (renamed from DAT in 2020). Seller unloads at destination.
- **DDP** — Delivered Duty Paid. Seller bears everything including import duty/clearance.

Rule of thumb: C-terms = seller pays main carriage but risk passes early (origin); D-terms = seller bears risk to destination.

---

## Time & transport terms

- **ETD / ETA / ATD / ATA** — estimated/actual time of departure/arrival.
- **Transit time** — port-to-port (or door-to-door) duration.
- **Cut-offs** — deadlines before vessel departure: **VGM** (verified gross mass), **SI / doc cut-off** (shipping instructions), **gate-in / CY cut-off** (container into terminal). Missing a cut-off = rolled to the next sailing.
- **Roll / blank sailing** — container bumped to a later vessel / a cancelled sailing.

## Demurrage vs Detention vs Per-diem (and free time)

- **Free time** — the grace days a carrier/terminal allows before charges start.
- **Demurrage** — charge for a container sitting **inside the terminal/port** past free time (import: not picked up; export: gated in too early).
- **Detention** — charge for keeping a container **outside the terminal** (at your yard) past free time, i.e. holding the equipment.
- **Per-diem** — daily equipment-use charge, often used interchangeably with detention for the box itself.
- **Last free day (LFD)** — the date demurrage starts; the single most important field on an arrival notice. Always surface it.

## Containers & modes

- **FCL** — full container load. **LCL** — less-than-container load (consolidated, deconsolidated at destination CFS).
- Sizes/types: **20' / 40' / 40' HC (high cube) / 45'**, **reefer** (refrigerated), **flat rack**, **open top**, **tank**.
- **Drayage** — short-haul truck move (port↔yard/ramp). **Transloading** — moving cargo from a container to trailers. **Intermodal** — rail + truck.
- **LTL / FTL / parcel** — less-than-truckload / full-truckload / small-parcel. LTL uses **NMFC freight class** and a **PRO number**.
- **Accessorials** — extra charges: liftgate, residential delivery, inside delivery, detention at the dock, redelivery, reweigh.

## Documents (when each governs)

- **Bill of Lading (B/L)** — ocean transport contract + title document when negotiable. **MBL** (carrier→forwarder) vs **HBL** (forwarder→shipper). **Sea Waybill** — non-negotiable, no original needed (Express/Telex release).
- **Air Waybill (AWB)** — air; **never** a title document. **MAWB** (airline→forwarder) vs **HAWB** (forwarder→shipper). Number = 3-digit airline prefix · 7-digit serial · 1 check digit.
- **Commercial invoice + packing list** — the customs/value + the carton/weight detail.
- **ASN (EDI 856)** — advance ship notice; hierarchy Shipment→Order→Tare(pallet SSCC)→Pack(carton)→Item.
- **POD** — proof of delivery; exceptions must be noted **before** signing.

## Customs (US)

- **ISF "10+2" (Importer Security Filing)** — 10 importer data elements filed **≥24h before vessel loading**; penalties for late/incorrect.
- **Entry / CBP Form 7501 (Entry Summary)** — declares HTS codes, entered value, duty, MPF, HMF.
- **Hold / exam** — CBP may hold for document review or physical/X-ray exam. Classification & duty questions go to a **licensed customs broker** — the agent never advises on HTS/duty.

## OS&D (Over, Short & Damaged)

- **Over** — more received than billed. **Short** — fewer. **Damaged** — visible or **concealed** (found after delivery).
- Discipline: note the exception on the POD **before signing**, photograph it, and file the claim within the carrier's window. The agent **intakes** OS&D facts only — it never admits liability or settles.

## Acronyms quick list

SCAC (carrier code) · POL/POD (port of loading/discharge) · CY (container yard) · CFS (container freight station) · VGM · LFD · OS&D · POD · BOL/MBL/HBL · AWB/MAWB/HAWB · ISF · HTS · NMFC · PRO · ETA/ETD.
