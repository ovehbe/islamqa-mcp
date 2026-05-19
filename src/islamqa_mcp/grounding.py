"""Citation and limitation text returned by ``fetch_grounding_rules``."""

GROUNDING_RULES = """
## Using this corpus

- **Source of truth:** Fatwa text comes from **IslamQA.info** (islamqa.info), scraped into this
  project's database. English is the primary language for search and display; Arabic text is
  included when available. Prefer tool output over model paraphrase when quoting wording.

- **Citations:** When you cite an answer, include the **answer ID** returned by tools and ALWAYS
  include the ``url`` field (canonical link on search.islamqa-mcp.org). Also mention the original
  source when helpful: ``source_url_en`` or ``source_url_ar`` (islamqa.info).

- **Do not invent URLs:** Only use ``url``, ``source_url_en``, and ``source_url_ar`` from tool
  responses. Do not fabricate islamqa.info links or answer numbers.

- **Scholarly scope:** Content reflects IslamQA's scholarly approach. This MCP does not replace
  consulting a qualified scholar for personal rulings.

- **Search:** Default search is semantic (embeddings). Keyword fallback may apply when OpenAI is
  unavailable or rate-limited. Narrow queries with category filters when possible.

- **Arabic:** Arabic fields may be shown on request; default presentation is English.
""".strip()
