// الموقع العام: استكشاف المصادر، المكتبة، والقارئ المدمج.
// ملاحظة: reader.js يملك منطق الترجمة المباشرة وعناصره (chapterForm/status/pages).
(() => {
  const $ = (id) => document.getElementById(id);

  const exploreSource = $("exploreSource");
  const exploreQuery = $("exploreQuery");
  const exploreSearchBtn = $("exploreSearchBtn");
  const exploreStatus = $("exploreStatus");
  const exploreResults = $("exploreResults");
  const chapterListPanel = $("chapterListPanel");
  const chapterListTitle = $("chapterListTitle");
  const chapterList = $("chapterList");
  const libraryList = $("libraryList");
  const sourceList = $("sourceList");
  const readerView = $("readerView");
  const readerClose = $("readerClose");
  const readerTitle = $("readerTitle");
  const readerStatus = $("readerStatus");
  const readerPages = $("readerPages");

  function api(path, options) {
    return fetch(path, options).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "تعذّر إكمال الطلب.");
      return data;
    });
  }

  function setText(element, message) {
    if (element) element.textContent = message || "";
  }

  function clearChildren(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  // ===== المصادر =====
  async function loadSources() {
    try {
      const { sources } = await api("/api/sources");
      clearChildren(sourceList);
      exploreSource.textContent = "";
      for (const source of sources) {
        const card = document.createElement("article");
        card.className = "source-card";
        const title = document.createElement("h3");
        title.textContent = source.name;
        const note = document.createElement("p");
        note.textContent = source.search_note
          || (source.supports_title_search ? "يدعم البحث بالعنوان." : "الجلب برابط الفصل مباشرة.");
        card.append(title, note);
        sourceList.appendChild(card);
        if (source.supports_title_search) {
          const option = document.createElement("option");
          option.value = source.id;
          option.textContent = source.name;
          exploreSource.appendChild(option);
        }
      }
      if (!sources.length) sourceList.textContent = "لا توجد مصادر مسجلة حالياً.";
      if (!exploreSource.options.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "لا يوجد مصدر يدعم البحث";
        exploreSource.appendChild(option);
        exploreSearchBtn.disabled = true;
      }
    } catch (error) {
      sourceList.textContent = `تعذر تحميل المصادر: ${error.message}`;
    }
  }

  // ===== البحث عن الأعمال =====
  async function searchWorks() {
    const providerId = exploreSource.value;
    const query = exploreQuery.value.trim();
    if (!providerId) { setText(exploreStatus, "اختر مصدراً يدعم البحث أولاً."); return; }
    if (query.length < 2) { setText(exploreStatus, "أدخل حرفين على الأقل للبحث."); return; }
    exploreSearchBtn.disabled = true;
    setText(exploreStatus, "جارٍ البحث…");
    clearChildren(exploreResults);
    chapterListPanel.hidden = true;
    try {
      const { results } = await api(`/api/sources/${encodeURIComponent(providerId)}/search?q=${encodeURIComponent(query)}`);
      if (!results.length) { setText(exploreStatus, "لا نتائج مطابقة. جرّب عنواناً آخر."); return; }
      setText(exploreStatus, `عُثر على ${results.length} عمل.`);
      for (const work of results) {
        const card = document.createElement("article");
        card.className = "work-card";
        const cover = document.createElement("div");
        cover.className = "work-cover";
        if (work.cover_filename) {
          const img = document.createElement("img");
          img.loading = "lazy";
          img.alt = `غلاف ${work.title}`;
          img.src = `https://uploads.mangadex.org/covers/${encodeURIComponent(work.id)}/${encodeURIComponent(work.cover_filename)}.256.jpg`;
          cover.appendChild(img);
        }
        const info = document.createElement("div");
        info.className = "work-info";
        const title = document.createElement("h3");
        title.textContent = work.title || "بدون عنوان";
        const description = document.createElement("p");
        description.textContent = (work.description || "").slice(0, 180) || work.status || "";
        const open = document.createElement("button");
        open.type = "button";
        open.textContent = "عرض الفصول";
        open.addEventListener("click", () => openChapters(providerId, work));
        info.append(title, description, open);
        card.append(cover, info);
        exploreResults.appendChild(card);
      }
    } catch (error) {
      setText(exploreStatus, error.message);
    } finally {
      exploreSearchBtn.disabled = false;
    }
  }

  // ===== فصول العمل =====
  async function openChapters(providerId, work) {
    chapterListPanel.hidden = false;
    chapterListTitle.textContent = `فصول: ${work.title}`;
    chapterList.textContent = "جارٍ تحميل الفصول…";
    try {
      const { chapters } = await api(`/api/sources/${encodeURIComponent(providerId)}/works/${encodeURIComponent(work.id)}/chapters`);
      clearChildren(chapterList);
      if (!chapters.length) { chapterList.textContent = "لا توجد فصول إنجليزية متاحة لهذا العمل."; return; }
      for (const chapter of chapters) {
        const item = document.createElement("li");
        const name = document.createElement("span");
        name.className = "ch-name";
        name.textContent = `الفصل ${chapter.chapter}${chapter.title ? ` — ${chapter.title}` : ""}`;
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "استيراد";
        button.addEventListener("click", () => importChapter(providerId, chapter, button));
        item.append(name, button);
        chapterList.appendChild(item);
      }
    } catch (error) {
      chapterList.textContent = error.message;
    }
  }

  async function importChapter(providerId, chapter, button) {
    button.disabled = true;
    button.textContent = "جارٍ الاستيراد…";
    try {
      const { chapter: saved, already_imported } = await api("/api/source-import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: chapter.url, reading_layout: "vertical", series_type: "manhwa", reading_direction: "ltr" }),
      });
      button.textContent = already_imported ? "مستورد مسبقاً" : "تم الاستيراد";
      await loadLibrary();
      openReader(saved);
    } catch (error) {
      button.disabled = false;
      button.textContent = "استيراد";
      setText(exploreStatus, error.message);
    }
  }

  // ===== المكتبة =====
  async function loadLibrary() {
    try {
      const { chapters } = await api("/api/library");
      clearChildren(libraryList);
      if (!chapters.length) { libraryList.textContent = "المكتبة فارغة — استورد فصلاً من قسم استكشاف المصادر."; return; }
      for (const chapter of chapters) {
        const card = document.createElement("article");
        card.className = "library-card";
        const title = document.createElement("h3");
        title.textContent = chapter.title || chapter.series_title || "فصل بدون عنوان";
        const meta = document.createElement("p");
        meta.textContent = `${chapter.page_count || 0} صفحة`;
        const actions = document.createElement("div");
        actions.className = "library-actions";
        const read = document.createElement("button");
        read.type = "button";
        read.textContent = "اقرأ";
        read.addEventListener("click", () => openReader(chapter));
        const archive = document.createElement("button");
        archive.type = "button";
        archive.className = "archive-btn";
        archive.textContent = "أرشفة";
        archive.addEventListener("click", async () => {
          await api(`/api/library/${chapter.id}/archive`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ archived: true }),
          });
          await loadLibrary();
        });
        actions.append(read, archive);
        card.append(title, meta, actions);
        libraryList.appendChild(card);
      }
    } catch (error) {
      libraryList.textContent = `تعذر تحميل المكتبة: ${error.message}`;
    }
  }

  // ===== القارئ المدمج =====
  async function openReader(chapter) {
    readerView.hidden = false;
    readerTitle.textContent = chapter.title || chapter.series_title || "قارئ الفصل";
    clearChildren(readerPages);
    setText(readerStatus, "جارٍ تحميل الصفحات…");
    readerView.scrollIntoView({ behavior: "smooth" });
    try {
      const data = await api(`/api/library/${chapter.id}`);
      const detail = data.chapter || data;
      const pages = Array.isArray(detail.pages) ? detail.pages : [];
      setText(readerStatus, pages.length ? `${pages.length} صفحة.` : "لا صفحات محفوظة لهذا الفصل.");
      for (const page of pages) {
        const figure = document.createElement("figure");
        figure.className = "page";
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = `صفحة ${page.page_number}`;
        const renderedSrc = page.rendered_url || `/api/library/${chapter.id}/pages/${page.page_number}/rendered`;
        const originalSrc = page.image_url || `/api/library/${chapter.id}/pages/${page.page_number}/image`;
        img.src = renderedSrc;
        img.addEventListener("error", () => {
          if (img.dataset.fallback) { figure.remove(); return; }
          img.dataset.fallback = "1";
          img.src = originalSrc;
        }, { once: false });
        figure.appendChild(img);
        readerPages.appendChild(figure);
      }
    } catch (error) {
      setText(readerStatus, error.message);
    }
  }

  readerClose.addEventListener("click", () => {
    readerView.hidden = true;
    document.getElementById("library").scrollIntoView({ behavior: "smooth" });
  });
  exploreSearchBtn.addEventListener("click", searchWorks);
  exploreQuery.addEventListener("keydown", (event) => { if (event.key === "Enter") searchWorks(); });

  loadSources();
  loadLibrary();
})();
