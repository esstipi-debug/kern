# Thin aliases over the commands already documented in README.md and CLAUDE.md.
# Nothing here is required -- every target is one line you can run by hand. The
# point is that `make demo` is discoverable in three seconds, and
# `PYTHONPATH=. python examples/run_demo.py` is not.
#
# On Windows without make, run the command inside the target directly.
#
# `test` and `lint` are not free-hand: they must match .github/workflows/tests.yml
# exactly, and tests/test_ci_lint_and_coverage_scope.py fails when they do not.
# They had drifted -- coverage floor 80 against CI's 90, a --cov=nosilo_core for a
# package that stopped living in this repo at #226, and the narrow ruff path list
# that CI itself abandoned because it left seven packages unlinted. A convenience
# alias that runs a WEAKER check than CI is worse than no alias: it reports green
# on something CI will reject.

PYTHON ?= python
export PYTHONPATH := .

.PHONY: help demo test lint serve clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

demo:  ## Four briefs through the real orchestrator (~15s, no API keys)
	$(PYTHON) examples/run_demo.py

test:  ## Full suite with the coverage gate CI enforces
	$(PYTHON) -m pytest tests/ -q --cov=packs --cov=src --cov=scm_agent --cov=jobs --cov-fail-under=90

lint:  ## Same scope as CI
	$(PYTHON) -m ruff check .

serve:  ## Dashboard at / and the agent console at /console
	$(PYTHON) -m uvicorn webapp.app:app --reload

clean:  ## Drop generated demo output
	rm -rf output/demo
