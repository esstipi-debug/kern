"""Fetch the DataCo Smart Supply Chain dataset from Kaggle (~180k orders, CC0).

Requires a configured Kaggle token (`~/.kaggle/access_token` with a KGAT token,
or `~/.kaggle/kaggle.json`, or the KAGGLE_API_TOKEN env var) and the kaggle pkg:
    uv pip install --python .venv/Scripts/python.exe kaggle
Downloads + unzips to data/kaggle/dataco/ (gitignored). Used by
examples/run_scm_dataco.py.

    python scripts/fetch_dataco.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data" / "kaggle" / "dataco"
SLUG = "shashwatwork/dataco-smart-supply-chain-for-big-data-analysis"


def main() -> None:
    from kaggle.api.kaggle_api_extended import KaggleApi

    DEST.mkdir(parents=True, exist_ok=True)
    api = KaggleApi()
    api.authenticate()
    print(f"downloading {SLUG} ...", flush=True)
    api.dataset_download_files(SLUG, path=str(DEST), unzip=True)
    csv = DEST / "DataCoSupplyChainDataset.csv"
    if csv.exists():
        rows = sum(1 for _ in csv.open(encoding="latin-1", errors="ignore"))
        print(f"done -> {csv}  ({rows:,} rows)")
    else:
        print(f"done -> {DEST}")


if __name__ == "__main__":
    main()
