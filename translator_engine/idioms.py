"""Small reviewed English dialogue phrases with natural Arabic equivalents.

This exact phrase memory complements the NMT model; it is intentionally narrow
and never adds story facts that are absent from the English text.
"""

from __future__ import annotations

import re


def normalize_dialogue(text: str) -> str:
    text = text.casefold().replace("’", "'")
    text = re.sub(r"[^\w']+", " ", text, flags=re.UNICODE)
    return " ".join(text.split()).strip(" '")


REVIEWED_PHRASES = {
    # Reviewed dialogue from Getsuyoubi no Tawawa, chapter 117, Part 26.
    "that new girl from division two's got moves": "الفتاة الجديدة في القسم الثاني بارعة!",
    "they were just like that": "كانوا هكذا فحسب!",
    "any sexual harassment and you're dead meat": "أي تحرّش جنسي، وستكون نهايتك!",
    "that's not what i meant": "لم أقصد ذلك!",
    "that's not what imenti": "لم أقصد ذلك!",
    "what's going on with the ethics training of the elite division one": "ما الذي يجري في تدريب الأخلاقيات للفرقة الأولى من «النخبة»؟",
    "what's go ing on with the ethics training of the elite division onep": "ما الذي يجري في تدريب الأخلاقيات للفرقة الأولى من «النخبة»؟",
    "she had these long bangs like this": "كانت لها غرّة طويلة كهذه.",
    "she had these lonk bangs like this": "كانت لها غرّة طويلة كهذه.",
    "i'm just saying that the ones i saw were even at that level i'm talking about someone else": "أنا فقط أقول إن من رأيتهن بلغن هذا المستوى أيضاً. أنا أتحدث عن شخص آخر!",
    "i'm just saying that the ones i saw were on that level i'm talking about someone else": "أنا فقط أقول إن من رأيتهن بلغن هذا المستوى أيضاً. أنا أتحدث عن شخص آخر!",
    "whoops": "عفواً!",
    "dooh": "عفواً!",
    # Reviewed dialogue from Gotsuyoubi no Tawawa, chapter 117, Part 26.
    "want to go out somewhere this weekend": "أترغبين في الخروج إلى مكان ما في عطلة نهاية هذا الأسبوع؟",
    "want to go out somewhere this week end": "أترغبين في الخروج إلى مكان ما في عطلة نهاية هذا الأسبوع؟",
    "hmm": "هممم...",
    "hmm hmm": "هممم؟",
    "hh": "إمم...",
    "hey": "هيه...",
    "instead of me": "بدلاً مني،",
    "nope": "كلا.",
    "you should have your darling sensei take you out somewhere": "لمَ لا تدعين أستاذكِ العزيز يصطحبكِ إلى مكان ما؟",
    "you should have your darling sensei take you out some where": "لمَ لا تدعين أستاذكِ العزيز يصطحبكِ إلى مكان ما؟",
    "we always get swarmed with pickup artists when i go out with you its super annoying": "كلما خرجتُ معكِ، حاصرنا المتحرشون. هذا مزعج للغاية!",
    "we always get swarmed with pickup artists when i go out with t's you ax": "كلما خرجتُ معكِ، حاصرنا المتحرشون. هذا مزعج للغاية!",
    "you could even put on another cosplay outfit to really drive him wild": "يمكنكِ حتى ارتداء زيّ تنكري آخر لتسلبِي لُبَّه...",
    "you could even put on another cos play outfit to really drive him wild": "يمكنكِ حتى ارتداء زيّ تنكري آخر لتسلبِي لُبَّه...",
    "whats with that face": "ما حكاية ذلك الوجه؟",
    "for real what is that even supposed to mean": "حقاً، ماذا يُفترض أن يعني ذلك؟",
    "huh its my fault": "ماذا؟ أهذا خطئي أنا؟",
    "huh it's my fault": "ماذا؟ أهذا خطئي أنا؟",
    "whats wiith tal face": "ما حكاية ذلك الوجه؟",
    "what's wiith tal face": "ما حكاية ذلك الوجه؟",
    "for real what is that even supposed to mean": "حقاً، ماذا يُفترض أن يعني ذلك؟",
    "1 1'm s00orry": "أ-أنا آسف جداً!",
    "im so sorry": "أنا آسف جداً!",
    "i m so sorry": "أنا آسف جداً!",
    "its been half a year since you joined yet youve been late after being late after being late": "مرّ نصف عام على انضمامكِ، ومع ذلك ما زلتِ تتأخرين دائماً!",
    "it's been half a year since you joined yet you've been late after being late after being late": "مرّ نصف عام على انضمامكِ، ومع ذلك ما زلتِ تتأخرين دائماً!",
    "how long are you going to keep acting like some kid": "إلى متى ستواصلين التصرف كطفلة؟!",
    "if you cant even be motivated to work": "إذا لم تستطيعي تحفيز نفسك على العمل،",
    "if you can't even be motivated to work": "إذا لم تستطيعي تحفيز نفسك على العمل،",
    "you have some nerve showing your face here": "ما أوقحك! كيف تجرؤ على الظهور هنا؟",
    "i thought i told you to stay away from me": "ألم أقل لك أن تبتعد عني؟",
    "you can do it i believe in you": "أنت قادر على ذلك! أنا أؤمن بك.",
    "how long are you going to keep acting like": "إلى متى ستواصلين التظاهر بأنكِ...",
    "its been half a year since you joined yet": "مرّ نصف عام على انضمامك! ومع ذلك...",
    "it's been half a year since you joined yet": "مرّ نصف عام على انضمامك! ومع ذلك...",
    "youve been late after being late after being late": "تتأخرين مرةً تلو الأخرى!",
    "you've been late after being late after being late": "تتأخرين مرةً تلو الأخرى!",
    "some kid": "أمجرد طفل؟!",
    "late": "متأخرة!",
    "izutsu printing if you cant even be motivated to": "إذا لم يكن لديكِ حتى الحافز لـ...",
    "izutsu printing if you can't even be motivated to": "إذا لم يكن لديكِ حتى الحافز لـ...",
    "then hooome": "إذن، إلى البيت!",
    "then home": "إذن، إلى البيت!",
    "then stay hooome": "إذن عودي إلى البيت فوراً!",
    "geez reiwa energy and power harrassment come hand in hand huh": "يا للعجب، حماسة عصر رييوا والتسلط الوظيفي يسيران جنباً إلى جنب، أليس كذلك؟",
    "geez reiwa energy and power harassment come hand in hand huh": "يا للعجب، حماسة عصر رييوا والتسلط الوظيفي يسيران جنباً إلى جنب، أليس كذلك؟",
    "the boss's gone mad again": "لقد جنّ المدير مجدداً...",
    "boss": "أيها المدير!",
    "congratulations on defeating the floor boss eugene": "تهانينا يا يوجين على هزيمتك لزعيم الطابق!",
    "keep up the good work and continue progressing": "أحسنت، واصل التقدم!",
    "keep up the good work and continue progressing s": "أحسنت، واصل التقدم!",
    "its a great way to pass the time": "طريقة رائعة لتمضية الوقت!",
    "it's a great way to pass the time": "طريقة رائعة لتمضية الوقت!",
    "thats easy for you to say": "يسهل عليك قول ذلك!",
    "that's easy for you to say": "يسهل عليك قول ذلك!",
    "what do you think you are doing": "ما الذي تظن نفسك فاعله؟",
    "what do you think you're doing": "ما الذي تظن نفسك فاعله؟",
    "its none of your business": "هذا لا يعنيك.",
    "it's none of your business": "هذا لا يعنيك.",
    "dont get the wrong idea": "لا تسيء فهم الأمر.",
    "don't get the wrong idea": "لا تسيء فهم الأمر.",
    "how dare you": "كيف تجرؤ؟",
    "stay away from me": "ابتعد عني.",
}

_NORMALIZED_PHRASES = {normalize_dialogue(source): target for source, target in REVIEWED_PHRASES.items()}


def translate_reviewed_phrase(text: str) -> str | None:
    """Return a reviewed phrase translation for exact whole-utterance matches."""
    return _NORMALIZED_PHRASES.get(normalize_dialogue(text))
