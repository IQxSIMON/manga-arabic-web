const loadBtn = document.getElementById("loadBtn");
const statusBox = document.getElementById("status");
const pagesBox = document.getElementById("pages");
const readingLayout = document.getElementById("readingLayout");
const unsupportedLayoutMessage = "هذا نمط تقليب صفحات، وهو غير مدعوم حالياً. يركز التطبيق على الفصول ذات التمرير العمودي من أعلى إلى أسفل.";
const chooseLayoutMessage = "حدّد نمط الفصل أولاً: التمرير العمودي مدعوم، وتقليب الصفحات المنفصلة غير مدعوم حالياً.";

function isVerticalLayoutSelected() {
  if (readingLayout.value === "vertical") return true;
  const message = readingLayout.value === "page_by_page" ? unsupportedLayoutMessage : chooseLayoutMessage;
  window.alert(message);
  setStatus(message);
  return false;
}

readingLayout.addEventListener("change", () => {
  const supported = readingLayout.value === "vertical";
  document.getElementById("readingLayoutNote").textContent = supported
    ? "النمط مدعوم عند كون النسخة إنجليزية."
    : readingLayout.value === "page_by_page" ? unsupportedLayoutMessage : "اختر نمط قراءة الفصل قبل الاستيراد أو الترجمة.";
  if (readingLayout.value === "page_by_page") window.alert(unsupportedLayoutMessage);
});

function setStatus(message) { statusBox.textContent = message || ""; }

const chapterUrlField = document.getElementById("chapterUrl");
let pasteStartTimer = null;
chapterUrlField.addEventListener("paste", () => {
  window.clearTimeout(pasteStartTimer);
  pasteStartTimer = window.setTimeout(() => {
    if (chapterUrlField.value.trim() && !loadBtn.disabled) loadBtn.click();
  }, 80);
});
chapterUrlField.addEventListener("keydown", event => {
  if (event.key === "Enter") {
    event.preventDefault();
    if (!loadBtn.disabled) loadBtn.click();
  }
});

function formatDuration(seconds) {
  const totalSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
  if (totalSeconds < 60) return `${totalSeconds}ث`;
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  if (minutes < 60) return remainingSeconds ? `${minutes}د ${remainingSeconds}ث` : `${minutes}د`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours}س ${remainingMinutes}د` : `${hours}س`;
}

const importForm = document.getElementById("importForm");
const libraryList = document.getElementById("libraryList");
const localReader = document.getElementById("localReader");
const glossaryList = document.getElementById("glossaryList");
let readingPositionObserver = null;
let readingPositionTimer = null;
let readingPositionSaveQueue = Promise.resolve();

async function loadGlossary() {
  const response = await fetch("/api/glossary");
  const data = await response.json();
  glossaryList.replaceChildren();
  data.terms.forEach(term => {
    const row = document.createElement("div");
    row.className = "glossary-row";
    const source = document.createElement("span");
    source.dir = "auto";
    source.textContent = term.source_term;
    const target = document.createElement("span");
    target.dir = "auto";
    target.textContent = term.target_term;
    const scope = document.createElement("small");
    scope.textContent = term.scope_key === "*" ? "مصطلح عام" : `خاص بالسلسلة: ${term.scope_key}`;
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "secondary glossary-edit";
    edit.textContent = "تعديل المصطلح";
    edit.addEventListener("click", () => {
      const form = document.getElementById("glossaryForm");
      form.elements.source.value = term.source_term;
      form.elements.target.value = term.target_term;
      form.elements.scope.value = term.scope_key === "*" ? "" : term.scope_key;
      // This endpoint updates by the normalized source+scope key. Keep that
      // key stable while editing so the action cannot accidentally add a duplicate.
      form.elements.source.readOnly = true;
      form.elements.scope.readOnly = true;
      form.querySelector('button[type="submit"]').textContent = "حفظ التعديل";
      document.getElementById("cancelGlossaryEdit").hidden = false;
      form.scrollIntoView({behavior: "smooth", block: "center"});
      form.elements.target.focus();
    });
    row.append(source, target, scope, edit);
    glossaryList.append(row);
  });
}

async function loadTranslationEngine({probe = false} = {}) {
  const select = document.getElementById("translationEngine");
  const note = document.getElementById("translationEngineNote");
  const assetStatus = document.getElementById("translationAssetStatus");
  try {
    const response = await fetch(`/api/translation-engine${probe ? "?probe=1" : ""}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "تعذر معرفة محرك الترجمة.");
    select.replaceChildren();
    (data.engines || []).forEach(engine => {
      const option = document.createElement("option");
      option.value = engine.id;
      option.textContent = engine.available
        ? engine.label
        : `${engine.label} — ${engine.reason || "غير متاح"}`;
      option.disabled = !engine.available;
      select.append(option);
    });
    select.value = data.current || "index_translate_9b";
    note.textContent = data.note || "يعمل التطبيق بمحرك Index المحلي فقط.";
    const indexStatus = data.model_status?.index_translate_9b;
    if (indexStatus) {
      assetStatus.textContent = indexStatus.available
        ? "ملفات النموذج وllama.cpp موجودة محلياً. وجودها لا يعني أن الخادم يعمل؛ فحص الاتصال لا يشغّل النموذج."
        : `المحرك غير جاهز: ${indexStatus.availability_reason || "ملفات التشغيل ناقصة"}. راجع المسارات الموضحة أعلاه.`;
      assetStatus.classList.toggle("status-error", !indexStatus.available);
    } else {
      assetStatus.textContent = "لم تصل حالة ملفات النموذج من الخادم.";
      assetStatus.classList.add("status-error");
    }
    const serverDiagnostic = data.server_diagnostic;
    if (serverDiagnostic) {
      assetStatus.textContent += ` ${serverDiagnostic.message || "انتهى فحص الخادم المحلي."}`;
      assetStatus.classList.toggle("status-error", !["ready", "not_running"].includes(serverDiagnostic.status));
      assetStatus.dataset.serverStatus = serverDiagnostic.status || "unknown";
    }
  } catch (error) {
    note.textContent = error.message;
    assetStatus.textContent = "تعذر قراءة حالة الملفات المحلية.";
    assetStatus.classList.add("status-error");
  }
}

document.getElementById("refreshTranslationAssets").addEventListener("click", async event => {
  event.currentTarget.disabled = true;
  try { await loadTranslationEngine({probe: true}); }
  finally { event.currentTarget.disabled = false; }
});

document.getElementById("translationEngine").addEventListener("change", async event => {
  const select = event.currentTarget; const note = document.getElementById("translationEngineNote");
  select.disabled = true;
  try {
    const response = await fetch("/api/translation-engine", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({engine:select.value})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || "تعذر تغيير المحرك.");
    note.textContent = data.note || "يعمل التطبيق بمحرك Index المحلي فقط.";
  } catch (error) { note.textContent = error.message; await loadTranslationEngine(); }
  finally { select.disabled = false; }
});

async function loadLibrary() {
  await Promise.all([
    loadLibraryList(libraryList, false),
    loadLibraryList(document.getElementById("archivedLibraryList"), true),
  ]);
}

function sourceCreditsElement(chapter) {
  const providerNames = {mangadex: "MangaDex", manhuaplus: "ManhuaPlus.org"};
  const providerName = providerNames[chapter.source_provider];
  if (!providerName) return null;
  const line = document.createElement("small");
  line.className = "note";
  const url = chapter.source_chapter_url;
  if (url) {
    try {
      const parsed = new URL(url);
      const allowedHost = chapter.source_provider === "mangadex"
        ? ["mangadex.org", "www.mangadex.org"]
        : ["manhuaplus.org", "www.manhuaplus.org"];
      if (parsed.protocol === "https:" && allowedHost.includes(parsed.hostname)) {
        const link = document.createElement("a");
        link.href = parsed.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = providerName;
        line.append("المصدر: ", link);
      }
    } catch (_) { /* Ignore malformed saved source URLs. */ }
  }
  if (!line.childNodes.length) line.append(`المصدر: ${providerName}`);
  const credits = (Array.isArray(chapter.source_attributions) ? chapter.source_attributions : [])
    .filter(item => typeof item?.name === "string" && item.name.trim() && item.name.trim() !== providerName);
  if (credits.length) {
    line.append(" · مجموعات النشر: ");
    credits.forEach((credit, index) => {
      if (index) line.append("، ");
      const name = credit.name.trim();
      let linked = false;
      if (chapter.source_provider === "mangadex" && typeof credit.url === "string") {
        try {
          const parsed = new URL(credit.url);
          if (parsed.protocol === "https:" && parsed.hostname === "mangadex.org"
              && /^\/group\/[0-9a-f-]{36}\/?$/i.test(parsed.pathname)) {
            const link = document.createElement("a");
            link.href = parsed.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = name;
            line.append(link);
            linked = true;
          }
        } catch (_) { /* Keep untrusted credit text visible without linking it. */ }
      }
      if (!linked) line.append(name);
    });
  }
  return line;
}

async function loadLibraryList(target, archived) {
  try {
    const response = await fetch(`/api/library?archived=${archived ? "true" : "false"}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "تعذر تحميل المكتبة.");
    target.replaceChildren();
    if (!data.chapters.length) {
      target.textContent = archived ? "لا توجد فصول مؤرشفة." : "لم تستورد فصولاً بعد.";
      return;
    }
    data.chapters.forEach(chapter => {
      const item = document.createElement("article");
      item.className = "library-item";
      const details = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = chapter.title;
      const meta = document.createElement("small");
      meta.textContent = `${chapter.series_title || chapter.title} · ${chapter.series_type} · ${chapter.page_count} صفحة · آخر قراءة: ${chapter.last_read_page || 1}`;
      details.append(title, meta);
      const credits = sourceCreditsElement(chapter);
      if (credits) details.append(credits);
      const open = document.createElement("button");
      open.type = "button";
      open.textContent = "فتح القارئ";
      open.addEventListener("click", () => openLocalChapter(chapter.id));
      const archive = document.createElement("button");
      archive.type = "button";
      archive.className = "secondary";
      archive.textContent = archived ? "استعادة الفصل" : "أرشفة الفصل";
      archive.addEventListener("click", async () => {
        archive.disabled = true;
        try {
          const response = await fetch(`/api/library/${encodeURIComponent(chapter.id)}/archive`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({archived: !archived}),
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || "تعذر تحديث الفصل.");
          await loadLibrary();
          setStatus(archived ? "استُعيد الفصل إلى المكتبة." : "أُرشف الفصل وبقيت ملفاته محفوظة.");
        } catch (error) {
          setStatus(error.message);
          archive.disabled = false;
        }
      });
      const actions = document.createElement("div");
      actions.className = "library-actions";
      actions.append(open, archive);
      item.append(details, actions);
      target.append(item);
    });
  } catch (error) {
    target.textContent = error.message;
  }
}

async function openLocalChapter(chapterId) {
  readingPositionObserver?.disconnect();
  readingPositionObserver = null;
  window.clearTimeout(readingPositionTimer);
  localReader.replaceChildren();
  const response = await fetch(`/api/library/${encodeURIComponent(chapterId)}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "تعذر فتح الفصل.");
  const chapter = data.chapter;
  const heading = document.createElement("div");
  heading.className = "section-heading";
  const title = document.createElement("h2");
  title.textContent = `${chapter.title} — القارئ المحلي`;
  const exportLink = document.createElement("a");
  exportLink.className = "button-link";
  exportLink.href = `/api/library/${encodeURIComponent(chapterId)}/export`;
  exportLink.textContent = "تصدير حزمة مع الترجمة";
  const cbzLink = document.createElement("a");
  cbzLink.className = "button-link secondary-link";
  cbzLink.href = `/api/library/${encodeURIComponent(chapterId)}/export?format=cbz`;
  cbzLink.textContent = "CBZ الأصلية";
  const renderedCbz = document.createElement("a");
  renderedCbz.className = "button-link rendered-link";
  renderedCbz.href = `/api/library/${encodeURIComponent(chapterId)}/export?format=cbz&edition=rendered`;
  renderedCbz.textContent = "CBZ بالمعاينات العربية";
  renderedCbz.title = chapter.pages.every(page => page.rendered_url)
    ? "تصدير المعاينات التي أنشأتها"
    : "أنشئ معاينة لكل صفحات الفصل أولاً";
  if (!chapter.pages.every(page => page.rendered_url)) {
    renderedCbz.setAttribute("aria-disabled", "true");
  }
  renderedCbz.addEventListener("click", event => {
    if (renderedCbz.getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      setStatus("أنشئ أو حدّث معاينة كل صفحات الفصل قبل تصدير CBZ المترجم.");
    }
  });
  const updateRenderedExportAvailability = () => {
    const ready = chapter.pages.length > 0 && chapter.pages.every(page => page.rendered_url);
    if (ready) {
      renderedCbz.removeAttribute("aria-disabled");
      renderedCbz.title = "تصدير معاينات الفصل المترجمة";
    } else {
      renderedCbz.setAttribute("aria-disabled", "true");
      renderedCbz.title = "أنشئ معاينة لكل صفحات الفصل أولاً";
    }
  };
  updateRenderedExportAvailability();
  localReader.classList.add("reader-continuous");
  const readingModeButton = document.createElement("button");
  readingModeButton.type = "button";
  readingModeButton.className = "secondary";
  readingModeButton.textContent = "إظهار أدوات المراجعة";
  readingModeButton.setAttribute("aria-pressed", "true");
  readingModeButton.title = "عرض صفحات الفصل متصلة رأسياً، أو إظهار أدوات كل صفحة للمراجعة";
  readingModeButton.addEventListener("click", () => {
    const continuous = localReader.classList.toggle("reader-continuous");
    readingModeButton.textContent = continuous ? "إظهار أدوات المراجعة" : "العودة للقراءة المتصلة";
    readingModeButton.setAttribute("aria-pressed", String(continuous));
  });
  const chapterTitleForm = document.createElement("form");
  chapterTitleForm.className = "series-title-form chapter-title-form";
  const chapterTitleLabel = document.createElement("label");
  chapterTitleLabel.textContent = "عنوان الفصل في المكتبة";
  const chapterTitleInput = document.createElement("input");
  chapterTitleInput.name = "title";
  chapterTitleInput.maxLength = 160;
  chapterTitleInput.required = true;
  chapterTitleInput.value = chapter.title;
  chapterTitleInput.dir = "auto";
  chapterTitleLabel.append(chapterTitleInput);
  const chapterTitleSave = document.createElement("button");
  chapterTitleSave.type = "submit";
  chapterTitleSave.textContent = "حفظ عنوان الفصل";
  chapterTitleForm.append(chapterTitleLabel, chapterTitleSave);
  chapterTitleForm.addEventListener("submit", async event => {
    event.preventDefault();
    chapterTitleSave.disabled = true;
    try {
      const savedResponse = await fetch(`/api/library/${encodeURIComponent(chapterId)}/title`, {
        method: "PATCH", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({title: chapterTitleInput.value.trim()}),
      });
      const saved = await savedResponse.json();
      if (!savedResponse.ok) throw new Error(saved.error || "تعذر حفظ عنوان الفصل.");
      chapterTitleInput.value = saved.chapter.title;
      title.textContent = `${saved.chapter.title} — القارئ المحلي`;
      await loadLibrary();
      setStatus("حُفظ عنوان الفصل دون تغيير عنوان السلسلة أو بيانات الترجمة.");
    } catch (error) {
      setStatus(error.message);
    } finally {
      chapterTitleSave.disabled = false;
    }
  });
  const seriesForm = document.createElement("form");
  seriesForm.className = "series-title-form";
  const seriesLabel = document.createElement("label");
  seriesLabel.textContent = "عنوان السلسلة لتجميع ذاكرة المصطلحات بين الفصول";
  const seriesInput = document.createElement("input");
  seriesInput.name = "series_title";
  seriesInput.maxLength = 160;
  seriesInput.required = true;
  seriesInput.value = chapter.series_title || chapter.title;
  let activeSeriesTitle = seriesInput.value;
  seriesInput.dir = "auto";
  seriesLabel.append(seriesInput);
  const seriesSave = document.createElement("button");
  seriesSave.type = "submit";
  seriesSave.textContent = "حفظ اسم السلسلة";
  const seriesGlossary = document.createElement("button");
  seriesGlossary.type = "button";
  seriesGlossary.className = "secondary";
  seriesGlossary.textContent = "استخدمه كنطاق للقاموس";
  seriesGlossary.addEventListener("click", () => {
    if (seriesInput.value.trim() !== activeSeriesTitle) {
      setStatus("احفظ اسم السلسلة أولاً حتى يطابق نطاق القاموس الفصول الحالية.");
      return;
    }
    const scope = document.querySelector('#glossaryForm input[name="scope"]');
    scope.value = activeSeriesTitle;
    scope.scrollIntoView({behavior: "smooth", block: "center"});
    scope.focus();
    setStatus("أصبح عنوان السلسلة نطاق القاموس؛ أضف المصطلح لحفظه لهذا العمل فقط.");
  });
  seriesForm.append(seriesLabel, seriesSave, seriesGlossary);
  seriesForm.addEventListener("submit", async event => {
    event.preventDefault();
    seriesSave.disabled = true;
    try {
      const savedResponse = await fetch(`/api/library/${encodeURIComponent(chapterId)}/series`, {
        method: "PATCH", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({series_title: seriesInput.value.trim()})
      });
      const saved = await savedResponse.json();
      if (!savedResponse.ok) throw new Error(saved.error || "تعذر حفظ اسم السلسلة.");
      seriesInput.value = saved.chapter.series_title;
      activeSeriesTitle = saved.chapter.series_title;
      await loadLibrary();
      setStatus("حُفظ اسم السلسلة؛ ستستخدم الفصول ذات الاسم نفسه ذاكرة ترجمة مشتركة.");
    } catch (error) {
      setStatus(error.message);
    } finally {
      seriesSave.disabled = false;
    }
  });
  const actions = document.createElement("div");
  actions.className = "reader-actions";
  const renderPageTasks = [];
  const translateAll = document.createElement("button");
  translateAll.type = "button";
  translateAll.textContent = "ترجمة الفصل كله محلياً";
  const pauseAll = document.createElement("button");
  pauseAll.type = "button";
  pauseAll.className = "secondary";
  let chapterPaused = Boolean(chapter.pause_requested);
  pauseAll.textContent = chapterPaused ? "استئناف الفصل" : "إيقاف مؤقت";
  pauseAll.disabled = !chapterPaused;
  let chapterRunActive = false;
  const renderAllButton = document.createElement("button");
  renderAllButton.type = "button";
  renderAllButton.className = "secondary";
  renderAllButton.textContent = "إنشاء معاينات الفصل كله";
  const regionHasUsableTranslation = line => {
    if (!line || typeof line !== "object") return false;
    const translated = String(line.translated || "").trim();
    return Boolean(translated) && /[\u0621-\u064A\u066E-\u06D3]/.test(translated);
  };
  const regionTranslationsComplete = result => Array.isArray(result?.translated_lines) &&
    result.translated_lines.every(line => line && typeof line === "object" &&
      (line.include === false || regionHasUsableTranslation(line)));
  const allPagesReadyForRender = () => chapter.pages.length > 0 && chapter.pages.every(page => {
    const result = page.result;
    if (result?.processing_status !== "complete" || result.translation_inputs_stale) return false;
    if (result.no_text_detected) return true;
    return Array.isArray(result.translated_lines) && result.translated_lines.length > 0 &&
      regionTranslationsComplete(result);
  });
  const updateRenderAllAvailability = () => {
    renderAllButton.disabled = !allPagesReadyForRender();
  };
  updateRenderAllAvailability();
  actions.append(translateAll, pauseAll, readingModeButton, exportLink, cbzLink, renderedCbz);
  actions.append(renderAllButton);
  heading.append(title, actions);
  const sourceCredits = sourceCreditsElement(chapter);
  if (sourceCredits) heading.append(sourceCredits);
  localReader.append(heading, chapterTitleForm, seriesForm);

  const pageJobs = [];
  const pageCards = new Map();
  chapter.pages.forEach(page => {
    const card = document.createElement("article");
    card.className = "page";
    card.dataset.pageNumber = String(page.page_number);
    const pageTitle = document.createElement("h3");
    pageTitle.textContent = `الصفحة ${page.page_number}`;
    const image = document.createElement("img");
    image.src = page.image_url;
    image.loading = "lazy";
    image.alt = `${chapter.title} — الصفحة ${page.page_number}`;
    const imageWrap = document.createElement("div");
    imageWrap.className = "image-wrap";
    const regionOverlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    regionOverlay.classList.add("ocr-overlay");
    regionOverlay.setAttribute("aria-hidden", "true");
    imageWrap.append(image, regionOverlay);
    let currentResult = page.result;
    let activeMaskEditor = null;
    const drawRegions = () => drawDialogueOverlay(regionOverlay, image, currentResult?.translated_lines || []);
    const drawMaskDraft = () => {
      regionOverlay.querySelector(".manual-mask-draft")?.remove();
      if (!activeMaskEditor?.points.length) return;
      const draft = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      draft.setAttribute("points", activeMaskEditor.points.map(point => point.join(",")).join(" "));
      draft.setAttribute("class", "manual-mask-draft");
      regionOverlay.append(draft);
    };
    regionOverlay.addEventListener("pointerdown", event => {
      if (!activeMaskEditor || !image.naturalWidth || !image.naturalHeight) return;
      const matrix = regionOverlay.getScreenCTM();
      if (!matrix) return;
      const point = regionOverlay.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      const local = point.matrixTransform(matrix.inverse());
      const bounds = activeMaskEditor.region.bubble_bbox;
      const x = Math.round(local.x);
      const y = Math.round(local.y);
      if (x < bounds[0] || x >= bounds[2] || y < bounds[1] || y >= bounds[3]) return;
      event.preventDefault();
      activeMaskEditor.points.push([x, y]);
      drawMaskDraft();
    });
    image.addEventListener("load", drawRegions);
    if (page.result) drawRegions();
    const hasSavedRegions = Array.isArray(page.result?.translated_lines) && page.result.translated_lines.length > 0;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = page.result?.translation_inputs_stale
      ? "تغيّر السياق — حدّث الترجمة"
      : page.result?.no_text_detected
      ? "لم يُكتشف نص — حفظ الصفحة الأصلية"
      : page.result?.processing_status === "paused"
      ? "صفحة متوقفة — استكمال لاحقاً"
      : page.result?.processing_status === "translation_in_progress"
        ? "صفحة غير مكتملة — استكمال الترجمة"
        : hasSavedRegions && !regionTranslationsComplete(page.result)
          ? "توجد مناطق بلا ترجمة — استكمال الصفحة"
        : page.result ? "عرض الترجمة المحفوظة" : "OCR وترجمة محلية";
    const renderButton = document.createElement("button");
    renderButton.type = "button";
    renderButton.className = "secondary";
    renderButton.disabled = Boolean(page.result?.translation_inputs_stale)
      || (!hasSavedRegions && !page.result?.no_text_detected)
      || (hasSavedRegions && !regionTranslationsComplete(page.result));
    let renderedUrl = page.rendered_url;
    let showingRendered = false;
    const layersLink = document.createElement("a");
    layersLink.className = "button-link secondary-link layers-export-link";
    layersLink.textContent = "تنزيل طبقات الصفحة";
    layersLink.href = `/api/library/${encodeURIComponent(chapterId)}/pages/${page.page_number}/layers.zip`;
    layersLink.setAttribute("download", "");
    const updateLayersAvailability = result => {
      const complete = !result?.translation_inputs_stale
        && (result?.no_text_detected || regionTranslationsComplete(result));
      layersLink.setAttribute("aria-disabled", String(!complete));
      layersLink.tabIndex = complete ? 0 : -1;
    };
    updateLayersAvailability(page.result);
    layersLink.addEventListener("click", event => {
      if (layersLink.getAttribute("aria-disabled") === "true") event.preventDefault();
    });
    renderButton.textContent = "إنشاء معاينة على الصورة";
    const overlayButton = document.createElement("button");
    overlayButton.type = "button";
    overlayButton.className = "secondary toggle-region-overlay";
    overlayButton.disabled = !hasSavedRegions;
    overlayButton.textContent = "إظهار مناطق الحوار";
    overlayButton.addEventListener("click", () => {
      drawRegions();
      const visible = regionOverlay.classList.toggle("visible");
      overlayButton.textContent = visible ? "إخفاء مناطق الحوار" : "إظهار مناطق الحوار";
    });
    const resultBox = document.createElement("div");
    resultBox.className = "page-review";
    if (page.result) renderLocalResult(resultBox, page.result, chapterId, page.page_number);
    const processPage = async () => {
      button.disabled = true;
      button.textContent = "جارٍ التعرف والترجمة على الجهاز…";
      try {
        const r = await fetch(`/api/library/${encodeURIComponent(chapterId)}/pages/${page.page_number}/translate`, {method: "POST"});
        const out = await r.json();
        if (!r.ok) throw new Error(out.error || "تعذرت معالجة الصفحة.");
        renderLocalResult(resultBox, out, chapterId, page.page_number);
        const hasDetectedRegions = Array.isArray(out.translated_lines) && out.translated_lines.length > 0;
        page.result = out;
        updateLayersAvailability(out);
        updateRenderAllAvailability();
        renderButton.disabled = Boolean(out.translation_inputs_stale)
          || (!hasDetectedRegions && !out.no_text_detected)
          || (hasDetectedRegions && !regionTranslationsComplete(out));
        overlayButton.disabled = !hasDetectedRegions;
        currentResult = out;
        drawRegions();
        if (out.processing_status === "paused") {
          button.textContent = "متوقف مؤقتاً — استكمال عند المتابعة";
          return "paused";
        }
        button.textContent = "تم الحفظ محلياً — إعادة المعالجة";
        return true;
      } catch (error) {
        let savedProgressRecovered = false;
        try {
          const savedResponse = await fetch(
            `/api/library/${encodeURIComponent(chapterId)}/pages/${page.page_number}/result`
          );
          if (savedResponse.ok) {
            const saved = await savedResponse.json();
            savedProgressRecovered = true;
            renderLocalResult(resultBox, saved, chapterId, page.page_number);
            page.result = saved;
            updateLayersAvailability(saved);
            currentResult = saved;
            drawRegions();
            const hasSavedRegions = Array.isArray(saved.translated_lines) && saved.translated_lines.length > 0;
            renderButton.disabled = Boolean(saved.translation_inputs_stale)
              || (!hasSavedRegions && !saved.no_text_detected)
              || (hasSavedRegions && !regionTranslationsComplete(saved));
            overlayButton.disabled = !hasSavedRegions;
          }
        } catch (_) {}
        resultBox.querySelector(".page-process-error")?.remove();
        const warning = document.createElement("p");
        warning.className = "quality-warning page-process-error";
        warning.textContent = savedProgressRecovered
          ? `${error.message} — حُفظ التقدم السابق؛ أعد المحاولة للاستكمال.`
          : error.message;
        resultBox.append(warning);
        button.textContent = "إعادة المحاولة";
        return false;
      } finally {
        button.disabled = false;
      }
    };
    pageJobs.push(processPage);
    button.addEventListener("click", processPage);
    const renderPage = async (forceRender = false) => {
      if (showingRendered && !forceRender) {
        image.src = page.image_url;
        showingRendered = false;
        renderButton.textContent = "تحديث المعاينة على الصورة";
        return false;
      }
      if (forceRender && page.rendered_url) {
        const savedLines = page.result?.translated_lines || [];
        const currentEdits = collectRegionEdits(resultBox);
        const matchesSaved = currentEdits.length === savedLines.length && currentEdits.every(edit => {
          const saved = savedLines.find(line => line.index === edit.index);
          return saved && saved.text === edit.source && saved.translated === edit.translated
            && (saved.include !== false) === edit.include;
        });
        if (matchesSaved) {
          image.src = `${page.rendered_url}?v=${Date.now()}`;
          renderedUrl = page.rendered_url;
          showingRendered = true;
          renderButton.textContent = "عرض الصورة الأصلية";
          return true;
        }
      }
      renderButton.disabled = true;
      renderButton.textContent = "جارٍ إصلاح المناطق المختارة وكتابة العربية…";
      try {
        const edits = collectRegionEdits(resultBox);
        const reviewResponse = await fetch(`/api/library/${encodeURIComponent(chapterId)}/pages/${page.page_number}/review`, {
          method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({regions: edits})
        });
        const reviewed = await reviewResponse.json();
        if (!reviewResponse.ok) throw new Error(reviewed.error || "تعذر حفظ المراجعة.");
        page.result = reviewed;
        currentResult = reviewed;
        drawRegions();
        const selected = edits.filter(region => region.include).map(region => region.index);
        const response = await fetch(`/api/library/${encodeURIComponent(chapterId)}/pages/${page.page_number}/render`, {
          method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({regions: selected})
        });
        const out = await response.json();
        if (!response.ok) throw new Error(out.error || "تعذر إنشاء المعاينة.");
        page.result.render_quality = out;
        applyRenderQuality(resultBox, out);
        const skippedIndices = new Set(out.skipped_region_indices || []);
        renderedUrl = out.rendered_url;
        page.rendered_url = renderedUrl;
        updateRenderedExportAvailability();
        image.src = `${renderedUrl}?v=${Date.now()}`;
        showingRendered = true;
        const note = document.createElement("p");
        note.className = "note quality-warning";
        const skippedLabel = [...skippedIndices].sort((a, b) => a - b).join("، ");
        note.textContent = `${out.quality_note} مناطق كُتبت: ${out.rendered_regions}، لم تُكتب: ${out.skipped_regions}${skippedLabel ? ` (رقم ${skippedLabel})` : ""}.${formatBubbleRepairStrategies(out.bubble_repair_strategies)}`;
        resultBox.append(note);
        renderButton.textContent = "عرض الصورة الأصلية";
        return true;
      } catch (error) {
        resultBox.append(document.createTextNode(error.message));
        return false;
      } finally {
        renderButton.disabled = false;
        if (!showingRendered) renderButton.textContent = "إعادة محاولة المعاينة";
      }
    };
    renderButton.addEventListener("click", () => renderPage());
    renderPageTasks.push(() => renderPage(true));
    const pageActions = document.createElement("div");
    pageActions.className = "reader-actions";
    pageActions.append(button, overlayButton, renderButton, layersLink);
    card.addEventListener("region-review-saved", event => {
      if (event.detail) {
        page.result = event.detail;
        updateLayersAvailability(event.detail);
        currentResult = event.detail;
        drawRegions();
      }
      resultBox.querySelectorAll(".region-review").forEach(row => {
        row.classList.remove("region-render-skipped");
        row.querySelector(".region-render-notice")?.remove();
      });
      if (showingRendered) image.src = page.image_url;
      showingRendered = false;
      renderedUrl = null;
      page.rendered_url = null;
      updateRenderedExportAvailability();
      renderButton.textContent = "تحديث المعاينة على الصورة";
    });
    card.append(pageTitle, imageWrap, pageActions, resultBox);
    localReader.append(card);
    pageCards.set(Number(page.page_number), card);
  });

  const initialPage = Math.max(1, Number(chapter.last_read_page) || 1);
  let restoringReadingPosition = initialPage > 1 && pageCards.has(initialPage);
  const scheduleReadingPositionSave = pageNumber => {
    window.clearTimeout(readingPositionTimer);
    readingPositionTimer = window.setTimeout(() => {
      readingPositionSaveQueue = readingPositionSaveQueue.catch(() => {}).then(async () => {
        const savedResponse = await fetch(`/api/library/${encodeURIComponent(chapterId)}/reading-position`, {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({page_number: pageNumber}),
        });
        if (!savedResponse.ok) throw new Error("تعذر حفظ موضع القراءة.");
      });
      readingPositionSaveQueue = readingPositionSaveQueue.catch(() => {});
    }, 900);
  };
  if ("IntersectionObserver" in window) {
    readingPositionObserver = new IntersectionObserver(entries => {
      if (restoringReadingPosition) return;
      const visiblePage = entries
        .filter(entry => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visiblePage) scheduleReadingPositionSave(Number(visiblePage.target.dataset.pageNumber));
    }, {rootMargin: "-35% 0px -45% 0px", threshold: [0, 0.25, 0.5]});
    pageCards.forEach(card => readingPositionObserver.observe(card));
  }
  if (restoringReadingPosition) {
    requestAnimationFrame(() => pageCards.get(initialPage)?.scrollIntoView({block: "start", behavior: "auto"}));
    window.setTimeout(() => { restoringReadingPosition = false; }, 700);
  }
  renderAllButton.addEventListener("click", async () => {
    if (!allPagesReadyForRender()) return;
    renderAllButton.disabled = true;
    let renderedCount = 0;
    try {
      for (let index = 0; index < renderPageTasks.length; index += 1) {
        setStatus(`جارٍ إنشاء المعاينة العربية للصفحة ${index + 1} من ${renderPageTasks.length}…`);
        if (await renderPageTasks[index]()) renderedCount += 1;
      }
      updateRenderedExportAvailability();
      const allRendered = chapter.pages.every(page => page.rendered_url);
      setStatus(allRendered
        ? `اكتملت معاينة ${renderedCount} من ${renderPageTasks.length} صفحة. يمكنك تصدير CBZ المترجم.`
        : `اكتملت معاينة ${renderedCount} من ${renderPageTasks.length} صفحة؛ حدّث الصفحات غير المكتملة قبل التصدير.`);
    } finally {
      updateRenderAllAvailability();
    }
  });
  translateAll.addEventListener("click", async () => {
    translateAll.disabled = true;
    if (chapterPaused) {
      try {
        const response = await fetch(`/api/library/${encodeURIComponent(chapterId)}/processing/resume`, {method: "POST"});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "تعذر استئناف الفصل.");
        chapterPaused = false;
      } catch (error) {
        setStatus(error.message);
        translateAll.disabled = false;
        return;
      }
    }
    chapterRunActive = true;
    pauseAll.disabled = false;
    pauseAll.textContent = "إيقاف مؤقت";
    let completed = 0;
    const failedPages = [];
    let progressPollBusy = false;
    const progressTimer = window.setInterval(async () => {
      if (progressPollBusy) return;
      progressPollBusy = true;
      try {
        const response = await fetch(`/api/library/${encodeURIComponent(chapterId)}/progress`);
        if (!response.ok) return;
        const progress = await response.json();
        const current = progress.current;
        if (!current) return;
        const stages = {
          bubble_text_detection: "اكتشاف الفقاعات والنصوص",
          bubble_and_external_text_detection: "اكتشاف الفقاعات والنصوص الخارجية",
          translation: "ترجمة الحوارات",
          complete: "حُفظت الصفحة",
          paused: "متوقفة مؤقتاً",
          paused_before_ocr: "متوقفة قبل OCR",
          ocr_in_progress: "اكتشاف الفقاعات والنصوص",
          translation_in_progress: "ترجمة الحوارات",
          context_changed: "تغيّر السياق — تحديث الترجمة مطلوب",
        };
        const stage = stages[current.stage] || stages[current.status] || "استكمال الصفحة";
        const detail = current.progress?.total_regions
          ? ` · منطقة OCR ${current.progress.completed_regions} من ${current.progress.total_regions}`
            + (current.progress.current_region ? ` · يعمل على المنطقة ${current.progress.current_region}` : "")
            + (current.current_region_elapsed_seconds != null
              ? ` · زمن المنطقة ${formatDuration(current.current_region_elapsed_seconds)}` : "")
          : "";
        const timings = current.translation_elapsed_seconds != null
          ? ` · OCR ${current.ocr_elapsed_seconds != null ? formatDuration(current.ocr_elapsed_seconds) : "—"} · ترجمة ${formatDuration(current.translation_elapsed_seconds)}` : "";
        setStatus(`تقدم الفصل: ${progress.completed_pages} من ${progress.page_count} صفحة مكتملة · الصفحة ${current.page_number}: ${stage}${detail}${timings}`);
      } catch (_) { /* Keep the in-page status if progress polling is temporarily unavailable. */ }
      finally { progressPollBusy = false; }
    }, 2000);
    try {
      for (const [index, processPage] of pageJobs.entries()) {
        setStatus(`جارٍ معالجة الصفحة ${index + 1} من ${pageJobs.length} محلياً…`);
        const pageResult = await processPage();
        if (pageResult === "paused") {
          chapterPaused = true;
          setStatus("توقفت مؤقتاً بعد حفظ موضع الصفحة. اضغط «استئناف الفصل» للمتابعة.");
          break;
        }
        if (pageResult) {
          completed += 1;
          chapterPaused = false;
        } else {
          failedPages.push(chapter.pages[index]?.page_number ?? index + 1);
        }
      }
      if (!chapterPaused) {
        await fetch(`/api/library/${encodeURIComponent(chapterId)}/processing/resume`, {method: "POST"});
      }
      if (!chapterPaused) {
        if (failedPages.length) {
          setStatus(`اكتملت ${completed} من ${pageJobs.length} صفحة. تعذرت الصفحات: ${failedPages.join("، ")} — أعد محاولة هذه الصفحات، وسيُستكمل العمل المحفوظ.`);
        } else {
          setStatus(`اكتملت معالجة ${completed} من ${pageJobs.length} صفحة. يمكنك التصدير أو مراجعة الصفحات أدناه.`);
        }
      }
    } finally {
      window.clearInterval(progressTimer);
      translateAll.disabled = false;
      chapterRunActive = false;
      pauseAll.disabled = !chapterPaused;
      pauseAll.textContent = chapterPaused ? "استئناف الفصل" : "إيقاف مؤقت";
    }
  });
  pauseAll.addEventListener("click", async () => {
    pauseAll.disabled = true;
    const wasRunning = chapterRunActive;
    try {
      const action = wasRunning ? "pause" : "resume";
      const response = await fetch(`/api/library/${encodeURIComponent(chapterId)}/processing/${action}`, {method: "POST"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "تعذر تغيير حالة المعالجة.");
      if (wasRunning && data.paused) {
        chapterPaused = true;
        pauseAll.textContent = "جارٍ الإيقاف بعد الفقاعة الحالية…";
        setStatus(data.message);
      } else if (wasRunning) {
        chapterPaused = false;
        pauseAll.disabled = true;
        pauseAll.textContent = "إيقاف مؤقت";
        setStatus(data.message);
      } else {
        chapterPaused = false;
        pauseAll.textContent = "إيقاف مؤقت";
        translateAll.click();
      }
    } catch (error) {
      setStatus(error.message);
      pauseAll.disabled = false;
    }
  });
  localReader.scrollIntoView({behavior: "smooth", block: "start"});
}

function drawDialogueOverlay(overlay, image, regions) {
  if (!image.naturalWidth || !image.naturalHeight || !Array.isArray(regions)) return;
  overlay.replaceChildren();
  overlay.setAttribute("viewBox", `0 0 ${image.naturalWidth} ${image.naturalHeight}`);
  overlay.setAttribute("preserveAspectRatio", "none");
  regions.forEach((region, index) => {
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", region.left);
    rect.setAttribute("y", region.top);
    rect.setAttribute("width", region.width);
    rect.setAttribute("height", region.height);
    rect.setAttribute("class", "dialogue-region-box");
    rect.dataset.regionIndex = String(region.index ?? index + 1);
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", region.left + 3);
    label.setAttribute("y", region.top + 16);
    label.setAttribute("class", "dialogue-region-label");
    label.textContent = String(region.index ?? index + 1);
    overlay.append(rect, label);
    const savedPolygon = Array.isArray(region.manual_bubble_polygon)
      ? region.manual_bubble_polygon.filter(point => Array.isArray(point) && point.length === 2
        && point.every(value => Number.isFinite(Number(value)))) : [];
    if (savedPolygon.length >= 3) {
      const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      polygon.setAttribute("points", savedPolygon.map(point => point.join(",")).join(" "));
      polygon.setAttribute("class", "manual-bubble-mask");
      polygon.dataset.regionIndex = String(region.index ?? index + 1);
      overlay.append(polygon);
    }
  });
}

function speechBubbleMaskDetail(timing) {
  if (timing?.speech_bubble_mask_count == null) return "";
  return ` · أقنعة الفقاعات ${timing.speech_bubble_mask_count} (نص أولي ${timing.speech_bubble_masks_with_primary_ocr ?? "—"}، بلا نص ${timing.speech_bubble_masks_without_primary_ocr ?? "—"})`;
}

function renderLocalResult(container, result, chapterId, pageNumber) {
  container.replaceChildren();
  const provider = document.createElement("p");
  provider.className = "note";
  const ocrTiming = result.ocr_timing_breakdown;
  const timingDetail = ocrTiming
    ? ` · فقاعات ${ocrTiming.bubble_detection_and_ocr_seconds ?? "—"}ث · خارجها ${ocrTiming.external_ocr_seconds ?? "—"}ث (${ocrTiming.external_region_count ?? 0} مناطق)${speechBubbleMaskDetail(ocrTiming)}`
    : "";
  provider.textContent = `OCR محلي على CPU · ${result.ocr_elapsed_seconds ?? "—"} ثانية${timingDetail}` +
    (result.translation_elapsed_seconds != null ? ` · الترجمة ${result.translation_elapsed_seconds}ث` : "") +
    (result.page_processing_elapsed_seconds != null ? ` · الصفحة كاملة ${result.page_processing_elapsed_seconds}ث` : "") +
    (result.translation_refresh_reason ? ` · تحديث الترجمات بسبب: ${result.translation_refresh_reason} (أُعيد استخدام OCR المحفوظ)` : "");
  const translated = document.createElement("p");
  translated.className = "translation";
  translated.textContent = result.translated || "لم تُكتشف ترجمة في هذه الصفحة.";
  const original = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "النص الإنجليزي المكتشف: فقاعات ونصوص خارجها للمانهوا";
  const source = document.createElement("p");
  source.className = "original";
  source.textContent = result.original;
  original.append(summary, source);
  container.append(provider, translated, original);

  if (result.no_text_detected) {
    const warning = document.createElement("p");
    warning.className = "quality-warning";
    warning.textContent = "لم يعثر OCR على نص قابل للقراءة. راجع الصفحة بصرياً؛ قد يفوّت النص الصغير أو المزخرف، وستُحفظ الصفحة الأصلية كما هي.";
    container.append(warning);
  }

  if (result.last_translation_error) {
    const warning = document.createElement("p");
    warning.className = "quality-warning translation-resume-warning";
    const failedRegion = result.last_translation_error.region_index;
    warning.textContent = `توقفت الترجمة عند المنطقة ${failedRegion ?? "غير المحددة"} بعد حفظ المناطق المكتملة. اضغط «استكمال الترجمة» لإعادة المحاولة. ${result.last_translation_error.message || ""}`;
    container.append(warning);
  }

  if (Array.isArray(result.translated_lines) && result.translated_lines.length) {
    const review = document.createElement("details");
    review.className = "region-review-list";
    const hasLowConfidence = result.translated_lines.some(region =>
      Number(region.confidence) < 0.6 || region.auto_exclusion_reason === "low_ocr_confidence");
    const hasOcrReviewHints = result.translated_lines.some(region => region.ocr_review_required === true);
    const hasSkippedRegions = (result.render_quality?.skipped_region_indices || []).length > 0;
    review.open = hasLowConfidence || hasOcrReviewHints || hasSkippedRegions || Boolean(result.last_translation_error);
    const reviewTitle = document.createElement("summary");
    reviewTitle.textContent = `مراجعة الفقاعات والنصوص الخارجية (${result.translated_lines.length}) قبل المعاينة`;
    review.append(reviewTitle);
    result.translated_lines.forEach(region => {
      const row = document.createElement("section");
      row.className = "region-review";
      row.dataset.index = region.index;
      row.dataset.regionId = region.region_id || "";
      const lowConfidence = Number(region.confidence) < 0.6
        || region.auto_exclusion_reason === "low_ocr_confidence";
      const ocrReviewRequired = region.ocr_review_required === true;
      if (lowConfidence) row.classList.add("region-ocr-low-confidence");
      if (ocrReviewRequired) row.classList.add("region-ocr-review-required");
      const includeLabel = document.createElement("label");
      includeLabel.className = "region-include";
      const include = document.createElement("input");
      include.type = "checkbox";
      include.checked = region.include !== false;
      const kind = region.region_type === "external_text" ? "النص الخارجي" : "الفقاعة";
      includeLabel.append(include, document.createTextNode(` تضمين ${kind} ${region.index} في صورة المعاينة`));
      const mergeSelectLabel = document.createElement("label");
      mergeSelectLabel.className = "region-merge-select";
      const mergeSelect = document.createElement("input");
      mergeSelect.type = "checkbox";
      mergeSelect.className = "region-merge-check";
      mergeSelect.disabled = region.region_type !== "speech_bubble"
        || region.bubble_id == null || !region.region_id;
      mergeSelectLabel.append(mergeSelect, document.createTextNode("تحديد للدمج مع منطقة متجاورة من الفقاعة نفسها"));
      const splitControls = document.createElement("div");
      splitControls.className = "region-split-controls";
      const splitToggle = document.createElement("button");
      splitToggle.type = "button";
      splitToggle.className = "secondary region-split-toggle";
      splitToggle.textContent = "تقسيم هذه المنطقة";
      splitToggle.disabled = !["speech_bubble", "external_text"].includes(region.region_type)
        || (region.region_type === "speech_bubble" && region.bubble_id == null) || !region.region_id
        || !Array.isArray(region.ocr_boxes) || region.ocr_boxes.length < 2;
      const splitEditor = document.createElement("div");
      splitEditor.className = "region-split-editor";
      splitEditor.hidden = true;
      const splitFirst = document.createElement("textarea");
      splitFirst.placeholder = "نص الجزء الأول";
      splitFirst.setAttribute("aria-label", `نص الجزء الأول من الفقاعة ${region.index}`);
      const splitSecond = document.createElement("textarea");
      splitSecond.placeholder = "نص الجزء الثاني";
      splitSecond.setAttribute("aria-label", `نص الجزء الثاني من الفقاعة ${region.index}`);
      const splitOptions = document.createElement("div");
      splitOptions.className = "region-split-options";
      const splitAxis = document.createElement("select");
      splitAxis.setAttribute("aria-label", "اتجاه تقسيم صناديق OCR");
      [["y", "أعلى / أسفل"], ["x", "يسار / يمين"]].forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        splitAxis.append(option);
      });
      const splitRatio = document.createElement("input");
      splitRatio.type = "number";
      splitRatio.min = "20";
      splitRatio.max = "80";
      splitRatio.step = "1";
      splitRatio.value = "50";
      splitRatio.setAttribute("aria-label", "نسبة موقع الفصل بين صناديق OCR");
      splitOptions.append(splitAxis, splitRatio);
      const splitApply = document.createElement("button");
      splitApply.type = "button";
      splitApply.className = "secondary region-split-apply";
      splitApply.textContent = "تقسيم وإعادة الترجمة لاحقاً";
      splitEditor.append(splitFirst, splitSecond, splitOptions, splitApply);
      splitToggle.addEventListener("click", () => {
        splitEditor.hidden = !splitEditor.hidden;
      });
      splitApply.addEventListener("click", async () => {
        splitApply.disabled = true;
        try {
          const response = await fetch(`/api/library/${encodeURIComponent(chapterId)}/pages/${pageNumber}/review`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({regions: collectRegionEdits(container),
              region_order: collectRegionOrder(container),
              split_regions: [{region_id: region.region_id,
                first_text: splitFirst.value, second_text: splitSecond.value,
                axis: splitAxis.value, ratio: Number(splitRatio.value)}]})
          });
          const saved = await response.json();
          if (!response.ok) throw new Error(saved.error || "تعذر تقسيم منطقة الفقاعة.");
          translated.textContent = saved.translated;
          container.closest(".page")?.dispatchEvent(new CustomEvent("region-review-saved", {detail: saved}));
          setStatus("قُسمت المنطقة حسب صناديق OCR. استكمل الترجمة لإنتاج ترجمتين مستقلتين في موضعيهما.");
        } catch (error) {
          setStatus(error.message);
        } finally {
          splitApply.disabled = false;
        }
      });
      splitControls.append(splitToggle, splitEditor);
      const maskControls = document.createElement("div");
      maskControls.className = "region-mask-controls";
      const maskStart = document.createElement("button");
      maskStart.type = "button";
      maskStart.className = "secondary mask-draw-start";
      maskStart.textContent = "رسم حدود قناع الفقاعة";
      maskStart.disabled = region.region_type !== "speech_bubble"
        || region.bubble_id == null || !region.region_id
        || !Array.isArray(region.bubble_bbox) || region.bubble_bbox.length !== 4;
      const maskSave = document.createElement("button");
      maskSave.type = "button";
      maskSave.className = "secondary mask-draw-save";
      maskSave.textContent = "حفظ الحدود المرسومة";
      maskSave.disabled = true;
      const maskReset = document.createElement("button");
      maskReset.type = "button";
      maskReset.className = "secondary mask-draw-reset";
      maskReset.textContent = "العودة إلى حد الكاشف";
      maskReset.disabled = !Array.isArray(region.manual_bubble_polygon);
      const maskCancel = document.createElement("button");
      maskCancel.type = "button";
      maskCancel.className = "secondary mask-draw-cancel";
      maskCancel.textContent = "إلغاء الرسم";
      const maskHelp = document.createElement("small");
      maskHelp.textContent = "انقر أو المس نقاطاً حول داخل الفقاعة ثم احفظ. التعديل يقص القناع المكتشف ولا يوسعه.";
      const stopMaskDrawing = () => {
        activeMaskEditor = null;
        regionOverlay.classList.remove("mask-drawing");
        drawRegions();
      };
      const saveBubbleMask = async polygon => {
        maskSave.disabled = true;
        maskReset.disabled = true;
        try {
          const response = await fetch(`/api/library/${encodeURIComponent(chapterId)}/pages/${pageNumber}/review`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({regions: collectRegionEdits(container),
              region_order: collectRegionOrder(container),
              bubble_mask_edits: [{region_id: region.region_id, polygon}]})
          });
          const saved = await response.json();
          if (!response.ok) throw new Error(saved.error || "تعذر حفظ قناع الفقاعة.");
          translated.textContent = saved.translated;
          stopMaskDrawing();
          container.closest(".page")?.dispatchEvent(new CustomEvent("region-review-saved", {detail: saved}));
          setStatus(polygon
            ? "حُفظ القناع اليدوي. سيبقى مقيداً بالقناع الذي اكتشفه النظام؛ أنشئ معاينة لمراجعة النتيجة."
            : "أُزيل القناع اليدوي وعاد النظام إلى حدود الكاشف.");
        } catch (error) {
          setStatus(error.message);
        } finally {
          maskSave.disabled = false;
          maskReset.disabled = false;
        }
      };
      maskStart.addEventListener("click", () => {
        activeMaskEditor = {region, points: []};
        maskSave.disabled = false;
        drawRegions();
        regionOverlay.classList.add("visible", "mask-drawing");
        drawMaskDraft();
        setStatus("ارسم حدود الداخل بنقر نقاط متتابعة داخل الفقاعة، ثم اضغط حفظ الحدود المرسومة.");
      });
      maskSave.addEventListener("click", () => {
        if (!activeMaskEditor || activeMaskEditor.points.length < 3) {
          setStatus("أضف ثلاث نقاط على الأقل داخل الفقاعة قبل الحفظ.");
          return;
        }
        void saveBubbleMask(activeMaskEditor.points);
      });
      maskReset.addEventListener("click", () => void saveBubbleMask(null));
      maskCancel.addEventListener("click", stopMaskDrawing);
      maskControls.append(maskStart, maskSave, maskReset, maskCancel, maskHelp);
      const coordinates = document.createElement("small");
      coordinates.className = "line-coords";
      const rotation = region.region_type === "external_text" ? ` · الميل ${Math.round(region.angle || 0)}°` : "";
      const hasFailedAttempt = region.translation_attempt_elapsed_seconds != null;
      const regionSeconds = region.translation_attempt_elapsed_seconds ?? region.translation_elapsed_seconds;
      const regionTime = Number.isFinite(Number(regionSeconds))
        ? ` · ${hasFailedAttempt ? "محاولة فاشلة" : "ترجمة"} ${Number(regionSeconds).toFixed(1)}ث` : "";
      const exclusionReason = region.auto_exclusion_reason === "source_credit"
        ? " · استُبعد رابط حقوق المصدر تلقائياً"
        : region.auto_exclusion_reason === "scanlation_credit"
          ? " · أُبقيت أسماء فريق الترجمة الأصلية"
          : region.auto_exclusion_reason === "low_ocr_confidence"
            ? " · أُبقي النص الأصلي لأن قراءة OCR غير واثقة؛ راجعه قبل الاستبدال"
          : "";
      const ocrReviewNote = ocrReviewRequired
        ? ` · راجع OCR: قد توجد ${Number(region.ocr_unrecognized_word_count) || "عدة"} كلمات غير مقروءة بدقة`
        : "";
      const ocrSuggestions = Array.isArray(region.ocr_suspicious_near_words)
        ? region.ocr_suspicious_near_words
          .filter(item => item && typeof item.observed === "string" && typeof item.candidate === "string")
          .slice(0, 3)
          .map(item => `«${item.candidate}» بدل «${item.observed}»`)
        : [];
      const ocrSuggestionNote = ocrSuggestions.length
        ? ` · اقتراح OCR غير مؤكد: ${ocrSuggestions.join("؛ ")}`
        : "";
      coordinates.textContent = `${lowConfidence || ocrReviewRequired ? "تحقق من OCR · " : ""}موقع ${kind}: ${Math.round(region.left)}, ${Math.round(region.top)} · ثقة النص ${Math.round((region.confidence || 0) * 100)}%${rotation}${regionTime}${exclusionReason}${ocrReviewNote}${ocrSuggestionNote}`;
      const locate = document.createElement("button");
      locate.type = "button";
      locate.className = "secondary locate-region";
      locate.textContent = "إظهار موقع المنطقة على الصورة";
      locate.addEventListener("click", () => {
        const page = container.closest(".page");
        const image = page?.querySelector(".image-wrap img");
        const overlay = page?.querySelector(".ocr-overlay");
        if (!page || !image || !overlay) return;
        const focusRegion = () => {
          drawDialogueOverlay(overlay, image, result.translated_lines);
          overlay.classList.add("visible");
          const toggle = page.querySelector(".toggle-region-overlay");
          if (toggle) toggle.textContent = "إخفاء مناطق الحوار";
          overlay.querySelectorAll(".dialogue-region-box.selected")
            .forEach(item => item.classList.remove("selected"));
          const target = [...overlay.querySelectorAll(".dialogue-region-box")]
            .find(item => Number(item.dataset.regionIndex) === Number(region.index));
          target?.classList.add("selected");
          const imageRect = image.getBoundingClientRect();
          const scaleY = imageRect.height / image.naturalHeight;
          const sourceCenterY = Number(region.top) + Number(region.height) / 2;
          const viewportTargetY = imageRect.top + window.scrollY + sourceCenterY * scaleY;
          window.scrollTo({
            top: Math.max(0, viewportTargetY - window.innerHeight / 2),
            behavior: "smooth",
          });
        };
        if (image.complete && image.naturalWidth) focusRegion();
        else {
          image.addEventListener("load", focusRegion, {once: true});
          image.scrollIntoView({behavior: "smooth", block: "nearest"});
        }
      });
      const source = document.createElement("textarea");
      source.className = "region-source-edit";
      source.value = region.text;
      source.dir = "auto";
      source.setAttribute("aria-label", `تصحيح النص الإنجليزي ${region.index}`);
      const target = document.createElement("textarea");
      target.className = "region-target-edit";
      target.value = region.translated;
      target.setAttribute("aria-label", `مراجعة الترجمة العربية ${region.index}`);
      let layoutEditor = null;
      if (region.bubble_id != null && Array.isArray(region.bubble_bbox)) {
        layoutEditor = document.createElement("div");
        layoutEditor.className = "region-layout-editor";
        const caption = document.createElement("small");
        caption.textContent = "حدود كتابة العربية داخل الفقاعة (بكسل). اتركها فارغة للتوزيع التلقائي.";
        layoutEditor.append(caption);
        const manual = Array.isArray(region.manual_layout_bbox) ? region.manual_layout_bbox : null;
        const values = manual ? [manual[0], manual[1], manual[2] - manual[0], manual[3] - manual[1]] : [];
        ["س", "ص", "العرض", "الارتفاع"].forEach((labelText, position) => {
          const label = document.createElement("label");
          label.textContent = labelText;
          const input = document.createElement("input");
          input.type = "number";
          input.min = "0";
          input.step = "1";
          input.className = "region-layout-edit";
          input.value = values[position] ?? "";
          label.append(input);
          layoutEditor.append(label);
        });
        const reset = document.createElement("button");
        reset.type = "button";
        reset.className = "secondary";
        reset.textContent = "حدود تلقائية";
        reset.addEventListener("click", () => layoutEditor.querySelectorAll("input").forEach(input => { input.value = ""; }));
        layoutEditor.append(reset);
      }
      const speakerHint = document.createElement("input");
      speakerHint.className = "region-speaker-hint";
      speakerHint.type = "text";
      speakerHint.maxLength = 160;
      speakerHint.value = region.speaker_hint || "";
      speakerHint.placeholder = "معلومة اختيارية مؤكدة عن المتحدث (مثال: امرأة تخاطب مديرها)";
      speakerHint.setAttribute("aria-label", `معلومة سياقية مؤكدة عن المتحدث ${region.index}`);
      const retranslate = document.createElement("button");
      retranslate.type = "button";
      retranslate.className = "secondary retranslate-region";
      retranslate.textContent = "إعادة ترجمة هذه المنطقة بالسياق";
      retranslate.addEventListener("click", async () => {
        retranslate.disabled = true;
        retranslate.textContent = "جارٍ إعادة ترجمة المنطقة…";
        setStatus(`جارٍ إعادة ترجمة المنطقة ${region.index} مع سياق الحوار…`);
        try {
          const response = await fetch(
            `/api/library/${encodeURIComponent(chapterId)}/pages/${pageNumber}/regions/${region.index}/translate`,
            {method: "POST", headers: {"Content-Type": "application/json"},
             body: JSON.stringify({source: source.value, speaker_hint: speakerHint.value})}
          );
          const saved = await response.json();
          if (!response.ok) {
            if (Number.isFinite(Number(saved.attempt_elapsed_seconds))) {
              region.translation_attempt_elapsed_seconds = Number(saved.attempt_elapsed_seconds);
              coordinates.textContent = `${lowConfidence ? "تحقق من OCR · " : ""}موقع ${kind}: ${Math.round(region.left)}, ${Math.round(region.top)} · ثقة النص ${Math.round((region.confidence || 0) * 100)}%${rotation} · محاولة فاشلة ${region.translation_attempt_elapsed_seconds.toFixed(1)}ث`;
            }
            throw new Error(saved.error || "تعذرت إعادة ترجمة المنطقة.");
          }
          const updatedRegion = saved.translated_lines.find(item => item.index === region.index);
          if (updatedRegion) {
            source.value = updatedRegion.text;
            target.value = updatedRegion.translated;
            speakerHint.value = updatedRegion.speaker_hint || "";
          }
          translated.textContent = saved.translated;
          container.closest(".page")?.dispatchEvent(new CustomEvent("region-review-saved", {detail: saved}));
          setStatus(`أُعيدت ترجمة المنطقة ${region.index}. راجعها ثم أنشئ معاينة محدثة.`);
        } catch (error) {
          setStatus(error.message);
        } finally {
          retranslate.disabled = false;
          retranslate.textContent = "إعادة ترجمة هذه المنطقة بالسياق";
        }
      });
      if (lowConfidence) {
        const warning = document.createElement("small");
        warning.className = "region-ocr-notice";
        warning.textContent = "ثقة قراءة النص منخفضة؛ قارن النص الإنجليزي مع الصورة قبل اعتماد الترجمة.";
        row.append(warning);
      }
      const orderControls = document.createElement("div");
      orderControls.className = "region-order-controls";
      const moveRegion = (direction) => {
        const rows = [...review.querySelectorAll(".region-review")];
        const position = rows.indexOf(row);
        if (direction < 0 && position > 0) review.insertBefore(row, rows[position - 1]);
        if (direction > 0 && position < rows.length - 1) rows[position + 1].after(row);
      };
      [[-1, "↑", "نقل الحوار إلى السابق في ترتيب القراءة"],
       [1, "↓", "نقل الحوار إلى التالي في ترتيب القراءة"]].forEach(([direction, label, title]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "secondary region-order-move";
        button.textContent = label;
        button.title = title;
        button.addEventListener("click", () => moveRegion(direction));
        orderControls.append(button);
      });
      row.append(orderControls, mergeSelectLabel, splitControls, maskControls,
        includeLabel, coordinates, locate, source, target);
      if (layoutEditor) row.append(layoutEditor);
      row.append(speakerHint, retranslate);
      review.append(row);
    });
    const save = document.createElement("button");
    save.type = "button";
    save.className = "secondary save-review";
    save.textContent = "حفظ تصحيحات النصوص والترجمة";
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        const response = await fetch(`/api/library/${encodeURIComponent(chapterId)}/pages/${pageNumber}/review`, {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({regions: collectRegionEdits(container), region_order: collectRegionOrder(container)})
        });
        const saved = await response.json();
        if (!response.ok) throw new Error(saved.error || "تعذر حفظ التصحيحات.");
        translated.textContent = saved.translated;
        container.closest(".page")?.dispatchEvent(new CustomEvent("region-review-saved", {detail: saved}));
          setStatus(saved.translation_inputs_stale
            ? `حُفظ ترتيب القراءة للصفحة ${pageNumber}. استكمل الترجمة لتحديث سياق الحوارات.`
            : `حُفظت مراجعة الصفحة ${pageNumber}.`);
      } catch (error) {
        setStatus(error.message);
      } finally {
        save.disabled = false;
      }
    });
    review.append(save);
    const merge = document.createElement("button");
    merge.type = "button";
    merge.className = "secondary merge-regions";
    merge.textContent = "دمج المنطقتين المحددتين";
    merge.addEventListener("click", async () => {
      const selectedRows = [...review.querySelectorAll(".region-merge-check:checked")]
        .map(input => input.closest(".region-review"));
      if (selectedRows.length !== 2) {
        setStatus("حدد منطقتين فقط من الفقاعة نفسها لدمجهما.");
        return;
      }
      merge.disabled = true;
      try {
        const response = await fetch(`/api/library/${encodeURIComponent(chapterId)}/pages/${pageNumber}/review`, {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({regions: collectRegionEdits(container),
            region_order: collectRegionOrder(container),
            merge_regions: [selectedRows.map(row => row.dataset.regionId)]})
        });
        const saved = await response.json();
        if (!response.ok) throw new Error(saved.error || "تعذر دمج المنطقتين.");
        translated.textContent = saved.translated;
        container.closest(".page")?.dispatchEvent(new CustomEvent("region-review-saved", {detail: saved}));
        setStatus("دُمجت المنطقتان. استكمل الترجمة لإعادة صياغة الحوار الموحّد بالسياق.");
      } catch (error) {
        setStatus(error.message);
      } finally {
        merge.disabled = false;
      }
    });
    review.append(merge);
    container.append(review);
  }
  if (!result.render_stale && result.render_quality) {
    applyRenderQuality(container, result.render_quality);
  }
}

function applyRenderQuality(container, quality) {
  const skipped = new Set(quality.skipped_region_indices || []);
  container.querySelectorAll(".region-review").forEach(row => {
    const wasSkipped = skipped.has(Number(row.dataset.index));
    row.classList.toggle("region-render-skipped", wasSkipped);
    const oldNotice = row.querySelector(".region-render-notice");
    if (oldNotice) oldNotice.remove();
    if (wasSkipped) {
      const notice = document.createElement("small");
      notice.className = "region-render-notice";
      notice.textContent = "لم تُكتب هذه الترجمة داخل المنطقة؛ راجعها أو اختصرها ثم حدّث المعاينة.";
      row.append(notice);
    }
  });
}

function formatBubbleRepairStrategies(strategies) {
  if (!strategies || typeof strategies !== "object") return "";
  const labels = {
    interior_fill: "تعبئة موضعية",
    bubble_overlay: "إعادة بناء التدرج",
    text_inpainting: "ترميم موضعي",
  };
  const summary = Object.entries(labels)
    .map(([key, label]) => [label, Number(strategies[key] || 0)])
    .filter(([, count]) => Number.isFinite(count) && count > 0)
    .map(([label, count]) => `${label}: ${count}`);
  return summary.length ? ` طريقة إصلاح الفقاعات: ${summary.join("، ")}.` : "";
}

function collectRegionEdits(container) {
  return [...container.querySelectorAll(".region-review")].map(row => {
    const layoutInputs = [...row.querySelectorAll(".region-layout-edit")];
    const layoutValues = layoutInputs.map(input => input.value.trim());
    return {
      index: Number(row.dataset.index),
      include: row.querySelector("input[type=checkbox]").checked,
      source: row.querySelector(".region-source-edit").value,
      translated: row.querySelector(".region-target-edit").value,
      speaker_hint: row.querySelector(".region-speaker-hint").value,
      ...(layoutInputs.length ? {layout_bbox: layoutValues.every(value => value === "")
        ? null : layoutValues.map(value => value === "" ? null : Number(value))} : {}),
    };
  });
}

function collectRegionOrder(container) {
  const ids = [...container.querySelectorAll(".region-review")].map(row => row.dataset.regionId);
  return ids.every(Boolean) ? ids : null;
}

importForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!isVerticalLayoutSelected()) return;
  const button = document.getElementById("importBtn");
  const files = document.getElementById("localPages").files;
  const archive = document.getElementById("chapterArchive").files;
  if (!files.length && !archive.length) {
    setStatus("اختر صوراً متعددة أو ملف CBZ.");
    return;
  }
  const body = new FormData(importForm);
  body.set("reading_layout", readingLayout.value);
  button.disabled = true;
  button.textContent = "جارٍ التحقق والحفظ…";
  setStatus("يُحفظ الفصل داخل مكتبتك المحلية.");
  try {
    const response = await fetch("/api/library/import", {method: "POST", body});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "تعذر استيراد الفصل.");
    setStatus(`حُفظ «${data.chapter.title}» محلياً مع ${data.chapter.page_count} صفحة.`);
    importForm.reset();
    await loadLibrary();
    await openLocalChapter(data.chapter.id);
  } catch (error) {
    setStatus(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "حفظ الفصل محلياً";
  }
});

document.getElementById("refreshLibrary").addEventListener("click", loadLibrary);
loadLibrary();
loadGlossary();
loadSourceBlocks();
loadTranslationEngine();

async function loadSourceBlocks() {
  const target = document.getElementById("sourceBlocks");
  try {
    const response = await fetch("/api/source-blocks");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "تعذر تحميل قائمة الاستبعاد.");
    target.replaceChildren();
    data.blocks.forEach(block => {
      const row = document.createElement("div");
      row.className = "source-block-row";
      const label = document.createElement("span");
      label.textContent = `${block.source_name} — ${block.source_id}`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "secondary";
      remove.textContent = "إلغاء الاستبعاد";
      remove.addEventListener("click", async () => {
        remove.disabled = true;
        try {
          const response = await fetch(`/api/source-blocks/${encodeURIComponent(block.source_id)}`, {method: "DELETE"});
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || "تعذر إلغاء الاستبعاد.");
          await loadSourceBlocks();
          setStatus(result.removed ? "أُزيلت المجموعة من قائمة الاستبعاد." : "لم تعد المجموعة موجودة في قائمة الاستبعاد.");
        } catch (error) {
          setStatus(error.message);
          remove.disabled = false;
        }
      });
      row.append(label, remove);
      target.append(row);
    });
  } catch (error) {
    target.textContent = error.message;
  }
}

document.getElementById("sourceBlockForm").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget;
  const response = await fetch("/api/source-blocks", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(Object.fromEntries(new FormData(form)))});
  const data = await response.json();
  if (!response.ok) { setStatus(data.error || "تعذر استبعاد المجموعة."); return; }
  form.reset(); await loadSourceBlocks(); setStatus("تمت إضافة المجموعة إلى قائمة الاستبعاد المحلية.");
});

document.getElementById("sourceSearchForm").addEventListener("submit", async event => {
  event.preventDefault(); const results = document.getElementById("sourceResults");
  const sourceId = document.getElementById("sourceProvider").value;
  const sourceName = document.getElementById("sourceProvider").selectedOptions[0]?.textContent || sourceId;
  results.textContent = `جارٍ البحث في ${sourceName}…`;
  try {
    const response = await fetch(`/api/sources/${encodeURIComponent(sourceId)}/search?q=${encodeURIComponent(document.getElementById("sourceQuery").value.trim())}`);
    const data = await response.json(); if (!response.ok) throw new Error(data.error || "تعذر البحث.");
    results.replaceChildren();
    data.results.forEach(manga => {
      const item = document.createElement("article"); item.className = "library-item";
      const title = document.createElement("strong"); title.textContent = manga.title;
      const scopeNote = document.createElement("p"); scopeNote.className = "note";
      scopeNote.textContent = "تعرض الفصول الإنجليزية المتاحة؛ اختر التمرير العمودي، فبلد المنشأ غير مؤثر في الدعم.";
      const button = document.createElement("button"); button.textContent = "عرض الفصول الإنجليزية";
      const list = document.createElement("div"); list.className = "chapter-results";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const r = await fetch(`/api/sources/${encodeURIComponent(sourceId)}/works/${encodeURIComponent(manga.id)}/chapters`); const payload = await r.json();
          if (!r.ok) throw new Error(payload.error || "تعذر جلب الفصول.");
          scopeNote.textContent = `عُثر على ${payload.chapters.length} فصلاً إنجليزياً. يُعتمد العمل إذا كان نمطه التمرير العمودي.`;
          list.replaceChildren();
          payload.chapters.forEach(chapter => {
            const row = document.createElement("div"); row.className = "glossary-row";
            const groups = (chapter.groups || []).map(group => group.name).filter(Boolean).join(", ");
            const label = document.createElement("span"); label.textContent = `${chapter.chapter || "Chapter"}${chapter.title ? ` — ${chapter.title}` : ""}${groups ? ` · ${groups}` : ""}`;
            const open = document.createElement("button"); open.textContent = "فتح";
            open.addEventListener("click", () => { if (!isVerticalLayoutSelected()) return; document.getElementById("chapterUrl").value = chapter.url; loadBtn.click(); window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"}); });
            const importButton = document.createElement("button"); importButton.textContent = "تنزيل إلى مكتبتي";
            importButton.addEventListener("click", async () => {
              if (!isVerticalLayoutSelected()) return;
              importButton.disabled = true; importButton.textContent = "جارٍ تنزيل الصور…";
              try {
                const importedResponse = await fetch("/api/source-import", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({url:chapter.url, series_type:document.getElementById("sourceSeriesType").value, reading_direction:"ltr", reading_layout:readingLayout.value})});
                const imported = await importedResponse.json(); if (!importedResponse.ok) throw new Error(imported.error || "تعذر استيراد الفصل.");
                await loadLibrary(); await openLocalChapter(imported.chapter.id);
                setStatus(imported.already_imported
                  ? "هذا الفصل محفوظ مسبقاً؛ فُتحت النسخة المحلية دون إعادة تنزيل الصور."
                  : `تم تنزيل ${imported.chapter.page_count} صفحة إلى المكتبة المحلية.`);
              } catch (error) { list.append(document.createTextNode(error.message)); }
              finally { importButton.disabled = false; importButton.textContent = "تنزيل إلى مكتبتي"; }
            });
            row.append(label, open, importButton); list.append(row);
          });
          if (!payload.chapters.length) list.textContent = "لا توجد فصول إنجليزية ظاهرة؛ ربما استُبعدت مجموعاتها.";
        } catch (error) { list.textContent = error.message; }
        finally { button.disabled = false; }
      });
      item.append(title, scopeNote, button, list); results.append(item);
    });
    if (!data.results.length) results.textContent = "لم يُعثر على نتائج.";
  } catch (error) { results.textContent = error.message; }
});

document.getElementById("glossaryForm").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form));
  const response = await fetch("/api/glossary", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) {
    setStatus(result.error || "تعذر حفظ المصطلح.");
    return;
  }
  form.reset();
  form.elements.source.readOnly = false;
  form.elements.scope.readOnly = false;
  form.querySelector('button[type="submit"]').textContent = "حفظ المصطلح";
  document.getElementById("cancelGlossaryEdit").hidden = true;
  await loadGlossary();
  setStatus("حُفظ المصطلح. سيظهر أثره عند ترجمة العبارة المطابقة كاملة.");
});

document.getElementById("cancelGlossaryEdit").addEventListener("click", () => {
  const form = document.getElementById("glossaryForm");
  form.reset();
  form.elements.source.readOnly = false;
  form.elements.scope.readOnly = false;
  form.querySelector('button[type="submit"]').textContent = "حفظ المصطلح";
  document.getElementById("cancelGlossaryEdit").hidden = true;
});

document.getElementById("glossaryImportForm").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const file = document.getElementById("glossaryFile").files[0];
  const button = form.querySelector("button[type=submit]");
  if (!file) return;
  button.disabled = true;
  try {
    const importedGlossary = JSON.parse(await file.text());
    const response = await fetch("/api/glossary/import", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(importedGlossary)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "تعذر استيراد القاموس.");
    form.reset();
    await loadGlossary();
    setStatus(`دُمج ${result.imported} مصطلحاً في القاموس؛ لم تُحذف أي مصطلحات أخرى.`);
  } catch (error) {
    setStatus(error instanceof SyntaxError ? "ملف القاموس ليس JSON صالحاً." : error.message);
  } finally {
    button.disabled = false;
  }
});

loadBtn.addEventListener("click", async () => {
  if (!isVerticalLayoutSelected()) return;
  const url = document.getElementById("chapterUrl").value.trim();
  pagesBox.replaceChildren();
  setStatus("جارٍ جلب الفصل...");
  loadBtn.disabled = true;
  try {
    const response = await fetch("/api/chapter", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url, reading_layout: readingLayout.value})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "فشل جلب الفصل.");
    setStatus(`تم جلب ${data.pages.length} صفحة. بدأ اكتشاف النصوص وترجمتها تلقائياً؛ ستظهر كل صفحة بعد اكتمال رسمها بالعربية.`);
    if (Array.isArray(data.attributions) && data.attributions.length) {
      const credits = document.createElement("p"); credits.className = "note";
      credits.textContent = "المصدر ومجموعات الترجمة: " + data.attributions.map(item => item.name).join(", ");
      const link = document.createElement("a"); link.href = data.source_url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = "عرض صفحة الفصل";
      pagesBox.append(credits, link);
    }
    pagesBox.classList.add("vertical-pages");
    data.pages.forEach(page => {
      const card = document.createElement("article");
      card.className = "page automatic-translation";
      const heading = document.createElement("h3");
      heading.textContent = `الصفحة ${page.index}`;
      const imageWrap = document.createElement("div");
      imageWrap.className = "image-wrap";
      const img = document.createElement("img");
      img.className = "source-page-image";
      img.hidden = true;
      const overlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      overlay.classList.add("ocr-overlay");
      overlay.setAttribute("aria-hidden", "true");
      const imageProxy = `/api/image?url=${encodeURIComponent(page.image)}&referer=${encodeURIComponent(url)}`;
      img.src = imageProxy;
      img.alt = `Manga page ${page.index}`;
      img.loading = "lazy";
      imageWrap.append(img, overlay);
      const button = document.createElement("button");
      button.textContent = "استخراج النص وترجمته";
      button.className = "page-process-trigger";
      button.hidden = true;
      const result = document.createElement("div");
      button.addEventListener("click", async () => {
        button.disabled = true;
        button.textContent = "جارٍ تحميل الصفحة…";
        result.textContent = "جارٍ تنفيذ OCR والترجمة...";
        const jobId = crypto.randomUUID().replaceAll("-", "").toLowerCase();
        let progressPollActive = false;
        const progressTimer = window.setInterval(async () => {
          if (progressPollActive) return;
          progressPollActive = true;
          try {
            const response = await fetch(`/api/translate/progress/${jobId}`);
            if (!response.ok) return;
            const {progress} = await response.json();
            let stage = "جارٍ تجهيز الصفحة…";
            if (progress.stage === "ocr") stage = "جارٍ اكتشاف الفقاعات والنصوص…";
            if (progress.stage === "translation") {
              stage = `ترجمة المنطقة ${progress.current_region || 1} من ${progress.total_regions || "—"} · ${formatDuration(progress.current_region_elapsed_seconds)} · اكتمل ${progress.completed_regions || 0}`;
            }
            if (progress.stage === "partial") stage = "اكتمل جزء من الصفحة؛ راجع المناطق المتعثرة أدناه.";
            if (progress.stage === "complete") stage = "اكتملت ترجمة الصفحة؛ جارٍ عرض النتائج…";
            if (progress.stage === "failed") stage = progress.error || "تعذرت معالجة الصفحة.";
            button.textContent = stage;
            setStatus(`الصفحة ${page.index}: ${stage}`);
          } catch (_error) {
            // Progress is advisory; the translation request remains authoritative.
          } finally {
            progressPollActive = false;
          }
        }, 1500);
        try {
          const r = await fetch("/api/translate", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({image: page.image, referer: url, chapter_title: data.title,
              work_scope: data.series_key || data.series_title || data.title, page_index: page.index,
              job_id,
              reading_layout: readingLayout.value,
              series_type: document.getElementById("sourceSeriesType").value,
              reading_direction: "ltr"})
          });
          const out = await r.json();
          if (!r.ok) throw new Error(out.error || "فشلت المعالجة.");
          result.replaceChildren();
          const translatedTitle = document.createElement("h4");
          translatedTitle.textContent = "الترجمة العربية";
          const translated = document.createElement("p");
          translated.className = "translation";
          translated.textContent = out.translated;
          const originalTitle = document.createElement("h4");
          originalTitle.textContent = "النص الأصلي";
          const original = document.createElement("p");
          original.className = "original";
          original.textContent = out.original;
          const providerNote = document.createElement("p");
          providerNote.className = "note";
          if (out.ocr_provider?.startsWith("rapidocr_onnxruntime_cpu")) {
            const timing = out.ocr_timing_breakdown;
            const externalCount = out.translated_lines?.filter(line => line.region_type === "external_text").length || 0;
            providerNote.textContent = `OCR محلي · فقاعات ونص خارجي للمانهوا · ${out.ocr_elapsed_seconds ?? "—"}ث` +
              (timing ? ` · OCR الفقاعات ${timing.bubble_detection_and_ocr_seconds ?? "—"}ث · OCR الخارجي ${timing.external_ocr_seconds ?? "—"}ث (${externalCount} مناطق)${speechBubbleMaskDetail(timing)}` : "") +
              ` · الترجمة ${out.translation_elapsed_seconds ?? "—"}ث · الصفحة كاملة ${out.page_processing_elapsed_seconds ?? "—"}ث`;
          } else if (out.ocr_provider === "google_vision") providerNote.textContent = "محرك OCR: Google Cloud Vision";
          else if (out.ocr_provider === "ocr_space_fallback") providerNote.textContent = "محرك OCR: OCR.space (احتياطي بعد تعذر Google Vision)";
          else providerNote.textContent = "محرك OCR: OCR.space";
          result.append(providerNote, translatedTitle, translated);
          if (Array.isArray(out.translated_lines) && out.translated_lines.length) {
            const linesTitle = document.createElement("h4");
            linesTitle.textContent = "مراجعة الفقاعات والنصوص الخارجية قبل إدراجها";
            const linesList = document.createElement("div");
            linesList.className = "translated-lines";
            out.translated_lines.forEach(line => {
              const item = document.createElement("section");
              item.className = "translated-line";
              const source = document.createElement("p");
              source.className = "line-source";
              const kind = line.region_type === "external_text" ? "نص خارجي" : "فقاعة";
              source.textContent = `${line.index}. [${kind}] صحح النص الإنجليزي إذا احتاج`;
              const sourceEdit = document.createElement("textarea");
              sourceEdit.className = "region-source-edit remote-source-edit";
              sourceEdit.value = line.text || "";
              sourceEdit.maxLength = 4000;
              sourceEdit.dir = "auto";
              sourceEdit.setAttribute("aria-label", `تصحيح النص الإنجليزي ${line.index}`);
              const target = document.createElement("textarea");
              target.className = "line-target";
              target.value = line.translated || "";
              target.setAttribute("aria-label", `مراجعة الترجمة العربية ${line.index}`);
              target.dir = "auto";
              if (line.translation_error) {
                const warning = document.createElement("small");
                warning.className = "quality-warning";
                warning.textContent = `تعذرت ترجمة هذه المنطقة؛ راجع النص الإنجليزي أو أعد محاولة المنطقة. ${line.translation_error}`;
                item.append(warning);
              }
              const includeLabel = document.createElement("label");
              includeLabel.className = "region-include";
              const include = document.createElement("input");
              include.type = "checkbox";
              include.checked = true;
              includeLabel.append(include, document.createTextNode(" إدراج النص في المعاينة"));
              const coords = document.createElement("small");
              coords.className = "line-coords";
              const rotation = line.region_type === "external_text" ? ` · ميل ${Math.round(line.angle || 0)}°` : "";
              const hasFailedAttempt = line.translation_attempt_elapsed_seconds != null;
              const regionSeconds = line.translation_attempt_elapsed_seconds ?? line.translation_elapsed_seconds;
              const regionTime = Number.isFinite(Number(regionSeconds))
                ? ` · ${hasFailedAttempt ? "محاولة فاشلة" : "الترجمة"} ${Number(regionSeconds).toFixed(1)}ث` : "";
              coords.textContent = `الموقع: x=${Math.round(line.left)}, y=${Math.round(line.top)} — ${Math.round(line.width)}×${Math.round(line.height)} px${rotation}${regionTime}`;
              const retranslate = document.createElement("button");
              retranslate.type = "button";
              retranslate.className = "secondary retranslate-region";
              retranslate.textContent = "إعادة ترجمة المنطقة بالسياق";
              retranslate.addEventListener("click", async () => {
                retranslate.disabled = true;
                retranslate.textContent = "جارٍ إعادة الترجمة…";
                try {
                  const recentContext = out.translated_lines
                    .slice(Math.max(0, line.index - 3), line.index - 1)
                    .map(previous => previous.region_type === "external_text"
                      ? `Narration/caption: ${previous.text}`
                      : `Dialogue: ${previous.text}`)
                    .filter(Boolean).join("\n");
                  const response = await fetch("/api/translate/region", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                      source: sourceEdit.value, context: recentContext,
                      chapter_title: data.title, work_scope: data.series_key || data.series_title || data.title,
                      series_title: data.series_title || data.title, page_index: page.index,
                      region_index: line.index, bubble_id: line.bubble_id,
                      region_type: line.region_type || "speech_bubble",
                      reading_layout: readingLayout.value,
                    })
                  });
                  const translatedRegion = await response.json();
                  if (!response.ok) {
                    if (Number.isFinite(Number(translatedRegion.attempt_elapsed_seconds))) {
                      line.translation_attempt_elapsed_seconds = Number(translatedRegion.attempt_elapsed_seconds);
                      coords.textContent = `الموقع: x=${Math.round(line.left)}, y=${Math.round(line.top)} — ${Math.round(line.width)}×${Math.round(line.height)} px${rotation} · محاولة فاشلة ${line.translation_attempt_elapsed_seconds.toFixed(1)}ث`;
                    }
                    throw new Error(translatedRegion.error || "تعذرت إعادة الترجمة.");
                  }
                  line.text = translatedRegion.source;
                  line.translated = translatedRegion.translated;
                  line.translation_elapsed_seconds = translatedRegion.translation_elapsed_seconds;
                  delete line.translation_attempt_elapsed_seconds;
                  coords.textContent = `الموقع: x=${Math.round(line.left)}, y=${Math.round(line.top)} — ${Math.round(line.width)}×${Math.round(line.height)} px${rotation} · الترجمة ${Number(line.translation_elapsed_seconds).toFixed(1)}ث`;
                  line.translation_error = "";
                  sourceEdit.value = translatedRegion.source;
                  target.value = translatedRegion.translated;
                  item.querySelector(".quality-warning")?.remove();
                  original.textContent = out.translated_lines.map(region => region.text).filter(Boolean).join("\n");
                  translated.textContent = out.translated_lines
                    .map(region => region.translated).filter(Boolean).join("\n");
                  setStatus(`أُعيدت ترجمة المنطقة ${line.index}؛ راجعها قبل إنشاء المعاينة.`);
                } catch (error) {
                  setStatus(error.message);
                } finally {
                  retranslate.disabled = false;
                  retranslate.textContent = "إعادة ترجمة المنطقة بالسياق";
                }
              });
              item.append(source, sourceEdit, target, includeLabel, coords, retranslate);
              linesList.append(item);
            });
            result.append(linesTitle, linesList);
            const renderButton = document.createElement("button");
            renderButton.textContent = "إنشاء معاينة داخل الصورة";
            const renderNote = document.createElement("p"); renderNote.className = "quality-warning";
            renderNote.textContent = "تُعالج فقاعات الحوار والنصوص الإنجليزية الواضحة خارجها في المانهوا. قد تُتجاوز منطقة إذا لم يمكن تركيب العربية داخل حدودها بأمان.";
            const previewImage = document.createElement("img");
            previewImage.className = "translated-preview";
            previewImage.hidden = true;
            previewImage.alt = "معاينة تجريبية للصفحة مع النص العربي";
            renderButton.addEventListener("click", async () => {
              renderButton.disabled = true; renderButton.textContent = "جارٍ إنشاء المعاينة…";
              try {
                const reviewedRegions = out.translated_lines.filter((line, index) => {
                  const row = linesList.children[index];
                  line.translated = row.querySelector("textarea.line-target").value.trim();
                  return row.querySelector("input[type=checkbox]").checked && line.translated;
                });
                translated.textContent = reviewedRegions.map(line => `${line.index}. ${line.translated}`).join("\n");
                const rendered = await fetch("/api/translate/render", {
                  method: "POST", headers: {"Content-Type": "application/json"},
                  body: JSON.stringify({image: page.image, referer: url, regions: reviewedRegions,
                    reading_layout: readingLayout.value})
                });
                const preview = await rendered.json(); if (!rendered.ok) throw new Error(preview.error || "تعذر إنشاء المعاينة.");
                previewImage.src = preview.preview_url;
                previewImage.hidden = false;
                renderNote.textContent = `${preview.quality_note} أُدرج ${preview.rendered_regions}، وتُرك ${preview.skipped_regions} منطقة · رسم الصورة ${preview.render_elapsed_seconds ?? "—"}ث.${formatBubbleRepairStrategies(preview.bubble_repair_strategies)}`;
                setStatus(`اكتملت ترجمة الصفحة ${page.index}.`);
              } catch (error) { renderNote.textContent = error.message; setStatus(`تعذر إظهار الصفحة ${page.index}: ${error.message}`); }
              finally { renderButton.disabled = false; renderButton.textContent = "إعادة إنشاء المعاينة"; card.dispatchEvent(new Event("auto-page-complete")); }
            });
            renderButton.hidden = true;
            result.append(renderButton, renderNote);
            imageWrap.append(previewImage);
            renderButton.click();
          } else {
            result.replaceChildren();
            const empty = document.createElement("p");
            empty.className = "note";
            empty.textContent = "لم يُعثر على نص قابل للترجمة في هذه الصفحة؛ لم تُعرض الصورة الأصلية.";
            result.append(empty);
            card.dispatchEvent(new Event("auto-page-complete"));
          }
          // The source transcript is kept in memory for rendering, but is not shown in the reader.

          if (Array.isArray(out.ocr_lines) && out.ocr_lines.length) {
            overlay.replaceChildren();
            const drawOverlay = () => {
              if (!img.naturalWidth || !img.naturalHeight) return;
              overlay.setAttribute("viewBox", `0 0 ${img.naturalWidth} ${img.naturalHeight}`);
              overlay.setAttribute("preserveAspectRatio", "none");
              out.ocr_lines.forEach((line, index) => {
                const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
                rect.setAttribute("x", line.left);
                rect.setAttribute("y", line.top);
                rect.setAttribute("width", line.width);
                rect.setAttribute("height", line.height);
                rect.setAttribute("class", "ocr-box");
                const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
                title.textContent = line.text || `Text line ${index + 1}`;
                rect.append(title);
                overlay.append(rect);
              });
            };
            const overlayButton = document.createElement("button");
            overlayButton.className = "overlay-toggle";
            overlayButton.textContent = `إظهار مواقع النصوص المكتشفة (${out.ocr_lines.length})`;
            overlayButton.addEventListener("click", () => {
              if (!img.complete || !img.naturalWidth) {
                img.addEventListener("load", drawOverlay, {once: true});
              } else {
                drawOverlay();
              }
              const visible = overlay.classList.toggle("visible");
              overlayButton.textContent = visible ? "إخفاء مواقع النصوص" : `إظهار مواقع النصوص المكتشفة (${out.ocr_lines.length})`;
            });
            result.append(overlayButton);
          } else {
            const noBoxes = document.createElement("p");
            noBoxes.className = "note";
            noBoxes.textContent = "لم يوفر OCR إحداثيات للنصوص في هذه الصفحة؛ الترجمة النصية ما زالت متاحة.";
            result.append(noBoxes);
          }
          if (out.processing_incomplete) {
            const completed = out.translated_lines.filter(line => /[\u0600-\u06FF]/.test(line.translated || "")).length;
            const failedIndex = out.translation_failures?.[0]?.index;
            setStatus(`ظهرت ترجمة ${completed} من ${out.translated_lines.length} منطقة في الصفحة ${page.index}. تعثرت المنطقة ${failedIndex || "غير المحددة"}؛ يمكنك إعادة محاولتها من قائمتها.`);
          } else {
            setStatus(`اكتملت ترجمة الصفحة ${page.index}؛ راجع النصوص ثم أنشئ المعاينة داخل الصورة.`);
          }
        } catch (error) {
          result.textContent = error.message;
          setStatus(error.message);
          card.dispatchEvent(new Event("auto-page-complete"));
        } finally {
          window.clearInterval(progressTimer);
          button.disabled = false;
          button.textContent = "إعادة المحاولة";
        }
      });
      card.append(heading, imageWrap, button, result);
      pagesBox.append(card);
    });
    for (const card of pagesBox.querySelectorAll(".page")) {
      const pageFinished = new Promise(resolve => card.addEventListener("auto-page-complete", resolve, {once: true}));
      card.querySelector(".page-process-trigger").click();
      await pageFinished;
    }
  } catch (error) {
    setStatus(error.message);
  } finally {
    loadBtn.disabled = false;
  }
});
