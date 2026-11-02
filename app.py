import os
import re
import time
import hashlib
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, Response
from google_vision_ocr import extract_google_vision

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9"
})
CHAPTER_HOSTS = {"mangak.io", "www.mangak.io"}
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif")
OCR_CACHE = {}  # Avoid repeating OCR when translation needs a retry.


def valid_chapter_url(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in CHAPTER_HOSTS


def image_candidates(soup, page_url):
    found = []
    seen = set()
    for tag in soup.find_all(["img", "source"]):
        values = []
        for attr in ("src", "data-src", "data-lazy-src", "data-original", "data-url"):
            value = tag.get(attr)
            if value:
                values.append(value)
        srcset = tag.get("srcset")
        if srcset:
            values.extend(part.strip().split()[0] for part in srcset.split(",") if part.strip())
        for value in values:
            value = urljoin(page_url, value.strip())
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue
            if value not in seen and (any(parsed.path.lower().endswith(ext) for ext in IMAGE_EXTENSIONS) or "image" in value.lower()):
                seen.add(value)
                found.append(value)
    return found


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/api/chapter")
def get_chapter():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not valid_chapter_url(url):
        return jsonify(error="حالياً ندعم روابط الفصول من mangak.io فقط، ويجب أن يبدأ الرابط بـ https."), 400
    try:
        response = HTTP.get(url, timeout=25)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        images = image_candidates(soup, response.url)
        if not images:
            return jsonify(error="لم أجد صور الفصل في الصفحة. قد يستخدم الموقع تحميل JavaScript أو حماية تمنع الاستخراج."), 422
        return jsonify(title=(soup.title.get_text(" ", strip=True) if soup.title else "Manga chapter"),
                       pages=[{"index": i + 1, "image": image} for i, image in enumerate(images)])
    except requests.RequestException as exc:
        return jsonify(error=f"تعذر جلب الفصل: {exc}"), 502


def run_ocr_space(image_bytes, content_type, api_key):
    extension = "png" if "png" in content_type else "webp" if "webp" in content_type else "jpg"
    last_error = None
    for attempt in range(3):
        try:
            response = HTTP.post("https://api.ocr.space/parse/image",
                data={"apikey": api_key, "language": "eng", "isOverlayRequired": "true"},
                files={"file": (f"manga-page.{extension}", image_bytes, content_type)}, timeout=(20, 120))
            response.raise_for_status()
            payload = response.json()
            if payload.get("IsErroredOnProcessing"):
                raise RuntimeError(str(payload.get("ErrorMessage") or "OCR.space processing failed"))
            results = payload.get("ParsedResults") or []
            extracted = "\n".join(item.get("ParsedText", "") for item in results).strip()
            if not extracted: raise RuntimeError("OCR.space لم يتمكن من قراءة نص واضح.")
            lines = []
            for result in results:
                for line in (result.get("TextOverlay") or {}).get("Lines") or []:
                    words = line.get("Words") or []
                    if not words: continue
                    left = min(float(w.get("Left", 0)) for w in words)
                    top = min(float(w.get("Top", 0)) for w in words)
                    right = max(float(w.get("Left", 0)) + float(w.get("Width", 0)) for w in words)
                    bottom = max(float(w.get("Top", 0)) + float(w.get("Height", 0)) for w in words)
                    text = " ".join(str(w.get("WordText", "")) for w in words).strip()
                    if text and right > left and bottom > top:
                        lines.append({"text": text, "left": round(left,2), "top": round(top,2), "width": round(right-left,2), "height": round(bottom-top,2)})
            return extracted, lines
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2: time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"OCR.space فشل بعد 3 محاولات: {last_error}")


def translate_text(text, chapter_title="", page_index=None, line_mode=False):
    if not text.strip():
        return ""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("مفتاح Gemini غير مضبوط. أضف GEMINI_API_KEY في Termux ثم أعد تشغيل التطبيق.")

    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    system_instruction = """
أنت مترجم مانغا محترف من الإنجليزية إلى العربية.
المطلوب ترجمة أدبية سياقية للحوار والنصوص داخل صفحات المانغا، وليست ترجمة حرفية.
قواعد إلزامية:
- انقل المعنى المقصود والمشاعر والنبرة والعلاقة بين المتحدثين، ولا تلتزم بترتيب الكلمات الإنجليزي.
- استخدم عربية فصحى طبيعية وسلسة ومناسبة لحوار المانغا، وتجنب الركاكة والترجمة الآلية.
- اجعل الجمل موجزة بقدر الإمكان لتناسب فقاعات الكلام، من دون حذف معنى مهم.
- حافظ على أسماء الشخصيات والأماكن والمهارات والألقاب كما هي إذا لم يتوفر مقابل عربي ثابت، ولا تخترع أسماء.
- حافظ على ترتيب الأسطر والفقرات قدر الإمكان، وترجم المؤثرات الصوتية بما يناسب معناها.
- عند تفعيل وضع line_mode: كل سطر مرقم في الإدخال يقابله سطر ترجمة واحد بالترتيب نفسه، ولا تدمج الأسطر ولا تضف أرقاماً أو شروحاً.
- إذا كان النص OCR مشوشاً أو ناقصاً، استنتج بحذر من السياق ولا تضف حواراً غير موجود.
- أخرج الترجمة العربية فقط، دون النص الإنجليزي أو شروح أو مقدمات أو علامات اقتباس إضافية.
"""
    context = f"عنوان الفصل: {chapter_title or 'غير محدد'}"
    if page_index is not None:
        context += f"\nرقم الصفحة: {page_index}"
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": context + "\n\nالنص المستخرج من الصفحة:\n" + text[:12000]}]}],
        "generationConfig": {"temperature": 0.35, "topP": 0.9, "maxOutputTokens": 4096}
    }
    # Gemini may temporarily return 429/5xx when its service is busy.
    # Retry only transient failures; do not retry invalid keys or bad requests.
    retryable_statuses = {408, 429, 500, 502, 503, 504}
    response = None
    last_network_error = None
    for attempt in range(6):
        try:
            response = HTTP.post(
                endpoint,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=(15, 90)
            )
            if response.status_code not in retryable_statuses:
                break
            if attempt < 5:
                time.sleep([2, 4, 8, 12, 20][attempt])
        except requests.RequestException as exc:
            last_network_error = exc
            if attempt == 5:
                raise RuntimeError(f"تعذر الاتصال بخدمة Gemini بعد 6 محاولات: {exc}") from exc
            time.sleep([2, 4, 8, 12, 20][attempt])

    if last_network_error and response is None:
        raise RuntimeError(f"تعذر الاتصال بخدمة Gemini: {last_network_error}")
    if response is None:
        raise RuntimeError("لم تصل استجابة من Gemini.")

    if response.status_code >= 400:
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            detail = response.text
        if response.status_code in retryable_statuses:
            raise RuntimeError(
                f"Gemini ما زال مزدحماً بعد 6 محاولات (HTTP {response.status_code}). "
                f"انتظر دقيقة ثم أعد المحاولة. التفاصيل: {detail[:350]}"
            )
        raise RuntimeError(f"Gemini API (HTTP {response.status_code}): {detail[:500]}")
    data = response.json()
    candidates = data.get("candidates") or []
    parts = (candidates[0].get("content", {}).get("parts", []) if candidates else [])
    translated = "\n".join(part.get("text", "") for part in parts if part.get("text")).strip()
    if not translated:
        raise RuntimeError("Gemini لم يُرجع ترجمة نصية. قد يكون الطلب مرفوضاً أو النص غير واضح.")
    return translated


@app.get("/api/image")
def proxy_image():
    image_url = (request.args.get("url") or "").strip()
    referer = (request.args.get("referer") or "").strip()
    parsed = urlparse(image_url)
    ref_parsed = urlparse(referer)
    if parsed.scheme != "https" or not parsed.netloc or ref_parsed.hostname not in CHAPTER_HOSTS:
        return jsonify(error="رابط الصورة أو مرجع الفصل غير صالح."), 400
    try:
        image_response = HTTP.get(image_url, headers={"Referer": referer}, timeout=30)
        image_response.raise_for_status()
        content_type = image_response.headers.get("Content-Type", "").split(";")[0].lower()
        if not content_type.startswith("image/"):
            return jsonify(error="المصدر لم يُرجع ملف صورة."), 415
        if len(image_response.content) > 12 * 1024 * 1024:
            return jsonify(error="حجم الصورة أكبر من الحد المسموح (12 MB)."), 413
        return Response(image_response.content, mimetype=content_type, headers={"Cache-Control": "public, max-age=1800"})
    except requests.RequestException as exc:
        return jsonify(error=f"تعذر تحميل الصورة من المصدر: {exc}"), 502


@app.post("/api/translate")
def translate_page():
    data = request.get_json(silent=True) or {}
    image_url = (data.get("image") or "").strip()
    referer = (data.get("referer") or "").strip()
    parsed = urlparse(image_url)
    ref_parsed = urlparse(referer)
    if parsed.scheme != "https" or not parsed.netloc or ref_parsed.hostname not in CHAPTER_HOSTS:
        return jsonify(error="رابط الصورة أو مرجع الفصل غير صالح."), 400

    try:
        # Download once and reuse OCR results from the in-memory cache.
        image_response = HTTP.get(image_url, headers={"Referer": referer}, timeout=30)
        image_response.raise_for_status()
        content_type = image_response.headers.get("Content-Type", "").split(";")[0].lower()
        if not content_type.startswith("image/"):
            return jsonify(error="رابط الصفحة لم يُرجع صورة صالحة."), 415
        if len(image_response.content) > 12 * 1024 * 1024:
            return jsonify(error="حجم الصورة أكبر من الحد المسموح (12 MB)."), 413
        image_hash = hashlib.sha256(image_response.content).hexdigest()
        cached_ocr = OCR_CACHE.get(image_hash)
        if cached_ocr:
            extracted, ocr_lines, ocr_provider = cached_ocr
        else:
            provider = os.getenv("OCR_PROVIDER", "auto").strip().lower()
            vision_key = os.getenv("GOOGLE_CLOUD_VISION_API_KEY")
            ocr_key = os.getenv("OCR_SPACE_API_KEY")
            if provider not in {"auto", "google", "ocrspace"}:
                raise RuntimeError("OCR_PROVIDER يجب أن يكون auto أو google أو ocrspace.")
            use_google = provider == "google" or (provider == "auto" and bool(vision_key))
            if use_google:
                if not vision_key:
                    raise RuntimeError("مفتاح Google Vision غير مضبوط: GOOGLE_CLOUD_VISION_API_KEY.")
                try:
                    extracted, ocr_lines = extract_google_vision(image_response.content, vision_key)
                    ocr_provider = "google_vision"
                except Exception as google_error:
                    if not ocr_key:
                        raise RuntimeError(f"فشل Google Vision OCR: {google_error}") from google_error
                    extracted, ocr_lines = run_ocr_space(image_response.content, content_type, ocr_key)
                    ocr_provider = "ocr_space_fallback"
            else:
                if not ocr_key:
                    raise RuntimeError("أضف GOOGLE_CLOUD_VISION_API_KEY أو OCR_SPACE_API_KEY.")
                extracted, ocr_lines = run_ocr_space(image_response.content, content_type, ocr_key)
                ocr_provider = "ocr_space"
            if len(OCR_CACHE) >= 40:
                OCR_CACHE.pop(next(iter(OCR_CACHE)))
            OCR_CACHE[image_hash] = (extracted, ocr_lines, ocr_provider)
        line_texts = [line["text"] for line in ocr_lines if line.get("text")]
        if line_texts:
            numbered_text = "\n".join(f"{i + 1}. {line_text}" for i, line_text in enumerate(line_texts))
            translated_raw = translate_text(
                numbered_text, data.get("chapter_title", ""), data.get("page_index"), line_mode=True
            )
            translated_parts = []
            for output_line in translated_raw.splitlines():
                cleaned = re.sub(r"^\s*\d+[.)、-]\s*", "", output_line).strip()
                if cleaned:
                    translated_parts.append(cleaned)
            # Keep source-to-translation alignment explicit. If the model
            # changes line count, retain available translations and mark gaps.
            translated_lines = []
            for index, source_line in enumerate(ocr_lines):
                translated_line = translated_parts[index] if index < len(translated_parts) else ""
                translated_lines.append({
                    "index": index + 1,
                    "text": source_line["text"],
                    "translated": translated_line,
                    "left": source_line["left"],
                    "top": source_line["top"],
                    "width": source_line["width"],
                    "height": source_line["height"]
                })
            translated = "\n".join(item["translated"] for item in translated_lines if item["translated"])
        else:
            translated = translate_text(extracted, data.get("chapter_title", ""), data.get("page_index"))
            translated_lines = []
        return jsonify(original=extracted, translated=translated,
                       ocr_lines=ocr_lines, translated_lines=translated_lines, ocr_provider=ocr_provider)
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        return jsonify(error=f"تعذرت المعالجة: {exc}"), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
