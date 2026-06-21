# Changelog

## [2.6.0] - 2026-06-19

### Added
- **Job-fulfillment layer** (`jobs/`) — package the engine for real supply-chain
  freelance work:
  - `jobs/intake.py`: detect/normalize any client schema (CSV/Excel, ERP or
    Kaggle-style) to the canonical demand schema, aggregated per period
  - `jobs/inventory_optimization.py`: playbook — forecast → (s,Q)/(R,S) policy →
    budget allocation → structured `JobReport`
  - `jobs/qa.py`: automated verification of the report's numbers (nothing ships
    until it passes)
  - `jobs/deliverables.py`: client-ready Excel workbook + Markdown report + CSV
  - `examples/run_inventory_job.py`: end-to-end CLI; `jobs/SAMPLE_REPORT.md`
- 8 job-layer tests (120 total)

### Notes
- Human-in-the-loop by design — automates the analysis/deliverable, not Upwork
  bidding or client comms (ToS).

### Added
- **Inventory Planner web UI** (`webapp/`): a 4-tab dashboard (Portfolio, SKU
  Detail with demand/forecast chart, Budget Planner, Forecast Quality) served by
  FastAPI over the engine — every number comes from `src/`. Vanilla JS, no Node /
  build step; one `uvicorn` command
- `GET /api/portfolio` runs forecasting → policy → constraints with live
  what-if params (service level, lead time, order cost, budget); validated inputs
- `scripts/generate_portfolio.py` seeds an 8-SKU demand portfolio
- `tests/test_webapp.py` (9 tests, 110 total) via FastAPI TestClient
- Screenshots in `webapp/screenshots/`

### Changed
- `requirements-dev.txt`: add fastapi / uvicorn / httpx for the web UI + its tests

### Added
- **`SqlDemandSource`** (`src/sources.py`): live demand from any DB-API 2.0
  connection (SQLite, Postgres, MySQL ...) via a table name or parameterised
  query; validates bare table names as SQL identifiers to avoid injection
- `examples/run_sql_source.py`: live-data demo (SQLite stand-in for an ERP feed)
  running the same source → forecast → policy chain
- 4 SQL-adapter tests (101 total, ~91% coverage)

### Changed
- README: live SQL data path documented; ERP/WMS adapter now a thin layer

## [2.3.0] - 2026-06-19

### Added
- **Pluggable data sources** (`src/sources.py`): `DemandSource` protocol with
  `CsvDemandSource` and `DataFrameDemandSource` adapters — the engine is no
  longer hard-wired to CSV; a SQL/API/ERP adapter just satisfies the protocol
- **Business constraints** (`src/constraints.py`): MOQ, case-pack rounding,
  shelf-life caps, and `allocate_under_budget` — a portfolio budget allocator
  that trims safety stock to fit while preserving cycle-stock economics
- `examples/run_constrained_plan.py`: full chain source → forecast → policy →
  constraints
- 14 tests (97 total, ~91% coverage)

### Changed
- README: "engine → product" section now reflects the assembled AUTO chain

## [2.2.0] - 2026-06-19

### Added
- **Demand forecasting front-end** (`src/forecasting.py`): moving average, simple
  exponential smoothing, and Croston (intermittent demand), with auto method
  selection by ADI classification
- Forecast-error σ_e — the theoretically correct dispersion for safety stock
  (Vandeput 2021, §4.2.5) — exposed via `ForecastResult.to_engine_inputs`
- `examples/run_forecast_to_policy.py`: end-to-end history → forecast → (s,Q) policy
- 13 forecasting tests (83 total, ~90% coverage)

### Changed
- README: forecasting documented; "Future extensions" reframed as the
  engine → product ("AUTO") roadmap

## [2.1.0] - 2026-06-19

### Added
- `pyproject.toml`: PEP 621 packaging + Ruff, pytest and coverage config (pip-installable, no more `PYTHONPATH` hack)
- `requirements-dev.txt` for test/lint tooling (`pytest`, `pytest-cov`, `ruff`)
- Direct unit tests for previously-untested modules: `policies`, `risk_period`, `demand_variability`, `discrete_demand`, `export` (23 new tests, 70 total)
- CI now lints with Ruff and enforces a 80% coverage gate (measured coverage ~90%)

### Fixed
- `tests/test_multi_echelon.py`: `test_gsm_case1_higher_cost_than_optimal` asserted nothing — added the intended cost comparison
- Removed dead code / unused locals and imports flagged by Ruff (`eoq`, `fill_rate`, `policies`, `distributions`, `powerbi_export`, several tests/examples)

### Changed
- `pytest` moved out of runtime `requirements.txt` into dev deps
- Test config moved from `pytest.ini` into `pyproject.toml`

## [2.0.0] - 2026-06-18

### Added
- Product metadata from CSV: `unit_cost`, `lead_time_days` (`src/data_loader.py`)
- EOQ volume discounts §2.5.3 (`compute_eoq_volume_discount`)
- Gamma/auto safety stock in policies (`src/demand_variability.py`)
- Multi-SKU batch analysis (`src/batch.py`, `examples/run_batch.py`)
- Inventory charts (`examples/plot_inventory.py`)
- Sim-opt grid over R and Ss (`optimize_rs_simulation_grid`)
- GitHub Actions CI (Python 3.11–3.13)
- Rewritten FAQ and CONTRIBUTING (removed placeholder marketing)

### Changed
- Power BI export uses per-SKU lead time, unit cost, GSM params
- METHODOLOGY assumptions table updated
- README: CI badge, full project layout

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
