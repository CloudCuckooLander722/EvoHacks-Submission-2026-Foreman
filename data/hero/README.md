# Hero dataset: Medium app reviews

| | |
|---|---|
| Dataset | Medium App Reviews from Google Play Store |
| Hugging Face | [haoxianc/raqhea_medium-app-reviews-from-google-play-store](https://huggingface.co/datasets/haoxianc/raqhea_medium-app-reviews-from-google-play-store) (pinned revision `6942ead`) |
| Original | [raqhea/medium-app-reviews-from-google-play-store](https://www.kaggle.com/datasets/raqhea/medium-app-reviews-from-google-play-store) on Kaggle, by raqhea (alper) |
| Licence | **CC0 1.0** (public domain) |
| Language | English (filtered by the original author; `scripts/get_hero_data.py` adds a Latin-letters + stopword check) |
| Source size | 62,633 reviews of one app (Medium, Android), June 2015 – June 2025 |

One app keeps the themes coherent, and Medium's paid membership gives the backlog a real subscription/billing strand.

## Files

| File | Items | Size |
|---|---|---|
| `reviews_10k.json` | 10,000 | 7.9 MB |
| `reviews_1k.json` | 1,000 (a seeded subset of the 10k, same ids) | 0.8 MB |

Both are valid `items.json` (see `docs/CONTRACTS.md`):

```json
{
  "id": "r-12",
  "text": "there is no way to 1) add tags to new stories, 2) add title to new stories and 3) ...",
  "fields": {"rating": 3, "date": "2015-07-04", "app": "Medium"},
  "meta": {"source": "haoxianc/...", "reviewId": "f307875f-...", "content": "...", "score": "3", "at": "2015-07-04 01:07:06", ...}
}
```

`meta` is the full source row, including the dataset's own `predicted_category` and `sentiment` (from a RoBERTa model; we don't use them).

## Sampling

`python scripts/get_hero_data.py` (needs `pip install huggingface_hub`; it is not a package dependency). It is seeded (`20261011`), so re-running it gives byte-identical files.

- Text between 20 and 600 characters, English, no exact duplicate texts, valid rating and date.
- Ratings: 25% 1★, 10% 2★, 10% 3★, 15% 4★, 40% 5★. The store's natural mix at this length is about 66% 5★ and 11% 1★; the sample leans toward low ratings so a bug backlog has enough material, but keeps the U shape real app stores have.
- Items are numbered `r-1` … `r-10000` in date order. The 1k subset is a seeded random 1,000 of those, with the same ids.

Counts: 10k = 2,500 / 1,000 / 1,000 / 1,500 / 4,000 (1★ … 5★); 1k = 264 / 105 / 81 / 149 / 401.
