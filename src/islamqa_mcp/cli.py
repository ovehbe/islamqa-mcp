"""CLI entry: run the MCP server."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    p = argparse.ArgumentParser(description="IslamQA MCP server (FastMCP)")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="YAML config (optional). Default: env ISLAMQA_MCP_DB_PATH or ./data/islamqa.db",
    )
    p.add_argument(
        "--transport",
        default="stdio",
        choices=("stdio", "http", "sse", "streamable-http"),
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = p.parse_args()
    load_dotenv()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s %(message)s",
    )

    from islamqa_mcp.server import build_server

    server = build_server(config_yaml=args.config)
    try:
        server.run(transport=args.transport)
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
