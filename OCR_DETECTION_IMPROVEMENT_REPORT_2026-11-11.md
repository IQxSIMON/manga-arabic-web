# تقرير تحسين كشف وقراءة النصوص — 2026-11-11

## ما نُفذ

- أبقيت RapidOCR PP-OCRv6 small المسار الأساسي لكشف الأسطر وقراءة النص. لم يتغير نموذج Index أو إعدادات الترجمة أو موجّهاتها.
- أوقفت استدعاء RT-DETR في كل صفحة. على العينات الخمس السابقة لم يضف أي سطر OCR، بينما استهلك 15.4–25.4 ثانية لكل صفحة. بقي الكاشف وملفاته كما هي لاستخدامها في تشخيص الحالات الصعبة.
- أضفت إعادة قراءة اختيارية بنموذج PP-OCRv6 medium، تعمل فقط على حالة ارتباك حرفي واضحة مثل `!T`، ولا تقبل مرشحاً إلا إذا تحسنت قرائن الكلمات مع المحافظة على عدد الأسطر ومواقعها.
- شددت فصل الفقاعات المكدسة اعتماداً على فجوة الأسطر، ورفضت تقسيم قناع الفقاعة عندما لا يغطي صندوق RT-DETR بصورة كافية.
- أضفت تنزيل النموذج المتوسط اختيارياً مع فحص SHA-256، وأضفت `pyspellchecker` لاستخدامه في ترجيح المرشح فقط؛ لا يصحح النص تلقائياً.
- رفعت `PAGE_PIPELINE_VERSION` إلى 25 كي لا تُستخدم نتائج OCR المخزنة من النسخة السابقة كما لو كانت ناتجة عن المسار الحالي.

## القياس على الصفحات المحفوظة

كل القياسات التالية OCR فقط؛ لم يُشغّل Index ولم تُعد ترجمة أي صفحة.

| الصفحة | القياس السابق مع RT-DETR | القياس الحالي | الفرق |
|---|---:|---:|---:|
| 2 | 67.04 ث | 47.43 ث | أسرع 29.2% |
| 3 | 69.93 ث | 53.12 ث | أسرع 24.0% |
| 5 | 65.31 ث | 42.33 ث | أسرع 35.2% |
| 10 | 72.04 ث | 44.86 ث | أسرع 37.7% |
| 14 | 61.16 ث | 44.96 ث | أسرع 26.5% |
| **المجموع** | **335.48 ث** | **232.70 ث** | **أسرع 30.6%** |

تطابق عدد الأسطر المستخرج لكل صفحة بين القياسين: 30، 36، 42، 38، 21. تطابق عدد المناطق أيضاً في الصفحات 2 و3 و5 و10. في الصفحة 14 ارتفع عدد المناطق من 8 إلى 9 لأن فجوة النص فصلت حوارين مدمجين، مع بقاء عدد الأسطر 21. الزمن يتأثر بحالة الجهاز وONNX Runtime؛ القياس على خمس صفحات محفوظة وليس ضماناً لزمن كل فصل.

## تحسن مؤكد وحدود حالية

- في الصفحة 14 صححت القراءة المتوسطة `IS THAT !T,` إلى `IS THAT IT,` مع الاحتفاظ بإحداثيات السطر الأصلية. وكشف الاختبار المباشر أن `PUR(ASE` بقي غير موثوق؛ لم يُقبل `PURCASE` لأنه لم يحسن الكلمات المعروفة.
- بقي الحوار السفلي في الصفحة 14 ناقصاً: `VE ONLY ETED THE SH-UPS.`. قناع الفقاعة المقطوع من نموذج التقسيم يبدأ بعيداً إلى اليمين؛ القراءة المتوسطة لا تستطيع استعادة بكسلات خارج القناع. صندوق RT-DETR يحدد المنطقة الأوسع لكنه لا يحل المشكلة وحده، لذلك لم أغيّر هندسة الفقاعة أو قناع التبييض.
- بقيت أخطاء معروفة في الصفحة 5 مثل `O QUEST NOTICE`, `E..,` و`NDITIONS`. الإشارات الواضحة وحدها تستدعي النموذج المتوسط حالياً؛ لم تُقبل تخمينات أضعف لمجرد أنها مختلفة.
- لا يمكن إثبات اكتمال 100% من عيّنتين أو ضمانه آلياً لكل الأعمال. النصوص المزخرفة والمتداخلة والخلفيات المعقدة تتطلب مراجعة مجموعة أوسع واختباراً بصرياً.

## الملفات

- القياس النهائي: `translator_engine/data/benchmark_runs/ocr_saved_samples_20261111_104952.json`
- تقييم الصفحات الخمس النهائي: `translator_engine/data/benchmark_runs/ocr_saved_samples_20261111_110052.json`
- خط الأساس للصفحات الخمس: `translator_engine/data/benchmark_runs/ocr_saved_samples_20261111_093800.json`
- مقارنة small/medium: `translator_engine/data/benchmark_runs/ocr_recognizer_compare_20261111_100233.json`
- أداة تقييم OCR المحفوظ: `tools/evaluate_saved_ocr_samples.py`
- أداة تثبيت النموذج المتوسط عند الحاجة: `tools/install_ocr_medium_model.py`

## التحقق

- `translator_engine/tests/test_local_ocr.py`: **21 passed**.
- فحص Python compile لملفات المحرك والأداة و`app.py`: ناجح.
- لم تُشغّل اختبارات ترجمة أو أداء Index، ولم تُعد ترجمة نتائج محفوظة.

## استعادة النص عند قصّ قناع الفقاعة — متابعة 2026-11-11

أُضيف مسار استعادة احتياطي مشروط: لا يستدعي RT-DETR إلا إذا انتهى سطر OCR واحد أو أكثر بمحتوى لغوي مشكوك فيه عند حافة قناع الفقاعة نفسها. عندها يعيد OCR قراءة صندوق الفقاعة المكتشف بإحكام، ويصدر النص المستعاد كمنطقة `external_text` ذات مضلعات OCR، كي تزال الحروف الأصلية محلياً من دون إعادة رسم الفقاعة أو تعبئتها. اختيار صندوق ضيق مهم: الحشو المحيط زاد تشويش بعض الحروف في التجربة.

في الصفحة 14 المحفوظة، استعاد المسار النص الكامل `YOU'VE ONLY COMPLETED THE PUSH-UPS` من الجزء الذي كان يظهر `VE ONLY ETED THE SH-UPS.`. بقي حوار الفقاعة العليا `IS THAT IT, YOUNG MONARCH?` في منطقته المستقلة. وفي الصفحة 5 استعاد `LET'S SEE... THE QUEST CONDITIONS ARE...` بدلاً من `LET'S E.., THE QUEST NDITIONS ARE...`. بلغت كلفة RT-DETR نحو 10.43 ثانية في الصفحة 14 و10.66 ثانية في الصفحة 5؛ الصفحات الثلاث الأخرى من العينة لم تستدعِه. كلفة الاستعادة تظهر فقط عند الاشتباه في حروف مقصوصة عند حد القناع. لا يعني هذا القياس أن كل أخطاء OCR قد حُلّت.

أعيد OCR للصفحات الخمس قبل توسيع الشرط وبعده. بعد توسيعه، الصفحات 2 و3 و10 لم تستدعِ الكاشف، والصفحتان 5 و14 استخدمتاه لاستعادة فقرة في كل صفحة. عدد الأسطر بقي 42 في الصفحة 5، لكن النص تحسن؛ الصفحة 14 انتقلت من 21 إلى 22 سطراً. مراجعة بصرية بقصاصة أصلية من الصفحة 5 أظهرت إزالة النص الأسود من الفقاعة البيضاء واستبداله بنص عربي اختباري. وأُجريت معاينة مماثلة للفقاعة السوداء في الصفحة 14. هذه معاينات تركيب بالنص التجريبي وليست ترجمة فعلية. النتائج المفصلة محفوظة في `translator_engine/data/benchmark_runs/ocr_pages_05_14_tight_crop_20261111.json` و`translator_engine/data/benchmark_runs/ocr_saved_samples_20261111_targeted_rescue.json`؛ القياس المنفرد في `translator_engine/data/benchmark_runs/ocr_page14_targeted_rescue_20261111.json`. رُفع `PAGE_PIPELINE_VERSION` إلى 27 بعد تثبيت منطق الاستعادة الجديد كي لا تُعرض نتائج OCR المخزنة من المسار السابق على أنها حديثة.

الاختبارات: اختبارات OCR والكاشف والرسم وتقييم الجودة: **35 passed** بعد إضافة أداة المقارنة واختباراتها. فحوص Python compile و`git diff --check` اجتازت أيضاً. لم يُشغّل Index أو أي اختبار ترجمة.

## الخطوة التالية

منطقة إشعار الصفحة 3 محجوبة جزئياً بالشخصية؛ لا يمكن استعادة النص المغطى بأمان، لذلك يجب وسمه لمراجعة المستخدم وعدم توسيع قناع الإزالة فوق الرسم. التنبيه المعجمي أضيف لكنه يحتاج قياس false positives على أعمال إضافية، ثم توسيع مجموعة الحالات الصعبة المعلّمة يدوياً قبل تقدير الدقة العامة. تبقى النتائج مرتبطة بخمس صفحات محفوظة وليست دليلاً على اكتمال OCR لكل الأعمال.

## تقييم نصي قابل لإعادة التشغيل للحالات المصححة

مرجع الاختبار ونتائجه: `translator_engine/data/ocr_recovery_gold_cases.json` و`translator_engine/data/benchmark_runs/ocr_recovery_quality_20261111.json`. الأداة `tools/evaluate_ocr_recovery_quality.py` تقارن النصين السابق والحالي بـ WER وCER بعد تطبيع علامات الترقيم. الصفحة 5: WER/CER انخفضا من 33.3%/14.3% إلى 0%/0%. الصفحة 14: انخفضا من 50%/35.7% إلى 0%/0%. هذان مثالان مستهدفان من أصل خمس صفحات، ولا يصلحان كتقدير لدقة OCR الكلية. اختبار الأداة: `translator_engine/tests/test_ocr_recovery_quality.py`.

## تنبيه مراجعة النص الخارجي — تحديث 2026-11-11

أضيفت إشارة مراجعة غير مانعة للنصوص الخارجية التي تحوي ثلاث كلمات قابلة للتقييم على الأقل وكلمتين أو أكثر غير معروفتين لقاموس spellchecker. لا تعدّل OCR ولا الترجمة ولا تمنع عرض النص؛ تمرر سبب التنبيه إلى قائمة مراجعة القارئ وتفتح القائمة تلقائياً. في العينة المحفوظة ظهرت الإشارة في 4 مناطق من 17 نصاً خارجياً: شعار الصفحة 3 وإشعاراها، وتعليق المهمة في الصفحة 5. فحص المساعد اللغوي على المناطق الـ17 استغرق 0.26 ثانية في تشغيل منفصل، يتضمن التحميل الكسول للقاموس؛ هذا ليس قياساً لكامل خط OCR. المؤشر إرشادي وقد يلتقط أسماء الشخصيات والمصطلحات الخيالية، لذلك لا يستبعد النص تلقائياً.

إصدار خط المعالجة الحالي `PAGE_PIPELINE_VERSION=29`. اجتازت اختبارات OCR والكاشف والرسم ومقارنة الجودة وتجميع Index في جولة سابقة، ثم اجتازت اختبارات OCR ومقارنة المراجعة المركزة في الجولات الأخيرة. لم يُشغّل Index أو أي اختبار ترجمة.

## تحقق فعلي من تنبيه المراجعة — الصفحتان 3 و5

أُعيد OCR محلياً للصفحتين دون ترجمة. الصفحة 3: **58.08 ثانية**، 36 سطراً، 12 منطقة، و3 تنبيهات OCR، من دون تشغيل RT-DETR. الصفحة 5: **54.01 ثانية**، 42 سطراً، 12 منطقة، تنبيه واحد، و9.38 ثانية لRT-DETR عند استعادة الفقاعة المقصوصة. النص المستعاد كان `LET'S SEE... THE QUEST CONDITIONS ARE.`. لم يظهر التنبيه على مناطق فقاعات الحوار. النتيجة الخام في `translator_engine/data/benchmark_runs/ocr_pages_03_05_review_hints_20261111.json`. هذا يثبت عمل المسار على الصفحتين المختبرتين، ولا يقيس التغطية على صفحات أخرى.

## مراجعة بصرية مستقلة للصفحات الخمس — 2026-11-11

راجعت الصور الأصلية ومخططات المناطق المحفوظة للصفحات 2 و3 و5 و10 و14، من دون تشغيل OCR أو Index مجدداً. هذه المراجعة لا تُعدّ مجموعة مرجعية كاملة، لكنها أضافت فحصاً بصرياً مستقلاً عن أرقام السطور:

- **الصفحة 2:** نصوص الفقاعات والتعليقات الواضحة متطابقة إجمالاً مع المناطق المحفوظة. لا يظهر فقد واسع للمناطق في المخطط؛ ولا تثبت هذه الملاحظة دقة كل حرف.
- **الصفحة 3:** حوار الفقاعات واضح ومفصول. عنوان العمل المزخرف يُقرأ قراءة رديئة (`LEUEING RAGNAROK AROAO`) ويجب إبقاؤه ضمن مراجعة OCR؛ لوحة الإشعار الأولى قابلة للقراءة، لكن الأيقونة قبل `NOTICE` تظهر كأنها `O`. نسخة الإشعار الثانية محجوبة بالشخصية، فلا يصح استكمالها بالتخمين أو توسيع قناع إزالة النص فوق الرسم.
- **الصفحة 5:** فقاعات الحوار الواضحة جيدة بصرياً، مع استعادة مؤكدة للنص `LET'S SEE... THE QUEST CONDITIONS ARE.`. اللوحات المائلة/المزخرفة خارج الفقاعات أصعب: OCR يخلط رمز اللوحة مع `QUEST NOTICE`، وبعض نص لوحة الأهداف يحتاج مراجعة يدوية. لا يوجد دليل يبرر استبدال النص الأصلي أو إزالة منطقة أكبر تلقائياً.
- **الصفحة 10:** المناطق المحفوظة تتضمن تعليقين خارجيين مقروءين. لم يظهر في هذا الفحص المختصر نقص جديد واضح يكفي لتبرير قاعدة عامة.
- **الصفحة 14:** حوار `YOU'VE ONLY COMPLETED THE PUSH-UPS` استُعيد بنجاح. كلمة `PURCHASE` في رسم مائل محجوبة بإصبع؛ OCR ينتج `PURO-ASE`، وهذا مثال مؤكد على أن الحروف المخفية لا يمكن استعادتها من الصورة نفسها.

توجد حالتان مستعادَتان ومقاسوَتان رقمياً (الصفحتان 5 و14)، إضافة إلى عيوب مرئية منفصلة في الشعار المزخرف والإشعار المحجوب والنص المائل. لم أضف WER/CER لهذه العيوب لعدم وجود مرجع كامل موثوق لبعضها، ولم أرفع النتيجة إلى ادعاء دقة عامة. الأولوية العملية التالية هي تكبير مجموعة الحالات المرجعية للنص الواضح والمقروء، مع إبقاء العناوين المزخرفة والمحتوى المحجوب حالات مراجعة لا تُصحح بالتخمين.

لم تتغير خوارزمية OCR أو إعدادات Index والترجمة في هذه المراجعة، ولم تُشغّل ترجمة جديدة.

## مجموعة مرجعية بصرية موسّعة — 2026-11-11

أُنشئت أداة `tools/create_ocr_review_sheets.py` لإخراج قصاصات مناطق OCR من النتائج والصور المحفوظة من دون إعادة تشغيل OCR. راجعت القصاصات وأضفت 37 حالة مرجعية من الصفحات الخمس (280 كلمة)، بما فيها فقّاعة الصفحة 10 التي كان التقسيم قد أسقط وسطها. استُبعدت عمداً الشعارات المزخرفة والنصوص المحجوبة أو الملتبسة. تقارن `tools/evaluate_ocr_visual_gold.py` المناطق بمطابقة هندسية انطلاقاً من تقرير مرجعي ثابت، لأن عدد/ترتيب المناطق يتغير عند الدمج والاستعادة.

القياس الأحدث `translator_engine/data/benchmark_runs/ocr_visual_gold_adjacent_recovery_20261111.json` وجد **19 خطأ كلمة من 280 (WER 6.79%)** في خط الأساس، مقابل **0 من 280** بعد استعادة الفقاعة في الصفحة 10 وفصلها في الصفحة 14. تحسنت منطقتان. هذا **ليس** تقديراً لدقة OCR العامة: الحالات منتقاة، وتغطي نصوصاً مقروءة في المناطق المكتشفة، ولا تحسب الاستدعاء الكامل أو النصوص التي لم تُكتشف. لا ينبغي تفسير الصفر على أنه اكتمال.

اختبارات OCR والكاشف والرسم ومقارنة الاستعادة وتجميع Index: **46 passed in 16.15s**. فحوص `py_compile` و`git diff --check` ناجحة. لم يُشغّل نموذج Index ولم تُعد أي ترجمة. لم تتغير نماذج الترجمة أو تعليماتها أو إعداداتها، فلا يوجد أثر على سرعة الترجمة أو جودتها.

جُرّب دون تعديل الكود توسيع تنبيه القاموس إلى الفقاعات عند وجود كلمتين على الأقل وكلمة مجهولة واحدة. كان سيعلّم **7 من 36 منطقة سليمة** للمراجعة، بسبب أسماء/مصطلحات وأرقام وتعبيرات مزخرفة؛ لذلك لم أوسّع قاعدة التنبيه. فحص القاعدة الاستكشافية استخدم النصوص المحفوظة فقط، ولم يشغّل OCR أو الترجمة.

في مراجعة القصاصات غُطّيت بصرياً **53 منطقة مكتشفة** عبر الصفحات الخمس (10، 12، 12، 10، 9). كانت المناطق المرقمة فوق نص إنجليزي ظاهر، مع حالات تجزئة/صعوبة مذكورة أعلاه. هذا فحص ارتباط إيجابي للمناطق التي اكتشفها النظام، وليس قياساً للاستدعاء (Recall): لا يثبت أن كل نص في كل صفحة له منطقة مقابلة، لذلك لا أستنتج نسبة تغطية من المقام غير المتوفر.

## تدقيق تغطية بصري محدود

قورنت مناطق OCR الـ53 ومخططات الاكتشاف بالصفحات الأصلية الخمس. لم أجد في هذه المراجعة السريعة منطقة نص إنجليزية واضحة بالكامل لا يقابلها أي اكتشاف؛ لكن هذه النتيجة **ملاحظة بصرية وليست Recall مقاساً**، لأن الصفحة لا تملك ترميزاً مستقلاً شاملاً لكل صندوق نص، ولأن بعض النصوص المحجوبة/المزخرفة لا يمكن اعتبارها مرجعاً مؤكداً. لم أضع مقاماً أو نسبة تغطية مصطنعة.

| الصفحة | مناطق OCR الحالية | ملاحظة التغطية |
|---|---:|---|
| 2 | 10 | الفقاعات والتعليقات الواضحة لها مناطق مقابلة؛ مؤثر كوري خارج نطاق OCR الإنجليزي الحالي. |
| 3 | 12 | الحوار والإشعارات لها مناطق؛ الشعار المزخرف مجزأ/رديء القراءة، وإشعار ثانٍ محجوب جزئياً. |
| 5 | 12 | الفقاعات ولوحة المهمة مغطاة بمناطق؛ النص المائل/المزخرف أصعب، مع استعادة مؤكدة لإحدى الفقاعات المقصوصة. |
| 10 | 10 | مناطق النص المقروء ظاهرة؛ تقسيم بعض العبارة بين منطقة قصيرة وأخرى يحتاج تحسيناً مستقلاً عن OCR الحرفي. |
| 14 | 9 | الفقاعات ظاهرة، ومنطقة `PURCHASE` محددة لكن الحروف محجوبة بإصبع وصندوقها فضفاض. |

تؤكد بيانات خط الأساس أن RT-DETR الشامل قدّم **153 مقترح منطقة** على الصفحات الخمس لكنه لم يضف أي سطر OCR مقبول (`comic_detector_ocr_line_count=0` لكل صفحة)، مقابل نحو **106.4 ثانية** في مجموع زمنه. لذلك أبقيته خارج المسار العادي، واستُخدم الاسترداد الموجّه فقط في الحافتين المشكوك بهما في الصفحتين 5 و14. هذه مقارنة مفيدة للأداء، لكنها لا تثبت أن OCR لم يفت نصوصاً خارج تلك المقترحات.

الخلاصة الهندسية: يوجد دليل جيد على صحة مناطق كثيرة وعلى تحسن موضعي، لكن Recall العام لا يزال غير قابل للحساب بهذه العينة. يتطلب قياسه الحقيقي مجموعة صناديق مرجعية مستقلة مرسومة يدوياً فوق الصفحات، تشمل النصوص المتروكة والتقسيمات المقصودة، ثم مطابقة IoU مع الكشف الحالي.

## استعادة فقّاعة مقسومة في الصفحة 10 — تحديث

كشفت مراجعة الصورة الأصلية أن فقّاعة واحدة تضم خمسة أسطر: `IF YOU / FOLLOW HIM, YOU / WILL BECOME FAR MORE / POWERFUL THAN YOU / ARE NOW,`. خط المعالجة السابق استخرج `IF YOU` من قناع الفقاعة، واستخرج الأسطر الأخيرة خارج القناع، لكنه أسقط `FOLLOW HIM, YOU`. إعادة OCR لقصاصة محلية تشمل الفقّاعة والامتداد المجاور أعادت الأسطر الخمسة بثقة متوسطة **0.977**.

أُضيف استرداد احتياطي موضعي: لا يعمل إلا عند وجود سطر OCR ملاصق لحافة قناع فقاعة ونص خارجي مجاور ومتراصف معه. لا يقبل المرشح إلا إذا حافظ ترتيباً على كل كلمات OCR السابقة وأضاف كلمتين على الأقل بمتوسط ثقة ≥0.82؛ وإلا تبقى النتائج كما هي. على صفحة 10 المحفوظة أعاد الفقاعة كاملة إلى منطقة واحدة دون RT-DETR. زمن الصفحة الكامل **45.60 ثانية**، منها **1.46 ثانية** لإعادة القصاصة، ونتجت **9 مناطق** بدل 10. هذا قياس OCR لصفحة واحدة لا زمن ترجمة، ولا يبرر ادعاء تسريع عام.

ملف التشغيل الخام: `translator_engine/data/benchmark_runs/ocr_page10_adjacent_bubble_recovery_20261111.json`. وملف مقارنة العينة ذات الـ37 حالة: `translator_engine/data/benchmark_runs/ocr_visual_gold_adjacent_recovery_20261111.json`. رُفع `PAGE_PIPELINE_VERSION` إلى **29** لمنع عرض نتائج مخزنة من خط المعالجة السابق على أنها حديثة.
## Numeric status counters: recognition without translation

The saved page 5 contains a quest panel with three `[0/100]` values and one `[0/10KM]` value. RapidOCR already recognized these at 0.9977–1.000 confidence, but the English-letter filter discarded them. A narrowly matched bracket-counter rule now records them in `nontranslatable_annotations` only when each is horizontally aligned with a nearby recognized English list label. Unrelated digits and non-bracketed ratios remain excluded.

These annotations are deliberately kept out of `regions`, `translated_lines`, and the Index request sequence. They therefore add no translation tokens, prompts, or model calls and do not change translation wording, context, or output quality. The same existing OCR detection pass supplies the annotations; this adds filtering/association work only.

Validation on the saved original `page_05/original_english.webp`:

- Four of four counters captured with the exact text and coordinates; confidence 99.77–100%.
- The OCR run produced 11 translatable regions; the four counters are separate metadata and were excluded from translation. The prior saved run had 12 regions, including a mixed region that combined `[0/10KM]` with the neighboring footnote. This change removes that counter from Index input; no translation model was run for validation.
- OCR elapsed: 43.4458 seconds. The preceding saved run for this same page was 44.7041 seconds; this is a single-run comparison and does not establish an OCR speedup.
- Focused OCR regression suite: 36 passed in 15.09 seconds.
- The measured page output is saved as `translator_engine/data/benchmark_runs/ocr_page05_counter_annotations_20261111.json`.

The feature records counters for review and preserves the original image unchanged; it does not paint over or translate numeric status values. Existing cached pages are not invalidated, so they will acquire this metadata when OCR is next run for them.

## Additional visual coverage review: pages 2 and 10

The saved detection overlays were compared against the two remaining originals. The legible dialogue in the visible strips has a corresponding region; punctuation-only bubbles and Korean decorative sound marks remain intentionally outside the English dialogue path. A boundary case remains on page 10: the first large black speech balloon is clipped by the top edge of the source image. OCR captured its full dialogue, but the bubble segmenter returned 8 masks and did not produce a mask for that balloon, so its dialogue is typed as `external_text`. A padding experiment on the page's top crop did not recover a safe bubble mask. No forced bubble classification was added, because there is no reliable mask to guide painting or typesetting. This is a segmentation/rendering classification limitation, not a missed OCR transcript.

Visual review of page 14 also confirms the partly obscured `PURCHASE` lettering remains an uncertain OCR region; the image itself hides some glyphs behind a hand. It should remain reviewable rather than be auto-corrected from context. These known cases reinforce that the five-page sample is useful for targeted regressions but cannot establish complete detection recall.

For page 10, a fresh overlay was rendered directly from the actual recovered OCR result (not the older overlay embedded in the review ZIP) and checked against the source. It shows the full five-line continuation as one external-text region, with the following two speech balloons correctly bounded. The six current-result strips are under `translator_engine/data/benchmark_runs/ocr_page10_current_coverage/`.

A threshold diagnostic on the clipped top balloon produced no accepted mask at 0.20; lowering to 0.15 and 0.10 returned two low-confidence overlapping proposals (0.152 and 0.118). Their masks spill beyond the balloon into the adjacent illustration, as shown in `translator_engine/data/benchmark_runs/ocr_page10_edge_bubble_threshold_debug.png`. The global bubble threshold was not lowered and neither proposal was added to the render path. This avoids trading detection recall for image damage; a future fix needs edge-aware segmentation that can prove a clean interior mask before classifying the region as a balloon.

## Expanded crop recovery for dialogue clipped by a bubble mask

The saved page 14 OCR result exposed a separate failure mode: the segmenter returned a left boundary at x=195 for a dark balloon whose visible lettering began well to the left. The original OCR lines were `VE ONLY`, `ETED THE`, and `SH-UPS.` despite confidence values of 0.89–0.996. A narrow expanded crop over the same balloon restored all three lines as `YOu'VE ONLY`, `COMPLETED THE`, and `PUSH-UPS` (case-insensitive WER 0 against the visually checked line).

Added a fallback that runs only when a speech-bubble OCR line touches its detected mask edge and the bubble has suspicious OCR fragments. It retries an expanded crop for that bubble, assigns candidate text to existing lines by geometric overlap, and accepts a candidate only with high OCR confidence, preserved recognized words, and a lexical/fragment improvement. It then synchronizes the saved OCR boxes and polygons. Healthy lines and all other pages remain on the ordinary path; uncertain candidates retain the prior OCR text. The normal page-level detector is skipped for a bubble after all suspect edge lines have been recovered.

Validation on the archived `page_14/original_english.webp`:

- Targeted fallback recovered 3/3 affected lines and emitted `YOu'VE ONLY COMPLETED THE PUSH-UPS`; the leading capitalization is imperfect but text content matches the visual reference.
- One integrated full-page OCR run: 43.372 seconds total, 21 lines, 9 regions; the local edge-crop retry took 3.8031 seconds. A prior saved run was 44.9645 seconds, but this is not a controlled speed comparison, so no OCR speedup is claimed.
- Focused edge-recovery tests: 4 passed. Full local OCR regression file: 37 passed in 14.93 seconds.
- Index was not invoked and no translation model, prompt, or generation configuration changed. No page pipeline version bump was made, so cached completed pages will not be automatically invalidated or retranslated; the fallback applies when OCR is next performed.

The detailed measurement is in `translator_engine/data/benchmark_runs/ocr_page14_expanded_edge_recovery_20261111.json`. This single case proves the fallback on one dark, partially mis-segmented balloon only; broader visual ground-truth pages are still required before estimating general precision/recall or changing segmentation thresholds.

## Five-page saved-sample check of expanded edge recovery

Ran a crop-only comparison over the saved OCR and original images for pages 2, 3, 5, 10, and 14. No full-page OCR or translation was rerun. The fallback did not activate on pages 2, 3, or 10. On page 5 it recovered `SEE.., THE QUEST` from `E.., THE QUEST` and `CONDITIONS ARE...` from `NDITIONS ARE...`; a visual review of the saved crop confirms the source reads “SEE... THE QUEST CONDITIONS ARE...”. The OCR still has punctuation noise (`..,`) and the separate `LET'S` line was already correct. On page 14 it recovered all three clipped lines, yielding the visually confirmed sentence “YOU'VE ONLY COMPLETED THE PUSH-UPS.”

Crop-only retry time was 2.842 seconds for page 5 and 3.837 seconds for page 14 in this run. These measurements include the helper's local OCR work and are not a controlled page-latency comparison. The other three pages had no crop retry. Results and per-line coordinates/confidences are saved in `translator_engine/data/benchmark_runs/ocr_saved_edge_recovery_review_20261111.json`; the reproducible evaluator is `tools/benchmark_saved_edge_recovery.py`.

This adds two positive cases but still does not measure false negatives: the evaluator starts from existing OCR detections and therefore cannot prove that all text regions on each page were found. Do not interpret five pages or the two recovered examples as a corpus-wide accuracy estimate.

## Additional visual review of page 14 strips — 2026-11-11

Compared all five saved original/detection strips for page 14. Each clearly legible English dialogue block shown in the strips has a corresponding text region: the top `A HUNDREED...!` bubble, the connected dark-bubble dialogue, `B-BOTH OF HIS ARMS ARE BROKEN!`, `KIEEEEK!`, both lines in the large dark bubble, `SH-SHOP WINDOW.`, and the large `PURCHASE` lettering. The recovered `YOU'VE ONLY COMPLETED THE PUSH-UPS` text is also visually supported by a tight crop of the original and has case-insensitive WER 0 against that crop's transcription.

Two limits remain distinct. First, the `PURCHASE` glyphs are partly occluded by a hand; the detector's broad region covers them, but the saved OCR text remains uncertain and must not be context-corrected automatically. Second, the overlay strips were created before the latest edge-crop retry, so their region outline does not prove the geometry of the recovered text region after retry. Korean decorative sound effects in the last strip are visible but intentionally excluded from the current English OCR/translation scope. This is a visual check of saved strips, not a full detection-recall ground-truth annotation.

### Reproducible region-level transcription audit

Added a manually checked reference for the nine OCR regions in `translator_engine/data/benchmark_runs/ocr_page14_targeted_rescue_20261111.json`. Seven clearly legible regions were scored; all seven matched exactly (exact-region rate 7/7, WER 0, CER 0). The stylized `HUNDREED` lettering and partly occluded `PURCHASE` are explicitly excluded from the aggregate rather than guessed. The evaluator and inputs are `tools/evaluate_manual_ocr_audit.py` and `translator_engine/data/benchmark_runs/ocr_manual_audit_page14_20261111.json`; computed output is `translator_engine/data/benchmark_runs/ocr_manual_audit_page14_result_20261111.json`.

This is a selective transcription score on one page, not a 100% page accuracy claim: the evaluator is conditioned on already detected, manually matched regions and cannot count missed text. The audit adds no OCR inference, Index call, or production-path cost. Translation model, prompt, generation settings, and caching were unchanged.

### Page 5 manually checked baseline

Added a second manual reference for 11 legible regions on the saved page 5 run from before expanded edge-crop recovery. Baseline was 9/11 exact region matches, WER 4.60%, and CER 1.61%. The two errors are the quest notice (`O QUEST NOTICE ... COMBATANT' HAS ARRIVED`) and the dialogue `LET'S E.., THE QUEST NDITIONS ARE...`. Scoring the recorded crop-recovery text for that dialogue gives the same exact-region count (the punctuation still differs), while WER falls to 3.45% and CER to 0.80%. Thus the crop recovery restores the missing words and halves character error on this selected region set, but does not fully fix punctuation or the quest notice. One decorative-icon heading was excluded from scoring.

Audit input: `translator_engine/data/benchmark_runs/ocr_manual_audit_page05_20261111.json`; baseline and crop-recovery results: `translator_engine/data/benchmark_runs/ocr_manual_audit_page05_result_20261111.json` and `translator_engine/data/benchmark_runs/ocr_manual_audit_page05_recovered_result_20261111.json`. This remains a single-page selective transcription audit; it does not measure missed detections, and the score must not be generalized to other pages.

### Page 10 adjacent-line omission and recovery

The current visual review found that the original page clearly contains `IF YOU FOLLOW HIM, YOU WILL BECOME FAR MORE POWERFUL THAN YOU ARE NOW,`, while the earlier OCR output split the region and omitted `FOLLOW HIM, YOU`. The adjacent-bubble crop fallback had already been exercised on this saved page: it joined the continuation and recovered the full sentence as one region. The measured fallback work was 1.4567 seconds; the page run took 45.5369 seconds total and did not invoke the page-level RT-DETR detector. This is a single-run observation, not a controlled end-to-end speedup measurement.

Re-scoring the existing 37-region visual reference against the immediate pre-recovery saved OCR and the adjacent-recovery output gives WER 4.29% (12 word errors / 280 reference words) before, versus 0% after; one region improved, and the recovered sentence matches the manual reference. The earlier initial saved-run comparison was WER 6.79% (19/280), showing cumulative changes but not isolating the adjacent-recovery effect. The reproducible incremental result is `translator_engine/data/benchmark_runs/ocr_visual_gold_adjacent_incremental_20261111.json`; the earlier-run comparison is `ocr_visual_gold_adjacent_recovery_revalidated_20261111.json`.

This comparison covers manually selected clear OCR regions, not detection recall or all text on the page. The `PURCHASE` occlusion and ambiguous stylized lettering remain outside these exact-text scores. The page-10 comparison reuses saved OCR artifacts; Index and translation code/settings were not run or changed. The visual-gold evaluator's scope label now reports its actual number of scored cases rather than a stale fixed count.

Validation for this audit-tool/report update: `tools/evaluate_ocr_visual_gold.py` and `tools/evaluate_manual_ocr_audit.py` compile successfully; saved visual-gold comparison reruns with 37 cases, 12 baseline word errors and zero current word errors; focused OCR/timing tests pass (**40 passed in 19.97 seconds**); `git diff --check` passes. The `rtk pytest` shim itself could not spawn pytest under Windows, so the same tests were run through `.venv\\Scripts\\python.exe -m pytest`.

### Expanded paired visual-gold comparison — 39 regions

The reference now includes the two previously omitted, fully legible regions recovered by crop OCR: page 5 region 5 (`LET'S SEE... THE QUEST CONDITIONS ARE...`) and page 14 region 3 (`YOU'VE ONLY COMPLETED THE PUSH-UPS`). A saved-output mapping applies the recorded crop-only OCR strings to those exact region indexes; it does not use the references as OCR output. The page 10 sentence recovered by adjacent-crop OCR remains included as the third improved region.

Paired result using the immediate pre-recovery saved OCR (`ocr_saved_samples_20261111_110052.json`) against the saved current page-10 adjacent-crop report plus the two recorded edge-crop outputs: 17 word errors / 292 reference words (WER 5.82%) before versus 0 / 292 (WER 0%) after, across 39 manually matched clear regions. Three regions improved. The detailed rows are in `translator_engine/data/benchmark_runs/ocr_visual_gold_combined_recovery_20261111.json`; annotations are in `translator_engine/data/ocr_visual_gold_cases.json`; the explicit crop mapping is `translator_engine/data/benchmark_runs/ocr_edge_recovery_region_overrides_20261111.json`.

This is a selected transcription benchmark, not full-page recall: it includes only readable, manually matched text and excludes ambiguous/occluded cases. It must not be presented as general OCR accuracy or proof that no text is missed. The comparison reuses saved outputs and crop measurements; no full OCR pass or Index translation was run. Focused validation after adding the recovery override test: **45 passed in 15.37 seconds** across visual-gold matching, local OCR, and OCR timing tests. Python compilation and `git diff --check` passed.

### Conservative external-text cleanup — saved page 3 and page 5

The expanded 41-region benchmark exposed two external quest notices previously omitted from the selected references. On page 3, OCR joined `OTHERWORLDLYINHABITANTS` and read the circled notice icon as a leading `O`; a 2× crop-only diagnostic produced the correct word boundary, while 1× did not. On page 5, the same icon was read as `O`, but upscaling the whole notice did not improve recognition and introduced an accented-E candidate, so no general upscaling retry was adopted.

Added a low-cost post-recognition cleanup for `external_text` only: remove the leading OCR `O` when it occupies the circular icon immediately before `NOTICE` or `QUEST NOTICE`, and split an all-caps concatenated token only when the English dictionary allows exactly one split and region confidence is at least 0.70. The recognizer output is preserved in `ocr_normalization_original_text` with a change reason. Bubble dialogue is unchanged, no second OCR inference is made, and no translation call or setting is touched.

Re-scoring the 41 saved visual-gold regions with the production cleanup applied to the two scored external notice outputs lowers current WER from 1.23% (4/325 words) to 0% (0/325); the original baseline remains 6.46% (21/325). A read-only diagnostic over every external region in the same saved five-page OCR report found exactly three regions changed: the two scored notices and a second, partly occluded page-3 notice. In that partly occluded notice, the compound was split but the damaged ending `STHAS ENDED` correctly remained unresolved. This is a deterministic transformation of saved OCR text, not a new full-page OCR run. The new measurement is `translator_engine/data/benchmark_runs/ocr_visual_gold_normalized_recovery_20261111.json`; the testable production helper is in `ocr_engine/local_rapidocr.py`.

Regression validation after this production change: **48 passed in 16.02 seconds** across local OCR, visual-gold scoring, and OCR timing tests; the changed Python modules compile, and `git diff --check` is clean. No Index model or translation code/settings were loaded or modified.

### Integration and post-OCR cost check

The app stores `ocr_result["regions"]` as `dialogue_regions`, initializes the matching `translated_lines` from those regions, and submits each `region["text"]` to the translation adapter. Therefore the external-text normalization is on the real OCR-to-translation path, while the original OCR wording remains available in region metadata when a normalization is applied. The same normalization helper exits immediately for speech-bubble regions.

A bounded CPU-only microbenchmark over the nine `external_text` regions already present in `ocr_pages_03_05_review_hints_20261111.json` measured **0.547 ms per nine regions** (300 iterations; 0.061 ms per region after warm-up). This is normalization-only and must not be confused with OCR inference or end-to-end page time. It confirms this cleanup does not account for the long page-processing time. Fresh regression run: **48 passed in 15.76 seconds**; `git diff --check` passed. No OCR model, Index server, or translation was run.

Reproduction audit: running the scorer with its default files omits the saved crop overrides and uses an earlier baseline, so it does not reproduce the 21-to-0 result. The exact comparison was rerun with baseline `ocr_saved_samples_20261111_110052.json`, current and anchor `ocr_saved_samples_20261111_adjacent_recovery.json` / `ocr_saved_samples_20261111_110052.json`, the two saved edge-crop overrides, and external normalization. It reproduced **21/325 word errors (WER 6.46%) to 0/325 (WER 0%)** across the same 41 manually selected, clear regions. The reproducible output is `translator_engine/data/benchmark_runs/ocr_visual_gold_reverified_20261111.json`. This remains a selected transcription score and does not establish full-page recall.

### Saved detection-overlay spot audit

I visually compared seven original/detection strips already present in `ocr_coverage_review_temp`: page 2 segments 1–2, page 3 segment 1, page 5 segments 1–2, page 10 segment 1, and page 14 segment 1. In the fully visible portions, **16 legible English dialogue/caption/notice regions** had corresponding numbered detection boxes. No clear missed English text region was visible in those portions. Partial regions at strip edges were not counted, and non-English sound effects were excluded because this iteration targets English-to-Arabic text. These strips are convenience crops from the five-page review archive, not independent annotations; the spot audit is qualitative and must not be reported as a page-wide recall percentage. Stylized logos and content clipped by strip edges remain outside this finding.

I then completed all five saved strips for page 2 and checked the original against the numbered detection overlay and saved OCR result. All **10 visually eligible English dialogue/narration regions** were present with matching numbered detection regions and OCR text: page-level manual coverage **10/10**. The ellipsis-only bubble and Korean sound effects were recorded as exclusions. The machine-readable audit is `translator_engine/data/ocr_page_coverage_audit_20261111.json`. This is a single-page coverage result and is not generalized to the other pages or to new works.

The same full-strip review is now complete for page 3. All **8 fully legible dialogue/narration references** had matching detection regions (8/8); after the saved notice cleanup and crop recovery, those references also had 0 word errors. The second, foreground-obscured notice is detected as region 12 but remains unscored because no reliable complete transcription is available. The logo and chapter label are explicitly outside this dialogue/narration coverage denominator. Details are in `translator_engine/data/ocr_page_coverage_audit_page03_20261111.json`. Page 3 is a second independent page check, not a general recall estimate.

Page 5 is now visually reviewed across all four saved strips. Its nine clear manually transcribed dialogue/notice regions are all detected and score 0 word errors after the saved recoveries and normalization. The lower quest card initially looked like its right-side `[0/100]` values were missed; a full local page OCR run showed they are emitted as **four separate nontranslatable counter annotations** (`[0/100]` ×3 and `[0/10KM]`) while the task labels and note are grouped into external text regions. No code change was needed, avoiding an unnecessary extra OCR pass in ordinary processing. That one full-page run took **44.1909 seconds**: bubble detection/OCR 21.3957 s, external OCR 18.1517 s, and edge recovery 1.8718 s. Artifacts: `translator_engine/data/ocr_page_coverage_audit_page05_20261111.json` and `translator_engine/data/benchmark_runs/ocr_page05_full_pipeline_recheck_20261111.json`. This is one page-level integration observation, not a chapter or speed claim.

Page 10 was also reviewed across all five strips. The original overlay has 10 numbered OCR boxes for 9 physical speech bubbles because one bubble was split into adjacent OCR fragments. The saved adjacent-crop recovery combines them into a complete region; the omitted phrase `FOLLOW HIM, YOU` is restored, and all 8 scored clear references have 0 word errors. All 9 eligible bubbles have a region after recovery. The per-page mapping and the remaining base-OCR limitation are recorded in `translator_engine/data/ocr_page_coverage_audit_page10_20261111.json`.

Page 14 was reviewed independently across all five saved strips. Nine English text regions were visually identified and each has a detection region. Six clear references in the existing visual-gold set score 0 word errors after crop recovery (the fragmented `YOU'VE ONLY COMPLETED THE PUSH-UPS` line was repaired); this is not a 100% page OCR accuracy claim because two detected regions were not in the gold score and one is visually ambiguous. The external lettering `PURCHASE` is partly covered by the sword/hand and OCR returned `PURO-ASE` at confidence 0.889. This is a concrete high-confidence OCR failure; automatic spelling correction would be guesswork, so the audit records it as uncertain rather than silently changing it. Full mapping: `translator_engine/data/ocr_page_coverage_audit_page14_20261111.json`.

To make this failure visible in normal review, external OCR now raises a **review-only hint** for an all-caps hyphenated token when both long components are absent from the local English dictionary. It preserves the OCR output verbatim and does not rewrite or retransmit it. A replay over the 15 saved external-text regions flagged three: two already caught by the multiple-unknown-word rule, plus `PURO-ASE`; known compounds and short stutters such as `SH-SHOP` are not flagged by the new rule. The existing UI already opens/highlights OCR review hints, so no translation path or layout behavior changed. Focused tests passed 50 cases at this point, including the obscured token and non-regression cases. This heuristic is a review cue, not a correction or an accuracy increase by itself; names and unusual compounds may be flagged for human review.

A second low-cost review cue was explored after examining a separate damaged notice in page 3. Its OCR region contains `STHAS` and omits surrounding words; the panel is partly obscured, so it remains unscored in the exact-transcription benchmark. The first broad rule for one unknown word in long external text raised alerts from 3 to 4, but a visual audit showed it also alerted on the clearly printed proper name `AMMUT` because `KM` in a progress counter counted as another unknown token. That broad rule was replaced with narrower checks: structured progress counters are excluded only from review-word counting (the shared OCR recovery scorer is unchanged), and an unknown fused token is flagged only if it has one dictionary split whose second part is common English. A final replay over the same 15 external regions flags three: `LEUEING RAGNAROK AROAO`, `STHAS`, and `PURO-ASE`; it no longer flags the clearly printed `AMMUT`. These remain review-only hints, not text correction. Focused OCR and API integration tests now pass **56 cases**. This small sample cannot establish the false-positive rate for names or unusual prose in other works.

I tested one bounded retry experiment on the saved, partly occluded page 3 notice using both the normal RapidOCR recognizer and the already-installed PP-OCRv6 Medium recognizer, each on the original crop, a 2x upscale, CLAHE 2x, and a deskewed crop. None recovered the occluded words; deskewing made the small recognizer join `STHAS ENDED` into `STHASENDED`. With model initialization excluded, PP-OCRv6 Medium took **7.69–8.78 seconds per crop** in the deskew follow-up and showed no accuracy gain, so it was not wired into normal page processing. The exact inputs, outputs, and observed crop timings are saved in `translator_engine/data/benchmark_runs/ocr_page03_medium_recognizer_retry_probe_20261111.json`. This is one difficult region, not a general timing benchmark.

A second focused preprocessing probe used the saved page-14 sign crop. The primary crop read `PURASE`; Otsu thresholding emitted `VM` and `PUR(ASE`, CLAHE returned `PURO-ASE`, and 2x enlargement emitted an extra `V1` while retaining `PURASE`. None recovered the hand-occluded middle letters. The crop-only timings ranged from **0.88 to 2.80 seconds** on this setup. No preprocessing change was adopted because none produced a correct result, and the crop was kept review-only. The machine-readable comparison is `translator_engine/data/benchmark_runs/ocr_page14_preprocessing_probe_20261111.json`.

These visual audits show that crop recovery fixes some clipped bubble text, but the current detector/OCR still has no reliable general measure for missed text and can be overconfident when artwork occludes letters. The observed 0-WER gold scores apply only to manually selected clear regions on five saved pages. They should not be presented as model-wide detection recall or general accuracy. The page 5 integration recheck took about 44.2 seconds on the current local setup, so the per-page processing cost remains a material limitation; no translation speed or model configuration was changed in this OCR audit.

### Consolidated five-page coverage snapshot

I reconciled the per-page audits with the visual-gold scorer rather than collapsing their different denominators into a single accuracy percentage:

| Page | Manual coverage denominator | Detected | Clear gold transcription score |
|---|---:|---:|---:|
| 02 | 10 fully legible regions | 10/10 | 10/10 at 0 word errors |
| 03 | 8 fully legible regions | 8/8; one obscured notice also detected | 8/8 at 0 word errors |
| 05 | 9 clear text regions; 4 counters separately | 9/9; counters 4/4 | 9/9 at 0 word errors |
| 10 | 9 physical speech bubbles after recovery | 9/9 | 8/8 at 0 word errors; ninth bubble manually checked outside gold |
| 14 | 9 identifiable English text regions | 9/9 | 6/6 at 0 word errors; three are ambiguous or unscored |

Across selected clear references this is 41 regions / 325 words: saved baseline 21 word errors (WER 6.46%), recovered/normalized current output 0 errors (WER 0%). The page-level denominators include manual coverage checks beyond those 41 gold cases, so these are two distinct measurements. This summary is machine-readable at `translator_engine/data/benchmark_runs/ocr_five_page_coverage_summary_20261111.json`. These pages come from one work and are not a representative sample of vertical comics generally.


### Targeted RT-DETR fallback for clipped bubbles — 2026-11-11

The saved page-14 run showed that full-page RT-DETR was invoked as a fallback for clipped bubble OCR, even though only a small set of bubbles remained suspicious. A controlled, warmed same-process comparison on the saved 12,945 × 800 page measured 6.575 s for all 23 full-page proposals versus 1.207 s for two padded local ROIs (six and three proposals respectively), an 81.65% reduction in detector time for those two targeted regions. This is a one-page paired timing probe, not a chapter latency claim; the ROI calls were deliberately restricted to two known cases.

The production fallback now crops a padded local region around each suspicious bubble, shifts detections back into page coordinates, and retains the existing confidence, overlap, and OCR quality checks. On the saved page-14 OCR inputs, the production recovery function detected the bubble locally and replaced the damaged region `VE ONLY ETED THE SH-UPS` with `YOU'VE ONLY COMPLETED THE PUSH-UPS`; its isolated run took 3.627 s total, with 1.113 s in RT-DETR. No full-page OCR, Index call, or translation ran. Exact run artifacts: `translator_engine/data/benchmark_runs/ocr_page14_detector_warm_roi_compare_20261111_211623.json` and `translator_engine/data/benchmark_runs/ocr_page14_roi_recovery_run_20261111_211531.json`.

Regression validation passed **59 focused tests** across local OCR, comic detector, visual-gold scoring, OCR timing, and the OCR review-hint API. This change only narrows the optional fallback detector input; it does not alter the primary OCR path or translation model/settings. Coverage is still limited to saved pages from one work, so general recall and style robustness remain unverified.


### Apostrophe-safe OCR lexical scoring — 2026-11-11

The edge-recovery and review heuristics use dictionary membership to identify suspicious OCR fragments. A replay over the 37 saved speech-bubble regions found that unconditional apostrophe removal treated valid contractions and possessives as unknown tokens. The scorer now checks the written token, its apostrophe-free spelling, and (for possessive `'s`) its base form. The OCR text itself is never changed.

On the same saved sample, bubble regions containing at least one unknown token decreased from 11 to 8; the three resolved cases were `HAVEN'T` / `TODAY'S` dialogue on page 3 and `YOU'LL` on page 5. Remaining unknowns include names, interjections, sound effects, stylized text, and genuine OCR fragments, so dictionary membership is not treated as a correction signal by itself. Replay: `translator_engine/data/benchmark_runs/ocr_apostrophe_word_count_replay_20261111_212415.json`. The focused OCR/detector/gold/timing/API suite passes **60 tests** after this adjustment.


### Multi-ROI coordinate regression check — 2026-11-11

Added a synthetic tall-page regression case with two suspicious bubbles separated by more than 1,000 pixels. It verifies that the fallback makes two local detector calls, keeps them below half the page height, does not cross-associate their proposals, and restores OCR line coordinates to the correct page locations within 1.5 pixels. The focused OCR/detector/visual-gold/timing/API suite now passes **61 tests**. This validates coordinate handling and isolation, not the visual accuracy of arbitrary model proposals.


### Review-hint replay after lexical scoring update — 2026-11-11

Replayed the current external-text review logic over the same 15 saved OCR regions after changing apostrophe handling. It still flags three regions with the same `(page, reason)` signature; the page-3 notice is displayed in its normalized form while `ocr_normalization_original_text` retains the raw OCR output. The saved `[0/10KM] ... AMMUT` proper-name example remains unflagged. Replay artifact: `translator_engine/data/benchmark_runs/ocr_review_hint_replay_post_lexical_fix_20261111.json`.


### Avoiding detector retries caused by contractions — 2026-11-11

Added a regression test for a valid edge-of-bubble sentence containing contractions and a possessive. It proves apostrophe-aware word scoring recognizes the text as healthy and skips the RT-DETR fallback entirely. The focused suite now passes **62 tests**. This guards the latency side of the OCR change; it is not an end-to-end page timing result.


### Conservative near-word review cue for speech bubbles — 2026-11-11

Speech-bubble regions now receive a review-only cue when a high-confidence OCR token (confidence ≥0.90) has exactly one one-edit dictionary candidate above the common-word frequency threshold. The candidate never replaces the OCR text. A replay of the 37 saved bubble regions flagged exactly one token, `HUNDREED` → possible `hundred` (source confidence 0.9971); names, the low-frequency `TAEGYU` → `taegu` candidate, and the `SH-SHOP` stutter were not flagged. Mean helper replay time was 0.00366 s for all 37 regions (about 99 μs/region) over 25 replays; this excludes image/OCR inference. Results: `translator_engine/data/benchmark_runs/ocr_bubble_nearword_review_replay_20261111.json`.

The focused OCR/detector/visual-gold/timing/API suite passes **64 tests**, including tests for the unique-common-candidate alert and conservative non-alert cases. This helps expose a possible recognition error to the reader; it does not improve the recognizer's transcription and does not establish false-positive rates beyond these five pages from one work.


### Current full-page OCR integration check — page 14 — 2026-11-11

Ran the current local OCR pipeline once on the saved original `page_14/original_english.webp`; no translation or network call occurred. It returned 21 OCR lines and 9 grouped regions, matching the nine visually identified English text regions on this page. The edge-crop recovery path corrected the clipped bubble to `YOU'VE ONLY COMPLETED THE PUSH-UPS`; the bubble near-word reviewer flagged the ambiguous `HUNDREED` lettering, while the existing external-text reviewer flagged the partly occluded `PURO-ASE` without correcting it.

A fresh score against the seven clear references in the page-14 manual audit was **7/7 exact regions, WER 0, CER 0**. The two ambiguous/occluded areas remain excluded from the score. The current full-page run also scored **0/35 word errors across the six page-14 visual-gold cases**, compared with 15/35 in the older saved baseline; this is a selected-region comparison and the accumulated changes are not isolated to this run. Artifacts: `translator_engine/data/benchmark_runs/ocr_page14_current_full_pipeline_20261111_213748.json`, `ocr_page14_current_manual_audit_20261111.json`, and `ocr_page14_current_full_pipeline_gold_score_20261111.json`.

The one-page run took **47.55 s** total (25.67 s bubble detection/OCR, 17.44 s external OCR, 4.28 s edge recovery); the comic detector was not invoked. This confirms current end-to-end OCR integration on one saved page, but not cross-work detection recall or a stable performance average. Full-page processing time remains a material limitation.
\n\n### Current full-page OCR replay across five saved pages — 2026-11-11\n\nReplayed the local OCR pipeline once on each saved original page (02, 03, 05, 10, 14). Translation/Index and network services were not invoked. The current run totaled **224.41 s** (44.88 s/page average). The earlier saved runs averaged 51.47 s/page; observed elapsed time was 12.8% lower, but runs were not controlled and page 14 changed from a full-page RT-DETR fallback to targeted ROI recovery, so this is descriptive and not causal.\n\n| Page | Current seconds | Prior saved seconds | Observed reduction | Regions | OCR lines | ROI recoveries | Review flags |\n|---|---:|---:|---:|---:|---:|---:|---:|\n| page_02 | 42.43 | 49.90 | 15.0% | 10 | 30 | 0 | 0 |\n| page_03 | 49.71 | 53.90 | 7.8% | 12 | 36 | 0 | 2 |\n| page_05 | 41.38 | 44.70 | 7.4% | 11 | 41 | 2 | 0 |\n| page_10 | 43.34 | 45.54 | 4.8% | 9 | 39 | 0 | 0 |\n| page_14 | 47.55 | 63.31 | 24.9% | 9 | 21 | 3 | 2 |\n\nAcross the selected visual-gold set, current OCR has **0/325 word errors (WER 0)** versus 21/325 previously (WER 6.46%). This is limited to 41 clear regions across five pages from one work; it does not establish complete detection recall or general accuracy. Page 14 still has two review-only ambiguous/occluded regions, and page 03 has two review flags. Current end-to-end OCR remains around 41–50 seconds per page in this environment. This is OCR timing, not translation timing.\n\nMachine-readable details: 	ranslator_engine/data/benchmark_runs/ocr_current_five_page_integration_summary_20261111.json. No source-code changes were made in this replay; the focused suite had already passed 64 tests before it.\n\n\n### Standalone chapter-marker false-positive filter — 2026-11-11\n\nThe saved page-03 integration replay had emitted CH 033 as a speech-bubble region even though it is page furniture. Added a narrow standalone pattern for CH/CHAPTER plus a number. It is applied to OCR line text before bubble regions are assembled; normal sentences such as Chapter 3 was strange. remain eligible.\n\nValidation: **60 local OCR tests passed**. A fresh full OCR run on the saved page 03 removed the false CH 033 region (page regions 12 → 11) while preserving all eight manually audited dialogue/narration regions and their OCR text. The run took 51.02 s versus the prior saved 49.71 s; these are uncontrolled runs, so no speed improvement is claimed. No translation was invoked. Replay details: 	ranslator_engine/data/benchmark_runs/ocr_page03_chapter_marker_regression_20261111.json.\n\n\n### Stylized title recognizer probe — 2026-11-11\n\nA page-03 visual audit confirmed the external OCR crop contains the highly ornate SOLO LEVELING RAGNAROK logo. On the exact same 1.7x-upscaled title crop, local PP-OCRv6 Small took 2.40 s and returned EUINS / TRANARORK / ARAO; the installed PP-OCRv6 Medium recognizer took 6.74 s and returned PEQUELING / JRAAR / RINe. Neither read the logo correctly, and Medium was about 2.8x slower. Therefore Medium is not a useful fallback for this case; keep the text flagged for manual review rather than inventing a correction. Existing proper credit text ORIGINAL WORK DAUL was identical from both recognizers. Diagnostic output: 	ranslator_engine/data/benchmark_runs/ocr_page03_title_recognizer_comparison_20261111.json. This is a single-crop probe, not a model-wide comparison.\n\n\n### Supplemental second-work screenshot coverage check — 2026-11-11\n\nA saved user screenshot of a different vertical webtoon was available locally (Screenshot (12).png). The browser-rendered comic crop is only **230 × 774 px**, so this is a supplemental low-resolution coverage check, not a raw-page quality or speed benchmark. Current OCR produced all three visually legible text regions (two connected narration panels and one speech balloon) in their correct top-to-bottom order. Manual comparison: **3/3 regions exact, 29/29 words, WER 0** after case/punctuation normalization. The OCR call took 8.84 s including lazy model initialization, so it is not comparable to warm full-resolution page timings. No translation ran. Evidence: 	ranslator_engine/data/benchmark_runs/ocr_user_screenshot12_vertical_sample_20261111.json. This adds a second-work check, but one screenshot cannot establish general cross-work recall.\n\n\n### Reducing proper-name false positives in OCR review — 2026-11-11\n\nThe second-work screenshot test found a false review warning on the correctly recognized character name KATYA: the fused-word heuristic treated the rare suffix YA as a plausible common word. Raised the minimum frequency of the proposed second token from 1e-5 to 1e-4. This changes review signaling only; OCR source text and translation are not rewritten.\n\nValidation: **72 focused OCR/detector/gold/timing/recovery tests passed**. A replay over 16 saved external-text regions reduced prior review flags from 4 to 3: the KATYA false alarm disappeared, while the genuine-looking STHAS join, damaged title lettering, and occluded PURO-ASE remain flagged. The replay used saved OCR only, with no new OCR inference or translation. Evidence: 	ranslator_engine/data/benchmark_runs/ocr_review_hint_replay_common_suffix_20261111.json; screenshot14 before/after cue replay: 	ranslator_engine/data/benchmark_runs/ocr_screenshot14_review_hint_replay_20261111.json.\n\n\n### Overlapping slanted external lettering review — 2026-11-11\n\nA browser-rendered screenshot of a translated vertical page contained handwritten English text crossing the edge of an Arabic speech balloon. Previously the OCR retained only WHEN HER HEAD GETS BLOWN OFF. and silently omitted the beginning. The raw external OCR produced overlapping boxes at nearly identical −16-degree angles; geometric grouping separated these boxes and duplicate suppression hid the suspicious prefix.\n\nThe grouping now keeps heavily overlapping rows together only when their slant is nearly parallel (within 6 degrees). Deskew retries can be accepted on a measured gain in known-word coverage when confidence remains adequate. Low-confidence unknown words in external groups now set an OCR review flag, which opens the existing editable source-text review in the UI. Regression validation: **75 focused OCR/detector/gold/timing/recovery tests passed**.\n\nOn the same saved screenshot, the result is now one region, ELG EHEN WHEN HER HEAD GETS BLOWN OFF., flagged low_confidence_external_ocr_line with two unknown words. Against the 12-word manual reference, WER stayed at **6/12 (0.50)**; this is not an OCR accuracy gain. It is a visibility/review improvement: the uncertain overlapping prefix is no longer dropped without warning. The screenshot is low resolution and already has Arabic bubble text, so it is not a production-page accuracy benchmark. No translation ran and translation speed was not measured. Artifacts: 	ranslator_engine/data/benchmark_runs/ocr_user_screenshot16_before_after_20261111.json, ocr_user_screenshot16_after_slanted_group_fix_20261111.json, and ocr_user_screenshot16_external_debug_20261111.json.\n
### Selective split recovery for merged vertical-comic balloons — 2026-11-11

Visual inspection of the saved original pages found two large masks that joined adjacent dialogue balloons: one at the top of page 03 and one around the quest dialogue on page 05. The local comic detector separated both into distinct, high-confidence balloon proposals (page 03: 0.918 and 0.885; page 05: 0.884 and 0.910). The existing `_split_bubbles_with_detector` logic had regression tests but was not connected to the page extraction path.

Connected it behind a cheap mask-shape gate: only a large mask (at least `max(15,000 px, 22% of page_width²)`) with solidity at most 0.94 and at least two deep convexity defects is sent to the auxiliary detector, on a padded local crop. Child masks keep the original parent ID for renderer compatibility, and the segmentation mask is still required to support both children. In the five saved pages, only the two visually confirmed merged masks triggered; the other three pages triggered none. Measured extra detector time was 1.21 s and 0.77 s for the two candidate crops.

Bubble-only extraction on the original saved pages confirmed one split on each page. Page 03 retained its two separate dialogue regions and returned seven speech regions total (the chapter marker is excluded); page 05 returned all eight expected speech regions. OCR text for the inspected dialogue remained intact. End-to-end bubble-only extraction took 30.30 s on page 03 and 23.90 s on page 05; these include model loading and per-bubble OCR and are not controlled before/after timing comparisons. Index and translation were not run.

The separate five-page bbox comparison artifact records only agreement with saved OCR region boxes. These boxes were generated by the existing application and are **not independent instance-mask ground truth**, so its overlap ratios must not be interpreted as bubble detector precision or recall. It is retained as a diagnostic locator only: `translator_engine/data/benchmark_runs/bubble_detection_saved_five_page_20261111.json`. The targeted split integration evidence is in `translator_engine/data/benchmark_runs/bubble_targeted_split_integration_20261111.json`; visual overlays are `page_03_bubble_boxes_audit_20261111.png` and `page_05_bubble_boxes_audit_20261111.png` in the same directory.

Validation: **81 focused OCR, bubble-detector, visual-gold, timing, and recovery tests passed** after adding regression cases for selective probing, smooth single balloons, mask-backed splitting, and parent-ID preservation. The separate renderer suite then passed **6 tests in 1.75 s**, confirming existing bubble rendering and outside-pixel protections still pass. A broader suite including library/security/source-provider tests stopped producing output at 52% for several minutes and was interrupted; those additional suites remain unverified. Translation speed is unchanged by this code path; only preprocessing time can increase on a qualifying complex balloon.

### Polygon-aware overlap for slanted OCR near balloons — 2026-11-11

The low-resolution, browser-rendered Screenshot (16) had a handwritten caption laid diagonally beside and across the segmented balloon edge. Axis-aligned bounds reported 25% overlap for `SHE DOESN'T`, although the mapped rotated OCR polygon overlapped the safe balloon mask by only 0.94%. Checking the actual polygon in rotated-caption refinement allowed the confident prefix to remain in the OCR region while preserving fail-closed behavior for invalid polygons. The same saved screenshot improved from 6/12 word errors (WER 0.50) before the earlier grouping fix to 4/12 (WER 0.33) with polygon-aware overlap. The new output still contains the uncertain token `EGAEHVEN`, so it is review-flagged; this does not establish general OCR accuracy. Evidence: `translator_engine/data/benchmark_runs/ocr_user_screenshot16_polygon_overlap_comparison_20261111.json` and `ocr_user_screenshot16_polygon_overlap_20261111.json`.

### Cross-chapter sample status

The user supplied ManhuaPlus chapter 920 (`Demon Magic Emperor`). Both the web fetch tool and the project's allowlisted provider request were unable to connect; the provider request failed with Windows `WinError 10013` while connecting to `manhuaplus.org:443`. No page images were obtained and that chapter has **not** been tested. An original page image or chapter archive attached locally will let the cross-chapter check proceed without relying on this blocked network path.

The Brave window inventory showed a window title mentioning this chapter, but the Computer Use layer could not verify its current URL and stopped before returning a page snapshot. No browser interaction or image extraction followed. This does not count as an inspected sample.


### Bubble confidence threshold check — 2026-11-11

Swept the existing segmentation threshold at 0.15, 0.20, and 0.25 on pages 03 and 05 (six inference runs, 74.62 s total). The detector returned exactly 8 masks on page 03 and 7 on page 05 at all three thresholds; the saved-region-box overlap proxy also did not improve. Therefore the default threshold was left unchanged. Details are in `translator_engine/data/benchmark_runs/bubble_confidence_sweep_pages03_05_20261111.json`; its region-box comparison is diagnostic only, not independent bubble ground truth.

### Regression review on saved pages 02, 10, and 14 — 2026-11-11

Bubble-only extraction on the remaining saved originals took 75.61 s total (page 02: 25.95 s; page 10: 23.42 s; page 14: 26.24 s). Pages 02 and 10 each returned 8 regions matching the saved reference count. Page 14 returned 6 regions while the saved comparison reference has 5. Visual inspection of the original page-14 crop confirms the current OCR keeps the adjacent balloons `IS THAT IT, YOUNG MONARCH?` and `YOU'VE ONLY COMPLETED THE PUSH-UPS` as separate dialogue regions. The old comparison reference had merged/corrupted them as `IS THAT !T, YOUNG MONARCH? VE ONLY ETED THE SH-UPS.`, producing a misleading 8/24 word-error count. That count is a reference-alignment defect, not evidence of an 8-word OCR regression. The current output's `YOu'VE` casing is a minor OCR normalization issue; the visible source reads `YOU'VE`. A manually inspected source-backed note is stored in `bubble_page14_visual_review_20261111.json`; the original metric artifact is preserved unchanged at `bubble_only_regression_pages02_10_14_20261111.json`.

This does not establish independent precision/recall for the full five-page sample: most saved reference text/regions came from earlier application output. Page 14 is now a manually checked local example, not a statistically meaningful accuracy score. Translation and Index were not run.

The page-14 review also exposed isolated lowercase OCR characters inside a long, visibly all-uppercase balloon (`YOu'VE`). Added a conservative final-pass casing normalization for speech-bubble regions only: it requires at least 18 Latin letters, at least one lowercase character, and a 92% uppercase ratio. It changes case only, records the raw OCR string and normalization tag, and runs after recovery stages so recovered balloon text is covered. The real page-14 extraction changed this phrase to `YOU'VE ONLY COMPLETED THE PUSH-UPS`; page segmentation and words were unchanged. Runtime was 37.06 s for this single invocation, compared with 38.54 s in the prior run, but that difference is normal run-to-run noise and not a speedup claim. No translation was run. Evidence: `bubble_page14_caps_normalization_runtime_20261111.json`.

Validation after this change: **83 focused OCR, bubble-detector, visual-gold, timing, and recovery tests passed in 20.19 s**, plus **6 renderer tests passed in 0.44 s**. The specific casing regression tests passed separately (2/2). The existing broader library/security/provider suites were not rerun.

### OCR correction suggestions surfaced in region review — 2026-11-12

The OCR engine already emits a conservative candidate when a high-confidence bubble word is one edit away from exactly one common English word, but the translation API discarded that candidate and the review UI showed only a generic warning. The API now preserves `ocr_suspicious_near_words`; the UI displays up to three candidates as explicitly unconfirmed suggestions. Suggestions are not applied automatically, and text is rendered with `textContent` to avoid treating OCR content as markup. The page-14 review demonstrates why: the image really spells `HUNDREED`, so the proposed `HUNDRED` is a false positive and must remain a user-checkable suggestion.

Validation: the focused API integration test passed (**1 passed**) and `node --check static/app.js` passed. The change does not call the translation engine or add OCR inference work, so it should not change translation latency or model output. Browser rendering of the suggestion label has not been visually checked yet.

After moving speech-bubble review-hint generation to the same final pass as casing normalization (so recovered regions also receive hints), the full `test_local_ocr.py` file passed (**71 passed in 21.44 s**) and the focused translate-API propagation test passed (**1 passed in 2.16 s**). JavaScript syntax validation and `git diff --check` also passed. A combined run of the OCR and all source-provider tests stopped producing output at 77% and was interrupted after about 80 seconds; that run is not counted as passing. The relevant focused suites passed independently.

### Independent page-level visual gold — saved page 02, 2026-11-12

To avoid scoring OCR against regions produced by the same pipeline, page 02 was reviewed from the original page and its bubble crops were manually transcribed. Nine speech balloons are visible and all nine corresponding masks were returned by the segmenter (observed count-based precision/recall 9/9 on this page). Eight word-bearing dialogue balloons produced OCR regions; all **47 reference words matched** when punctuation is ignored (WER 0.00 for this one page). A ninth, pause-only balloon with six dots was not emitted as a text region. A crop-only OCR probe misread the dots as `Q` at 0.850 confidence, so this is recorded separately rather than hidden inside lexical WER. The bubble itself is segmented and remains untouched in the rendered source when no text region is emitted, but punctuation-only dialogue is not represented in the OCR region list.

These independent checks are not enough to estimate chapter-level performance or guarantee all balloon styles. Manual page-02 transcripts and region metrics are in `translator_engine/data/benchmark_runs/page02_independent_manual_gold_20261112.json`; its 9-mask detector output is in `page02_bubble_masks_20261112.json`; inspection sheet: `page02_manual_gold_review_sheet_20261112.png`. The full-page review and pause-balloon crop are saved beside them. Page 10 has a separate prior manual coverage audit reporting 9/9 visible speech balloons covered after crop recovery, but most of its text references derive from an existing visual-gold set. Page 03 still needs independent full-page transcription before reporting a broader aggregate OCR score.

### Independent visual gold and near-threshold merged-balloon recovery — page 14, 2026-11-12

Full-page review counted six speech balloons; the raw bubble segmenter returned five masks because it merged two adjoining black balloons under one low-confidence parent (0.2828). The merged safe-mask area was 137,488 px, just below the 140,800 px normal probe floor at an 800 px page width. The existing shape gate accepted the candidate when called directly, and local ComicTextDetector returned two spatially distinct proposals. A narrow exception now permits a concave mask with confidence below 0.35 to enter the same shape-validation path when it is within 5% of the area floor. The detector split only this parent: **5 raw masks → 6 refined balloon instances**, with about **1.11 s** spent on the selective crop detector pass.

The first full extraction after splitting exposed OCR regressions (`I5` and `1T` for `IS` and `IT`). A conservative correction was added for speech-bubble OCR only: it substitutes digit/letter lookalikes only at confidence ≥0.90, only when the configured digit mapping yields exactly one dictionary word above a common-word frequency floor, and records the original string and correction list. The rerun returned `IS THAT IT, YOUNG MONARCH?` at confidence 0.9201, kept the printed `HUNDREED` unchanged despite its dictionary suggestion, and emitted six speech regions. Their **21 words** (counting contractions and hyphenated words as one, ignoring punctuation) matched the source (WER 0.00). One final period after `PUSH-UPS` was omitted, so punctuation accuracy is not perfect. The output and timing are `page14_near_floor_split_validation_20261112.json`; independent transcript and metrics are `page14_independent_manual_gold_20261112.json`; raw pre-split detections are in `page14_bubble_masks_20261112.json`.

Post-change validation: **73 local OCR tests passed in 19.77 s**, **6 renderer tests passed in 1.66 s**, and the focused OCR-suggestion API test passed (**1 passed in 2.02 s**). No translation model ran. The 25.13 s full page runtime is a single run and not a before/after speed claim; the split adds about 1.11 s on this candidate page. The six child-mask crop was visually reviewed: both masks stay within the adjoining balloons and overlap only at their narrow shared seam. See `page14_split_masks_visual_20261112.png`.

### Independent visual gold — saved page 05, 2026-11-12

Manual review of the full original page found eight speech balloons. The raw segmenter returned seven masks, merging the connected quest-condition bubbles under one low-confidence parent (0.2501). The standard concavity/area probe split it into two proposals; current extraction still emits eight regions, including the two separate bubble texts with the same parent ID for renderer compatibility. The child-mask overlay was visually checked: both masks stay inside the joined balloons and overlap only near their connector (`page05_split_masks_visual_20261112.png`). All **59 manually transcribed tokens** (including numbers; contractions and hyphenated compounds each count as one; punctuation is ignored) match (WER 0.00). The one confirmed error is punctuation in `LET'S SEE...`: OCR produced `LET'S SEE..,`. The page-level sheet and connected-balloon crop are in `page05_manual_review_sheet_20261112.png` and `page05_connected_bubbles_crop_20261112.png`; independent gold, raw masks, and current OCR output are in `page05_independent_manual_gold_20261112.json`, `page05_bubble_masks_20261112.json`, and `page05_current_visual_gold_validation_20261112.json`.

Across the three independently transcribed pages (02, 05, and 14), the sample contains **23 visible speech balloons**. After selective splitting, **23/23 balloon instances** were represented and all **22 word-bearing dialogue regions** were emitted. Their combined **127 word tokens** (including numbers; contractions and hyphenated compounds treated as single tokens; punctuation ignored) scored WER 0.00. Three punctuation mismatches were observed (one comma omission, one final-period omission, and `..,` instead of `...`), and the pause-only balloon on page 02 was not emitted as a text region. This three-page sample is still too small to generalize to chapters or other art styles.


### Independent visual-gold extension — saved page 03, 2026-11-12

The original vertical page and seven speech-bubble crops were reviewed independently of the OCR text. All **7 visually reviewed word-bearing balloons** have a corresponding final OCR region; the two touching balloons at the top share a detector parent before selective splitting and are emitted as two regions afterward. The chapter badge and rectangular system notice are excluded from this speech-only score. All **41 source word tokens** match after ignoring punctuation (WER 0.00), and no punctuation mismatch was confirmed on these seven regions. The transcript, comparison, and review-image paths are in `translator_engine/data/benchmark_runs/page03_independent_manual_gold_20261112.json`.

Across the four independently transcribed pages (02, 03, 05, and 14), the reviewed set now contains **30 visible speech balloons**, **29 word-bearing dialogue regions**, and **168 source word tokens**. The observed word-level WER remains 0.00; the three previously confirmed punctuation mismatches remain, and page 02's pause-only balloon still has no emitted text region. This is a selected sample from one work, not a population estimate. It does not verify external text, sound effects, every mask boundary, or cross-work detection. The user supplied a third-work chapter URL (`manhuaplus.org/manga/demon-magic-emperor/chapter-920`), but the web fetch returned `Cache miss` in this environment and no local page images for that chapter were available during this audit. A real cross-work assessment still needs the user-provided page images or a reachable local import.

Initial source-access check: the chapter page and eight image links were visible in the in-app browser, and its first CDN image opened at **1200×13056 px**. Direct `requests` from project Python initially failed with `WinError 10013`, so no chapter image had yet been saved or tested at that point. A later bounded, user-authorized network request downloaded one image; its OCR results are recorded in the cross-work section below.


### Cross-work vertical-strip sample — Magic Emperor chapter 920, 2026-11-12

After verifying the CDN image size, one 1.14 MB page was downloaded from the user-provided chapter URL to `translator_engine/data/benchmark_runs/magic_emperor_920_sample/chapter920_page0.webp` (1200×13056; SHA-256 is recorded in the accompanying JSON). No additional chapter pages were downloaded. The speech-only local OCR run emitted **9/9 visually counted dialogue balloons** in top-to-bottom order, including an irregular spiky balloon and two adjoining balloons. Independent reading of the original and region crops matched **129/129 word tokens** (WER 0.00, punctuation ignored); no bubble-boundary IoU was claimed. The full-page overview, region crops, OCR JSON, manual transcript, and timings are stored under that sample directory.

The full run took **60.41 s** on this page: 26.58 s for bubble detection/OCR and 33.35 s for external-text OCR. This is one uncontrolled CPU run, not translation time or a throughput estimate. No translation or Index request ran. The long cover and page credits reveal the remaining weak area: six external-text regions were emitted; the two lower promotional/footer regions were exact, while four cover/credit regions contained clear errors or duplicated/split lines. Region 4 (`DD: cu TS: LOU`) was not flagged by the previous review heuristic. Existing PP-OCRv6 medium weights were probed on these six saved crops without downloading a model; they improved the visible credit transcription in one crop but left other stylized cover lettering garbled, so they were not selected as a general fallback. Probe readings are saved in `chapter920_page0_external_medium_probe.json`; no inference-time measurement was captured for that exploratory crop probe.

Implemented a review-only guard in `ocr_engine/local_rapidocr.py`: external regions containing multiple uncommon 2–3 letter uppercase tokens now receive `ambiguous_short_uppercase_tokens`. It does not modify OCR text or invoke another model. The saved page-920 output replay now flags `DD: cu TS: LOU` for review and retains its original text. Common short English words, ordinary dialogue, and hyphenated words are excluded. Regression tests cover both the credit-like OCR error and false-warning cases. Validation: **75 tests passed** in `translator_engine/tests/test_local_ocr.py` (19.50 s); the initial test run exposed a false alert for `YOU'VE ONLY COMPLETED THE PUSH-UPS`, which was fixed by excluding common words and hyphen-bound fragments before the passing rerun. The new rule only surfaces suspicious OCR for review; it does not repair the source transcription. Replay: `chapter920_page0_external_review_hint_replay.json`; independent audit: `chapter920_page0_independent_review.json`.

This is the first independently reviewed sample from a second work and supports strong observed speech-region coverage on one vertical strip. It does **not** establish chapter-wide or multi-work recall, and external stylized lettering remains a material limitation.


### Cross-work sample extension — Magic Emperor chapter 920, pages 1–2 — 2026-11-12

Downloaded two more source strips from the user-provided chapter, preserving the first strip. Speech-only OCR was run once per page; translation and Index were not called. Page 1 took **27.73 s** and emitted 8 regions/17 OCR lines; page 2 took **21.40 s** and emitted 5 regions/12 lines. Independent visual review found **8/8** and **5/5** word-bearing balloons, respectively, with no extra speech regions in these selected strips.

The visual-gold score is not perfect: among **118 manually transcribed words**, OCR made **4 word errors (WER 3.39%, punctuation ignored)**. On page 1 the stylized burst `UP TO THIS!` became `UPHO TO ISI` (two token errors). On page 2 the dark red-text bubble `MMM, NOT A BAD IDEA. BUT WILL IT WORK?` was emitted as `MMM, NOT A BAD IDEA. BUT WORK`, omitting `WILL IT`. The other 11 selected dialogue bubbles matched at word level. These failures show why region count alone overstates OCR quality; the images demonstrate successful bubble localization but do not establish pixel-level mask IoU.

Artifacts include `chapter920_page1_ocr.json`, `chapter920_page2_ocr.json`, numbered overlay tiles and bubble-crop sheets, plus `chapter920_pages1_2_independent_review.json`. Source hashes are stored with each OCR result. Across the first three strips from this second work, detection coverage is **22/22 visually counted word-bearing bubbles**, while only the first strip currently has a separate manually word-scored gold record. The two-page extension's measured WER is limited to those 13 reviewed bubbles and must not be extrapolated to the entire chapter or other series.

Practical next step: improve OCR recognition confidence for stylized burst text and short missing tokens, and keep the current review-only warning strategy. Do not change the deployed recognizer based on these two examples alone; first collect a broader independent gold set from more chapter pages and art styles.


### Selective medium-recognizer retry threshold — 2026-11-12

The saved page-1 burst bubble had confidence **0.7627** and OCR text `UPHO TO ISI`. A bounded recognizer probe on that exact saved bubble crop returned `UP TO THIS!` from PP-OCRv6 Medium at confidence **0.8961**; the primary recognizer returned `UPHO TO IS` at 0.8493 on the crop. Medium required **2.89 s** after model initialization in this probe. A separate invocation through the existing retry path corrected the crop and preserved its original region coordinates; elapsed time was **4.17 s** including fresh process/model initialization. No page was reprocessed and no translation was run.

Expanded the existing selective retry gate in `ocr_engine/local_rapidocr.py` from confidence below 0.55 to below 0.80, while retaining the `!` glyph-confusion trigger and lexical-improvement requirement before text replacement. The high-confidence `THE MAP OF THE MANOR!` bubble remains above the gate. In the 22 saved speech regions across pages 0–2 of this work, only one uncorrected region was below 0.80; page 2's second line had already been medium-rescued by the previous logic. This sample indicates a narrow added retry cost here, but is not a chapter-wide estimate. The omitted `WILL IT` on page 2 remains unresolved because the medium recognizer also returned `BUT WORK` on that bubble crop.

Regression evidence: `translator_engine/tests/test_local_ocr.py` **77 passed**; visual-gold and OCR-timing checks **7 passed**; `git diff --check` passed. The correction is verified on a single saved crop only. Current full-page JSON and the manual-gold baseline remain unchanged and preserve the original OCR mistake for comparison. Verification artifact: `translator_engine/data/benchmark_runs/magic_emperor_920_sample/chapter920_page1_threshold_retry_verification.json`.


### Targeted sparse-line crop recovery — 2026-11-12

The page-2 crop probe showed that rereading the entire bubble did not restore `WILL IT`, but applying the installed small recognizer to only the OCR line crop with 28 px horizontal and 20 px vertical context returned the exact `BUT WILL IT WORK?` at confidence **0.9926**. The production adapter now applies this retry only when a speech-bubble line has confidence below 0.90, spans at least 250 px, and has unusually low letter density. It preserves the original box/polygon and accepts a candidate only when dictionary-supported word count improves without reducing the valid-word ratio. Other lines and the full page do not receive an additional pass.

The production recovery path was run on the saved page-2 bubble crop: `BUT  WORK` → `BUT WILL IT WORK?`, retaining the saved geometry; fresh-process elapsed time was **4.48 s**, including model initialization. This complements the medium-recognizer retry on page 1 (`UPHO TO ISI` → `UP TO THIS!`). Applying both verified crop recoveries to the 13 manually reviewed bubbles on pages 1–2 changes the selected-subset score from **4/118 word errors (WER 3.39%)** to **0/118 (WER 0.00%)**. This is not a full-page rerun, does not validate other pages, and the cold timings are not warm per-crop latency estimates.

Added deterministic tests for sparse-line gating, geometry preservation, and unchanged confident/compact lines. The current OCR suite passes **79 tests**; visual-gold/timing checks pass **7 tests**. See `chapter920_page2_missed_line_scale_probe.json`, `chapter920_page2_sparse_line_retry_verification_v2.json`, and `chapter920_threshold_retry_effect.json`. The original saved page OCR and gold transcript remain untouched so the before/after evidence is preserved.


### Current full-page cross-work validation after selective retries — 2026-11-12

This update supersedes the intermediate notes above that said page 2's `WILL IT` omission remained unresolved or that page 1 was verified only as a crop. Reprocessed the three saved speech-only strips from chapter 920 after the final retry logic. No external lettering, translation, or Index ran. The output region counts remained 9, 8, and 5, matching the independent visual count of word-bearing bubbles: **22/22**. Comparing the full-page results with independent transcripts, all **247 words** match (WER **0.00%**, punctuation excluded), compared with 4 errors/247 words (WER **1.62%**) in the saved baseline. This validates both OCR corrections inside the full-page path. Outputs and score: `chapter920_page0_retry_full_ocr.json`, `chapter920_page1_retry_full_ocr_v5.json`, `chapter920_page2_retry_full_ocr_v5.json`, and `chapter920_current_cross_work_visual_gold_score.json`.

The before/after geometry comparison also matched exactly for all **22/22 regions** (`left`, `top`, `width`, `height`, bubble box, OCR boxes, and polygons), confirming that these text retries changed transcription without shifting the existing region layout.

Validation after the final safety gate and retry ordering: **87 OCR, visual-gold, and timing tests passed**; `git diff --check` passed.

Observed speech-only OCR runtimes were 26.79 s, 30.22 s, and 24.16 s for pages 0, 1, and 2 (81.17 s total). The corresponding earlier runs took 28.35 s, 27.73 s, and 21.40 s (77.47 s total). The observed total was **3.70 s / 4.8% higher**, but these were uncontrolled runs; retry cost and normal CPU/runtime variability cannot be separated from this sample. This is OCR time, not translation time. The extra retries are gated to uncertain regions; chapter-wide retry frequency and latency remain unmeasured.

The earlier statement that only page 0 had manual word-level gold is also superseded: pages 1 and 2 now have their own manual transcripts and independent review records. The combined three-strip score is in `chapter920_current_cross_work_visual_gold_score.json`; external lettering and bubble-boundary IoU are excluded.


### Regression check on four earlier vertical pages — 2026-11-12

Ran the final speech-only pipeline once on the four independently reviewed pages (02, 03, 05, 14) already stored in `QUALITY_REVIEW_SAMPLES.zip`. The engine emitted all **29/29 word-bearing dialogue regions** represented by the manual gold. Across **170 reference words**, the score remained **0/170 word errors (WER 0.00%)**, matching the saved prior OCR word score; no new recognition errors appeared. Retry count was zero on all four pages, so the selective fallback added no inference call to these examples. The existing pause-only bubble on page 02 remains excluded from word-region recall, as before.

Speech-only processing times were 29.87 s (page 02), 33.24 s (03), 27.96 s (05), and 24.69 s (14). These differ from earlier full OCR timings that also included external lettering and therefore should not be compared as a speedup/regression. This check only validates dialogue OCR and preserves the scope boundary around external text.

Current outputs and per-region comparison are in `translator_engine/data/benchmark_runs/retry_regression_20261112/`; aggregate: `saved_pages_retry_regression_score.json`. The OCR/visual-gold/timing suite had **87 passing tests** immediately before this replay; no production source changed during the regression run.


### External-text review false-positive correction — 2026-11-12

Replaying the saved chapter-920 OCR audit exposed a false warning on the exact caption `IF YOU ENJOYED THIS SERIES AND WANT A FASTER RELEASE PLEASE READ ON MANHUAPLUS.COM`: the short-token heuristic treated `AND` and `.COM` as suspicious. Added `AND` to the common-word exclusions and prevented period-delimited domain suffixes from being counted as standalone role codes. The recognizer text remains unchanged.

Replay on the six saved external OCR regions now flags the four previously audited garbled cover/credit regions for review and leaves both exact promotional/footer regions unflagged. In particular, `DD: cu TS: LOU` still receives `ambiguous_short_uppercase_tokens`, while the exact `.COM` captions no longer do. This is a review-cue calibration on one saved page, not an OCR recognition improvement or a general false-positive-rate estimate. Replay artifact: `chapter920_page0_external_review_hint_replay_v2.json`.

Regression validation after this change: **88 tests passed** across local OCR, visual-gold, and OCR timing suites. No OCR model inference or translation ran during the heuristic replay.


### Targeted vertical-strip seam recovery — page 10, 2026-11-12

Independent visual review of saved `QUALITY_REVIEW_SAMPLES.zip` page 10 found a low-confidence mask cut at a strip boundary. Its partial box was `(167, 2888, 631, 3038)` while the actual black dialogue balloon extended to about y=3313; speech-only OCR consequently returned only `IF YOU`. A naive increase of strip overlap from 160 to 500 px was rejected: on this image it increased segmenter calls from **9 to 12**, took **15.34 s to 16.29 s**, and returned **8 to 7 masks**, losing the partial proposal rather than repairing it.

The vertical bubble segmenter now selectively retries only masks below confidence 0.60 that touch an internal strip edge. It runs one taller full-width context crop (1800–2400 px high), accepts only a proposal spatially overlapping the partial mask, and relies on mask-aware deduplication to keep the fuller contour. Confident masks and page-edge proposals do not trigger this retry. A saved-crop probe found that 700 px of vertical padding was insufficient; 900 px recovered the complete mask `(149, 2887, 660, 3314)` at confidence 0.3584.

Full speech-only OCR on the saved page now returns **8/8** manually transcribed dialogue regions in reading order, **83/83 words** (WER **0.00%**), including the complete `IF YOU FOLLOW HIM, YOU WILL BECOME FAR MORE POWERFUL THAN YOU ARE NOW` line. The bubble segmenter returned eight corresponding dialogue masks. The top-clipped black balloon remains outside the speech-mask track and is read by the existing external-text route; this classification limitation is recorded in `translator_engine/data/benchmark_runs/page10_manual_extension_20261112/seam_retry_score.json`. Full OCR took **30.21 s** once; this is a single run, not a before/after speed claim. No translation model ran.

Added unit coverage for recovery of an uncertain internal seam mask and for skipping the retry on confident masks. The focused OCR, visual-gold, timing, and seam-recovery suite passes **90 tests in 32.72 s**. Full-page visual review beyond this saved page and pixel-level mask IoU remain outstanding; do not generalize the 8/8 result to all long-strip art styles.

### Saved long-strip geometry regression — 2026-11-12

Ran the updated bubble segmenter (without OCR or translation) against the three saved 1200 px wide chapter-920 strips. Counts were **9, 9, and 6 masks**, compared with manual counts of **9, 8, and 5 word-bearing speech balloons**. Visual review confirmed the two surplus masks on pages 1–2 are false positives over character/clothing artwork. Segmenter times were **9.50 s, 9.50 s, and 7.38 s**. These are detector-only runs and not comparable with full OCR timings.

Confidence inspection shows a single global threshold is not a safe fix: the page-1 false mask scored **0.3256**, the page-2 false mask scored **0.8691**, and a real short dialogue bubble on page 2 scored only **0.6156**. Raising the cutoff enough to remove the high-confidence false positive would discard that real bubble. No threshold was changed; OCR already suppresses masks that yield no readable dialogue, so these extras do not become translated regions.

Re-ran current speech-only OCR on both pages containing those visually confirmed false masks. It produced exactly **8/8** and **5/5** manually transcribed dialogue regions; the page-1 false mask and page-2 false mask each generated **zero OCR regions**. All **118/118 reference words** matched (WER **0.00%**). Runtimes were **29.294 s** and **23.643 s** in one run each; there is no controlled before/after timing claim. No external-text route, Index, or translation ran. Results and source hashes: `translator_engine/data/benchmark_runs/magic_emperor_920_sample/chapter920_current_false_mask_ocr_validation.json`.

### Four-page long-strip regression after seam repair — 2026-11-12

Re-ran the speech-only full OCR path on the four saved independent visual-gold pages (02, 03, 05, 14) after adding seam-context recovery. Current output has **29/29** manually annotated dialogue regions and **170/170 correct words (WER 0.00%)**, preserving the prior OCR score on these pages. Per-page times were **26.967 s**, **31.986 s**, **26.302 s**, and **22.852 s**. These are one run each and should not be used as a speed comparison against earlier uncontrolled runs. No external lettering, Index, or translation ran. Outputs and aggregate: `translator_engine/data/benchmark_runs/seam_retry_regression_20261112/`; aggregate score: `seam_retry_regression_score.json`.

### Runtime bubble-mask diagnostics — 2026-11-12

The OCR extraction result now includes `speech_bubble_mask_count`, `speech_bubble_masks_with_primary_ocr`, and `speech_bubble_masks_without_primary_ocr`. Flask now carries these counters through its timing summary, and the web result notes show them for both local chapter-page processing and remote-page processing. These additive counters expose mask-to-text drop-off without altering OCR decisions; empty masks are not automatically called false positives because pause-only bubbles are valid. On saved page 02, the pipeline reported **9 masks, 8 with readable primary OCR, and 1 without**; independent review identifies the unmatched instance as the pause-only ellipsis bubble. The focused OCR/detection suite passes **91 tests in 31.35 s**, including a test where one of two masks yields no text. JavaScript syntax check and `git diff --check` passed. Capture: `translator_engine/data/benchmark_runs/seam_retry_regression_20261112/page02_mask_diagnostics.json`.

Compared the resulting mask boxes with the saved OCR dialogue `bubble_bbox` values: all **22/22** regions had exact box overlap (intersection over smaller box area = 1.0). This confirms the mask pass remains geometrically consistent with the independently saved dialogue OCR output on these three pages; it does not prove there are no unrecognized bubbles, and the OCR outputs predate the seam-retry code. The surplus masks are shown in `translator_engine/data/benchmark_runs/magic_emperor_920_sample/chapter920_seam_extra_masks_review.jpg`. Other artifacts: `chapter920_seam_detector_regression.json` and `chapter920_seam_geometry_comparison.json` in the same directory.

### Full OCR integration after seam recovery — page 10, 2026-11-12

Ran the full local OCR path once with external-text extraction enabled on the same saved page. It returned **9 regions total: 8 speech bubbles and 1 external-text region**. The seam-clipped `IF YOU FOLLOW HIM...` paragraph is now a speech-bubble region instead of being recovered as external lettering; the top black panel remains one external-text region. No duplicate region appeared, and the remaining eight dialogue regions still match the manual references (83 words, WER 0.00%). The external panel text also matches its transcript. Runtime was **52.97 s** in this single run; the earlier full-path run took 55.46 s, but the runs are uncontrolled and this difference is not evidence of a speedup. No translation model ran. Full result: `translator_engine/data/benchmark_runs/page10_manual_extension_20261112/full_ocr_seam_retry_validation.json`.

The independent nine-region full-page score confirms **104/104 words correct (WER 0.00%)**, including the top external panel and all eight dialogue balloons; the formerly clipped phrase is classified as `speech_bubble`. Score: `translator_engine/data/benchmark_runs/page10_manual_extension_20261112/full_seam_visual_gold_score_v2.json`.

Measured the seam-retry cost separately on page 10 in one warm-session run: the original strip path took **13.542 s** and returned eight masks, while targeted seam repair took **14.689 s** and returned eight masks including the recovered full balloon. The additional **1.147 s (8.47% of segmentation time)** is about 2.2% of the single full OCR runtime above; CPU variation and single-run scope apply. It adds detection time, not model translation time. Measurement artifact: `translator_engine/data/benchmark_runs/page10_manual_extension_20261112/seam_retry_cost_comparison.json`.

### Dark filled oval fallback integration — 2026-11-12

Resumed validation of the experimental dark oval fallback using the saved eight-page set (three chapter-920 strips plus five visual-gold pages). The geometry scan found 13 strong dark-ellipse candidates; visual review classified 12 as dark dialogue balloons and one as a full-width black scene separator. The separator is rejected by the fallback's maximum width/height ratio. Production detection merged the candidates into existing masks on the other seven pages; the three chapter-920 strip mask counts stayed 9, 9, and 6. Page 10 increased from eight speech masks to nine by adding the previously missed top black balloon. The shape scan cost ranged from 0.10 to 0.23 s per full image; cold end-to-end bubble detection ranged from 7.00 to 13.35 s in this single pass and is not a before/after speed comparison.

Ran full local OCR once on saved page 10 with external text enabled after adding the dark mask. It returned nine regions, all classified as speech bubbles, with no duplicate external region. Against the independent page-10 transcript, all 104/104 words matched (WER 0.00%). Runtime was 50.173 s in one run; the prior 52.97 s run is uncontrolled, so no OCR speedup is claimed. No Index or translation ran. This validates OCR integration on one page, not final inpainting/typesetting visual quality or general recall.

The focused regression suite passes **92 tests** (`test_speech_bubbles.py`, local OCR, visual gold, and OCR timing). Artifacts: `page10_manual_extension_20261112/dark_oval_production_detection_benchmark_20261112.json`, `full_ocr_darkoval_fallback_20261112.json`, and `full_darkoval_visual_gold_score_20261112.json`.
