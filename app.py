import os
import re
import time
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, Response

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9"
})
CHAPTER_HOSTS = {"mangak.io", "www.mangak.io"}
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif")


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


def translate_text(text, chapter_title="", page_index=None):
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
    for attempt in range(4):
        try:
            response = HTTP.post(
                endpoint,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=60
            )
            if response.status_code not in retryable_statuses:
                break
            if attempt < 3:
                time.sleep(2 ** attempt)  # 1, 2, then 4 seconds
        except requests.RequestException as exc:
            last_network_error = exc
            if attempt == 3:
                raise RuntimeError(f"تعذر الاتصال بخدمة Gemini بعد 4 محاولات: {exc}") from exc
            time.sleep(2 ** attempt)

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
                f"Gemini ما زال مزدحماً بعد 4 محاولات (HTTP {response.status_code}). "
                f"أعد معالجة هذه الصفحة لاحقاً. التفاصيل: {detail[:350]}"
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

    api_key = os.getenv("OCR_SPACE_API_KEY")
    if not api_key:
        return jsonify(error="مفتاح OCR غير مضبوط. أضف OCR_SPACE_API_KEY إلى متغيرات البيئة في الاستضافة."), 503

    try:
        # Download the image with the chapter Referer, then upload its bytes to OCR.space.
        image_response = HTTP.get(image_url, headers={"Referer": referer}, timeout=30)
        image_response.raise_for_status()
        content_type = image_response.headers.get("Content-Type", "").split(";")[0].lower()
        if not content_type.startswith("image/"):
            return jsonify(error="رابط الصفحة لم يُرجع صورة صالحة."), 415
        if len(image_response.content) > 12 * 1024 * 1024:
            return jsonify(error="حجم الصورة أكبر من الحد المسموح (12 MB)."), 413
        extension = "png" if "png" in content_type else "webp" if "webp" in content_type else "jpg"
        ocr_response = HTTP.post(
            "https://api.ocr.space/parse/image",
            data={"apikey": api_key, "language": "eng", "isOverlayRequired": "false"},
            files={"file": (f"manga-page.{extension}", image_response.content, content_type)},
            timeout=60
        )
        ocr_response.raise_for_status()
        payload = ocr_response.json()
        if payload.get("IsErroredOnProcessing"):
            message = payload.get("ErrorMessage") or "فشل OCR."
            return jsonify(error=str(message)), 502
        results = payload.get("ParsedResults") or []
        extracted = "\n".join(item.get("ParsedText", "") for item in results).strip()
        if not extracted:
            return jsonify(error="لم يتمكن OCR من قراءة نص واضح في هذه الصفحة."), 422
        translated = translate_text(extracted, data.get("chapter_title", ""), data.get("page_index"))
        return jsonify(original=extracted, translated=translated)
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        return jsonify(error=f"تعذرت المعالجة: {exc}"), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
