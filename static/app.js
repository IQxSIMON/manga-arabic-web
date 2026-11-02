const loadBtn = document.getElementById("loadBtn");
const statusBox = document.getElementById("status");
const pagesBox = document.getElementById("pages");

function setStatus(message) { statusBox.textContent = message || ""; }

loadBtn.addEventListener("click", async () => {
  const url = document.getElementById("chapterUrl").value.trim();
  pagesBox.replaceChildren();
  setStatus("جارٍ جلب الفصل...");
  loadBtn.disabled = true;
  try {
    const response = await fetch("/api/chapter", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "فشل جلب الفصل.");
    setStatus(`تم العثور على ${data.pages.length} صفحة. الترجمة تظهر أسفل كل صورة.`);
    data.pages.forEach(page => {
      const card = document.createElement("article");
      card.className = "page";
      const heading = document.createElement("h3");
      heading.textContent = `الصفحة ${page.index}`;
      const imageWrap = document.createElement("div");
      imageWrap.className = "image-wrap";
      const img = document.createElement("img");
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
      const result = document.createElement("div");
      button.addEventListener("click", async () => {
        button.disabled = true;
        button.textContent = "جارٍ التحليل...";
        result.textContent = "جارٍ تنفيذ OCR والترجمة...";
        try {
          const r = await fetch("/api/translate", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({image: page.image, referer: url, chapter_title: data.title, page_index: page.index})
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
          result.append(translatedTitle, translated, originalTitle, original);

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
        } catch (error) {
          result.textContent = error.message;
        } finally {
          button.disabled = false;
          button.textContent = "إعادة المحاولة";
        }
      });
      card.append(heading, imageWrap, button, result);
      pagesBox.append(card);
    });
  } catch (error) {
    setStatus(error.message);
  } finally {
    loadBtn.disabled = false;
  }
});
