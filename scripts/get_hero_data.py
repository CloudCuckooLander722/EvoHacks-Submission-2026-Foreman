"""Download the hero dataset and write data/hero/reviews_10k.json and reviews_1k.json.

    pip install huggingface_hub        # not a package dependency; only this script needs it
    python scripts/get_hero_data.py

Source: "Medium App Reviews from Google Play Store" (CC0 1.0), the Hugging Face mirror of
the Kaggle dataset by raqhea. One app keeps the themes coherent, and Medium's paid
membership gives the backlog a real billing/subscription strand. See data/hero/README.md.

The sample is seeded, so re-running the script gives byte-identical files.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter
from pathlib import Path

REPO_ID = "haoxianc/raqhea_medium-app-reviews-from-google-play-store"
REVISION = "6942eadb8c4e2fbfafd8564f53584efa3c5d5798"  # pinned so the sample never shifts
FILENAME = "dataset.csv"
APP = "Medium"
SEED = 20261011
MIN_CHARS, MAX_CHARS = 20, 600
# The store's natural mix for 20-600 char reviews is ~66% five-star and ~11% one-star.
# This tilts it toward low ratings so a bug backlog has enough material, while keeping
# the U shape real app stores have (most reviews still 4-5 stars).
RATING_MIX = {1: 0.25, 2: 0.10, 3: 0.10, 4: 0.15, 5: 0.40}
ENGLISH_WORDS = {
    "the", "and", "is", "it", "to", "i", "a", "this", "of", "for", "app", "you", "my",
    "in", "not", "but", "on", "with", "are", "so", "can", "very", "good", "great",
}  # fmt: skip
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "hero"


def looks_english(text: str) -> bool:
    """Cheap guard on top of the source's own English filter: Latin letters plus a stopword."""
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isascii() for c in letters) / len(letters) < 0.95:
        return False
    return bool(ENGLISH_WORDS & set(re.findall(r"[a-z']+", text.lower())))


def clean_row(row: dict[str, str]) -> dict[str, str | None]:
    """The CSV writes missing values as the string "None"; make them real nulls."""
    return {k: (None if v in ("None", "") else v) for k, v in row.items()}


def load_rows(path: Path) -> list[dict[str, str | None]]:
    with path.open(encoding="utf-8", newline="") as f:
        return [clean_row(r) for r in csv.DictReader(f)]


def eligible(rows: list[dict[str, str | None]]) -> dict[int, list[dict[str, str | None]]]:
    """English, 20-600 chars, valid rating and date, no exact duplicate texts; by rating."""
    seen: set[str] = set()
    by_rating: dict[int, list[dict[str, str | None]]] = {r: [] for r in RATING_MIX}
    for row in rows:
        text = (row.get("content") or "").strip()
        key = text.lower()
        if not (MIN_CHARS <= len(text) <= MAX_CHARS) or key in seen or not looks_english(text):
            continue
        try:
            rating = int(row.get("score") or "")
        except ValueError:
            continue
        if rating not in by_rating or not row.get("at"):
            continue
        seen.add(key)
        by_rating[rating].append(row)
    return by_rating


def sample(by_rating: dict[int, list], n: int, rng: random.Random) -> list[dict]:
    """Draw `n` rows following RATING_MIX (rounding remainder goes to 5 stars)."""
    want = {r: int(n * share) for r, share in RATING_MIX.items()}
    want[5] += n - sum(want.values())
    picked: list[dict] = []
    for rating, k in want.items():
        pool = by_rating[rating]
        if len(pool) < k:
            raise SystemExit(f"only {len(pool)} eligible {rating}-star reviews, need {k}")
        picked.extend(rng.sample(pool, k))
    picked.sort(key=lambda r: (r["at"], r["reviewId"]))  # chronological, like an export
    return picked


def to_item(n: int, row: dict) -> dict:
    """One items.json item; `meta` keeps the full source row for traceability."""
    return {
        "id": f"r-{n}",
        "text": row["content"].strip(),
        "fields": {"rating": int(row["score"]), "date": row["at"][:10], "app": APP},
        "meta": {"source": REPO_ID, **row},
    }


def write_items(path: Path, items: list[dict], n_source: int) -> None:
    doc = {
        "source": {
            "filename": FILENAME,
            "kind": "hf_dataset",
            "dataset": REPO_ID,
            "revision": REVISION,
            "license": "CC0-1.0",
            "rows": n_source,
            "sampled": len(items),
            "seed": SEED,
        },
        "items": items,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(items)} items, {path.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--csv", type=Path, help="use a local copy of dataset.csv instead")
    p.add_argument("--n", type=int, default=10_000)
    p.add_argument("--subset", type=int, default=1_000)
    args = p.parse_args()

    if args.csv:
        path = args.csv
    else:
        from huggingface_hub import hf_hub_download

        path = Path(hf_hub_download(REPO_ID, FILENAME, repo_type="dataset", revision=REVISION))
    rows = load_rows(path)
    rng = random.Random(SEED)
    picked = sample(eligible(rows), args.n, rng)
    items = [to_item(i, row) for i, row in enumerate(picked, start=1)]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_items(OUT_DIR / f"reviews_{args.n // 1000}k.json", items, len(rows))
    subset = sorted(rng.sample(items, args.subset), key=lambda it: int(it["id"][2:]))
    write_items(OUT_DIR / f"reviews_{args.subset // 1000}k.json", subset, len(rows))
    print("ratings (10k):", dict(sorted(Counter(i["fields"]["rating"] for i in items).items())))
    print("ratings (1k): ", dict(sorted(Counter(i["fields"]["rating"] for i in subset).items())))


if __name__ == "__main__":
    main()
