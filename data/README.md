# Data directory (not in git)

Generated artifacts live here. Clone the repo, then build locally:

| File | Produced by |
|------|-------------|
| `answer_ids.txt` | `scripts/scrape_islamqa.py` (optional cache) |
| `answers.json` | `scripts/scrape_islamqa.py scrape` |
| `embeddings.db` | `scripts/embed_islamqa.py run` |
| `islamqa.db` | `scripts/build_db.py` |

Typical sizes after a full run: ~265 MB `answers.json`, ~650 MB `embeddings.db`, ~660 MB `islamqa.db`.
