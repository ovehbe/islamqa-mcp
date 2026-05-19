#!/usr/bin/env python3
"""Vendor the @modelcontextprotocol/ext-apps SDK as a self-contained ES module.

The MCP Apps host iframe in Claude/ChatGPT refuses dynamic imports from
esm.sh and other CDNs (``ui.csp.resourceDomains`` does not relax
``script-src``). To make the ``show_answer`` MCP app render reliably, we
bundle the fully-inlined ``app-with-deps.mjs`` variant of the SDK (zod and
``@modelcontextprotocol/sdk`` already baked in, zero external imports)
into ``src/islamqa_mcp/assets/ext-apps.bundle.mjs`` and inline it at
resource-render time. This mirrors what quran-mcp does via
``vite-plugin-singlefile`` -- just without the Vite build step.

Run this script whenever you want to refresh the pinned SDK version::

    python3 scripts/fetch_ext_apps.py --version 1.6.0

Pass ``--check`` in CI to verify the committed bundle matches the remote
artifact for the pinned version.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

DEFAULT_VERSION = "1.6.0"
GLOBAL_NAME = "__islamqaMcpSdk"
BUNDLE_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "islamqa_mcp"
    / "assets"
    / "ext-apps.bundle.js"
)


def bundle_url(version: str) -> str:
    return (
        f"https://esm.sh/@modelcontextprotocol/ext-apps@{version}"
        f"/es2022/app-with-deps.mjs"
    )


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "islamqa-mcp/fetch"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Unexpected status {resp.status} for {url}")
        return resp.read()


def sanity_check(data: bytes) -> None:
    """Reject anything that is not a self-contained ES module."""
    head = data[:512].decode("utf-8", errors="replace")
    if "esm.sh" not in head:
        raise RuntimeError(
            "Downloaded file is missing the expected esm.sh banner -- "
            "did the upstream URL change?"
        )
    # The *whole* file must not pull in any other module at runtime.
    if b"\nimport " in data or data.startswith(b"import "):
        raise RuntimeError(
            "Downloaded bundle still contains external `import` statements; "
            "refusing to vendor a non-self-contained build."
        )
    if b"import.meta" in data or re.search(rb"[^a-zA-Z_$]import\s*\(", data):
        raise RuntimeError(
            "Downloaded bundle uses import.meta or dynamic import() — "
            "cannot safely rewrite to a classic script."
        )


_EXPORT_RE = re.compile(
    rb"export\{([^{}]+)\}\s*;?\s*(?://#\s*sourceMappingURL=[^\n]*)?\s*\Z",
    re.DOTALL,
)


def rewrite_exports_to_global(data: bytes) -> bytes:
    """Turn the trailing ``export {X as A, Y as B, ...}`` into a classic
    ``window.__islamqaMcpSdk = { A: X, B: Y, ... };`` so the bundle can be
    served as a regular <script> with no ES module semantics.

    The MCP Apps host iframe does not allow our HTML's <script
    type="module"> to import from external URLs or from another inline
    module, so we flatten everything into one global object the app code
    can read off ``window``.
    """
    match = _EXPORT_RE.search(data)
    if match is None:
        raise RuntimeError(
            "Could not find the trailing `export {...}` clause in the bundle."
        )
    clause = match.group(1).decode("utf-8")
    entries: list[str] = []
    for raw in clause.split(","):
        raw = raw.strip()
        if not raw:
            continue
        # Forms: "X as Name" or just "Name"
        m = re.match(r"^([\w$]+)\s+as\s+([\w$]+)$", raw)
        if m:
            local, external = m.group(1), m.group(2)
        else:
            m = re.match(r"^([\w$]+)$", raw)
            if not m:
                raise RuntimeError(f"Unrecognized export entry: {raw!r}")
            local = external = m.group(1)
        # Only expose names that are valid JS identifiers (they already are
        # because the regex guarantees it).
        entries.append(f'  "{external}": {local}')

    assignment = (
        f"window.{GLOBAL_NAME} = {{\n"
        + ",\n".join(entries)
        + "\n};\n"
    )

    prefix = data[: match.start()]
    return prefix + assignment.encode("utf-8")


def write_bundle(
    data: bytes, version: str, *, path: Path = BUNDLE_PATH
) -> None:
    rewritten = rewrite_exports_to_global(data)
    digest = hashlib.sha256(rewritten).hexdigest()
    header = (
        f"// Vendored from {bundle_url(version)}\n"
        f"// version: {version}\n"
        f"// sha256: {digest}\n"
        f"// Refresh with: python3 scripts/fetch_ext_apps.py --version {version}\n"
        f"// Exports are attached to window.{GLOBAL_NAME} as a classic\n"
        f"// script so the MCP app host can inline it without ES module loaders.\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header.encode("utf-8") + rewritten)
    print(
        f"wrote {path} ({len(rewritten)} bytes, "
        f"sha256={digest[:16]}…)"
    )


def check_bundle(data: bytes, *, path: Path = BUNDLE_PATH) -> None:
    if not path.is_file():
        print(f"{path} missing", file=sys.stderr)
        sys.exit(2)
    expected = rewrite_exports_to_global(data)
    existing = path.read_bytes()
    # Skip the header (6 comment lines) before comparing bodies.
    lines = existing.split(b"\n", 6)
    existing_body = lines[-1] if len(lines) == 7 else existing
    if existing_body != expected:
        print(
            f"{path} does not match upstream bundle — re-run fetch_ext_apps.py",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"{path} matches upstream")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed bundle matches upstream; do not rewrite.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BUNDLE_PATH,
        help=f"Output path (default: {BUNDLE_PATH})",
    )
    args = parser.parse_args()

    url = bundle_url(args.version)
    print(f"fetching {url} …", file=sys.stderr)
    data = fetch(url)
    sanity_check(data)

    if args.check:
        check_bundle(data, path=args.output)
    else:
        write_bundle(data, args.version, path=args.output)


if __name__ == "__main__":
    main()
