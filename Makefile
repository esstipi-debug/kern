# Thin aliases over the commands already documented in README.md and CLAUDE.md.
# Nothing here is required -- every target is one line you can run by hand. The
# point is that `make demo` is discoverable in three seconds, and
# `PYTHONPATH=. python examples/run_demo.py` is not.
#
# On Windows without make, run the command inside the target directly.

PYTHON ?= python
export PYTHONPATH := .

.PHONY: help demo test lint serve clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

demo:  ## Four briefs through the real orchestrator (~15s, no API keys)
	$(PYTHON) examples/run_demo.py

test:  ## Full suite with the coverage gate CI enforces
	$(PYTHON) -m pytest tests/ -q --cov=nosilo_core --cov=packs --cov=src --cov-fail-under=80

lint:  ## Same scope as CI
	$(PYTHON) -m ruff check nosilo_core packs src tests examples

serve:  ## Dashboard at / and the agent console at /console
	$(PYTHON) -m uvicorn webapp.app:app --reload

clean:  ## Drop generated demo output
	rm -rf output/demo
