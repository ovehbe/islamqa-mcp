(() => {
  "use strict";

  const API_BASE = (typeof window !== "undefined" && window.ISLAMQA_API_BASE)
    || "https://api.islamqa-mcp.org";
  const THEME_KEY = "islamqa-search-theme";

  const state = { query: "", page: 1, perPage: 10, results: [], showArabic: false };

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
  const resultsSection = document.getElementById("resultsSection");
  const themeToggle = document.getElementById("themeToggle");

  function setLoading(on) {
    if (!loadingIndicator) return;
    loadingIndicator.hidden = !on;
    if (on) {
      resultsList.replaceChildren();
      pagination.hidden = true;
      statsText.textContent = "Searching…";
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
      return;
    }
    const lookup = parseLookup(q);
    if (lookup) {
      const data = await getJson(`${API_BASE}/api/answer/${lookup.id}`);
      state.results = data.answer ? [data.answer] : [];
      return;
    }
    const params = new URLSearchParams({ q, limit: "100" });
    if (categorySelect.value) params.set("category", categorySelect.value);
    const data = await getJson(`${API_BASE}/api/search?${params}`);
    state.results = data.results || [];
  }

  function formatSimilarity(sim) {
    if (typeof sim !== "number" || !Number.isFinite(sim)) return null;
    return `${Math.round(sim * 100)}% match`;
  }

  function renderResults() {
    resultsList.replaceChildren();
    const total = state.results.length;
    const pages = Math.max(1, Math.ceil(total / state.perPage));
    state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * state.perPage;
    const slice = state.results.slice(start, start + state.perPage);

    statsText.textContent = total
      ? `${total} result${total === 1 ? "" : "s"}`
      : (state.query.trim() ? "No results" : "");

    for (const item of slice) {
      const node = resultTemplate.content.cloneNode(true);
      const card = node.querySelector(".result-card");
      node.querySelector(".result-id").textContent = `#${item.id}`;
      const simEl = node.querySelector(".result-sim");
      const simLabel = formatSimilarity(item.similarity);
      if (simLabel) {
        simEl.textContent = simLabel;
        simEl.hidden = false;
      }
      node.querySelector(".result-title").textContent =
        item.title_en || item.title_ar || "Untitled";
      node.querySelector(".result-excerpt").textContent =
        item.excerpt_en || (item.answer_en || "").slice(0, 220) || (item.question_en || "").slice(0, 220);

      const chips = node.querySelector(".result-chips");
      for (const cat of item.categories || []) {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = cat.name_en || cat.name_ar;
        chips.appendChild(chip);
      }

      const openBtn = node.querySelector(".btn-open");
      const copyBtn = node.querySelector(".btn-copy");
      const sourceLink = node.querySelector(".btn-source");
      const url = item.url || shareUrl(item.id);
      sourceLink.href = item.source_url_en || item.url_en || `https://islamqa.info/en/answers/${item.id}`;
      sourceLink.addEventListener("click", (e) => e.stopPropagation());

      openBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openDetail(item.id);
      });
      copyBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        navigator.clipboard?.writeText(url);
      });
      card.addEventListener("click", () => openDetail(item.id));
      resultsList.appendChild(node);
    }

    pagination.hidden = total <= state.perPage;
    pageText.textContent = `Page ${state.page} of ${pages}`;
    prevPageBtn.disabled = state.page <= 1;
    nextPageBtn.disabled = state.page >= pages;
  }

  function showListView() {
    detailView.hidden = true;
    resultsSection.hidden = false;
    document.querySelector(".search-panel").hidden = false;
    document.querySelector(".hero").hidden = false;
  }

  function showDetailView() {
    detailView.hidden = false;
    resultsSection.hidden = true;
  }

  async function openDetail(id) {
    setLoading(true);
    try {
      const data = await getJson(`${API_BASE}/api/answer/${id}`);
      if (!data.answer) throw new Error("not found");
      renderDetail(data.answer);
      showDetailView();
      history.pushState({ id }, "", `?id=${id}`);
    } catch (e) {
      statsText.textContent = String(e.message || e);
    } finally {
      setLoading(false);
    }
  }

  function renderDetail(item) {
    state.showArabic = false;
    const cats = (item.categories || [])
      .map((c) => c.name_en || c.name_ar)
      .filter(Boolean)
      .join(" · ");

    detailCard.innerHTML = `
      <h1 class="detail-title">${escapeHtml(item.title_en || item.title_ar || "")}</h1>
      <p class="detail-meta">Answer #${item.id}${cats ? ` · ${escapeHtml(cats)}` : ""}</p>
      <section class="detail-section">
        <h3>Question</h3>
        <div class="detail-body">${escapeHtml(item.question_en || item.question_ar || "")}</div>
      </section>
      <section class="detail-section">
        <h3>Answer</h3>
        <div id="answerEn" class="detail-body">${escapeHtml(item.answer_en || "")}</div>
        <div id="answerArWrap" hidden>
          <h3 style="margin-top:16px">الجواب</h3>
          <div id="answerAr" class="detail-body detail-arabic"></div>
          <h3 style="margin-top:16px">السؤال</h3>
          <div id="questionAr" class="detail-body detail-arabic"></div>
        </div>
        ${item.answer_ar || item.question_ar ? '<button type="button" id="toggleArabic" class="btn-show-arabic">Show Arabic</button>' : ""}
      </section>
      <div class="detail-actions">
        <a href="${escapeAttr(item.url || shareUrl(item.id))}" target="_blank" rel="noopener">Open proof link</a>
        <a href="${escapeAttr(item.source_url_en || item.url_en || "")}" target="_blank" rel="noopener">View on IslamQA (EN)</a>
        ${item.url_ar ? `<a href="${escapeAttr(item.url_ar)}" target="_blank" rel="noopener">View on IslamQA (AR)</a>` : ""}
        <button type="button" id="copyProof">Copy proof link</button>
      </div>
    `;

    const toggleBtn = document.getElementById("toggleArabic");
    if (toggleBtn) {
      toggleBtn.addEventListener("click", () => {
        state.showArabic = !state.showArabic;
        const wrap = document.getElementById("answerArWrap");
        wrap.hidden = !state.showArabic;
        if (state.showArabic) {
          document.getElementById("answerAr").textContent = item.answer_ar || "";
          document.getElementById("questionAr").textContent = item.question_ar || "";
          toggleBtn.textContent = "Hide Arabic";
        } else {
          toggleBtn.textContent = "Show Arabic";
        }
      });
    }
    document.getElementById("copyProof")?.addEventListener("click", () => {
      navigator.clipboard?.writeText(item.url || shareUrl(item.id));
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  async function runSearch() {
    state.query = searchInput.value;
    state.page = 1;
    setLoading(true);
    try {
      await performSearch();
      renderResults();
    } catch (e) {
      statsText.textContent = String(e.message || e);
      resultsList.replaceChildren();
    } finally {
      setLoading(false);
    }
  }

  function bootstrapFromUrl() {
    const params = new URLSearchParams(location.search);
    const id = params.get("id");
    if (id && /^\d+$/.test(id)) {
      openDetail(Number.parseInt(id, 10));
      return;
    }
    const q = params.get("q");
    if (q) {
      searchInput.value = q;
      state.query = q;
      runSearch();
    }
  }

  searchBtn.addEventListener("click", runSearch);
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runSearch();
  });
  clearBtn.addEventListener("click", () => {
    searchInput.value = "";
    state.query = "";
    state.results = [];
    renderResults();
    showListView();
    history.pushState({}, "", location.pathname);
  });
  prevPageBtn.addEventListener("click", () => {
    state.page -= 1;
    renderResults();
  });
  nextPageBtn.addEventListener("click", () => {
    state.page += 1;
    renderResults();
  });
  backToSearchBtn.addEventListener("click", () => {
    showListView();
    history.pushState({}, "", location.pathname);
  });
  themeToggle?.addEventListener("click", toggleTheme);
  window.addEventListener("popstate", bootstrapFromUrl);



  const topNav = document.getElementById("topNav");
  if (topNav) {
    const onNavScroll = () => topNav.classList.toggle("scrolled", window.scrollY > 10);
    window.addEventListener("scroll", onNavScroll, { passive: true });
    onNavScroll();
  }

  const globalUsageStats = document.getElementById("globalUsageStats");

  function formatCount(n) {
    if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return "0";
    if (n < 1000) return String(Math.floor(n));
    for (const { v, s } of [{ v: 1e9, s: "b" }, { v: 1e6, s: "m" }, { v: 1e3, s: "k" }]) {
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
      if (window.location?.protocol && window.location.protocol !== "file:") {
        u.add(new URL("/api/stats", window.location.origin).href);
      }
    } catch {
      /* ignore */
    }
    u.add(`${API_BASE}/api/stats`);
    u.add("https://api.islamqa-mcp.org/api/stats");
    return [...u];
  }

  async function loadGlobalUsageStats() {
    if (!globalUsageStats) return;
    for (const url of statsUrlCandidates()) {
      try {
        const res = await fetch(url, { cache: "default", mode: "cors" });
        if (!res.ok) continue;
        const j = await res.json();
        globalUsageStats.classList.remove("global-usage-pending", "global-usage-missing");
        globalUsageStats.textContent =
          `${formatCount(j.total_searches ?? 0)} searches  ·  ${formatCount(j.total_lookups ?? 0)} lookups  ·  ${formatCount(j.unique_visitors ?? 0)} users`;
        return;
      } catch {
        /* try next */
      }
    }
    globalUsageStats.classList.add("global-usage-missing");
    globalUsageStats.classList.remove("global-usage-pending");
    globalUsageStats.textContent = "Stats unavailable (check /api and deploy)";
  }

  loadGlobalUsageStats();

  loadCategories().catch(() => {});
  bootstrapFromUrl();
})();
