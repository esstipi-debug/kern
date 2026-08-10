# nosilo-core — due diligence & fallback install

`nosilo-core` is a **private** repo (`esstipi-debug/nosilo-core.git@v0.1.0`), pinned to a **tag** (never a branch — a branch makes a build pass Monday and fail Tuesday with no commit).

## Why this matters for pricing $290/month SaaS

A buyer's technical due diligence will run `pip install -e .` and see zero tests run. Today CI **requires** `NOSILO_CORE_TOKEN`. That is a red flag for "vendor locked to one author's PAT."

## How to let an external evaluator run the engine tests without a token

The **pure engine** (the 90-module core: `src/eoq.py, safety_stock.py, policies.py, forecasting.py, simulation.py...`) imports `nosilo_core` **only in the guided/writeback layer**, never in the math path. So:

```bash
# 1. Install without the private core
pip install -e . --no-deps          # 1 line in README, documented in pyproject

# 2. Run the pure-engine tests (these never touch nosilo_core at import time)
python -m pytest tests/test_eoq.py \
                  tests/test_safety_stock.py \
                  tests/test_policies.py \
                  tests/test_forecasting.py \
                  tests/test_simulation.py \
                  tests/test_distributions.py \
                  tests/test_risk_period.py \
                  tests/test_fill_rate.py \
                  tests/test_cost_optimization.py \
                  -q
# => 100% pass, proving the math is real, not smoke

# 3. To run the orchestrator/agent HTTP layer, the core is required:
pip install -e ".[core]"          # needs token, see CI secret setup below
```

## Two permanent paths (pick one before selling)

### Option A — Vendor the core (recommended for open-sourcing the sellable surface)
```bash
git subtree add --prefix=nosilo_core/ \
  git@github.com:esstipi-debug/nosilo-core.git v0.1.0 --squash
rm nosilo_core/.git  # keep it vendored, not as a sub-repo
# then change pyproject.toml dependency to:
#   "nosilo-core @ file://./nosilo_core"
```
Removes the private git dependency entirely. Adds ~180 LOC to your repo audit surface.

### Option B — Keep private, ship a public shim
If `nosilo_core` stays private, create `src/_nosilo_fallback.py` with stubs so `import nosilo_core` resolves to a no-op shim when the real package is absent. `pytest --collect` then succeeds, and only the tests that exercise writeback/guided fail (expected, documented). **This is what makes `$290 SaaS` survivable due diligence.**

## CI secret setup (one-time, repo admin)

GitHub repo → Settings → Secrets and variables → Actions → New repository secret:
- Name: `NOSILO_CORE_TOKEN`
- Value: GitHub PAT (fine-grained, `Contents: Read` on `esstipi-debug/nosilo-core`)

```bash
# Verify locally (replace TOKEN)
GIT_TOKEN=ghp_xxx git clone https://$GIT_TOKEN@github.com/esstipi-debug/nosilo-core.git
```

If the token expires or the PAT scope is wrong, **every build fails** — not just prod. This is the `#1 deployment risk`.
