# Contributing

Thanks for your interest in contributing to islamqa-mcp.

## Getting started

```bash
git clone https://github.com/ovehbe/islamqa-mcp.git
cd islamqa-mcp
uv sync --extra dev
uv run ruff check
```

## Guidelines

- Keep PRs focused — one logical change per PR.
- Match existing code style. Run `ruff check` and `ruff format --check` before submitting.
- Write tests for new logic when practical.

## Do not commit data artifacts

The `data/` directory is gitignored and contains large generated files (~1.5 GB total). **Do not include `data/` contents in your PR.** The corpus is rebuilt locally by each developer using the pipeline scripts:

```bash
uv run python scripts/scrape_islamqa.py scrape
uv run python scripts/embed_islamqa.py run --batch-size 48 --sleep 0
uv run python scripts/build_db.py
```

If your change affects the pipeline or schema, describe the expected output change in your PR description so maintainers can rebuild and verify.

## Reporting issues

- Search existing issues before opening a new one.
- Include specific examples (answer ID, expected vs actual) for data issues.
- For bugs, include steps to reproduce and your Python version.

## License

By contributing you agree that your contributions will be licensed under GPL-3.0-only, consistent with the project license.
