# نقل التشغيل إلى القرص D

تاريخ التحقق: 2026-11-13

## المسارات المعتمدة

- المشروع: `D:\MangaArabic\manga-arabic-web`
- بيئة المشروع: `D:\MangaArabic\manga-arabic-web\.venv`
- Benchmark: `D:\MangaArabic\AI_Benchmark\scientific_translation_20261112`
- بيئة Benchmark: `D:\MangaArabic\AI_Benchmark\.venv`

## ما تم التحقق منه

- ملفات المشروع خارج `.git` موجودة في النسخة الجديدة؛ اختلاف الملفات المرصود كان ضمن بيانات Git فقط.
- فحص Git لم يسجل كائنات مفقودة أو تالفة، وحالة التغييرات والملفات غير المتتبعة مطابقة للنسخة المنقولة.
- بيئتا Python محدثتان لمساري D وتستخدمان Python 3.12.10.
- استيراد اعتماديات Flask وNumPy وONNX Runtime وSentencePiece وRapidOCR وOpenCV نجح من بيئة المشروع على D.
- الصفحة الرئيسية أعادت HTTP 200 من Flask test client على نسخة D؛ لم يُشغّل نموذج ترجمة.
- مسارات النماذج في إعداد Benchmark تشير إلى D، والملفات الأربعة موجودة بأحجامها المسجلة.
- مسارات التشغيل في سكربتات Benchmark تشير إلى D. السجلات والتقارير القديمة تحتفظ بمسارات C كأثر تاريخي.
- النسخ الاحتياطية لملفات الإعداد قبل تعديلها موجودة في `D:\MangaArabic\migration_backups\pre_d_switch_20261113`.

## تشغيل التطبيق

من PowerShell:

```powershell
Set-Location 'D:\MangaArabic\manga-arabic-web'
& '.\tools\run_web.ps1'
```

لإيقافه، استخدم `Ctrl+C` في نافذة التشغيل.

## ملاحظة قبل حذف النسخة القديمة

لم يُحذف `C:\MangaArabic\manga-arabic-web`. احتفظ به كنسخة رجوع إلى أن تتأكد يدوياً من تشغيل التطبيق وأي عمليات تحتاجها من D. تثبيت Python الأساسي في `C:\Users\BMW1975\AppData\Local\Programs\Python\Python312` مستقل عن مجلد المشروع؛ لا تحذف ذلك التثبيت عند حذف مجلد المشروع القديم.
