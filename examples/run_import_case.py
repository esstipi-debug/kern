"""Unify import documents into one case and a Chile field pack.

The folder may hold PDFs named after their document type, per-type JSON slices,
or a documents.json bundle. Reading a PDF needs the ``doc-reading`` extra
(``pip install -e ".[doc-reading]"``) and a document model - without an
ANTHROPIC_API_KEY the PDF is reported as unread rather than silently skipped.

    python examples/run_import_case.py tests/fixtures/import_case --out output/import_case
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from nosilo_core import llm  # noqa: E402

from jobs import import_case_job  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import case: unify extracted docs; draft Chile DIN pack (not a filing)",
    )
    parser.add_argument("data", help="folder with documents.json or a documents.json file")
    parser.add_argument("--out", default="output/import_case")
    parser.add_argument("--client", default="Client")
    parser.add_argument("--regime", default="")
    args = parser.parse_args(argv)

    params = {}
    if args.regime:
        params["regime"] = args.regime
    provider = llm.get_provider()
    payload = import_case_job.prepare(args.data, params, provider=provider)
    report = import_case_job.run(payload, params)
    issues = import_case_job.verify(report)
    if issues:
        print("QA failed: " + "; ".join(issues))
        return 1
    paths = import_case_job.write_operational(report, args.out, args.client)
    print(report.summary)
    for key, path in paths.items():
        print(f"{key}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
