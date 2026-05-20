"""FastMCP server: tools over ``islamqa.db``."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import numpy as np
from dotenv import load_dotenv
from fastmcp import Context, FastMCP
from fastmcp.resources import ResourceContent
from fastmcp.server.lifespan import lifespan
from fastmcp.tools import ToolResult
from mcp.types import Icon, TextContent
from openai import OpenAI
from starlette.requests import Request
from starlette.responses import Response

from islamqa_mcp.embeddings_index import EmbeddingIndex
from islamqa_mcp.grounding import GROUNDING_RULES
from islamqa_mcp.grounding_state import GroundingState
from islamqa_mcp.middleware_logging import ToolCallLoggingMiddleware
from islamqa_mcp.openai_fallback import should_fallback_to_keyword
from islamqa_mcp.query_cache import SearchResponseCache
from islamqa_mcp.rate_limit import RateLimiter
from islamqa_mcp.settings import AppConfig, load_app_config
from islamqa_mcp.stats import StatsTracker
from islamqa_mcp.store import IslamQAStore

load_dotenv()

logger = logging.getLogger("islamqa_mcp.server")

_SEARCH_APP_BASE_URL = os.environ.get(
    "ISLAMQA_SEARCH_APP_URL", "https://search.islamqa-mcp.org"
).strip().rstrip("/")

_ISLAMQA_APP_MIME = "text/html;profile=mcp-app"
_ISLAMQA_APP_ASSETS_DIR = Path(__file__).parent / "assets"
_ISLAMQA_APP_HTML_PATH = _ISLAMQA_APP_ASSETS_DIR / "islamqa_app.html"
_ISLAMQA_APP_SDK_PATH = _ISLAMQA_APP_ASSETS_DIR / "ext-apps.bundle.js"
_ISLAMQA_APP_SDK_PLACEHOLDER = "/*__SDK_BUNDLE__*/"


def _answer_url(answer_id: int) -> str:
    return f"{_SEARCH_APP_BASE_URL}/?id={answer_id}"


def _enrich_answer(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    aid = int(out["id"])
    out["url"] = _answer_url(aid)
    if out.get("url_en"):
        out["source_url_en"] = out["url_en"]
    if out.get("url_ar"):
        out["source_url_ar"] = out["url_ar"]
    return out


def _search_client_key(ctx: Context) -> str:
    try:
        from fastmcp.server.dependencies import get_http_request

        req = get_http_request()
        if req.client and req.client.host:
            return f"ip:{req.client.host}"
        return "ip:unknown"
    except Exception:
        pass
    rc = ctx.request_context
    if rc is not None:
        return f"mcp:{id(rc)}"
    return "stdio:default"


def _session_key(ctx: Context) -> str:
    rc = ctx.request_context
    sess = getattr(rc, "session", None) if rc is not None else None
    return hex(id(sess)) if sess is not None else "default"


def _record_mcp(ctx: Context, kind: str) -> None:
    lc = ctx.lifespan_context
    if not isinstance(lc, dict):
        return
    st = lc.get("stats")
    if st is not None:
        st.record("mcp", kind, _search_client_key(ctx))


def _format_detail_fallback(answer: dict[str, Any]) -> str:
    parts = [
        f"[{answer['id']}] {answer.get('title_en') or answer.get('title_ar', '')}",
        "",
        "Question:",
        answer.get("question_en") or answer.get("question_ar") or "",
        "",
        "Answer:",
        answer.get("answer_en") or answer.get("answer_ar") or "",
    ]
    if answer.get("url"):
        parts += ["", f"URL: {answer['url']}"]
    if answer.get("source_url_en"):
        parts.append(f"Source (EN): {answer['source_url_en']}")
    return "\n".join(parts)


def _format_search_fallback(
    query: str, results: list[dict[str, Any]], note: str | None
) -> str:
    if not results:
        return f'No results for "{query}".' + (f"\n{note}" if note else "")
    lines = [f'Search: "{query}" — {len(results)} result(s)']
    if note:
        lines.append(note)
    lines.append("")
    for i, r in enumerate(results[:10], 1):
        aid = r.get("answer_id") or r.get("id")
        sim = r.get("similarity")
        sim_str = (
            f" — {int(round(float(sim) * 100))}%"
            if isinstance(sim, (int, float))
            else ""
        )
        lines.append(f"{i}. #{aid}{sim_str} {r.get('title_en', '')}".strip())
        excerpt = (r.get("answer_excerpt") or r.get("question_excerpt") or "").strip()
        if excerpt:
            lines.append(f"   {excerpt[:200]}")
        if r.get("url"):
            lines.append(f"   {r['url']}")
    return "\n".join(lines)


_EMPTY_FALLBACK_TEXT = (
    "IslamQA Reader — no query given.\n\n"
    "Call show_answer(answer_id=…) to open a specific fatwa, "
    "or show_answer(query=…) to run a search in the reader."
)


def _slim_search_result(row: dict[str, Any], similarity: float | None) -> dict[str, Any]:
    return {
        "answer_id": int(row["id"]),
        "title_en": row.get("title_en"),
        "question_excerpt": (row.get("question_en") or "")[:280],
        "answer_excerpt": (row.get("answer_en") or row.get("excerpt_en") or "")[:280],
        "similarity": similarity,
        "categories": row.get("categories", []),
        "url": _answer_url(int(row["id"])),
        "source_url_en": row.get("url_en"),
        "source_url_ar": row.get("url_ar"),
    }


@lifespan
async def _lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    cfg: AppConfig = getattr(server, "_islamqa_cfg", None) or load_app_config()
    store = IslamQAStore(cfg.db_path)
    emb_index: EmbeddingIndex | None = None
    try:
        emb_index = await anyio.to_thread.run_sync(EmbeddingIndex.load, cfg.db_path)
        logger.info(
            "loaded embedding index rows=%s dim=%s",
            emb_index.mat.shape[0],
            emb_index.mat.shape[1],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("embedding index unavailable: %s", exc)
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_client = OpenAI(api_key=api_key) if api_key else None
    rate_limiter = RateLimiter(cfg.rate_limit_search_per_minute)
    search_cache = (
        SearchResponseCache(cfg.search_cache_max_entries)
        if cfg.search_cache_max_entries > 0
        else None
    )
    grounding = GroundingState()
    stats_tracker = StatsTracker()
    state = {
        "store": store,
        "config": cfg,
        "embeddings": emb_index,
        "openai": openai_client,
        "grounding": grounding,
        "search_rate_limiter": rate_limiter,
        "search_cache": search_cache,
        "stats": stats_tracker,
        "stats_boot_mono": time.monotonic(),
    }
    server._islamqa_state = state  # type: ignore[attr-defined]
    try:
        yield state
    finally:
        try:
            server._islamqa_state = None  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            stats_tracker.close()
        except Exception:
            pass
        store.close()


def build_server(*, config_yaml: Path | None = None) -> FastMCP:
    cfg = load_app_config(config_yaml=config_yaml)
    mcp = FastMCP(
        "islamqa-mcp",
        instructions=(
            "IslamQA.info fatwa corpus (~32k answers, English + Arabic). "
            "Call fetch_grounding_rules when citing answers. "
            "Never quote from memory: use fetch_answer or search_answers. "
            "ALWAYS include the 'url' field next to every citation. "
            "Use source_url_en / source_url_ar for the original islamqa.info page. "
            "search_answers defaults to semantic; use mode='keyword' for substring search. "
            "When the user asks to open, read, browse, or show a fatwa interactively, call show_answer. "
            "Prefer show_answer(answer_id=<id>) — the 'id' field on fetch_answer / search_answers rows. "
            "If you do not know that id yet, call fetch_answer or search_answers first, then show_answer. "
            "show_answer also returns a text fallback with the same 'url' field for non-App hosts."
        ),
        icons=[Icon(src="https://islamqa-mcp.org/logo.png")],
        lifespan=_lifespan,
    )
    mcp._islamqa_cfg = cfg  # type: ignore[attr-defined]
    mcp.add_middleware(ToolCallLoggingMiddleware())

    async def _semantic_search(
        ctx: Context,
        query: str,
        limit: int,
        category_filter: str | None,
    ) -> dict[str, Any]:
        cfg_l: AppConfig = ctx.lifespan_context["config"]
        store: IslamQAStore = ctx.lifespan_context["store"]
        idx = ctx.lifespan_context.get("embeddings")
        client = ctx.lifespan_context.get("openai")
        if idx is None or client is None:
            return {"ok": False, "reason": "semantic_unavailable", "results": [], "fallback": False}

        rl = ctx.lifespan_context.get("search_rate_limiter")
        if rl is not None and not rl.allow(_search_client_key(ctx)):
            return {"ok": False, "reason": "rate_limited", "results": [], "fallback": True}

        cache = ctx.lifespan_context.get("search_cache")
        cache_key = (query.strip().lower(), limit, category_filter or "", cfg_l.query_embedding_model)
        if cache is not None:
            hit = cache.get(cache_key)
            if hit is not None:
                return {"ok": True, "results": hit, "cache_hit": True, "fallback": False}

        allowed_ids: set[int] | None = None
        if category_filter:
            cat_id = store.get_category_id(category_filter)
            if cat_id is not None:
                allowed_ids = store.fetch_answer_ids_for_category(cat_id)

        def _embed() -> np.ndarray:
            r = client.embeddings.create(model=cfg_l.query_embedding_model, input=query)
            return np.asarray(r.data[0].embedding, dtype=np.float32)

        try:
            qv = await anyio.to_thread.run_sync(_embed)
        except Exception as exc:  # noqa: BLE001
            if should_fallback_to_keyword(exc):
                return {"ok": False, "reason": "openai_error", "fallback": True, "results": []}
            raise

        if int(qv.shape[0]) != int(idx.mat.shape[1]):
            return {"ok": False, "reason": "dimension_mismatch", "fallback": True, "results": []}

        if allowed_ids is not None:
            top = idx.topk_filtered(qv, limit, allowed_ids)
        else:
            top = idx.topk(qv, limit)

        ids = [i for i, _ in top]
        scores = {i: s for i, s in top}
        rows = store.fetch_answers_by_ids(ids)
        results = [_slim_search_result(r, float(scores[int(r["id"])])) for r in rows]
        if cache is not None:
            cache.set(cache_key, results)
        return {"ok": True, "results": results, "cache_hit": False, "fallback": False}

    def _keyword_search(
        ctx: Context,
        query: str,
        limit: int,
        category_filter: str | None,
    ) -> dict[str, Any]:
        store: IslamQAStore = ctx.lifespan_context["store"]
        cat_id = store.get_category_id(category_filter) if category_filter else None
        rows = store.search_answers(query, limit=limit, category_id=cat_id)
        return {
            "ok": True,
            "results": [_slim_search_result(r, None) for r in rows],
        }

    @mcp.tool()
    def list_categories(ctx: Context) -> list[dict[str, Any]]:
        """List topic categories (id, English/Arabic names, answer counts)."""
        return ctx.lifespan_context["store"].list_categories()

    @mcp.tool()
    def fetch_answer(ctx: Context, answer_id: int) -> dict[str, Any]:
        """Fetch one fatwa by IslamQA answer ID (English + Arabic when available)."""
        store: IslamQAStore = ctx.lifespan_context["store"]
        row = store.fetch_answer(int(answer_id))
        if row is None:
            return {"error": "not_found", "answer": None}
        _record_mcp(ctx, "lookup")
        return {"error": None, "answer": _enrich_answer(row)}

    @mcp.tool()
    async def search_answers(
        ctx: Context,
        query: str,
        limit: int = 20,
        category: str | None = None,
        mode: str = "semantic",
    ) -> dict[str, Any]:
        """Search fatwas: semantic (default), keyword, or both."""
        limit = max(1, min(int(limit), 100))
        cat_f = (category or "").strip() or None
        mode_l = mode.strip().lower()
        if mode_l not in {"semantic", "keyword", "both"}:
            return {"mode": mode_l, "error": "mode must be semantic, keyword, or both", "results": []}

        if mode_l == "keyword":
            kw = _keyword_search(ctx, query, limit, cat_f)
            _record_mcp(ctx, "search")
            return {"mode": "keyword", "results": kw["results"], "note": None}

        if mode_l == "semantic":
            sem = await _semantic_search(ctx, query, limit, cat_f)
            if sem["ok"]:
                _record_mcp(ctx, "search")
                return {"mode": "semantic", "results": sem["results"], "note": None}
            kw = _keyword_search(ctx, query, limit, cat_f)
            reason = sem.get("reason", "unknown")
            note = f"Semantic search unavailable ({reason}); used keyword search."
            _record_mcp(ctx, "search")
            return {"mode": "keyword_fallback", "results": kw["results"], "note": note}

        sem = await _semantic_search(ctx, query, limit, cat_f)
        kw = _keyword_search(ctx, query, limit, cat_f)
        _record_mcp(ctx, "search")
        return {"mode": "both", "semantic": sem, "keyword": kw}

    @mcp.tool()
    def fetch_grounding_rules(
        ctx: Context,
        nonce: str | None = None,
        force_full: bool = False,
    ) -> dict[str, Any]:
        """Citation and limitation guidance for IslamQA corpus."""
        grounding: GroundingState = ctx.lifespan_context["grounding"]
        return grounding.fetch(
            _session_key(ctx),
            nonce=nonce,
            force_full=force_full,
            full_text=GROUNDING_RULES,
        )

    @mcp.tool(
        name="show_answer",
        title="Show IslamQA Reader",
        description=(
            "Open the interactive IslamQA Reader UI for a user. Renders answer cards with "
            "category chips, question/answer text, optional Arabic, and proof links.\n\n"
            "PREFERRED ENTRY POINT: 'answer_id' (the database id returned by fetch_answer / "
            "search_answers as 'id' or 'answer_id').\n\n"
            "Recommended flow when the user asks to 'show / open / read / view' a fatwa:\n"
            "  1. If you do not already know answer_id from an earlier tool call, use "
            "fetch_answer or search_answers to find it.\n"
            "  2. Then call show_answer(answer_id=<that id>).\n\n"
            "Secondary entry points:\n"
            "  - 'query' — free-text search rendered inside the reader (semantic with keyword "
            "fallback).\n"
            "  - no arguments — opens an empty reader for the user to browse.\n\n"
            "Do not re-fetch the same answer just to re-read text — the structured response "
            "already contains the full row. Always surface the returned 'url' alongside citations."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        meta={"ui": {"resourceUri": "ui://islamqa.html"}},
        tags={"preview", "app", "islamqa"},
    )
    async def show_answer(
        ctx: Context,
        answer_id: int | None = None,
        query: str | None = None,
    ) -> ToolResult:
        store: IslamQAStore = ctx.lifespan_context["store"]
        q = (query or "").strip() or None

        structured: dict[str, Any] = {
            "kind": "empty",
            "answer": None,
            "query": None,
            "search_results": None,
            "search_mode": None,
            "search_note": None,
            "categories": store.list_categories(),
            "search_app_url": _SEARCH_APP_BASE_URL,
            "interactive": True,
        }

        if answer_id is not None:
            row = store.fetch_answer(int(answer_id))
            if row is None:
                return ToolResult(
                    content=[TextContent(type="text", text="Answer not found.")],
                    structured_content=structured,
                )
            row_out = _enrich_answer(row)
            structured["kind"] = "detail"
            structured["answer"] = row_out
            _record_mcp(ctx, "lookup")
            return ToolResult(
                content=[
                    TextContent(type="text", text=_format_detail_fallback(row_out)),
                ],
                structured_content=structured,
            )

        if q:
            sem = await _semantic_search(ctx, q, 30, None)
            if sem.get("ok"):
                results = sem["results"]
                mode = "semantic"
                note = "cached_response" if sem.get("cache_hit") else None
            else:
                kw = _keyword_search(ctx, q, 30, None)
                results = kw["results"]
                reason = sem.get("reason")
                if reason == "semantic_unavailable":
                    note = "Semantic search unavailable; used keyword search."
                elif reason == "rate_limited":
                    note = "Search rate limit exceeded; used keyword search."
                elif reason == "dimension_mismatch":
                    note = "Query embedding size does not match database; used keyword search."
                elif reason == "openai_error":
                    note = "OpenAI embedding failed; used keyword search."
                else:
                    note = "Semantic search failed; used keyword search."
                mode = "keyword_fallback"

            structured["kind"] = "search"
            structured["query"] = q
            structured["search_results"] = results
            structured["search_mode"] = mode
            structured["search_note"] = note
            _record_mcp(ctx, "search")
            return ToolResult(
                content=[
                    TextContent(type="text", text=_format_search_fallback(q, results, note)),
                ],
                structured_content=structured,
            )

        return ToolResult(
            content=[TextContent(type="text", text=_EMPTY_FALLBACK_TEXT)],
            structured_content=structured,
        )

    _ISLAMQA_APP_HTML: str | None = None
    if _ISLAMQA_APP_HTML_PATH.is_file():
        raw_html = _ISLAMQA_APP_HTML_PATH.read_text(encoding="utf-8")
        if _ISLAMQA_APP_SDK_PATH.is_file():
            sdk_js = _ISLAMQA_APP_SDK_PATH.read_text(encoding="utf-8")
            if _ISLAMQA_APP_SDK_PLACEHOLDER not in raw_html:
                logger.warning(
                    "islamqa app html missing SDK placeholder %r",
                    _ISLAMQA_APP_SDK_PLACEHOLDER,
                )
                _ISLAMQA_APP_HTML = raw_html
            else:
                _ISLAMQA_APP_HTML = raw_html.replace(_ISLAMQA_APP_SDK_PLACEHOLDER, sdk_js, 1)
                logger.info(
                    "loaded islamqa app html (%d bytes) from %s",
                    len(_ISLAMQA_APP_HTML),
                    _ISLAMQA_APP_HTML_PATH,
                )
        else:
            logger.warning(
                "islamqa app SDK missing at %s; run scripts/fetch_ext_apps.py",
                _ISLAMQA_APP_SDK_PATH,
            )
            _ISLAMQA_APP_HTML = raw_html
    else:
        logger.warning("islamqa app html missing at %s", _ISLAMQA_APP_HTML_PATH)

    @mcp.resource(
        "ui://islamqa.html",
        name="IslamQA Reader App",
        description=(
            "Interactive IslamQA reader with search, detail view, Arabic text, and citation URLs."
        ),
        mime_type=_ISLAMQA_APP_MIME,
        tags={"preview", "app"},
    )
    async def islamqa_app() -> list[ResourceContent]:
        if _ISLAMQA_APP_HTML is None:
            raise FileNotFoundError(
                f"IslamQA app HTML not found at {_ISLAMQA_APP_HTML_PATH}."
            )
        return [
            ResourceContent(
                _ISLAMQA_APP_HTML,
                mime_type=_ISLAMQA_APP_MIME,
                meta={
                    "ui": {
                        "csp": {
                            "resourceDomains": [],
                        },
                    },
                },
            ),
        ]

    # --- Icon route ---
    _ICON_PATH = Path(__file__).parent / "assets" / "icon.png"
    _icon_bytes: bytes | None = None
    if _ICON_PATH.is_file():
        _icon_bytes = _ICON_PATH.read_bytes()
        logger.info("loaded icon asset (%d bytes)", len(_icon_bytes))

    @mcp.custom_route("/icon.png", methods=["GET", "HEAD"])
    async def serve_icon(request: Request) -> Response:
        if _icon_bytes is None:
            return Response(status_code=404)
        return Response(
            content=_icon_bytes,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )

    # --- REST API ---
    def _api_cors_headers() -> dict[str, str]:
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        }

    def _api_json(data: Any, status: int = 200) -> Response:
        return Response(
            content=json.dumps(data).encode("utf-8"),
            status_code=status,
            media_type="application/json; charset=utf-8",
            headers=_api_cors_headers(),
        )

    def _api_state() -> dict[str, Any] | None:
        return getattr(mcp, "_islamqa_state", None)

    def _api_client_key(request: Request) -> str:
        host = request.client.host if request.client else None
        xff = request.headers.get("x-forwarded-for")
        if xff:
            host = xff.split(",")[0].strip() or host
        return f"ip:{host or 'unknown'}"

    def _record_api(state: dict[str, Any], request: Request, kind: str) -> None:
        if request.method == "HEAD":
            return
        st = state.get("stats")
        if st is not None:
            st.record("api", kind, _api_client_key(request))

    def _api_answer_item(row: dict[str, Any], similarity: float | None = None) -> dict[str, Any]:
        out = _enrich_answer(row)
        if similarity is not None:
            out["similarity"] = similarity
        if not out.get("excerpt_en"):
            out["excerpt_en"] = (out.get("answer_en") or out.get("question_en") or "")[:280]
        return out

    @mcp.custom_route("/api/categories", methods=["GET", "HEAD"])
    async def api_categories(request: Request) -> Response:
        state = _api_state()
        if state is None:
            return _api_json({"error": "server starting"}, status=503)
        return _api_json({"categories": state["store"].list_categories()})

    @mcp.custom_route("/api/answer/{answer_id:int}", methods=["GET", "HEAD"])
    async def api_answer_by_id(request: Request) -> Response:
        state = _api_state()
        if state is None:
            return _api_json({"error": "server starting", "answer": None}, status=503)
        store: IslamQAStore = state["store"]
        try:
            aid = int(request.path_params["answer_id"])
        except (KeyError, ValueError, TypeError):
            return _api_json({"error": "invalid_id", "answer": None}, status=400)
        row = store.fetch_answer(aid)
        if row is None:
            return _api_json({"error": "not_found", "answer": None}, status=404)
        _record_api(state, request, "lookup")
        return _api_json({"answer": _api_answer_item(row)})

    @mcp.custom_route("/api/stats", methods=["GET", "HEAD"])
    async def api_stats(request: Request) -> Response:
        state = _api_state()
        if state is None:
            body = {
                "total_searches": 0,
                "total_lookups": 0,
                "unique_visitors": 0,
                "uptime_seconds": 0,
            }
            return _api_json(body, status=503)
        boot = state.get("stats_boot_mono")
        uptime = int(max(0.0, time.monotonic() - float(boot))) if isinstance(boot, (int, float)) else 0
        st = state.get("stats")
        data = dict(st.get_stats()) if st else {}
        data["uptime_seconds"] = uptime
        return _api_json(data)

    @mcp.custom_route("/api/stats", methods=["OPTIONS"])
    async def api_stats_options(_request: Request) -> Response:
        return Response(status_code=204, headers=_api_cors_headers())

    @mcp.custom_route("/api/search", methods=["GET", "HEAD"])
    async def api_search(request: Request) -> Response:
        state = _api_state()
        if state is None:
            return _api_json({"results": [], "mode": "none", "note": "server starting"}, status=503)
        store: IslamQAStore = state["store"]
        cfg_local: AppConfig = state["config"]
        idx = state.get("embeddings")
        client = state.get("openai")
        cache = state.get("search_cache")
        rl = state.get("search_rate_limiter")

        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return _api_json({"results": [], "mode": "none", "note": "query too short"}, status=400)
        try:
            limit = max(1, min(int(request.query_params.get("limit") or 20), 100))
        except (TypeError, ValueError):
            limit = 20
        cat_filter = (request.query_params.get("category") or "").strip() or None

        def keyword_payload(note: str | None) -> Response:
            cat_id = store.get_category_id(cat_filter) if cat_filter else None
            rows = store.search_answers(q, limit=limit, category_id=cat_id)
            results = [_api_answer_item(r) for r in rows]
            return _api_json({"results": results, "mode": "keyword", "note": note})

        client_key = _api_client_key(request)

        if idx is None or client is None:
            _record_api(state, request, "search")
            return keyword_payload("semantic_unavailable")

        if rl is not None and not rl.allow(client_key):
            _record_api(state, request, "search")
            return keyword_payload("rate_limited")

        cache_key = (q.lower(), limit, cat_filter or "", cfg_local.query_embedding_model)
        if cache is not None:
            hit = cache.get(cache_key)
            if hit is not None:
                _record_api(state, request, "search")
                return _api_json({"results": hit, "mode": "semantic", "note": "cache"})

        allowed_ids: set[int] | None = None
        if cat_filter:
            cat_id = store.get_category_id(cat_filter)
            if cat_id is not None:
                allowed_ids = store.fetch_answer_ids_for_category(cat_id)

        def _embed() -> np.ndarray:
            r = client.embeddings.create(model=cfg_local.query_embedding_model, input=q)
            return np.asarray(r.data[0].embedding, dtype=np.float32)

        try:
            qv = await anyio.to_thread.run_sync(_embed)
        except Exception as exc:  # noqa: BLE001
            if should_fallback_to_keyword(exc):
                _record_api(state, request, "search")
                return keyword_payload(f"openai_error: {exc}")
            raise

        if int(qv.shape[0]) != int(idx.mat.shape[1]):
            _record_api(state, request, "search")
            return keyword_payload("dimension_mismatch")

        if allowed_ids is not None:
            top = idx.topk_filtered(qv, limit, allowed_ids)
        else:
            top = idx.topk(qv, limit)

        ids = [i for i, _ in top]
        scores = {i: s for i, s in top}
        rows = store.fetch_answers_by_ids(ids)
        results = [_api_answer_item(r, float(scores[int(r["id"])])) for r in rows]
        if cache is not None:
            cache.set(cache_key, results)
        _record_api(state, request, "search")
        return _api_json({"results": results, "mode": "semantic", "note": None})

    return mcp
