# Contributing

Thank you for improving **Kern** — an agentic supply-chain engine grounded in the SCM literature.

## Setup

```bash
git clone https://github.com/esstipi-debug/kern.git
cd kern
python -m venv venv
# Windows: venv\Scripts\activate
pip install -r requirements.txt
pytest
```

## What to contribute

| Area | Examples |
|------|----------|
| Models | New model sections, bug fixes in formulas |
| Tests | Numeric examples from the source texts (§ references) |
| Examples | New workflows, plots |
| Docs | FAQ, METHODOLOGY, case studies |
| Export | Excel/Power BI dataset improvements |

## Workflow

1. Fork and branch: `feature/short-description`
2. Match existing style: dataclasses, type hints, minimal scope
3. Add tests for book numeric examples when possible
4. Run `pytest` before PR
5. Update CHANGELOG.md for user-visible changes

## PR checklist

- [ ] Tests pass (`pytest`)
- [ ] No fake marketing claims in docs
- [ ] Source section referenced in docstrings where relevant
- [ ] CHANGELOG updated if behavior changed

## Code style

- PEP 8, type hints on public functions
- Pure functions in `src/`; CLI in `examples/`
- Do not commit secrets, `.env`, or local `output/`

## Questions

Open a GitHub issue with reproduction steps and Python version.
