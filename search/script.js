(() => {
  "use strict";

  const API_BASE = (typeof window !== "undefined" && window.ISLAMQA_API_BASE)
    || "https://api.islamqa-mcp.org";

  const CLAMP_OVERFLOW_PX = 4;
  const THEME_KEY = "islamqa-search-theme";

  const state = {
    query: "",
    page: 1,
    perPage: 10,
    results: [],
    singleLookup: false,
  };

  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === "dark" || theme === "light") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }

  function resolvedTheme() {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "dark" || saved === "light") return saved;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function toggleTheme() {
    const next = resolvedTheme() === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  }

  const savedTheme = localStorage.getItem(THEME_KEY);
  if (savedTheme === "dark" || savedTheme === "light") applyTheme(savedTheme);

  const searchInput = document.getElementById("searchInput");
  const searchBtn = document.getElementById("searchBtn");
  const clearBtn = document.getElementById("clearBtn");
  const categorySelect = document.getElementById("categorySelect");
  const statsText = document.getElementById("statsText");
  const resultsList = document.getElementById("resultsList");
  const pagination = document.getElementById("pagination");
  const prevPageBtn = document.getElementById("prevPageBtn");
  const nextPageBtn = document.getElementById("nextPageBtn");
  const pageText = document.getElementById("pageText");
  const resultTemplate = document.getElementById("resultTemplate");
  const loadingIndicator = document.getElementById("loadingIndicator");
  const detailView = document.getElementById("detailView");
  const detailCard = document.getElementById("detailCard");
  const backToSearchBtn = document.getElementById("backToSearch");

  function setLoading(on) {
    if (!loadingIndicator) return;
    loadingIndicator.hidden = !on;
    if (on) {
      resultsList.replaceChildren();
      pagination.hidden = true;
      statsText.textContent = "Searching\u2026";
    }
  }

  async function getJson(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`Request failed (${r.status})`);
    return r.json();
  }

  function parseLookup(query) {
    const m = query.trim().match(/^#?(\d+)$/);
    return m ? { id: Number.parseInt(m[1], 10) } : null;
  }

  function shareUrl(id) {
    return `${location.origin}/?id=${id}`;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function loadCategories() {
    const data = await getJson(`${API_BASE}/api/categories`);
    for (const c of data.categories || []) {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.name_en || c.name_ar || `Category ${c.id}`;
      categorySelect.appendChild(opt);
    }
  }

  async function performSearch() {
    const q = state.query.trim();
    if (!q) {
      state.results = [];
      state.singleLookup = false;
      return;
    }
    const lookup = parseLookup(q);
    if (lookup) {
      const data = await getJson(`${API_BASE}/api/answer/${lookup.id}`);
      state.results = data.answer ? [data.answer] : [];
      state.singleLookup = true;
      return;
    }
    const params = new URLSearchParams({ q, limit: "100" });
    if (categorySelect.value) params.set("category", categorySelect.value);
    const data = await getJson(`${API_BASE}/api/search?${params}`);
    state.results = data.results || [];
    state.singleLookup = false;
  }

  function formatSimilarity(sim) {
    if (typeof sim !== "number" || !Number.isFinite(sim) || sim <= 0) return null;
    const pct = Math.round(sim * 100);
    return pct > 0 ? `${pct}% match` : null;
  }

  let toastTimer = null;
  function showToast(msg) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    requestAnimationFrame(() => el.classList.add("is-visible"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("is-visible"), 1400);
  }

  async function copyToClipboard(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
      if (btn) {
        btn.classList.add("is-copied");
        setTimeout(() => btn.classList.remove("is-copied"), 900);
      }
      showToast("Copied");
    } catch {
      showToast("Copy failed");
    }
  }

  function setupClamp(textEl, expandBtn, expanded) {
    if (expanded) {
      textEl.classList.remove("is-clamped");
      expandBtn.hidden = true;
      return;
    }
    textEl.classList.add("is-clamped");
    requestAnimationFrame(() => {
      const overflow = textEl.scrollHeight - textEl.clientHeight > CLAMP_OVERFLOW_PX;
      if (overflow) {
        expandBtn.hidden = false;
        expandBtn.textContent = "Show more";
      } else {
        textEl.classList.remove("is-clamped");
        expandBtn.hidden = true;
      }
    });
  }

  function excerptText(item) {
    return item.excerpt_en
      || (item.answer_en || "").slice(0, 400)
      || (item.question_en || "").slice(0, 400)
      || "";
  }

  function renderCard(item, { detail = false } = {}) {
    const card = resultTemplate.content.firstElementChild.cloneNode(true);
    if (detail) card.classList.add("is-detail");

    card.querySelector(".chip-id").textContent = `#${item.id}`;

    const simEl = card.querySelector(".chip-sim");
    const simLabel = formatSimilarity(item.similarity);
    if (simLabel) {
      simEl.textContent = simLabel;
      simEl.hidden = false;
    }

    card.querySelector(".result-title").textContent =
      item.title_en || item.title_ar || "Untitled";

    const textEl = card.querySelector(".english-text");
    const expandBtn = card.querySelector(".expand-btn");

    if (detail) {
      const bodyWrap = card.querySelector(".result-body");
      bodyWrap.innerHTML = "";

      if (item.question_en || item.question_ar) {
        const qSection = document.createElement("div");
        qSection.className = "detail-section";
        const qHeading = document.createElement("h3");
        qHeading.className = "detail-section-heading";
        qHeading.textContent = "Question";
        const qBody = document.createElement("div");
        qBody.className = "english-text";
        qBody.style.whiteSpace = "pre-wrap";
        qBody.textContent = item.question_en || "";
        qSection.appendChild(qHeading);
        qSection.appendChild(qBody);
        bodyWrap.appendChild(qSection);
      }

      if (item.answer_en || item.answer_ar) {
        const aSection = document.createElement("div");
        aSection.className = "detail-section";
        const aHeading = document.createElement("h3");
        aHeading.className = "detail-section-heading";
        aHeading.textContent = "Answer";
        const aBody = document.createElement("div");
        aBody.className = "english-text";
        aBody.style.whiteSpace = "pre-wrap";
        aBody.textContent = item.answer_en || "";
        aSection.appendChild(aHeading);
        aSection.appendChild(aBody);
        bodyWrap.appendChild(aSection);
      }

      if (item.answer_ar || item.question_ar) {
        const arBtn = document.createElement("button");
        arBtn.type = "button";
        arBtn.className = "btn-show-arabic";
        arBtn.textContent = "Show Arabic";
        let arVisible = false;

        const arWrap = document.createElement("div");
        arWrap.hidden = true;

        if (item.question_ar) {
          const h = document.createElement("h3");
          h.className = "detail-section-heading";
          h.textContent = "\u0627\u0644\u0633\u0624\u0627\u0644";
          const p = document.createElement("p");
          p.className = "arabic-text";
          p.dir = "rtl";
          p.lang = "ar";
          p.textContent = item.question_ar;
          arWrap.appendChild(h);
          arWrap.appendChild(p);
        }
        if (item.answer_ar) {
          const h = document.createElement("h3");
          h.className = "detail-section-heading";
          h.textContent = "\u0627\u0644\u062c\u0648\u0627\u0628";
          const p = document.createElement("p");
          p.className = "arabic-text";
          p.dir = "rtl";
          p.lang = "ar";
          p.textContent = item.answer_ar;
          arWrap.appendChild(h);
          arWrap.appendChild(p);
        }

        arBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          arVisible = !arVisible;
          arWrap.hidden = !arVisible;
          arBtn.textContent = arVisible ? "Hide Arabic" : "Show Arabic";
        });

        bodyWrap.appendChild(arBtn);
        bodyWrap.appendChild(arWrap);
      }

      const links = document.createElement("div");
      links.className = "detail-links";

      const proofLink = document.createElement("a");
      proofLink.href = shareUrl(item.id);
      proofLink.target = "_blank";
      proofLink.rel = "noopener";
      proofLink.textContent = "Open proof link";
      links.appendChild(proofLink);

      const srcEn = item.source_url_en || item.url_en || `https://islamqa.info/en/answers/${item.id}`;
      if (srcEn) {
        const a = document.createElement("a");
        a.href = srcEn;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = "View on IslamQA (EN)";
        links.appendChild(a);
      }

      if (item.url_ar || item.source_url_ar) {
        const a = document.createElement("a");
        a.href = item.url_ar || item.source_url_ar;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = "View on IslamQA (AR)";
        links.appendChild(a);
      }

      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.textContent = "Copy proof link";
      copyBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        copyToClipboard(shareUrl(item.id), copyBtn);
      });
      links.appendChild(copyBtn);

      bodyWrap.appendChild(links);
    } else {
      textEl.textContent = excerptText(item);
      let expanded = false;
      setupClamp(textEl, expandBtn, expanded);
      expandBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        expanded = !expanded;
        if (expanded) {
          textEl.classList.remove("is-clamped");
          expandBtn.textContent = "Show less";
        } else {
          textEl.classList.add("is-clamped");
          expandBtn.textContent = "Show more";
        }
      });

      if (item.answer_ar || item.question_ar) {
        const arBlock = card.querySelector(".arabic-block");
        const arText = card.querySelector(".arabic-text");
        const preview = (item.answer_ar || item.question_ar || "").slice(0, 300);
        arText.textContent = preview;
        arBlock.hidden = false;
      }
    }

    const chips = card.querySelector(".result-chips");
    for (const cat of item.categories || []) {
      const chip = document.createElement("span");
      chip.className = "chip chip-cat";
      chip.textContent = cat.name_en || cat.name_ar;
      chips.appendChild(chip);
    }

    const sourceLink = card.querySelector(".source-link");
    sourceLink.href = item.source_url_en || item.url_en || `https://islamqa.info/en/answers/${item.id}`;
    sourceLink.addEventListener("click", (e) => e.stopPropagation());

    card.querySelector(".copy-link-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      copyToClipboard(shareUrl(item.id), e.currentTarget);
    });

    card.querySelector(".copy-text-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      const title = item.title_en || item.title_ar || "";
      const body = [
        `#${item.id} — ${title}`,
        "",
        item.question_en ? `Q: ${item.question_en.slice(0, 500)}` : "",
        "",
        item.answer_en ? item.answer_en.slice(0, 1000) : "",
        "",
        shareUrl(item.id),
      ].filter(Boolean).join("\n");
      copyToClipboard(body, e.currentTarget);
    });

    return card;
  }

  function enterDetailMode() {
    document.body.classList.add("is-detail-mode");
    resultsList.hidden = true;
    pagination.hidden = true;
    loadingIndicator && (loadingIndicator.hidden = true);
    detailView.hidden = false;
  }

  function exitDetailMode() {
    document.body.classList.remove("is-detail-mode");
    detailView.hidden = true;
    detailCard.replaceChildren();
    resultsList.hidden = false;
    history.replaceState(null, "", location.pathname);
    searchInput.value = "";
    state.query = "";
    state.results = [];
    state.singleLookup = false;
    state.page = 1;
    render();
    window.scrollTo({ top: 0 });
  }

  async function openDetailView(answerId) {
    enterDetailMode();
    detailCard.innerHTML = '<div class="loading-indicator"><span class="spinner" aria-hidden="true"></span><span class="loading-text">Loading\u2026</span></div>';

    const currentId = new URLSearchParams(location.search).get("id");
    if (currentId !== String(answerId)) {
      history.pushState({ answerId }, "", `?id=${answerId}`);
    }

    try {
      const data = await getJson(`${API_BASE}/api/answer/${answerId}`);
      if (!data.answer) {
        detailCard.innerHTML = '<div class="empty">Answer not found.</div>';
        return;
      }
      detailCard.replaceChildren(renderCard(data.answer, { detail: true }));
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      detailCard.innerHTML = `<div class="empty">Failed to load: ${escapeHtml(err)}</div>`;
    }
  }

  backToSearchBtn?.addEventListener("click", exitDetailMode);

  function render() {
    const total = state.results.length;
    if (total === 0) {
      statsText.textContent = state.query ? "No results found." : "Enter a query to start.";
      resultsList.innerHTML = state.query ? '<div class="empty">No results.</div>' : "";
      pagination.hidden = true;
      return;
    }

    if (state.singleLookup && total === 1) {
      statsText.textContent = "Direct lookup.";
      resultsList.replaceChildren(renderCard(state.results[0], { detail: true }));
      pagination.hidden = true;
      return;
    }

    const pages = Math.max(1, Math.ceil(total / state.perPage));
    state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * state.perPage;
    const end = Math.min(total, start + state.perPage);
    const rows = state.results.slice(start, end);
    statsText.textContent = `Showing ${start + 1}\u2013${end} of ${total} result(s).`;

    const frag = document.createDocumentFragment();
    for (const item of rows) {
      const card = renderCard(item);
      card.addEventListener("click", (e) => {
        if (e.target.closest(".icon-btn, .expand-btn, a, .btn-show-arabic")) return;
        openDetailView(item.id);
      });
      frag.appendChild(card);
    }
    resultsList.replaceChildren(frag);

    pagination.hidden = pages <= 1;
    prevPageBtn.disabled = state.page <= 1;
    nextPageBtn.disabled = state.page >= pages;
    pageText.textContent = `Page ${state.page} of ${pages}`;
  }

  async function submit() {
    state.query = searchInput.value.trim();
    state.page = 1;

    const lookup = parseLookup(state.query);
    if (lookup) {
      openDetailView(lookup.id);
      return;
    }

    setLoading(true);
    try {
      await performSearch();
      render();
    } catch (err) {
      statsText.textContent = String(err);
      resultsList.innerHTML = '<div class="empty">Search failed.</div>';
      pagination.hidden = true;
    } finally {
      setLoading(false);
    }
  }

  searchBtn.addEventListener("click", submit);
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });
  clearBtn.addEventListener("click", () => {
    state.query = "";
    state.results = [];
    state.singleLookup = false;
    state.page = 1;
    searchInput.value = "";
    render();
  });
  categorySelect.addEventListener("change", () => {
    if (state.query) submit();
  });
  prevPageBtn.addEventListener("click", () => {
    state.page -= 1;
    render();
  });
  nextPageBtn.addEventListener("click", () => {
    state.page += 1;
    render();
  });

  function formatCount(n) {
    if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return "0";
    if (n < 1000) return String(Math.floor(n));
    const tiers = [
      { v: 1e9, s: "b" },
      { v: 1e6, s: "m" },
      { v: 1e3, s: "k" },
    ];
    for (const { v, s } of tiers) {
      if (n >= v) {
        const t = n / v;
        return (t >= 10 ? t.toFixed(0) : t.toFixed(1)) + s;
      }
    }
    return String(n);
  }

  function statsUrlCandidates() {
    const u = new Set();
    try {
      if (typeof window !== "undefined" && window.location) {
        const p = String(window.location.protocol || "");
        if (p && p !== "file:") {
          u.add(new URL("/api/stats", window.location.origin).href);
          u.add(new URL("/api/stats/", window.location.origin).href);
        }
      }
    } catch { /* ignore */ }
    u.add(`${API_BASE}/api/stats`);
    u.add(`${API_BASE}/api/stats/`);
    u.add("https://api.islamqa-mcp.org/api/stats");
    return [...u];
  }

  const globalUsageStats = document.getElementById("globalUsageStats");

  async function loadGlobalUsageStats() {
    if (!globalUsageStats) return;
    for (const url of statsUrlCandidates()) {
      try {
        const res = await fetch(url, { cache: "default", mode: "cors" });
        if (!res.ok) continue;
        const j = await res.json();
        const s = j.total_searches ?? 0;
        const l = j.total_lookups ?? 0;
        const u = j.unique_visitors ?? 0;
        globalUsageStats.classList.remove("global-usage-pending", "global-usage-missing");
        globalUsageStats.textContent =
          `${formatCount(s)} searches  \u00b7  ${formatCount(l)} lookups  \u00b7  ${formatCount(u)} users`;
        return;
      } catch { /* try next */ }
    }
    globalUsageStats.classList.add("global-usage-missing");
    globalUsageStats.classList.remove("global-usage-pending");
    globalUsageStats.textContent = "";
  }

  function bootstrapFromUrl() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("app") === "1") document.body.classList.add("is-embedded");

    const id = params.get("id");
    if (id && /^\d+$/.test(id.trim())) {
      openDetailView(Number.parseInt(id.trim(), 10));
      return true;
    }

    const q = params.get("q");
    if (q && q.trim()) {
      searchInput.value = q.trim();
      submit();
      return true;
    }
    return false;
  }

  window.addEventListener("popstate", () => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get("id");
    if (id && /^\d+$/.test(id.trim())) {
      openDetailView(Number.parseInt(id.trim(), 10));
    } else if (document.body.classList.contains("is-detail-mode")) {
      exitDetailMode();
    }
  });

  document.getElementById("themeToggle")?.addEventListener("click", toggleTheme);

  loadGlobalUsageStats();

  loadCategories()
    .catch(() => {
      statsText.textContent = "Could not load categories.";
    })
    .finally(() => {
      if (!bootstrapFromUrl()) render();
    });
})();
