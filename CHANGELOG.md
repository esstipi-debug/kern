# Changelog

## [1.3.0] - 2026-06-18

### Added
- Power BI CSV dataset export `src/powerbi_export.py`
- `examples/build_powerbi_dataset.py`
- Power Query templates `power-bi/queries/*.pq`
- DAX measures `power-bi/measures.dax`
- Setup guide `power-bi/SETUP.md`
- `--powerbi` flag on `run_complete.py`
- Test for Power BI export (42 total)

## [1.2.0] - 2026-06-18

### Added
- Native Excel export `src/excel_export.py` and `examples/build_excel_workbook.py`
- `--excel` flag on `run_complete.py`
- GSM simulation with customer backorders at demand node
- `lost_sales` on `(s,Q)` simulation
- Agent skills README; skills synced to Claude Code
- Tests: Excel export, GSM backorders (41 total)

### Changed
- `requirements.txt`: openpyxl, pytest
- Excel templates README: live `.xlsx` workflow

## [1.1.0] - 2026-06-18

### Added
- End-to-end script `examples/run_complete.py` with optional CSV export
- CSV export layer `src/export.py` for Excel / Power BI
- GSM discrete simulation `simulate_serial_gsm` (Ch. 10.5)
- Lost sales mode on `(R,S)` simulation (`lost_sales=True`, §5.3.2)
- Tests for GSM simulation and lost sales (39 total)

### Changed
- README: agent skills section, `run_complete` quick start
- Excel templates README: CSV export workflow

## [1.0.0] - 2026-06-18

### Added
- Gamma demand, distribution selection, gamma loss (Ch. 9) — `src/distributions.py`
- Serial multi-echelon GSM allocation (Ch. 10) — `src/multi_echelon.py`
- Newsvendor discrete/continuous (Ch. 11) — `src/newsvendor.py`
- Histogram PMF and KDE discretization (Ch. 12) — `src/discrete_demand.py`
- Simulation-based safety stock optimization (Ch. 13) — `src/simulation_opt.py`
- Example `examples/run_part4.py`
- Tests for chapters 9–13

### Changed
- README and METHODOLOGY: full book coverage (Ch. 1–13) documented
- `src/__init__.py`: exports for all modules

## [0.2.0] - 2026-06-18

### Added
- Stochastic lead time in policies (Ch. 6) — `src/risk_period.py`
- Fill rate + normal loss function (Ch. 7) — `src/fill_rate.py`
- Cost/service optimization for (R,S) and (s,Q) (Ch. 8) — `src/cost_optimization.py`
- Example `examples/run_part3.py`
- Tests for fill rate, cost optimization, lead time

## [0.1.0] - 2026-06-18

### Added
- Python implementation aligned with Vandeput (2020), Part I–II:
  - EOQ model (`src/eoq.py`) — Ch. 2–3
  - Safety stock under normal demand (`src/safety_stock.py`) — Ch. 4
  - Policies `(s,Q)` and `(R,S)` (`src/policies.py`) — Ch. 5
  - Discrete-period simulation with backorders (`src/simulation.py`) — Ch. 5.3
- Sample demand data (`data/sample_demand.csv`)
- Runnable example (`examples/run_part1_part2.py`)
- Unit tests with book numeric example (§2.2.4)
- Documentation rewritten to reference the book

### Changed
- README: removed placeholder marketing; cites Vandeput (2020)
- Excel / Power BI folders marked as planned export layers

### Removed
- Claims of ARIMA/Prophet templates (not in the inventory optimization book)

### Known limitations
- Normal demand assumption only (Ch. 9+ not yet implemented)
- Backorders only (lost sales in Ch. 5.3.2 — planned)
- Single-echelon, single SKU

---

## [2.0.0] - 2026-03-18 (documentation-only release)

Initial markdown structure without executable templates.
