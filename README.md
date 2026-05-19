# islamqa-mcp

**Model Context Protocol (MCP) server and data pipeline** for [IslamQA.info](https://islamqa.info) fatwas (~32k answers, English + Arabic). Fetch and cite from a real corpus instead of quoting from model memory—same grounding mindset as [hadith-mcp](https://github.com/ovehbe/hadith-mcp) and [quran-mcp](https://github.com/quran/quran-mcp).

## Data sources and credits

- **Content** is scraped from [IslamQA.info](https://islamqa.info) (English and Arabic pages). This project is not affiliated with IslamQA; respect their terms of use and scholarly context when citing answers.
- **Architecture** follows patterns from **hadith-mcp** (FastMCP, SQLite + embeddings, static search UI, deploy units).

## Repository layout

| Path | Purpose |
|------|---------|
| `scripts/scrape_islamqa.py` | Discover IDs from sitemaps; incremental scrape → `data/answers.json` |
| `scripts/embed_islamqa.py` | OpenAI `text-embedding-3-large` → `data/embeddings.db` (incremental, batched) |
| `scripts/build_db.py` | Merge `answers.json` + `embeddings.db` → `data/islamqa.db` |
| `scripts/fetch_ext_apps.py` | Vendor MCP ext-apps SDK for `show_answer` interactive UI |
| `src/islamqa_mcp/server.py` | FastMCP tools + REST (`/api/search`, `/api/answer/{id}`, …) |
| `src/islamqa_mcp/assets/` | MCP App HTML + bundled SDK for `show_answer` |
| `search/` | Static search frontend |
| `site/` | Landing / setup page |
| `deploy/` | systemd + nginx examples |
| `data/` | **Gitignored** — see [`data/README.md`](data/README.md) |

## Quick start

```bash
uv sync
cp .env.example .env   # OPENAI_API_KEY required for embed + semantic search
```

### 1) Scrape (incremental)

```bash
uv run python scripts/scrape_islamqa.py scrape
```

### 2) Embed (incremental; resume-safe)

Default is one text per API call (slow). For a full corpus on a capable OpenAI tier, use batching:

```bash
# Full run (example: Tier 5 with high TPM/RPM)
uv run python scripts/embed_islamqa.py run --batch-size 48 --sleep 0 --commit-every 50

# Incremental / cautious
uv run python scripts/embed_islamqa.py run --batch-size 1 --sleep 0.12

uv run python scripts/embed_islamqa.py stats
```

### 3) Build canonical DB

```bash
uv run python scripts/build_db.py
# Optional: --fresh to delete existing islamqa.db first
```

### 4) Run MCP + REST

```bash
uv run islamqa-mcp --config config.yml --transport streamable-http
```

Point `ISLAMQA_MCP_DB_PATH` at your `islamqa.db` if it is not under `./data/`.

## MCP tools

- `fetch_grounding_rules` — citation rules (call before citing)
- `search_answers` — semantic (default) or keyword search
- `fetch_answer` — by IslamQA answer ID
- `list_categories` — topic list
- `show_answer` — interactive reader (MCP App) + text fallback

## REST API

- `GET /api/search?q=&limit=&category=`
- `GET /api/answer/{id}`
- `GET /api/categories`
- `GET /api/stats`

## Frontends

- `search/` — static search app (e.g. `search.islamqa-mcp.org`)
- `site/` — landing page (e.g. `islamqa-mcp.org`)

## Deploy

Step-by-step Ubuntu 22.04 setup: [`deploy/DEPLOY.md`](deploy/DEPLOY.md) (uv, systemd MCP + scrape timer, nginx, certbot).

After scrape → embed → `build_db.py`, restart `islamqa-mcp` (the scrape timer does this automatically).

## Environment

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Document embeddings + query vectors for search |
| `ISLAMQA_MCP_DB_PATH` | Override DB path (default `./data/islamqa.db`) |
| `ISLAMQA_SEARCH_APP_URL` | Citation base URL (default `https://search.islamqa-mcp.org`) |

## License

GPL-3.0-only — see [LICENSE](LICENSE).
