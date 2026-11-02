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
      const img = document.createElement("img");
      const imageProxy = `/api/image?url=${encodeURIComponent(page.image)}&referer=${encodeURIComponent(url)}`;
      img.src = imageProxy;
      img.alt = `Manga page ${page.index}`;
      img.loading = "lazy";
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
            body: JSON.stringify({image: page.image, referer: url})
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
        } catch (error) {
          result.textContent = error.message;
        } finally {
          button.disabled = false;
          button.textContent = "إعادة المحاولة";
        }
      });
      card.append(heading, img, button, result);
      pagesBox.append(card);
    });
  } catch (error) {
    setStatus(error.message);
  } finally {
    loadBtn.disabled = false;
  }
});
