"""CPU-based local text detection and recognition using PP-OCRv6_manga/ONNX Runtime."""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from itertools import product
from pathlib import Path
from threading import Lock
from time import perf_counter

import cv2
import numpy as np

from .speech_bubbles import SpeechBubble, active_bubble_provider, detect_speech_bubbles
from .comic_text_detector import ComicTextDetector, ComicTextRegion
from image_pipeline.image_codec import decode_color_image

EXTERNAL_BUBBLE_OVERLAP_MAX = 0.08
EXTERNAL_BUBBLE_EDGE_MARGIN = 8
COMMON_EXTERNAL_SOUND_EFFECTS = frozenset({
    "BAM", "BANG", "BOOM", "CRACK", "CRASH", "CLANG", "CLANK", "CLAP",
    "DING", "GASP", "KABOOM", "KNOCK", "POW", "SLAM", "SLAP", "SMASH", "SPLAT",
    "SPLASH", "SWISH", "THUD", "THUMP", "WHAM", "WHOOSH", "ZOOM", "KRAAH",
})
LOGGER = logging.getLogger(__name__)
RELAXED_BUBBLE_CONFIDENCE = 0.12
PRIMARY_BUBBLE_CONFIDENCE = 0.25
SINGULAR_ARTICLE_PLURAL_MENT_RE = re.compile(r"\b(?:a|an)\s+[A-Za-z]{4,}ments\b", re.IGNORECASE)


def _external_ocr_tile_intervals(
    length: int, tile_size: int, overlap: int,
) -> list[tuple[int, int]]:
    """Cover an axis with the regular grid and a context-sized final tail."""
    if length <= int(tile_size * 1.15):
        return [(0, length)]
    last_start = max(0, length - tile_size)
    positions = list(range(0, last_start + 1, tile_size - overlap))
    intervals = [(position, min(length, position + tile_size)) for position in positions]
    if not intervals or intervals[-1][0] != last_start:
        previous_end = intervals[-1][1] if intervals else 0
        seam_start = max(0, previous_end - overlap)
        # Keep enough local context for small lettering. Starting earlier than
        # the seam guard is safe: all those pixels were already covered by the
        # previous full tile, while the final crop stays much smaller.
        minimum_tail_height = min(tile_size, max(900, overlap * 2))
        tail_start = min(seam_start, max(0, length - minimum_tail_height))
        intervals.append((tail_start, length))
    return intervals


def _bubble_mask_overlap_fraction(first: SpeechBubble, second: SpeechBubble) -> float:
    """Return intersection over the smaller safe-mask area for two bubbles."""
    ax1, ay1, ax2, ay2 = first.bbox
    bx1, by1, bx2, by2 = second.bbox
    x1, y1, x2, y2 = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    first_mask = first.safe_mask[y1-ay1:y2-ay1, x1-ax1:x2-ax1] > 0
    second_mask = second.safe_mask[y1-by1:y2-by1, x1-bx1:x2-bx1] > 0
    intersection = int(np.count_nonzero(first_mask & second_mask))
    smaller = min(int(np.count_nonzero(first.safe_mask)),
                  int(np.count_nonzero(second.safe_mask)))
    return intersection / smaller if smaller else 0.0


def _relaxed_bubble_candidates(primary: list[SpeechBubble],
                               relaxed: list[SpeechBubble]) -> list[SpeechBubble]:
    """Keep only low-confidence proposals not duplicating a primary bubble."""
    return [
        candidate for candidate in relaxed
        if candidate.confidence < PRIMARY_BUBBLE_CONFIDENCE
        and not any(_bubble_mask_overlap_fraction(candidate, existing) >= 0.50
                    for existing in primary)
    ]


def _bubble_region_overlap_fraction(region: dict, bubble: SpeechBubble) -> float:
    """Measure how much OCR glyph area lies within a proposed bubble mask."""
    x1, y1, x2, y2 = bubble.bbox
    left = max(x1, int(region.get("left", 0)))
    top = max(y1, int(region.get("top", 0)))
    right = min(x2, int(region.get("left", 0) + region.get("width", 0)))
    bottom = min(y2, int(region.get("top", 0) + region.get("height", 0)))
    if right <= left or bottom <= top:
        return 0.0
    text_mask = np.zeros((bottom-top, right-left), dtype=np.uint8)
    polygons = region.get("ocr_polygons") or []
    for polygon in polygons:
        points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
        points[:, 0] -= left
        points[:, 1] -= top
        try:
            cv2.fillConvexPoly(text_mask, np.rint(points).astype(np.int32), 255)
        except cv2.error:
            text_mask[:] = 255
    text_area = int(np.count_nonzero(text_mask))
    if not text_area:
        return 0.0
    mask = bubble.safe_mask[top-y1:bottom-y1, left-x1:right-x1]
    return int(np.count_nonzero((mask > 0) & (text_mask > 0))) / text_area


_COMIC_DETECTOR = None
_COMIC_DETECTOR_INIT_LOCK = Lock()
_SPELLCHECKER = None
_SPELLCHECKER_LOCK = Lock()


def _get_comic_text_detector():
    global _COMIC_DETECTOR
    if _COMIC_DETECTOR is not None:
        return _COMIC_DETECTOR
    with _COMIC_DETECTOR_INIT_LOCK:
        if _COMIC_DETECTOR is None:
            _COMIC_DETECTOR = ComicTextDetector()
    return _COMIC_DETECTOR


class LocalRapidOCR:
    """Lazy local OCR adapter returning line text and pixel coordinates."""

    def __init__(self, min_confidence: float = 0.35):
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        self.min_confidence = min_confidence
        self._engine = None
        self._lock = Lock()
        self._medium_engine = None
        self._medium_lock = Lock()
        self._medium_unavailable = False

    def _get_engine(self):
        with self._lock:
            if self._engine is None:
                from .ppocrv6_manga import get_manga_ocr_engine
                self._engine = get_manga_ocr_engine()
            return self._engine

    def _get_medium_engine(self):
        """Use the active manga OCR engine for retries; never load legacy RapidOCR."""
        return self._get_engine()

    @staticmethod
    def _english_word_counts(text):
        """Return spelling evidence only; never autocorrect source words."""
        spellchecker = LocalRapidOCR._get_spellchecker()
        if spellchecker is None:
            return 0, 0
        words = [word.lower() for word in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
                 if len(word) >= 2]
        if not words:
            return 0, 0
        # Preserve dictionary-recognized contractions and possessives. Some
        # English lexicons store the punctuated form (haven't), others store
        # the base word (today); stripping apostrophes unconditionally made
        # ordinary dialogue look like suspicious OCR.
        def is_known_english_word(word):
            candidates = {word, word.replace("'", "")}
            if word.endswith("'s"):
                candidates.add(word[:-2])
            return any(candidate in spellchecker for candidate in candidates)

        valid = sum(is_known_english_word(word) for word in words)
        return valid, len(words)

    @classmethod
    def _has_trailing_duplicate_ocr_character(cls, text):
        """Detect an unknown token fixed by removing one duplicated final letter.

        This narrow cue catches high-confidence OCR artifacts such as THISS
        without treating arbitrary character names or unknown words as typos.
        A recognizer retry still has to produce a high-confidence, lexically
        better candidate before any source text is replaced.
        """
        spellchecker = cls._get_spellchecker()
        if spellchecker is None:
            return False
        for token in re.findall(r"\b[A-Za-z]{4,}\b", str(text)):
            lowered = token.casefold()
            if (lowered[-1] == lowered[-2]
                    and lowered not in spellchecker
                    and lowered[:-1] in spellchecker):
                return True
        return False

    @staticmethod
    def _get_spellchecker():
        global _SPELLCHECKER
        if _SPELLCHECKER is None:
            with _SPELLCHECKER_LOCK:
                if _SPELLCHECKER is None:
                    try:
                        from spellchecker import SpellChecker
                        _SPELLCHECKER = SpellChecker(language="en", distance=1)
                    except ImportError:
                        return None
        return _SPELLCHECKER

    @classmethod
    def _normalize_external_ocr_region(cls, region):
        """Conservatively fix OCR artifacts in external text without another inference pass."""
        normalized = dict(region)
        if normalized.get("region_type") != "external_text":
            return normalized
        original = str(normalized.get("text", ""))
        text = original
        changes = []

        # The circled exclamation icon before NOTICE is repeatedly recognized as O.
        for pattern in (r"^\s*O(?=\s*NOTICE\b)", r"^\s*O\s+(?=QUEST\s+NOTICE\b)"):
            text, removed = re.subn(pattern, "", text, count=1, flags=re.IGNORECASE)
            if removed:
                changes.append("removed_circled_notice_icon")
                break

        # Split an all-caps OCR token only when the English dictionary yields one unique split.
        spellchecker = cls._get_spellchecker()
        confidence = float(normalized.get("confidence", 0.0) or 0.0)
        if spellchecker is not None and confidence >= 0.70:
            def split_unique_known_compound(match):
                token = match.group(0)
                if len(token) < 14 or token.lower() in spellchecker:
                    return token
                split_points = [
                    point for point in range(4, len(token) - 3)
                    if token[:point].lower() in spellchecker and token[point:].lower() in spellchecker
                ]
                if len(split_points) != 1:
                    return token
                point = split_points[0]
                changes.append("split_unique_dictionary_compound")
                return token[:point] + " " + token[point:]

            text = re.sub(r"\b[A-Z]{14,}\b", split_unique_known_compound, text)

        if changes:
            normalized["text"] = text.strip()
            normalized["ocr_normalization_original_text"] = original
            normalized["ocr_normalization_changes"] = changes
        return normalized

    @classmethod
    def _add_external_ocr_review_hint(cls, region):
        """Flag suspicious external OCR for reader review; never rewrite source text."""
        if region.get("region_type") != "external_text":
            return region
        region.update(cls._normalize_external_ocr_region(region))
        text = str(region.get("text", ""))
        # Progress values such as [0/10KM] are annotations, not prose tokens;
        # exclude them from spelling diagnostics without changing OCR output or
        # the shared word-count helper used by recovery logic.
        review_text = re.sub(r"\[\s*\d+\s*/\s*\d+\s*[A-Z]{0,4}\s*\]", " ", text, flags=re.IGNORECASE)
        valid_words, total_words = cls._english_word_counts(review_text)
        unknown_words = max(0, total_words - valid_words)
        spellchecker = cls._get_spellchecker()
        unknown_hyphenated = []
        unique_fused_tokens = []
        if spellchecker is not None:
            usage_frequency = getattr(spellchecker, "word_usage_frequency", None)
            for token in re.findall(r"\b[A-Z]{3,}-[A-Z]{3,}\b", review_text):
                parts = token.lower().split("-")
                if all(part not in spellchecker for part in parts):
                    unknown_hyphenated.append(token)
            if callable(usage_frequency):
                for token in re.findall(r"\b[A-Z]{5,}\b", review_text):
                    if token.lower() in spellchecker:
                        continue
                    split_points = [
                        point for point in range(2, len(token) - 1)
                        if token[:point].lower() in spellchecker
                        and token[point:].lower() in spellchecker
                        # A merely dictionary-listed suffix can be part of a
                        # proper name (for example KATYA -> KAT + YA). Require
                        # a genuinely common second token before raising a
                        # fused-word warning; keep the source OCR untouched.
                        and usage_frequency(token[point:].lower()) >= 1e-4
                    ]
                    if len(split_points) == 1:
                        unique_fused_tokens.append(token)
        if unknown_hyphenated:
            region["ocr_review_required"] = True
            region["ocr_review_reason"] = "unrecognized_hyphenated_ocr_token"
            region["ocr_unrecognized_hyphenated_tokens"] = unknown_hyphenated
            region["ocr_unrecognized_word_count"] = unknown_words
        elif unique_fused_tokens:
            region["ocr_review_required"] = True
            region["ocr_review_reason"] = "possible_concatenated_english_tokens"
            region["ocr_suspicious_fused_tokens"] = unique_fused_tokens
            region["ocr_unrecognized_word_count"] = unknown_words
        elif total_words >= 3 and unknown_words >= 2:
            region["ocr_review_required"] = True
            region["ocr_review_reason"] = "multiple_unrecognized_english_tokens"
            region["ocr_unrecognized_word_count"] = unknown_words
        else:
            # Credits and overlays often contain role codes rather than prose.
            # Spellcheck can accept a corrupted code-like fragment (for example
            # `DD: cu TS: LOU`) as ordinary short words, so ask for review when
            # multiple uncommon short uppercase tokens occur together. Common
            # English function words and replies are excluded to avoid warnings
            # on captions such as `I AM OK` or `NO, IT IS UP TO US`.
            common_short_words = {
                "A", "AN", "AM", "AND", "AS", "AT", "BE", "BY", "DO", "GO", "HE",
                "HI", "IF", "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OH",
                "OK", "ON", "OR", "SO", "THE", "TO", "UP", "US", "WE", "YOU",
            }
            short_codes = sorted({
                token for token in re.findall(
                    r"(?<![A-Z0-9/.-])[A-Z]{2,3}(?![A-Z0-9/.-])", review_text
                )
                if token not in common_short_words
            })
            if len(short_codes) >= 2 and total_words >= 4:
                region["ocr_review_required"] = True
                region["ocr_review_reason"] = "ambiguous_short_uppercase_tokens"
                region["ocr_suspicious_short_uppercase_tokens"] = short_codes
        return region

    @staticmethod
    def _speech_bubble_ocr_structure_flags(text):
        """Return deterministic OCR warning cues without changing recognized text."""
        flags = []
        if re.search(r"[^A-Za-z0-9\s.,!?;:'\"()\[\]{}…’“”‘’—–-]", text):
            flags.append("unexpected_non_english_glyph")
        if re.search(r"(?<![A-Za-z])[A-Za-z]*\d+[A-Za-z]+|[A-Za-z]+\d+[A-Za-z0-9]*", text):
            flags.append("digit_embedded_in_english_token")
        tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
        isolated = [token.upper() for token in tokens
                    if len(token) == 1 and token.upper() not in {"A", "I"}]
        if len(tokens) >= 5 and isolated:
            flags.append("isolated_single_letter_in_prose")
        if len(tokens) >= 5 and len(isolated) >= 2:
            flags.append("multiple_isolated_single_letters")

        # Catch clear OCR morphology errors such as “a deployments”. This is
        # review metadata only: it never rewrites text or runs another model.
        if SINGULAR_ARTICLE_PLURAL_MENT_RE.search(text):
            flags.append("singular_article_before_plural_ment")

        # Short fragments such as TH and DI can be dictionary entries, but are
        # rarely valid stand-alone prose tokens in a long dialogue line.
        common_two_letter_words = {
            "am", "an", "as", "at", "be", "by", "do", "go", "he", "hi", "if",
            "in", "is", "it", "me", "my", "no", "of", "oh", "ok", "on", "or",
            "so", "to", "up", "us", "we", "ya",
        }
        short_fragments = [token.upper() for token in tokens
                           if len(token) == 2 and token.lower() not in common_two_letter_words]
        if len(tokens) >= 5 and short_fragments:
            flags.append("uncommon_two_letter_fragment_in_prose")

        # A subject pronoun immediately after a preposition is a strong
        # contextual OCR cue when followed by a noun-like token.
        prepositions = {
            "about", "above", "across", "after", "against", "around", "at", "before",
            "behind", "below", "beneath", "beside", "between", "by", "despite", "down",
            "during", "for", "from", "in", "inside", "into", "near", "of", "off", "on",
            "onto", "out", "outside", "over", "past", "since", "through", "to", "toward",
            "under", "until", "up", "upon", "with", "within", "without",
        }
        subject_pronouns = {"he", "she", "they", "we", "i"}
        common_verbs = {
            "am", "are", "be", "been", "being", "can", "could", "did", "do", "does",
            "had", "has", "have", "is", "may", "might", "must", "shall", "should",
            "was", "were", "will", "would",
        }
        lowered = [token.lower() for token in tokens]
        for index in range(len(lowered) - 2):
            if (lowered[index] in prepositions
                    and lowered[index + 1] in subject_pronouns
                    and lowered[index + 2] not in common_verbs):
                flags.append("subject_pronoun_after_preposition")
                break
        return flags, isolated

    @classmethod
    def _add_speech_bubble_ocr_review_hint(cls, region):
        """Flag suspicious dialogue OCR for review; never autocorrect source text.

        Confidence is not sufficient evidence of correctness. Structural cues
        (foreign glyphs, embedded digits, or stray single letters) are checked
        at every confidence level. Dictionary checks are conservative and only
        add review metadata; names and source text remain untouched.
        """
        if region.get("region_type") != "speech_bubble":
            return region
        text = str(region.get("text", ""))
        confidence = float(region.get("confidence", 0.0) or 0.0)
        reasons = []
        if confidence < 0.85:
            reasons.append("low_confidence_speech_bubble_ocr")

        structure_flags, isolated = cls._speech_bubble_ocr_structure_flags(text)
        reasons.extend(structure_flags)

        spellchecker = cls._get_spellchecker()
        words = [word.lower() for word in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
                 if len(word) >= 3]
        unknown = []
        suspicious = []
        candidates_fn = getattr(spellchecker, "candidates", None)
        usage_frequency = getattr(spellchecker, "word_usage_frequency", None)
        if spellchecker is not None and confidence >= 0.85:
            for word in words:
                if word in spellchecker or word.replace("'", "") in spellchecker:
                    continue
                unknown.append(word.upper())
                if not callable(candidates_fn) or not callable(usage_frequency):
                    continue
                candidates = candidates_fn(word) or set()
                common = sorted({
                    candidate for candidate in candidates
                    if candidate != word and usage_frequency(candidate) >= 1e-5
                })
                if len(common) == 1:
                    candidate = common[0]
                    suspicious.append({
                        "observed": word.upper(),
                        "candidate": candidate,
                        "frequency": round(float(usage_frequency(candidate)), 8),
                    })

            if len(unknown) >= 2:
                reasons.append("multiple_unrecognized_english_tokens")
            unknown_set = set(unknown)
            line_boxes = region.get("ocr_boxes") or []
            if (len(words) in (2, 3, 4) and len(unknown) == 1
                    and words[0].upper() == unknown[0]
                    and 2 <= len(unknown[0]) <= 4
                    and 2 <= len(line_boxes) <= 12):
                reasons.append("unrecognized_short_leading_word_in_bubble")
            if any(word in unknown_set and index > 0 and words[index - 1] == "the"
                   for index, word in enumerate(words)):
                reasons.append("unrecognized_token_after_definite_article")
            if suspicious:
                reasons.append("possible_ocr_typo_near_common_word")

        if not reasons:
            return region
        region["ocr_review_required"] = True
        region["ocr_review_reason"] = reasons[0]
        region["ocr_review_reasons"] = reasons
        if unknown:
            region["ocr_unrecognized_word_count"] = len(unknown)
            region["ocr_unrecognized_tokens"] = sorted(set(unknown))
        if isolated:
            region["ocr_suspicious_single_letter_tokens"] = isolated
        if suspicious:
            region["ocr_suspicious_near_words"] = suspicious
        return region

    def _rescue_suspicious_bubble_region_from_line_boxes(self, image, region):
        """Re-read flagged bubble text from its detected line boxes, at 2x scale.

        This path is limited to suspicious multi-line speech bubbles. A candidate
        replaces the original only when every crop is high-confidence, dictionary
        coverage improves, most known source tokens remain, and character-level
        similarity stays high. Ordinary dialogue and external text use no retry.
        """
        if (region.get("region_type") != "speech_bubble"
                or not region.get("ocr_review_required")
                or region.get("ocr_recovery_method") == "suspicious_bubble_line_boxes"):
            return False
        reasons = set(region.get("ocr_review_reasons") or [region.get("ocr_review_reason")])
        retry_cues = {
            "uncommon_two_letter_fragment_in_prose",
            "multiple_unrecognized_english_tokens",
            "possible_ocr_typo_near_common_word",
            "digit_embedded_in_english_token",
            "unrecognized_token_after_definite_article",
            "low_confidence_speech_bubble_ocr",
            "isolated_single_letter_in_prose",
            "unrecognized_short_leading_word_in_bubble",
        }
        if not reasons.intersection(retry_cues):
            return False
        boxes = region.get("ocr_boxes") or []
        if not 2 <= len(boxes) <= 12:
            return False

        crops = []
        for box in boxes:
            try:
                left, top, width, height = [float(value) for value in box[:4]]
            except (TypeError, ValueError):
                return False
            x1, y1 = max(0, int(left)), max(0, int(top))
            x2 = min(image.shape[1], int(np.ceil(left + width)))
            y2 = min(image.shape[0], int(np.ceil(top + height)))
            line_h = max(1.0, height)
            pad_x = max(4, round(line_h * 0.12))
            pad_y = max(3, round(line_h * 0.10))
            crop = image[max(0, y1 - pad_y):min(image.shape[0], y2 + pad_y),
                         max(0, x1 - pad_x):min(image.shape[1], x2 + pad_x)]
            if crop.size == 0:
                return False
            crops.append(cv2.resize(
                crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC
            ))

        try:
            result = self._get_engine().recognize_txt(crops)
            texts = getattr(result, "txts", None) or ()
            scores = getattr(result, "scores", None) or ()
        except Exception:
            LOGGER.debug("Suspicious bubble line-box OCR retry failed", exc_info=True)
            return False
        if len(texts) != len(crops) or len(scores) != len(crops):
            return False

        candidate_lines = []
        confidences = []
        minimum_line_confidence = (
            0.99 if "unrecognized_short_leading_word_in_bubble" in reasons else 0.95
        )
        for raw_text, raw_score in zip(texts, scores):
            text = str(raw_text or "").strip()
            try:
                confidence = float(raw_score)
            except (TypeError, ValueError):
                return False
            if (not text or not np.isfinite(confidence)
                    or confidence < minimum_line_confidence
                    or not self._is_english_source_text(text)):
                return False
            candidate_lines.append(text)
            confidences.append(confidence)

        original = str(region.get("text", "")).strip()
        candidate_parts = []
        for line in candidate_lines:
            if (candidate_parts
                    and re.search(r"[-‐‑–—]$", candidate_parts[-1].rstrip())
                    and re.match(r"^[A-Za-z]", line.lstrip())):
                candidate_parts[-1] = candidate_parts[-1].rstrip() + line.lstrip()
            else:
                candidate_parts.append(line)
        candidate = " ".join(candidate_parts)
        old_valid, old_total = self._english_word_counts(original)
        new_valid, new_total = self._english_word_counts(candidate)
        old_coverage = old_valid / max(1, old_total)
        new_coverage = new_valid / max(1, new_total)
        candidate_tokens = {
            token.casefold() for token in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", candidate)
        }
        suspicious_single_letters = {
            str(token).casefold()
            for token in region.get("ocr_suspicious_single_letter_tokens", ())
        }
        recovered_single_letter = (
            "isolated_single_letter_in_prose" in reasons
            and bool(suspicious_single_letters)
            and not suspicious_single_letters.intersection(candidate_tokens)
        )
        suspicious_unknowns = {
            str(token).casefold() for token in region.get("ocr_unrecognized_tokens", ())
        }
        recovered_unknown_word = (
            "unrecognized_short_leading_word_in_bubble" in reasons
            and bool(suspicious_unknowns)
            and not suspicious_unknowns.intersection(candidate_tokens)
        )
        coverage_unchanged = abs(new_coverage - old_coverage) <= 1e-9
        if (not new_total or new_valid < old_valid + 1
                or new_coverage < 0.90
                or (new_coverage <= old_coverage
                    and not ((recovered_single_letter or recovered_unknown_word)
                             and coverage_unchanged))
                or not self._looks_like_dialogue(candidate)
                or self.is_likely_nonverbal_effect(candidate)):
            return False

        old_letters = "".join(re.findall(r"[A-Za-z]", original)).casefold()
        new_letters = "".join(re.findall(r"[A-Za-z]", candidate)).casefold()
        minimum_similarity = (
            0.90 if "unrecognized_short_leading_word_in_bubble" in reasons else 0.78
        )
        if SequenceMatcher(None, old_letters, new_letters).ratio() < minimum_similarity:
            return False

        spellchecker = self._get_spellchecker()
        known_tokens = [
            token.casefold() for token in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", original)
            if len(token) >= 3 and spellchecker is not None
            and token.casefold() in spellchecker
        ]
        if (len(known_tokens) >= 4
                and sum(token in candidate_tokens for token in known_tokens) / len(known_tokens) < 0.78):
            return False

        for key in (
            "ocr_review_required", "ocr_review_reason", "ocr_review_reasons",
            "ocr_unrecognized_word_count", "ocr_unrecognized_tokens",
            "ocr_suspicious_single_letter_tokens", "ocr_suspicious_near_words",
        ):
            region.pop(key, None)
        region.update({
            "text": candidate,
            "confidence": round(sum(confidences) / len(confidences), 4),
            "ocr_rescued": True,
            "ocr_recovery_method": "suspicious_bubble_line_boxes",
        })
        return True

    @staticmethod
    def _normalize_near_all_caps_bubble(region):
        """Repair isolated OCR casing noise in long, visibly all-caps balloon text.

        This only changes letter case when the region is already overwhelmingly
        uppercase (at least 92% of its letters), has enough text to infer a style,
        and contains lowercase characters. It does not alter words or punctuation.
        """
        if region.get("region_type") != "speech_bubble":
            return region
        text = str(region.get("text", ""))
        letters = [char for char in text if char.isalpha() and char.isascii()]
        if len(letters) < 18:
            return region
        uppercase = sum(char.isupper() for char in letters)
        lowercase = sum(char.islower() for char in letters)
        if lowercase and uppercase / len(letters) >= 0.92:
            region["ocr_casing_original_text"] = text
            region["ocr_casing_normalization"] = "near_all_caps_to_uppercase"
            region["text"] = text.upper()
        return region

    @classmethod
    def _normalize_unique_common_digit_confusions(cls, region):
        """Repair digit-for-letter OCR substitutions only for one common word."""
        if (region.get("region_type") != "speech_bubble"
                or float(region.get("confidence", 0.0) or 0.0) < 0.90):
            return region
        text = str(region.get("text", ""))
        if not re.search(r"[0-9]", text):
            return region
        spellchecker = cls._get_spellchecker()
        usage_frequency = getattr(spellchecker, "word_usage_frequency", None)
        if spellchecker is None or not callable(usage_frequency):
            return region

        digit_letters = {
            "0": "O", "1": "IL", "2": "Z", "3": "E", "4": "A",
            "5": "S", "6": "G", "7": "T", "8": "B", "9": "G",
        }
        corrections = []

        def replace_if_unique_common(match):
            token = match.group(0)
            positions = [index for index, char in enumerate(token) if char.isdigit()]
            if not positions or len(positions) > 2:
                return token
            options = [digit_letters.get(char, "") if char.isdigit() else char
                       for char in token]
            if any(not option for option in options):
                return token
            plausible = set()
            for replacements in product(*options):
                candidate = "".join(replacements)
                lowered = candidate.lower()
                if (candidate.isalpha() and lowered in spellchecker
                        and usage_frequency(lowered) >= 1e-4):
                    plausible.add(candidate)
                    if len(plausible) > 1:
                        return token
            if len(plausible) != 1:
                return token
            candidate = next(iter(plausible))
            corrections.append({"observed": token, "candidate": candidate})
            return candidate

        corrected = re.sub(r"\b[A-Z0-9]{2,}\b", replace_if_unique_common, text)
        if corrections:
            region["ocr_digit_confusion_original_text"] = text
            region["ocr_digit_confusion_corrections"] = corrections
            region["text"] = corrected
        return region

    @staticmethod
    def _needs_medium_ocr(text, confidence):
        # Retry low-confidence and structurally garbled OCR even when confidence
        # is high: the visual sample contains wrong readings at >0.90 confidence.
        # Replacement remains subject to the token-preservation guard below.
        if confidence < 0.80 or re.search(r"![A-Za-z]", text):
            return True
        structure_flags, _isolated = LocalRapidOCR._speech_bubble_ocr_structure_flags(text)
        if structure_flags:
            return True
        spellchecker = LocalRapidOCR._get_spellchecker()
        if spellchecker is None:
            return False
        words = [word.lower() for word in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
                 if len(word) >= 2]
        unknown = [word for word in words
                   if word not in spellchecker and word.replace("'", "") not in spellchecker]
        if len(words) >= 4 and len(unknown) >= 2:
            return True
        return any(
            word in unknown and index > 0 and words[index - 1] == "the"
            for index, word in enumerate(words)
        )

    @classmethod
    def _needs_unmasked_medium_bubble_retry(cls, lines):
        """Return whether a flagged bubble merits an unmasked context retry."""
        if not lines:
            return False
        combined_text = " ".join(str(line.get("text", "")) for line in lines)
        try:
            combined_confidence = min(float(line.get("confidence", 0.0)) for line in lines)
        except (TypeError, ValueError):
            combined_confidence = 0.0
        needs_medium = (
            any(cls._needs_medium_ocr(line.get("text", ""), line.get("confidence", 0.0))
                for line in lines)
            or cls._needs_medium_ocr(combined_text, combined_confidence)
        )
        if not needs_medium:
            return False
        flags, _isolated = cls._speech_bubble_ocr_structure_flags(combined_text)
        return bool(set(flags).intersection({
            "unexpected_non_english_glyph",
            "uncommon_two_letter_fragment_in_prose",
        }))

    @classmethod
    def _preserves_known_tokens(cls, original, candidate, *, ignore_tokens_near_glyphs=False):
        """Require recognized source words to survive retries in the same order."""
        spellchecker = cls._get_spellchecker()
        if spellchecker is None:
            return False
        source = str(original)
        suspicious_positions = [
            index for index, char in enumerate(source)
            if not (char.isascii() and (char.isalnum() or char.isspace()
                or char in ".,!?;:'\"()[]{}…’“”‘’—–-"))
        ] if ignore_tokens_near_glyphs else []
        original_tokens = []
        for match in re.finditer(r"[A-Za-z]+(?:'[A-Za-z]+)?", source):
            token = match.group(0)
            if (token.casefold() not in spellchecker
                    or (len(token) <= 1 and token.casefold() not in {"i", "a"})):
                continue
            if any(match.start() - 3 <= pos <= match.end() + 2 for pos in suspicious_positions):
                continue
            original_tokens.append(token.casefold())
        if not original_tokens:
            # Only permit anchor-free replacement when all source anchors are
            # adjacent to a visibly malformed non-English glyph.
            return bool(ignore_tokens_near_glyphs and suspicious_positions)
        candidate_tokens = [
            token.casefold()
            for token in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", str(candidate))
        ]
        cursor = 0
        for original_token in original_tokens:
            match = None
            for index in range(cursor, len(candidate_tokens)):
                candidate_token = candidate_tokens[index]
                if candidate_token == original_token:
                    match = index
                    break
                # Permit one glyph-level edit only for a four-letter-or-longer
                # known word. This recovers cases such as BUTT -> BUT while
                # keeping short character names/acronyms as hard anchors.
                if (len(original_token) >= 4 and candidate_token in spellchecker
                        and cls._tokens_one_edit_apart(original_token, candidate_token)):
                    match = index
                    break
            if match is None:
                return False
            cursor = match + 1
        return True

    @staticmethod
    def _tokens_one_edit_apart(first, second):
        if first == second:
            return True
        if abs(len(first) - len(second)) > 1:
            return False
        i = j = edits = 0
        while i < len(first) and j < len(second):
            if first[i] == second[j]:
                i += 1
                j += 1
                continue
            edits += 1
            if edits > 1:
                return False
            if len(first) > len(second):
                i += 1
            elif len(second) > len(first):
                j += 1
            else:
                i += 1
                j += 1
        edits += (len(first) - i) + (len(second) - j)
        return edits <= 1

    def _rescue_bubble_lines(self, crop, lines, unmasked_crop=None):
        """Retry suspicious bubbles with context while preserving text geometry."""
        if not lines:
            return lines
        combined_text = " ".join(str(line.get("text", "")) for line in lines)
        try:
            combined_confidence = min(float(line.get("confidence", 0.0)) for line in lines)
        except (TypeError, ValueError):
            combined_confidence = 0.0
        structure_flags, _isolated = self._speech_bubble_ocr_structure_flags(combined_text)
        structure_flags = set(structure_flags)
        if not (any(self._needs_medium_ocr(line["text"], line["confidence"]) for line in lines)
                or self._needs_medium_ocr(combined_text, combined_confidence)):
            return lines
        engine = self._get_medium_engine()
        if engine is None:
            return lines

        def retry(source_crop, method):
            try:
                result = engine(source_crop)
                boxes = getattr(result, "boxes", None) if result is not None else None
                texts = getattr(result, "txts", None) if result is not None else None
                raw_scores = getattr(result, "scores", None) if result is not None else None
                if boxes is None or texts is None or raw_scores is None:
                    return None
                recognized = [
                    (box, str(text or "").strip(), float(score))
                    for box, text, score in zip(boxes, texts, raw_scores)
                ]
            except Exception:
                LOGGER.debug("Medium OCR bubble retry failed", exc_info=True)
                return None
            recognized = [item for item in recognized
                          if item[1] and self._is_english_source_text(item[1])]
            recognized.sort(key=lambda item: float(np.asarray(item[0])[:, 1].mean()))
            if len(recognized) != len(lines):
                return None
            candidate_text = " ".join(item[1] for item in recognized)
            allow_glyph_tolerance = "unexpected_non_english_glyph" in structure_flags
            if not self._preserves_known_tokens(
                combined_text, candidate_text,
                ignore_tokens_near_glyphs=allow_glyph_tolerance,
            ):
                return None
            rescued = []
            changed = False
            for line, item in zip(lines, recognized):
                candidate = item[1]
                old_valid, old_total = self._english_word_counts(line["text"])
                new_valid, new_total = self._english_word_counts(candidate)
                if (
                    new_valid > old_valid
                    and new_valid / max(1, new_total) >= old_valid / max(1, old_total)
                    and (old_valid == 0 or self._preserves_known_tokens(
                        line["text"], candidate,
                        ignore_tokens_near_glyphs=allow_glyph_tolerance,
                    ))
                    and len(candidate) >= max(2, int(len(line["text"]) * 0.55))
                ):
                    replacement = dict(
                        line, text=candidate, ocr_rescued=True,
                        ocr_recovery_method=method,
                    )
                    if np.isfinite(item[2]):
                        replacement["confidence"] = round(item[2], 4)
                    rescued.append(replacement)
                    changed = True
                else:
                    rescued.append(line)
            return rescued if changed else None

        rescued = retry(crop, "medium_bubble_crop")
        if rescued is not None:
            return rescued

        # A small subset of errors is caused by the safe mask trimming or
        # distorting glyphs. Retry only bubbles with a foreign-glyph artifact
        # or a prose-level two-letter fragment; other bubbles stay single-pass.
        if (unmasked_crop is not None
                and structure_flags.intersection({
                    "unexpected_non_english_glyph",
                    "uncommon_two_letter_fragment_in_prose",
                })):
            rescued = retry(unmasked_crop, "unmasked_medium_bubble_crop")
            if rescued is not None:
                return rescued
        return lines

    @staticmethod
    def _needs_sparse_line_retry(line):
        """Spot a low-confidence, unusually sparse line inside a large bubble."""
        text = str(line.get("text", ""))
        width = float(line.get("width", 0.0) or 0.0)
        confidence = float(line.get("confidence", 0.0) or 0.0)
        letter_count = len(re.findall(r"[A-Za-z]", text))
        return (
            confidence < 0.90
            and width >= 250
            and letter_count >= 5
            and letter_count / max(1.0, width) <= 0.027
        )

    def _rescue_sparse_bubble_lines(self, crop, lines, origin=(0, 0), scale=1.0):
        """Re-OCR only sparse uncertain text-line crops, not the whole page/bubble."""
        targets = [line for line in lines if self._needs_sparse_line_retry(line)]
        if not targets:
            return lines
        engine = self._get_engine()
        rescued = list(lines)
        changed = False
        origin_x, origin_y = origin
        # The saved missed-word case needed a little more vertical context
        # than the original detector box so the recognizer could decode the
        # complete line rather than repeat the truncated token sequence.
        pad_x, pad_y = round(28 * scale), round(20 * scale)
        for index, line in enumerate(lines):
            if line not in targets:
                continue
            x1 = round((float(line["left"]) - origin_x) * scale)
            y1 = round((float(line["top"]) - origin_y) * scale)
            x2 = x1 + round(float(line["width"]) * scale)
            y2 = y1 + round(float(line["height"]) * scale)
            crop_x1, crop_y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
            crop_x2 = min(crop.shape[1], x2 + pad_x)
            crop_y2 = min(crop.shape[0], y2 + pad_y)
            if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
                continue
            line_crop = crop[crop_y1:crop_y2, crop_x1:crop_x2].copy()
            try:
                result = engine(line_crop)
                boxes = getattr(result, "boxes", None) if result is not None else None
                texts = getattr(result, "txts", None) if result is not None else None
                scores = getattr(result, "scores", None) if result is not None else None
                if boxes is None or texts is None or scores is None:
                    continue
                center_y = (y1 + y2) / 2 - crop_y1
                candidates = []
                for box, text, score in zip(boxes, texts, scores):
                    text = str(text or "").strip()
                    if not text or not self._is_english_source_text(text):
                        continue
                    points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
                    if not len(points) or not np.isfinite(points).all():
                        continue
                    box_center_y = float(points[:, 1].mean())
                    candidates.append((abs(box_center_y - center_y), text, float(score)))
            except Exception:
                LOGGER.debug("Sparse bubble line OCR retry failed", exc_info=True)
                continue
            if not candidates:
                continue
            _, candidate, candidate_confidence = min(candidates, key=lambda item: item[0])
            old_valid, old_total = self._english_word_counts(line["text"])
            new_valid, new_total = self._english_word_counts(candidate)
            if (new_valid > old_valid
                    and new_valid / max(1, new_total) >= old_valid / max(1, old_total)
                    and self._preserves_known_tokens(line["text"], candidate)
                    and len(candidate) >= max(2, int(len(line["text"]) * 0.55))):
                replacement = dict(rescued[index], text=candidate, ocr_rescued=True,
                                   ocr_recovery_method="targeted_sparse_line_crop")
                if np.isfinite(candidate_confidence):
                    replacement["confidence"] = round(candidate_confidence, 4)
                rescued[index] = replacement
                changed = True
        return rescued if changed else lines

    def _rescue_fragmented_bubble_line_pairs(self, image, lines):
        """Join and recognize two same-row fragments when the detector split one word line."""
        if len(lines) < 2:
            return lines
        pairs = []
        for first_index, first in enumerate(lines):
            for second_index in range(first_index + 1, len(lines)):
                second = lines[second_index]
                first_h, second_h = float(first["height"]), float(second["height"])
                typical_h = max(1.0, min(first_h, second_h))
                first_cy = float(first["top"]) + first_h / 2
                second_cy = float(second["top"]) + second_h / 2
                if abs(first_cy - second_cy) > typical_h * 0.40:
                    continue
                left = min(float(first["left"]), float(second["left"]))
                right = max(float(first["left"]) + float(first["width"]),
                            float(second["left"]) + float(second["width"]))
                gap = max(0.0, max(float(first["left"]), float(second["left"]))
                          - min(float(first["left"]) + float(first["width"]),
                                float(second["left"]) + float(second["width"])))
                if gap > typical_h * 1.25 or right - left < typical_h * 2.5:
                    continue
                first_valid, first_total = self._english_word_counts(first.get("text", ""))
                second_valid, second_total = self._english_word_counts(second.get("text", ""))
                if (first_valid == first_total and second_valid == second_total
                        and first_total and second_total):
                    continue
                pairs.append((first_index, second_index, left, right,
                              min(float(first["top"]), float(second["top"])),
                              max(float(first["top"]) + first_h,
                                  float(second["top"]) + second_h),
                              typical_h))
        if not pairs:
            return lines
        crops = []
        for _, _, left, right, top, bottom, line_h in pairs:
            pad_x, pad_y = max(6, round(line_h * 0.55)), max(6, round(line_h * 0.45))
            x1, y1 = max(0, int(left - pad_x)), max(0, int(top - pad_y))
            x2, y2 = min(image.shape[1], int(np.ceil(right + pad_x))), min(
                image.shape[0], int(np.ceil(bottom + pad_y))
            )
            crops.append(image[y1:y2, x1:x2].copy())
        try:
            result = self._get_engine().recognize_txt(crops)
            texts = getattr(result, "txts", None) or ()
            scores = getattr(result, "scores", None) or ()
        except Exception:
            LOGGER.debug("Fragmented bubble line recognition retry failed", exc_info=True)
            return lines
        if len(texts) != len(pairs) or len(scores) != len(pairs):
            return lines
        rescued = list(lines)
        removed = set()
        used = set()
        for pair, candidate, raw_score in zip(pairs, texts, scores):
            first_index, second_index, left, right, top, bottom, line_h = pair
            if first_index in used or second_index in used:
                continue
            candidate = str(candidate or "").strip()
            try:
                confidence = float(raw_score)
            except (TypeError, ValueError):
                continue
            original = f"{lines[first_index].get('text', '')} {lines[second_index].get('text', '')}"
            old_valid, old_total = self._english_word_counts(original)
            new_valid, new_total = self._english_word_counts(candidate)
            old_letters = "".join(re.findall(r"[A-Za-z]", original)).casefold()
            new_letters = "".join(re.findall(r"[A-Za-z]", candidate)).casefold()
            if (not candidate or confidence < 0.97
                    or not self._is_english_source_text(candidate)
                    or new_valid < 1
                    or new_valid / max(1, new_total) <= old_valid / max(1, old_total)
                    or new_valid / max(1, new_total) < 0.80
                    or SequenceMatcher(None, old_letters, new_letters).ratio() < 0.30):
                continue
            merged = dict(lines[first_index], text=candidate,
                          left=round(left, 2), top=round(top, 2),
                          width=round(right - left, 2), height=round(bottom - top, 2),
                          confidence=round(confidence, 4), ocr_rescued=True,
                          ocr_recovery_method="direct_recognition_of_fragmented_bubble_line")
            rescued[first_index] = merged
            removed.add(second_index)
            used.update((first_index, second_index))
        return [line for index, line in enumerate(rescued) if index not in removed]

    def _rescue_malformed_bubble_lines(
        self, image, lines, *, minimum_confidence=0.97,
        allow_low_similarity=False, allow_short_malformed=False,
    ):
        """Use the installed recognizer directly on isolated suspicious bubble lines."""
        targets, crops = [], []
        for index, line in enumerate(lines):
            text = str(line.get("text", ""))
            valid, total = self._english_word_counts(text)
            letters = len(re.findall(r"[A-Za-z]", text))
            digit_count = len(re.findall(r"\d", text))
            digit_confusion = digit_count >= 2 and letters >= 1
            try:
                line_confidence = float(line.get("confidence", 1.0))
            except (TypeError, ValueError):
                line_confidence = 1.0
            single_letter_tokens = re.findall(
                r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", text
            )
            suspicious_single_letter = (
                not allow_short_malformed
                and any(token.casefold() not in {"a", "i", "o"}
                        for token in single_letter_tokens)
            )
            short_unknown_fragment = (
                not allow_short_malformed
                and valid == 0 and letters >= 5 and line_confidence <= 0.96
            )
            malformed_word = total > 0 and (
                (valid < total and (
                    letters >= 6
                    or self._has_trailing_duplicate_ocr_character(text)
                    or (allow_short_malformed and letters >= 4
                        and line_confidence <= 0.90)
                ))
                or short_unknown_fragment
                or (suspicious_single_letter and line_confidence <= 0.97)
            )
            if not (malformed_word or digit_confusion):
                continue
            left, top = int(line["left"]), int(line["top"])
            right = int(np.ceil(float(line["left"]) + float(line["width"])))
            bottom = int(np.ceil(float(line["top"]) + float(line["height"])))
            line_h = max(1.0, float(line["height"]))
            pad_x, pad_y = max(8, round(line_h * 0.22)), max(6, round(line_h * 0.28))
            x1, y1 = max(0, left - pad_x), max(0, top - pad_y)
            x2, y2 = min(image.shape[1], right + pad_x), min(image.shape[0], bottom + pad_y)
            crop = image[y1:y2, x1:x2]
            if crop.size:
                targets.append(index)
                crops.append(crop.copy())
            if len(crops) >= 8:
                break
        if not crops:
            return lines
        try:
            result = self._get_engine().recognize_txt(crops)
            texts = getattr(result, "txts", None) or ()
            scores = getattr(result, "scores", None) or ()
        except Exception:
            LOGGER.debug("Malformed bubble line direct recognition failed", exc_info=True)
            return lines
        if len(texts) != len(crops) or len(scores) != len(crops):
            return lines
        rescued = list(lines)
        for index, candidate, raw_score in zip(targets, texts, scores):
            candidate = str(candidate or "").strip()
            try:
                confidence = float(raw_score)
            except (TypeError, ValueError):
                continue
            original = str(lines[index].get("text", ""))
            old_valid, old_total = self._english_word_counts(original)
            new_valid, new_total = self._english_word_counts(candidate)
            old_letters = "".join(re.findall(r"[A-Za-z]", original)).casefold()
            new_letters = "".join(re.findall(r"[A-Za-z]", candidate)).casefold()
            similarity = SequenceMatcher(None, old_letters, new_letters).ratio()
            digit_confusion = len(re.findall(r"\d", original)) >= 2
            # A weak dictionary fragment or isolated OCR letter can be misleading.
            # Allow replacement only when the crop retry yields a complete,
            # higher-count dictionary phrase at exceptionally high confidence.
            single_letter_tokens = re.findall(
                r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", original
            )
            suspicious_single_letter = any(
                token.casefold() not in {"a", "i", "o"} for token in single_letter_tokens
            )
            strong_single_letter_recovery = (
                suspicious_single_letter
                and new_total > 0
                and new_valid == new_total
                and new_valid > old_valid
                and confidence >= 0.99
            )
            weak_anchor_full_recovery = (
                old_valid > 0
                and old_valid / max(1, old_total) < 0.50
                and new_total > 0
                and new_valid == new_total
                and confidence >= 0.99
            )
            token_replacement_recovery = (
                weak_anchor_full_recovery or strong_single_letter_recovery
            )
            if (not candidate or not np.isfinite(confidence) or confidence < minimum_confidence
                    or not self._is_english_source_text(candidate)
                    or new_valid < 1
                    or (new_valid / max(1, new_total) <= old_valid / max(1, old_total)
                        and not strong_single_letter_recovery)
                    or len(candidate) < max(3, int(len(original) * 0.60))
                    or (old_valid and not self._preserves_known_tokens(original, candidate)
                        and not token_replacement_recovery)
                    or (not old_valid and similarity < 0.30 and confidence < 0.98
                        and not digit_confusion and not allow_low_similarity)):
                continue
            replacement = dict(lines[index], text=candidate, confidence=round(confidence, 4),
                               ocr_rescued=True,
                               ocr_recovery_method="direct_recognition_of_malformed_bubble_line")
            rescued[index] = replacement
        return rescued

    def _rescue_large_stylized_bubble_lines(self, image, lines):
        """Retry severely damaged oversized bubble lines with tight Medium crops."""
        if len(lines) < 2:
            return lines
        original_text = " ".join(str(line.get("text", "")) for line in lines)
        old_valid, old_total = self._english_word_counts(original_text)
        structure_flags, _isolated = self._speech_bubble_ocr_structure_flags(original_text)
        structurally_damaged = bool(set(structure_flags).intersection({
            "digit_embedded_in_english_token", "unexpected_non_english_glyph",
        }))
        if (old_total < 2 or (
                old_valid / max(1, old_total) >= 0.70 and not structurally_damaged
        )):
            return lines
        targets = [
            (index, line) for index, line in enumerate(lines)
            if float(line.get("height", 0.0) or 0.0) >= 80
        ]
        if len(targets) < 2 or len(targets) > 4:
            return lines
        engine = self._get_medium_engine()
        if engine is None:
            return lines
        crops = []
        for _index, line in targets:
            left, top = int(line["left"]), int(line["top"])
            right = int(np.ceil(float(line["left"]) + float(line["width"])))
            bottom = int(np.ceil(float(line["top"]) + float(line["height"])))
            crop = image[max(0, top):min(image.shape[0], bottom),
                         max(0, left):min(image.shape[1], right)]
            if crop.size == 0:
                return lines
            crops.append(crop.copy())
        try:
            result = engine.recognize_txt(crops)
            texts = getattr(result, "txts", None) or ()
            scores = getattr(result, "scores", None) or ()
        except Exception:
            LOGGER.debug("Tight Medium OCR retry for stylized bubble failed", exc_info=True)
            return lines
        if len(texts) != len(targets) or len(scores) != len(targets):
            return lines

        replacements = []
        for (index, line), raw_text, raw_score in zip(targets, texts, scores):
            candidate = str(raw_text or "").strip()
            try:
                confidence = float(raw_score)
            except (TypeError, ValueError):
                return lines
            if (not candidate or not np.isfinite(confidence) or confidence < 0.95
                    or not self._is_english_source_text(candidate)):
                return lines
            replacements.append((index, line, candidate, confidence))
        candidate_text = " ".join(item[2] for item in replacements)
        new_valid, new_total = self._english_word_counts(candidate_text)
        old_ratio = old_valid / max(1, old_total)
        new_ratio = new_valid / max(1, new_total)
        # Digit-contaminated tokens can be split by the lexicon into fake
        # anchors (for example X11MI -> MI). Keep meaningful known words, but
        # ignore the malformed token and isolated two-letter fragments.
        anchor_text = re.sub(r"\b[A-Za-z0-9]*\d[A-Za-z0-9]*\b", " ", original_text)
        if structurally_damaged:
            anchor_text = re.sub(r"\b[A-Za-z]{1,2}\b", " ", anchor_text)
        if (not new_total or new_ratio < 0.80
                or (new_valid <= old_valid and new_ratio <= old_ratio)
                or not self._preserves_known_tokens(anchor_text, candidate_text)):
            return lines

        rescued = list(lines)
        for index, line, candidate, confidence in replacements:
            rescued[index] = dict(
                line, text=candidate, confidence=round(confidence, 4),
                ocr_rescued=True,
                ocr_recovery_method="tight_medium_stylized_bubble_retry",
            )
        return rescued

    def _rescue_terminal_short_fragment(self, image, lines):
        """Retry a short unknown final line in a clearly continuing dialogue bubble.

        This targets cases such as a final “BT” after a comma in long prose.
        It needs surrounding dialogue, a high-confidence single-word recognizer
        result, one-character edit distance, and a common dictionary word.
        """
        if len(lines) < 3:
            return lines
        ordered = sorted(lines, key=lambda line: float(line.get("top", 0.0)))
        target = ordered[-1]
        original = str(target.get("text", "")).strip()
        match = re.fullmatch(r"([A-Za-z]{2})([.,!?…]*)", original)
        if not match:
            return lines
        short_word, punctuation = match.groups()
        spellchecker = self._get_spellchecker()
        if spellchecker is None:
            return lines
        usage_frequency = getattr(spellchecker, "word_usage_frequency", None)
        short_frequency = (
            float(usage_frequency(short_word.casefold()))
            if callable(usage_frequency) else None
        )
        if (short_word.casefold() in spellchecker
                and (short_frequency is None or short_frequency >= 1e-5)):
            return lines
        previous = str(ordered[-2].get("text", "")).rstrip()
        if not re.search(r"[,;:—–-…]$", previous):
            return lines
        context = " ".join(str(line.get("text", "")) for line in ordered[:-1])
        if len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", context)) < 7:
            return lines

        left, top = int(target["left"]), int(target["top"])
        right = int(np.ceil(float(target["left"]) + float(target["width"])))
        bottom = int(np.ceil(float(target["top"]) + float(target["height"])))
        pad_x = max(4, round(float(target["height"]) * 0.22))
        pad_y = max(4, round(float(target["height"]) * 0.12))
        crop = image[max(0, top-pad_y):min(image.shape[0], bottom+pad_y),
                     max(0, left-pad_x):min(image.shape[1], right+pad_x)]
        if crop.size == 0:
            return lines
        try:
            result = self._get_engine().recognize_txt([crop])
            candidates = getattr(result, "txts", None) or ()
            scores = getattr(result, "scores", None) or ()
            candidate = str(candidates[0] or "").strip()
            confidence = float(scores[0])
        except Exception:
            return lines
        candidate_tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", candidate)
        if len(candidate_tokens) != 1:
            return lines
        candidate_word = candidate_tokens[0].casefold()
        usage_frequency = getattr(spellchecker, "word_usage_frequency", None)
        is_common = (
            callable(usage_frequency) and usage_frequency(candidate_word) >= 1e-4
        ) or candidate_word in {
            "and", "are", "but", "can", "for", "her", "his", "not", "our",
            "out", "the", "was", "who", "you",
        }
        if (not np.isfinite(confidence) or confidence < 0.95
                or candidate_word not in spellchecker or not is_common
                or len(candidate_word) != 3
                or not self._tokens_one_edit_apart(short_word.casefold(), candidate_word)):
            return lines

        replacement = dict(
            target, text=candidate, confidence=round(confidence, 4),
            ocr_rescued=True, ocr_recovery_method="terminal_short_fragment_context_retry",
        )
        if punctuation and not re.search(r"[.,!?…]$", candidate):
            replacement["text"] += punctuation[-1]
        output = list(lines)
        target_index = next(index for index, line in enumerate(lines) if line is target)
        output[target_index] = replacement
        return output

    def _rescue_malformed_bubble_regions(self, image, regions, lines):
        """Retry only malformed speech lines from late/secondary OCR recovery."""
        rescued_count = 0
        for region in regions:
            if region.get("region_type") != "speech_bubble":
                continue
            boxes = region.get("ocr_boxes") or []
            if not boxes:
                continue
            members = []
            member_indexes = []
            used = set()
            for box in boxes:
                try:
                    target = np.asarray(box, dtype=np.float32).reshape(4)
                except (TypeError, ValueError):
                    continue
                match = None
                for index, line in enumerate(lines):
                    if index in used:
                        continue
                    try:
                        geometry = np.asarray(
                            [line["left"], line["top"], line["width"], line["height"]],
                            dtype=np.float32,
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                    if np.max(np.abs(geometry - target)) <= 2.5:
                        match = index
                        break
                if match is None:
                    continue
                used.add(match)
                member_indexes.append(match)
                members.append(dict(lines[match]))
            if len(members) != len(boxes):
                continue
            rescued = self._rescue_malformed_bubble_lines(
                image, members, minimum_confidence=0.92, allow_low_similarity=True,
                allow_short_malformed=True,
            )
            if not any(
                new.get("text") != old.get("text")
                for old, new in zip(members, rescued)
            ):
                continue
            old_text = " ".join(line.get("text", "") for line in members)
            new_text = " ".join(line.get("text", "") for line in rescued)
            old_valid, old_total = self._english_word_counts(old_text)
            new_valid, new_total = self._english_word_counts(new_text)
            if (old_total < 2 or old_valid < 1 or new_total == 0
                    or new_valid != new_total
                    or new_valid / new_total <= old_valid / max(1, old_total)):
                continue
            for index, replacement in zip(member_indexes, rescued):
                lines[index].update(replacement)
            region["text"] = " ".join(line["text"] for line in rescued)
            region["ocr_recovered"] = True
            region["ocr_recovery_method"] = "direct_recognition_of_malformed_recovered_bubble_lines"
            rescued_count += 1
        return rescued_count

    def _recover_clipped_bubble_text(self, image, bubbles, lines, regions, reading_direction):
        """Run RT-DETR only when OCR fragments touch an incomplete bubble mask.

        Recovered detector crops are emitted as local external-text repairs so
        the original bubble shape and segmentation mask remain untouched.
        """
        edge_lines_by_bubble = {}
        for line in lines:
            bubble_id = line.get("bubble_id")
            if bubble_id is None or not 1 <= int(bubble_id) <= len(bubbles):
                continue
            bubble = bubbles[int(bubble_id) - 1]
            x1, y1, x2, y2 = bubble.bbox
            line_left = float(line["left"])
            line_right = line_left + float(line["width"])
            touches_edge = line_left <= x1 + 2 or line_right >= x2 - 2
            if not touches_edge:
                continue
            valid, total = self._english_word_counts(line["text"])
            if total and valid < total:
                edge_lines_by_bubble.setdefault(int(bubble_id), []).append(line)

        # A single clipped line can omit a whole word when a bubble mask starts
        # inside a letter. Only such edge lines with unknown lexical content
        # request the full-page detector; ordinary pages stay on the fast path.
        suspicious_ids = {
            bubble_id for bubble_id, group in edge_lines_by_bubble.items()
            if len(group) >= 1
        }
        if not suspicious_ids:
            return lines, regions, 0.0, 0, 0, 0, 0.0, 0

        edge_started = perf_counter()
        output_lines, output_regions, edge_recovery_count = self._recover_edge_clipped_bubble_lines(
            image, list(lines), list(regions), suspicious_ids,
        )
        edge_recovery_seconds = perf_counter() - edge_started

        # Recheck the remaining fragments. Successfully expanded lines must
        # not trigger the heavier full-page detector retry.
        edge_lines_by_bubble = {}
        for line in output_lines:
            bubble_id = line.get("bubble_id")
            if (line.get("ocr_edge_crop_rescued") or bubble_id is None
                    or not 1 <= int(bubble_id) <= len(bubbles)):
                continue
            x1, _y1, x2, _y2 = bubbles[int(bubble_id) - 1].bbox
            line_left = float(line["left"])
            line_right = line_left + float(line["width"])
            if not (line_left <= x1 + 2 or line_right >= x2 - 2):
                continue
            valid, total = self._english_word_counts(line["text"])
            if total and valid < total:
                edge_lines_by_bubble.setdefault(int(bubble_id), []).append(line)
        suspicious_ids = set(edge_lines_by_bubble)
        if not suspicious_ids:
            return (output_lines, output_regions, 0.0, 0, 0, 0,
                    edge_recovery_seconds, edge_recovery_count)

        detector = _get_comic_text_detector()
        if not detector.available:
            return (output_lines, output_regions, 0.0, 0, 0, 0,
                    edge_recovery_seconds, edge_recovery_count)

        # The fallback is needed only for a few suspicious bubbles. Running
        # RT-DETR over every tile of a very tall webtoon page wastes time and
        # returns proposals unrelated to those bubbles. Keep the full-page
        # coordinate system, but ask the detector to inspect a padded local
        # crop around each suspicious bubble instead.
        height, width = image.shape[:2]
        started = perf_counter()
        detections = []
        for bubble_id in sorted(suspicious_ids):
            bubble_x1, bubble_y1, bubble_x2, bubble_y2 = bubbles[bubble_id - 1].bbox
            edge_group = edge_lines_by_bubble[bubble_id]
            left = min(float(bubble_x1), *(float(line["left"]) for line in edge_group))
            top = min(float(bubble_y1), *(float(line["top"]) for line in edge_group))
            right = max(float(bubble_x2), *(float(line["left"]) + float(line["width"])
                                             for line in edge_group))
            bottom = max(float(bubble_y2), *(float(line["top"]) + float(line["height"])
                                              for line in edge_group))
            margin = max(180, int(round(max(right - left, bottom - top) * 0.60)))
            crop_left = max(0, int(np.floor(left - margin)))
            crop_top = max(0, int(np.floor(top - margin)))
            crop_right = min(width, int(np.ceil(right + margin)))
            crop_bottom = min(height, int(np.ceil(bottom + margin)))
            roi = image[crop_top:crop_bottom, crop_left:crop_right]
            if roi.size == 0:
                continue
            try:
                local_detections = detector.detect(roi)
            except Exception:
                LOGGER.warning("Targeted RT-DETR bubble ROI recovery failed", exc_info=True)
                continue
            detections.extend(
                ComicTextRegion(
                    label=item.label,
                    confidence=item.confidence,
                    bbox=(item.bbox[0] + crop_left, item.bbox[1] + crop_top,
                          item.bbox[2] + crop_left, item.bbox[3] + crop_top),
                )
                for item in local_detections
            )
        detector_elapsed = perf_counter() - started

        recovered_count = 0
        attempted_count = len(detections)
        for bubble_id in sorted(suspicious_ids):
            edge_group = edge_lines_by_bubble[bubble_id]
            old_text = " ".join(line["text"] for line in sorted(edge_group, key=lambda item: item["top"]))
            old_top = min(float(line["top"]) for line in edge_group)
            old_bottom = max(float(line["top"]) + float(line["height"]) for line in edge_group)
            for detection in detections:
                if detection.label != "bubble" or detection.confidence < 0.80:
                    continue
                dx1, dy1, dx2, dy2 = detection.bbox
                if (dy2 <= old_top or dy1 >= old_bottom
                        or not any(
                            max(0, min(dx2, float(line["left"]) + float(line["width"]))
                                - max(dx1, float(line["left"]))) > 0
                            for line in edge_group
                        )):
                    continue
                parent_region = None
                for region in output_regions:
                    if region.get("bubble_id") != bubble_id or region.get("region_type") != "speech_bubble":
                        continue
                    rx1, ry1, rx2, ry2 = region.get("bubble_bbox", [0, 0, 0, 0])
                    intersection = max(0, min(dx2, rx2) - max(dx1, rx1)) * max(
                        0, min(dy2, ry2) - max(dy1, ry1)
                    )
                    if intersection / max(1, min((dx2 - dx1) * (dy2 - dy1),
                                                  (rx2 - rx1) * (ry2 - ry1))) >= 0.18:
                        parent_region = region
                        break
                if parent_region is None:
                    continue

                # Use the detector's tight box: added background changed
                # glyph recognition in saved edge-clipped cases and can pull
                # neighboring art into the crop.
                left, top = max(0, dx1), max(0, dy1)
                right, bottom = min(width, dx2), min(height, dy2)
                crop = image[top:bottom, left:right].copy()
                if crop.size == 0:
                    continue
                try:
                    result = self._get_engine()(crop)
                except Exception:
                    LOGGER.debug("RT-DETR bubble crop OCR retry failed", exc_info=True)
                    continue
                if result is None or result.boxes is None:
                    continue
                recovered = []
                for box, text, score in zip(result.boxes, result.txts or [], result.scores or []):
                    text = str(text).strip()
                    try:
                        confidence = float(score)
                        points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
                    except (TypeError, ValueError):
                        continue
                    if (not text or confidence < max(self.min_confidence, 0.70)
                            or not np.isfinite(points).all() or len(points) < 4
                            or not self._is_english_source_text(text)
                            or not self._looks_like_dialogue(text)):
                        continue
                    points[:, 0] += left
                    points[:, 1] += top
                    points[:, 0] = np.clip(points[:, 0], 0, width - 1)
                    points[:, 1] = np.clip(points[:, 1], 0, height - 1)
                    bx1, by1 = float(points[:, 0].min()), float(points[:, 1].min())
                    bx2, by2 = float(points[:, 0].max()), float(points[:, 1].max())
                    recovered.append({
                        "text": text, "left": bx1, "top": by1,
                        "width": bx2 - bx1, "height": by2 - by1,
                        "confidence": confidence, "ocr_polygon": points.tolist(),
                        "region_type": "external_text", "angle": 0.0,
                    })
                recovered.sort(key=lambda item: (item["top"], item["left"]))
                if len(recovered) < len(edge_group):
                    continue
                candidate_text = " ".join(line["text"] for line in recovered)
                old_valid, old_total = self._english_word_counts(old_text)
                new_valid, new_total = self._english_word_counts(candidate_text)
                average_confidence = sum(line["confidence"] for line in recovered) / len(recovered)
                if (new_valid < old_valid + 1 or new_valid / max(1, new_total)
                        < old_valid / max(1, old_total) or average_confidence < 0.80
                        or len(candidate_text) < len(old_text) * 1.05):
                    continue

                replacement = {
                    "text": candidate_text, "region_type": "external_text",
                    "left": min(line["left"] for line in recovered),
                    "top": min(line["top"] for line in recovered),
                    "width": max(line["left"] + line["width"] for line in recovered)
                            - min(line["left"] for line in recovered),
                    "height": max(line["top"] + line["height"] for line in recovered)
                             - min(line["top"] for line in recovered),
                    "confidence": average_confidence,
                    "ocr_boxes": [[line["left"], line["top"], line["width"], line["height"]]
                                  for line in recovered],
                    "ocr_polygons": [line["ocr_polygon"] for line in recovered],
                    "angle": 0.0,
                    "ocr_recovered": True,
                }
                old_bounds = (
                    float(parent_region["left"]), float(parent_region["top"]),
                    float(parent_region["left"]) + float(parent_region["width"]),
                    float(parent_region["top"]) + float(parent_region["height"]),
                )
                output_regions.remove(parent_region)
                output_regions.append(replacement)
                output_lines = [
                    line for line in output_lines
                    if not (line.get("bubble_id") == bubble_id
                            and max(0.0, min(old_bounds[2], float(line["left"]) + float(line["width"]))
                                    - max(old_bounds[0], float(line["left"])))
                            * max(0.0, min(old_bounds[3], float(line["top"]) + float(line["height"]))
                                  - max(old_bounds[1], float(line["top"]))) > 0)
                ]
                output_lines.extend(recovered)
                recovered_count += len(recovered)
                break

        bubble_count = sum(item.label == "bubble" for item in detections)
        return (output_lines, output_regions, detector_elapsed,
                attempted_count, bubble_count, recovered_count,
                edge_recovery_seconds, edge_recovery_count)

    def _recover_adjacent_bubble_continuations(self, image, bubbles, lines, regions):
        """Retry a partial bubble crop when nearby OCR proves its continuation.

        Bubble segmentation can retain only the first line of a tall balloon,
        leaving the remaining lines just outside its mask. When OCR found an
        adjacent continuation, re-read the combined tight rectangle and accept
        it only if it preserves every recognized word and adds at least two
        confidently recognized words.
        """
        started = perf_counter()
        height, width = image.shape[:2]
        output_lines = list(lines)
        output_regions = list(regions)
        recovered_count = 0

        def box_of(item):
            left, top = float(item["left"]), float(item["top"])
            return left, top, left + float(item["width"]), top + float(item["height"])

        def words(text):
            return re.findall(r"[A-Z]+(?:'[A-Z]+)?|\d+", str(text).upper().replace("’", "'"))

        def contains_in_order(container, required):
            cursor = 0
            for token in container:
                if cursor < len(required) and token == required[cursor]:
                    cursor += 1
            return cursor == len(required)

        for bubble_id, bubble in enumerate(bubbles, 1):
            bubble_regions = [
                region for region in output_regions
                if region.get("region_type") == "speech_bubble"
                and region.get("bubble_id") == bubble_id
            ]
            if not bubble_regions:
                continue
            x1, y1, x2, y2 = bubble.bbox
            bubble_width = max(1, x2 - x1)
            bubble_lines = [line for line in output_lines if line.get("bubble_id") == bubble_id]
            edge_lines = []
            for line in bubble_lines:
                _, line_top, _, line_bottom = box_of(line)
                edge_tolerance = max(8.0, float(line["height"]) * 0.20)
                if (line_top <= y1 + edge_tolerance
                        or line_bottom >= y2 - edge_tolerance):
                    edge_lines.append(line)
            if not edge_lines:
                continue

            adjacent = []
            for external in output_regions:
                if external.get("region_type") != "external_text":
                    continue
                ex1, ey1, ex2, ey2 = box_of(external)
                overlap_x = max(0.0, min(float(x2), ex2) - max(float(x1), ex1))
                overlap_ratio = overlap_x / max(1.0, min(float(bubble_width), ex2 - ex1))
                if overlap_ratio < 0.55:
                    continue

                for line in edge_lines:
                    _, line_top, _, line_bottom = box_of(line)
                    line_gap_limit = max(36.0, float(line["height"]) * 0.90)
                    if line_bottom >= y2 - max(8.0, float(line["height"]) * 0.20):
                        gap = max(0.0, ey1 - float(y2))
                        if ey2 <= y1 or gap > line_gap_limit:
                            continue
                        adjacent.append((gap, external, line))
                    elif line_top <= y1 + max(8.0, float(line["height"]) * 0.20):
                        gap = max(0.0, float(y1) - ey2)
                        if ey1 >= y2 or gap > line_gap_limit:
                            continue
                        adjacent.append((gap, external, line))
            if not adjacent:
                continue

            # A single nearest continuation avoids merging a neighboring
            # narration box or a second utterance into this retry.
            _gap, external_region, edge_line = min(adjacent, key=lambda item: item[0])
            parent_region = min(
                bubble_regions,
                key=lambda region: abs(
                    float(region["top"]) + float(region["height"]) / 2
                    - (float(edge_line["top"]) + float(edge_line["height"]) / 2)
                ),
            )
            old_text_parts = [parent_region.get("text", ""), external_region.get("text", "")]
            old_tokens = words(" ".join(old_text_parts))
            if len(old_tokens) < 3:
                continue

            parent_box = box_of(parent_region)
            external_box = box_of(external_region)
            crop_left = min(parent_box[0], external_box[0])
            crop_top = min(parent_box[1], external_box[1])
            crop_right = max(parent_box[2], external_box[2])
            crop_bottom = max(parent_box[3], external_box[3])
            padding = max(12, int(max(crop_right - crop_left, crop_bottom - crop_top) * 0.06))
            left = max(0, int(crop_left) - padding)
            top = max(0, int(crop_top) - padding)
            right = min(width, int(np.ceil(crop_right)) + padding)
            bottom = min(height, int(np.ceil(crop_bottom)) + padding)
            crop = image[top:bottom, left:right].copy()
            if crop.size == 0:
                continue
            try:
                result = self._get_engine()(crop)
            except Exception:
                LOGGER.debug("Adjacent bubble crop OCR retry failed", exc_info=True)
                continue
            if result is None or result.boxes is None:
                continue

            recovered = []
            for points, text, confidence_value in zip(
                    result.boxes, result.txts or [], result.scores or []):
                text = str(text or "").strip()
                try:
                    confidence = float(confidence_value)
                    polygon = np.asarray(points, dtype=np.float32).reshape(-1, 2)
                except (TypeError, ValueError):
                    continue
                if (not text or confidence < 0.78 or len(polygon) < 4
                        or not np.isfinite(polygon).all()
                        or not self._is_english_source_text(text)):
                    continue
                polygon[:, 0] = np.clip(polygon[:, 0] + left, 0, width - 1)
                polygon[:, 1] = np.clip(polygon[:, 1] + top, 0, height - 1)
                bx1, by1 = float(polygon[:, 0].min()), float(polygon[:, 1].min())
                bx2, by2 = float(polygon[:, 0].max()), float(polygon[:, 1].max())
                recovered.append({
                    "text": text, "left": bx1, "top": by1,
                    "width": bx2 - bx1, "height": by2 - by1,
                    "confidence": confidence, "ocr_polygon": polygon.tolist(),
                })
            recovered.sort(key=lambda line: (line["top"], line["left"]))
            candidate_text = " ".join(line["text"] for line in recovered)
            candidate_tokens = words(candidate_text)
            if (not recovered or not contains_in_order(candidate_tokens, old_tokens)
                    or len(candidate_tokens) < len(old_tokens) + 2
                    or sum(line["confidence"] for line in recovered) / len(recovered) < 0.82):
                continue

            removed_regions = [parent_region, external_region]
            output_regions = [region for region in output_regions
                              if all(region is not removed for removed in removed_regions)]
            source_boxes = [parent_box, external_box]
            retained_lines = []
            for line in output_lines:
                if line.get("bubble_id") == bubble_id:
                    lx1, ly1, lx2, ly2 = box_of(line)
                    if any(max(0.0, min(lx2, bx2) - max(lx1, bx1))
                           * max(0.0, min(ly2, by2) - max(ly1, by1))
                           / max(1.0, min((lx2 - lx1) * (ly2 - ly1),
                                          (bx2 - bx1) * (by2 - by1))) >= 0.50
                           for bx1, by1, bx2, by2 in source_boxes):
                        continue
                elif line.get("bubble_id") is None:
                    lx1, ly1, lx2, ly2 = box_of(line)
                    if any(max(0.0, min(lx2, bx2) - max(lx1, bx1))
                           * max(0.0, min(ly2, by2) - max(ly1, by1))
                           / max(1.0, min((lx2 - lx1) * (ly2 - ly1),
                                          (bx2 - bx1) * (by2 - by1))) >= 0.50
                           for bx1, by1, bx2, by2 in source_boxes):
                        continue
                retained_lines.append(line)
            output_lines = retained_lines + recovered

            recovered.sort(key=lambda line: line["top"])
            leftmost = min(line["left"] for line in recovered)
            topmost = min(line["top"] for line in recovered)
            rightmost = max(line["left"] + line["width"] for line in recovered)
            bottommost = max(line["top"] + line["height"] for line in recovered)
            output_regions.append({
                "text": candidate_text, "region_type": "external_text",
                "left": leftmost, "top": topmost,
                "width": rightmost - leftmost, "height": bottommost - topmost,
                "confidence": sum(line["confidence"] for line in recovered) / len(recovered),
                "bubble_id": bubble_id, "ocr_boxes": [
                    [line["left"], line["top"], line["width"], line["height"]]
                    for line in recovered
                ],
                "ocr_polygons": [line["ocr_polygon"] for line in recovered],
                "angle": 0.0, "ocr_recovered": True,
                "ocr_recovery_method": "adjacent_bubble_crop",
            })
            recovered_count += 1

        return output_lines, output_regions, perf_counter() - started, recovered_count

    def _recover_low_confidence_wide_bubble_region(self, image, lines, regions, reading_direction):
        """Retry a clipped, low-confidence dialogue bubble with one wider crop."""
        started = perf_counter()
        output_lines = list(lines)
        output_regions = list(regions)
        recovered_count = 0
        height, width = image.shape[:2]

        for region in output_regions:
            bubble_box = region.get("bubble_bbox")
            if (region.get("region_type") != "speech_bubble"
                    or not region.get("ocr_edge_crop_rescued")
                    or not bubble_box or len(bubble_box) < 4
                    or float(region.get("bubble_confidence", 1.0) or 0.0) > 0.35
                    or len(str(region.get("text", ""))) < 60):
                continue
            try:
                bx1, by1, _bx2, by2 = [float(value) for value in bubble_box[:4]]
                old_boxes = region.get("ocr_boxes") or []
                line_lefts = [float(box[0]) for box in old_boxes if len(box) >= 4]
            except (TypeError, ValueError):
                continue
            # The existing line crop already attempted edge recovery. Only
            # retry when OCR geometry still extends substantially past the
            # detector's low-confidence bubble bounds.
            if (not line_lefts or min(line_lefts) >= bx1 - 25.0):
                continue
            crop_left = 0
            crop_right = width
            crop_top = max(0, int(by1) - 40)
            crop_bottom = min(height, int(np.ceil(by2)) + 40)
            if (crop_bottom <= crop_top or
                    (crop_right - crop_left) * (crop_bottom - crop_top) > 1_000_000):
                continue
            crop = image[crop_top:crop_bottom, crop_left:crop_right].copy()
            try:
                result = self._get_engine()(crop)
            except Exception:
                LOGGER.debug("Wide clipped-bubble OCR retry failed", exc_info=True)
                continue
            boxes = getattr(result, "boxes", None)
            texts = getattr(result, "txts", None)
            scores = getattr(result, "scores", None)
            if boxes is None or texts is None or scores is None:
                continue

            recovered = []
            for polygon, text, score in zip(boxes, texts, scores):
                text = str(text or "").strip()
                try:
                    confidence = float(score)
                    points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
                except (TypeError, ValueError):
                    continue
                if (not text or not np.isfinite(confidence) or confidence < 0.90
                        or len(points) < 4 or not np.isfinite(points).all()
                        or not self._is_english_source_text(text)):
                    continue
                points[:, 0] += crop_left
                points[:, 1] += crop_top
                left, top = float(points[:, 0].min()), float(points[:, 1].min())
                right, bottom = float(points[:, 0].max()), float(points[:, 1].max())
                recovered.append({
                    "text": text, "left": left, "top": top,
                    "width": right - left, "height": bottom - top,
                    "confidence": confidence, "ocr_polygon": points.tolist(),
                    "bubble_id": region.get("bubble_id"),
                    "angle": 0.0, "ocr_recovered": True,
                    "ocr_recovery_method": "low_confidence_wide_bubble_crop",
                })
            recovered.sort(key=lambda item: (
                item["top"],
                -item["left"] if reading_direction == "rtl" else item["left"],
            ))
            candidate_text = " ".join(item["text"] for item in recovered).strip()
            old_text = str(region.get("text", "")).strip()
            old_tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", old_text.casefold())
            new_tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", candidate_text.casefold())
            old_valid, old_total = self._english_word_counts(old_text)
            new_valid, new_total = self._english_word_counts(candidate_text)
            if (len(recovered) < 4 or not old_tokens or not new_tokens
                    or sum(item["confidence"] for item in recovered) / len(recovered) < 0.94
                    or SequenceMatcher(None, old_tokens, new_tokens).ratio() < 0.86
                    or new_valid < old_valid or new_total < old_total - 1
                    or len(candidate_text) < len(old_text) * 0.90
                    or not self._looks_like_dialogue(candidate_text)
                    or candidate_text.casefold() == old_text.casefold()):
                continue

            target_boxes = [
                (float(box[0]), float(box[1]),
                 float(box[0]) + float(box[2]), float(box[1]) + float(box[3]))
                for box in old_boxes if len(box) >= 4
            ]
            retained = []
            for line in output_lines:
                if line.get("bubble_id") != region.get("bubble_id"):
                    retained.append(line)
                    continue
                line_box = (float(line["left"]), float(line["top"]),
                            float(line["left"]) + float(line["width"]),
                            float(line["top"]) + float(line["height"]))
                if not any(
                    max(0.0, min(line_box[2], target[2]) - max(line_box[0], target[0]))
                    * max(0.0, min(line_box[3], target[3]) - max(line_box[1], target[1]))
                    / max(1.0, min(
                        (line_box[2] - line_box[0]) * (line_box[3] - line_box[1]),
                        (target[2] - target[0]) * (target[3] - target[1]),
                    )) >= 0.50
                    for target in target_boxes
                ):
                    retained.append(line)
            output_lines = retained + recovered

            xs = [item["left"] for item in recovered]
            ys = [item["top"] for item in recovered]
            rights = [item["left"] + item["width"] for item in recovered]
            bottoms = [item["top"] + item["height"] for item in recovered]
            region.update({
                "text": candidate_text,
                "left": min(xs), "top": min(ys),
                "width": max(rights) - min(xs),
                "height": max(bottoms) - min(ys),
                "confidence": sum(item["confidence"] for item in recovered) / len(recovered),
                "ocr_boxes": [[item["left"], item["top"], item["width"], item["height"]]
                              for item in recovered],
                "ocr_polygons": [item["ocr_polygon"] for item in recovered],
                "ocr_recovered": True,
                "ocr_recovery_method": "low_confidence_wide_bubble_crop",
            })
            recovered_count += 1

        return output_lines, output_regions, perf_counter() - started, recovered_count

    def _recover_adjacent_fragmented_bubble_pair(self, image, lines, regions, reading_direction):
        """Re-read a suspicious bubble fragment together with its adjoining speech region.

        A segmented long bubble can leave the final few lines outside its mask.
        When those lines are flagged as structurally malformed and the adjacent
        region clearly continues the same utterance, one small raw-image crop
        can recover the complete sentence without repeating page detection.
        """
        started = perf_counter()
        output_lines = list(lines)
        output_regions = list(regions)
        recovered_count = 0
        fragment_flags = {
            "isolated_single_letter_in_prose",
            "multiple_isolated_single_letters",
            "uncommon_two_letter_fragment_in_prose",
        }

        def bounds(region):
            left, top = float(region["left"]), float(region["top"])
            return (
                left, top,
                left + float(region["width"]),
                top + float(region["height"]),
            )

        for fragment in list(output_regions):
            if (fragment.get("region_type") != "speech_bubble"
                    or fragment.get("bubble_id") is None):
                continue
            flags, _isolated = self._speech_bubble_ocr_structure_flags(
                str(fragment.get("text", ""))
            )
            if not fragment_flags.intersection(flags):
                continue

            fl, ft, fr, fb = bounds(fragment)
            fw, fh = max(1.0, fr - fl), max(1.0, fb - ft)
            neighbors = []
            for candidate in output_regions:
                if candidate is fragment or candidate.get("region_type") not in {
                    "speech_bubble", "external_text",
                }:
                    continue
                same_bubble = candidate.get("bubble_id") == fragment.get("bubble_id")
                if same_bubble:
                    if candidate.get("region_type") != "speech_bubble":
                        continue
                    confidences = [
                        item.get("bubble_confidence") for item in (fragment, candidate)
                    ]
                    if (any(value is None for value in confidences)
                            or min(float(value) for value in confidences) > 0.40):
                        continue
                cl, ct, cr, cb = bounds(candidate)
                cw, ch = max(1.0, cr - cl), max(1.0, cb - ct)
                horizontal_overlap = max(0.0, min(fr, cr) - max(fl, cl))
                horizontal_ratio = horizontal_overlap / min(fw, cw)
                if horizontal_ratio < 0.55:
                    continue
                if cb <= ft:
                    gap = ft - cb
                elif fb <= ct:
                    gap = ct - fb
                else:
                    gap = 0.0
                if gap > max(24.0, min(fh, ch) * 0.15):
                    continue
                neighbors.append((gap, -horizontal_ratio, same_bubble, candidate))
            if not neighbors:
                continue

            _gap, _negative_overlap, same_bubble_pair, neighbor = min(
                neighbors, key=lambda item: (item[0], item[1])
            )
            source_regions = sorted(
                [fragment, neighbor], key=lambda item: (float(item["top"]), float(item["left"]))
            )
            old_text = " ".join(str(item.get("text", "")) for item in source_regions).strip()
            if not self._looks_like_dialogue(old_text):
                continue

            boxes = [bounds(item) for item in source_regions]
            wide_same_bubble = (
                same_bubble_pair
                and min(float(item.get("bubble_confidence", 1.0) or 0.0)
                        for item in source_regions) <= 0.40
            )
            crop_left = 0 if wide_same_bubble else max(0, int(min(box[0] for box in boxes)) - 40)
            crop_top = max(0, int(min(box[1] for box in boxes)) - 40)
            crop_right = (image.shape[1] if wide_same_bubble else
                          min(image.shape[1], int(np.ceil(max(box[2] for box in boxes))) + 40))
            crop_bottom = min(image.shape[0], int(np.ceil(max(box[3] for box in boxes))) + 40)
            if (crop_right <= crop_left or crop_bottom <= crop_top
                    or (crop_right - crop_left) * (crop_bottom - crop_top) > 1_500_000):
                continue
            crop = image[crop_top:crop_bottom, crop_left:crop_right].copy()
            try:
                result = self._get_engine()(crop)
            except Exception:
                LOGGER.debug("Adjacent fragmented bubble crop OCR retry failed", exc_info=True)
                continue
            if result is None or result.boxes is None or result.txts is None:
                continue

            recovered = []
            scores = result.scores if result.scores is not None else []
            for polygon, text, score in zip(result.boxes, result.txts, scores):
                text = str(text or "").strip()
                try:
                    confidence = float(score)
                    points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
                except (TypeError, ValueError):
                    continue
                if (not text or len(points) < 4 or not np.isfinite(points).all()
                        or not np.isfinite(confidence) or confidence < 0.80
                        or not self._is_english_source_text(text)):
                    continue
                points[:, 0] = np.clip(points[:, 0] + crop_left, 0, image.shape[1] - 1)
                points[:, 1] = np.clip(points[:, 1] + crop_top, 0, image.shape[0] - 1)
                left, top = float(points[:, 0].min()), float(points[:, 1].min())
                right, bottom = float(points[:, 0].max()), float(points[:, 1].max())
                recovered.append({
                    "text": text, "left": left, "top": top,
                    "width": right - left, "height": bottom - top,
                    "confidence": confidence, "ocr_polygon": points.tolist(),
                    "bubble_id": fragment.get("bubble_id"),
                })
            recovered.sort(key=lambda item: (
                item["top"],
                -item["left"] if reading_direction == "rtl" else item["left"],
            ))
            candidate_text = " ".join(item["text"] for item in recovered)
            old_tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", old_text.casefold())
            new_tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", candidate_text.casefold())
            candidate_flags, _candidate_isolated = self._speech_bubble_ocr_structure_flags(
                candidate_text
            )
            if (len(recovered) < 4
                    or sum(item["confidence"] for item in recovered) / len(recovered) < 0.90
                    or len(new_tokens) < max(4, len(old_tokens) - 1)
                    or SequenceMatcher(None, old_tokens, new_tokens).ratio()
                    < (0.76 if same_bubble_pair else 0.82)
                    or len(candidate_text) < len(old_text) * 0.90
                    or not self._looks_like_dialogue(candidate_text)
                    or fragment_flags.intersection(candidate_flags)):
                continue

            region_left = min(box[0] for box in boxes)
            region_top = min(box[1] for box in boxes)
            region_right = max(box[2] for box in boxes)
            region_bottom = max(box[3] for box in boxes)
            replacement = {
                "text": candidate_text, "region_type": "speech_bubble",
                "left": region_left, "top": region_top,
                "width": region_right - region_left,
                "height": region_bottom - region_top,
                "confidence": sum(item["confidence"] for item in recovered) / len(recovered),
                "bubble_id": fragment.get("bubble_id"),
                "bubble_bbox": [region_left, region_top, region_right, region_bottom],
                "ocr_boxes": [
                    [item["left"], item["top"], item["width"], item["height"]]
                    for item in recovered
                ],
                "ocr_polygons": [item["ocr_polygon"] for item in recovered],
                "ocr_recovered": True,
                "ocr_recovery_method": "adjacent_fragmented_bubble_crop",
            }

            retained_lines = []
            for line in output_lines:
                line_box = bounds(line)
                line_area = max(
                    1.0, (line_box[2] - line_box[0]) * (line_box[3] - line_box[1])
                )
                covered = 0.0
                for box in boxes:
                    overlap = max(0.0, min(line_box[2], box[2]) - max(line_box[0], box[0])) * max(
                        0.0, min(line_box[3], box[3]) - max(line_box[1], box[1])
                    )
                    covered = max(covered, overlap / line_area)
                if covered < 0.50:
                    retained_lines.append(line)
            output_lines = retained_lines + recovered
            output_regions = [
                item for item in output_regions
                if item is not fragment and item is not neighbor
            ]
            output_regions.append(replacement)
            recovered_count += 1

        return output_lines, output_regions, perf_counter() - started, recovered_count

    def _recover_edge_clipped_bubble_lines(self, image, lines, regions, suspicious_ids):
        """Retry only OCR lines that touch a bubble mask edge and contain a fragment.

        Bubble masks can be too tight for stylized lettering.  A narrow expanded
        crop often restores the clipped glyphs without running the page detector.
        A candidate is kept only when it has strong OCR confidence, overlaps the
        original line, retains every known source word, and improves or safely
        expands an unknown fragment.  Uncertain alternatives leave the source
        untouched for the existing targeted detector/review path.
        """
        height, width = image.shape[:2]
        original_boxes = {}
        rescued = 0

        def box_iou(first, second):
            ax1, ay1, ax2, ay2 = first
            bx1, by1, bx2, by2 = second
            intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
                0.0, min(ay2, by2) - max(ay1, by1)
            )
            union = max(1.0, (ax2 - ax1) * (ay2 - ay1)
                        + (bx2 - bx1) * (by2 - by1) - intersection)
            return intersection / union

        def words(text):
            return re.findall(r"[A-Z]+(?:'[A-Z]+)?|\d+", str(text).upper().replace("â€™", "'"))

        try:
            ocr_engine = self._get_engine()
        except Exception:
            LOGGER.debug("Edge-clipped bubble OCR retry unavailable", exc_info=True)
            return lines, regions, rescued

        # One expanded crop per suspicious bubble can restore neighboring
        # clipped lines together (including a valid-looking partial word that
        # a dictionary-only trigger would miss). Keep this crop page-local.
        wide_candidates = {}
        for region in regions:
            bubble_id = region.get("bubble_id")
            if (region.get("region_type") != "speech_bubble"
                    or bubble_id not in suspicious_ids or not region.get("bubble_bbox")):
                continue
            bx1, by1, bx2, by2 = [float(value) for value in region["bubble_bbox"]]
            grouped_lines = []
            for line in lines:
                if line.get("bubble_id") != bubble_id:
                    continue
                line_box = (float(line["left"]), float(line["top"]),
                            float(line["left"]) + float(line["width"]),
                            float(line["top"]) + float(line["height"]))
                if any(box_iou(
                    (float(box[0]), float(box[1]), float(box[0]) + float(box[2]),
                     float(box[1]) + float(box[3])), line_box,
                ) >= 0.60 for box in region.get("ocr_boxes", [])):
                    grouped_lines.append(line)
            edge_lines = [line for line in grouped_lines
                          if float(line["left"]) <= bx1 + max(8, float(line["height"]) * 0.12)
                          or float(line["left"]) + float(line["width"])
                          >= bx2 - max(8, float(line["height"]) * 0.12)]
            if not edge_lines:
                continue
            expands_left = any(float(line["left"]) <= bx1 + max(8, float(line["height"]) * 0.12)
                               for line in edge_lines)
            expands_right = any(float(line["left"]) + float(line["width"])
                                >= bx2 - max(8, float(line["height"]) * 0.12)
                                for line in edge_lines)
            pad_x = max(28, int((by2 - by1) * 0.75), int(min(bx1, width * 0.20)))
            pad_y = max(12, int((by2 - by1) * 0.10))
            crop_left = max(0, int(bx1) - (pad_x if expands_left else 0))
            crop_top = max(0, int(by1) - pad_y)
            opposite_pad = max(28, int((by2 - by1) * 0.10))
            crop_right = min(width, int(np.ceil(bx2))
                             + (pad_x if expands_right else opposite_pad))
            crop_bottom = min(height, int(np.ceil(by2)) + pad_y)
            crop = image[crop_top:crop_bottom, crop_left:crop_right]
            if crop.size == 0:
                continue
            try:
                result = ocr_engine(crop)
            except Exception:
                LOGGER.debug("Expanded bubble OCR retry failed", exc_info=True)
                continue
            wide_boxes = getattr(result, "boxes", None)
            wide_texts = getattr(result, "txts", None)
            wide_scores = getattr(result, "scores", None)
            if wide_boxes is None or wide_texts is None or wide_scores is None:
                continue
            entries = []
            for points, text, score in zip(wide_boxes, wide_texts, wide_scores):
                text = str(text or "").strip()
                try:
                    score = float(score)
                    polygon = np.asarray(points, dtype=np.float32).reshape(-1, 2)
                except (TypeError, ValueError):
                    continue
                if (not text or not np.isfinite(score) or score < 0.82
                        or len(polygon) < 4 or not np.isfinite(polygon).all()
                        or not self._is_english_source_text(text)):
                    continue
                polygon[:, 0] += crop_left
                polygon[:, 1] += crop_top
                box = (float(polygon[:, 0].min()), float(polygon[:, 1].min()),
                       float(polygon[:, 0].max()), float(polygon[:, 1].max()))
                entries.append((box, text, score, polygon.tolist()))
            wide_candidates[id(region)] = entries

        for line_index, line in enumerate(lines):
            bubble_id = line.get("bubble_id")
            if bubble_id not in suspicious_ids:
                continue
            region = next((item for item in regions
                           if item.get("region_type") == "speech_bubble"
                           and item.get("bubble_id") == bubble_id
                           and any(box_iou(
                               (float(box[0]), float(box[1]), float(box[0]) + float(box[2]),
                                float(box[1]) + float(box[3])),
                               (float(line["left"]), float(line["top"]),
                                float(line["left"]) + float(line["width"]),
                                float(line["top"]) + float(line["height"])),
                           ) >= 0.60 for box in item.get("ocr_boxes", []))), None)
            if region is None:
                continue
            bx1, by1, bx2, by2 = [float(value) for value in region.get("bubble_bbox", [])]
            left, top = float(line["left"]), float(line["top"])
            right, bottom = left + float(line["width"]), top + float(line["height"])
            edge_margin = max(8.0, float(line["height"]) * 0.12)
            touches_left = left <= bx1 + edge_margin
            touches_right = right >= bx2 - edge_margin
            if not (touches_left or touches_right):
                continue

            old_words = words(line.get("text", ""))
            old_valid, old_total = self._english_word_counts(line.get("text", ""))
            if old_total < 1 or (old_valid >= old_total and not wide_candidates.get(id(region))):
                continue

            horizontal_pad = max(28, int(float(line["height"]) * 1.40))
            near_pad = max(12, int(float(line["height"]) * 0.35))
            crop_left = max(0, int(left - (horizontal_pad if touches_left else near_pad)))
            crop_right = min(width, int(np.ceil(right + (horizontal_pad if touches_right else near_pad))))
            vertical_pad = max(8, int(float(line["height"]) * 0.18))
            crop_top = max(0, int(top - vertical_pad))
            crop_bottom = min(height, int(np.ceil(bottom + vertical_pad)))
            crop = image[crop_top:crop_bottom, crop_left:crop_right]
            if crop.size == 0:
                continue
            crop = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            old_box = (left, top, right, bottom)
            candidates = []
            for candidate in wide_candidates.get(id(region), []):
                candidate_box = candidate[0]
                vertical_overlap = max(0.0, min(old_box[3], candidate_box[3])
                                      - max(old_box[1], candidate_box[1]))
                vertical_ratio = vertical_overlap / max(
                    1.0, min(old_box[3] - old_box[1], candidate_box[3] - candidate_box[1])
                )
                if vertical_ratio >= 0.45 and box_iou(candidate_box, old_box) >= 0.12:
                    candidates.append(candidate)

            if not candidates:
                try:
                    result = ocr_engine(crop)
                except Exception:
                    LOGGER.debug("Expanded edge-line OCR retry failed", exc_info=True)
                    continue
                boxes = getattr(result, "boxes", None)
                texts = getattr(result, "txts", None)
                scores = getattr(result, "scores", None)
                if boxes is None or texts is None or scores is None:
                    continue
            else:
                boxes, texts, scores = [], [], []

            for points, text, score in zip(boxes, texts, scores):
                text = str(text or "").strip()
                try:
                    score = float(score)
                    polygon = np.asarray(points, dtype=np.float32).reshape(-1, 2)
                except (TypeError, ValueError):
                    continue
                if (not text or not np.isfinite(score) or score < 0.82
                        or len(polygon) < 4 or not np.isfinite(polygon).all()
                        or not self._is_english_source_text(text)):
                    continue
                polygon[:, 0] = polygon[:, 0] / 2.0 + crop_left
                polygon[:, 1] = polygon[:, 1] / 2.0 + crop_top
                candidate_box = (
                    float(polygon[:, 0].min()), float(polygon[:, 1].min()),
                    float(polygon[:, 0].max()), float(polygon[:, 1].max()),
                )
                # Keep only detections aligned with this source line; wider
                # crops may also see panel numbers or neighboring captions.
                if box_iou(candidate_box, old_box) < 0.12:
                    continue
                candidates.append((candidate_box, text, score, polygon.tolist()))
            candidates.sort(key=lambda item: item[0][0])
            if not candidates:
                continue

            candidate_text = " ".join(item[1] for item in candidates)
            candidate_words = words(candidate_text)
            new_valid, new_total = self._english_word_counts(candidate_text)
            if not candidate_words or new_total == 0:
                continue
            # Keep all correctly recognized words in order. An unknown edge
            # fragment may be extended by the crop (ETED -> COMPLETED, VE ->
            # YOU'VE), but a wholly different reading is never substituted.
            known_old = []
            spellchecker = _SPELLCHECKER
            if spellchecker is not None:
                known_old = [word.replace("'", "").lower() for word in old_words
                             if len(word.replace("'", "")) >= 2
                             and word.replace("'", "").lower() in spellchecker]
            candidate_normalized = [word.replace("'", "").lower() for word in candidate_words]
            cursor = 0
            for token in candidate_normalized:
                if (cursor < len(known_old)
                        and (token == known_old[cursor]
                             or (len(known_old[cursor]) >= 2
                                 and known_old[cursor] in token
                                 and len(token) >= len(known_old[cursor]) + 2))):
                    cursor += 1
            if cursor != len(known_old):
                continue
            expanded_fragment = any(
                len(old.replace("'", "")) >= 2
                and any(old.replace("'", "") in new.replace("'", "")
                        for new in candidate_words if len(new.replace("'", ""))
                        >= len(old.replace("'", "")) + 2)
                for old in old_words
            )
            if (new_valid < old_valid or new_total < old_total
                    or (new_valid == old_valid and not expanded_fragment)
                    or sum(item[2] for item in candidates) / len(candidates) < 0.86):
                continue

            old_signature = (float(line["left"]), float(line["top"]),
                             float(line["left"]) + float(line["width"]),
                             float(line["top"]) + float(line["height"]))
            original_boxes[line_index] = old_signature
            points = np.asarray([point for item in candidates for point in item[3]], dtype=np.float32)
            new_left, new_top = float(points[:, 0].min()), float(points[:, 1].min())
            new_right, new_bottom = float(points[:, 0].max()), float(points[:, 1].max())
            line.update({
                "text": candidate_text,
                "left": round(new_left, 2), "top": round(new_top, 2),
                "width": round(new_right - new_left, 2),
                "height": round(new_bottom - new_top, 2),
                "confidence": round(sum(item[2] for item in candidates) / len(candidates), 4),
                "ocr_polygon": [[new_left, new_top], [new_right, new_top],
                                [new_right, new_bottom], [new_left, new_bottom]],
                "ocr_edge_crop_rescued": True,
            })
            rescued += 1

        if rescued:
            # Keep region geometry and line-level geometry in sync so downstream
            # JSON review and typesetting never refer to the obsolete clipped box.
            for region in regions:
                if region.get("region_type") != "speech_bubble":
                    continue
                region_lines = []
                new_boxes, new_polygons = [], []
                for box in region.get("ocr_boxes", []):
                    target = (float(box[0]), float(box[1]),
                              float(box[0]) + float(box[2]), float(box[1]) + float(box[3]))
                    match = None
                    for index, line in enumerate(lines):
                        if line.get("bubble_id") != region.get("bubble_id"):
                            continue
                        source = original_boxes.get(index)
                        if source is None:
                            source = (float(line["left"]), float(line["top"]),
                                      float(line["left"]) + float(line["width"]),
                                      float(line["top"]) + float(line["height"]))
                        if box_iou(target, source) >= 0.60:
                            match = line
                            break
                    if match is None:
                        new_boxes.append(box)
                        new_polygons.append(None)
                        continue
                    new_boxes.append([match["left"], match["top"], match["width"], match["height"]])
                    new_polygons.append(match.get("ocr_polygon"))
                    region_lines.append(match)
                if not region_lines:
                    continue
                region["ocr_boxes"] = new_boxes
                region["ocr_polygons"] = [polygon if polygon is not None else existing
                                          for polygon, existing in zip(
                                              new_polygons, region.get("ocr_polygons", []))]
                region["text"] = " ".join(line["text"] for line in sorted(
                    region_lines, key=lambda item: (item["top"], item["left"])
                ))
                region["confidence"] = round(sum(line["confidence"] for line in region_lines)
                                               / len(region_lines), 4)
                region["ocr_edge_crop_rescued"] = True
                region["ocr_recovery_method"] = "expanded_bubble_edge_crop"

        return lines, regions, rescued

    @staticmethod
    def _has_truncated_external_tail(text):
        """Identify long OCR groups ending in an implausible 1–2 letter word."""
        value = str(text or "").strip()
        if len(value) < 35 or not value or value[-1] in ".!?…'\"”’":
            return False
        words = re.findall(r"[A-Za-z]+", value)
        if len(words) < 5:
            return False
        final_word = words[-1].casefold()
        common_short_words = {
            "a", "i", "am", "an", "as", "at", "be", "by", "do", "go",
            "he", "if", "in", "is", "it", "me", "my", "no", "of", "oh",
            "on", "or", "so", "to", "up", "us", "we",
        }
        return len(final_word) <= 2 and final_word not in common_short_words

    def _recover_truncated_external_bubble_region(self, image, region, bubbles):
        """Re-read one malformed external OCR group when it lies in a bubble."""
        if region.get("region_type") != "external_text" or not self._has_truncated_external_tail(
            region.get("text", "")
        ):
            return None
        left, top = float(region["left"]), float(region["top"])
        right = left + float(region["width"])
        bottom = top + float(region["height"])
        region_area = max(1.0, (right - left) * (bottom - top))
        best = None
        best_overlap = 0.0
        for bubble in bubbles:
            bx1, by1, bx2, by2 = bubble.bbox
            overlap_w = max(0.0, min(right, bx2) - max(left, bx1))
            overlap_h = max(0.0, min(bottom, by2) - max(top, by1))
            overlap = (overlap_w * overlap_h) / region_area
            if overlap > best_overlap:
                best, best_overlap = bubble, overlap
        if best is None or best_overlap < 0.45:
            return None

        bx1, by1, bx2, by2 = best.bbox
        height, width = image.shape[:2]
        crop_left = max(0, int(min(left, bx1) - 24))
        crop_top = max(0, int(min(top, by1) - 24))
        crop_right = min(width, int(np.ceil(max(right, bx2) + 24)))
        crop_bottom = min(height, int(np.ceil(max(bottom, by2) + 24)))
        crop = image[crop_top:crop_bottom, crop_left:crop_right]
        if crop.size == 0:
            return None
        try:
            result = self._get_engine()(crop)
        except Exception:
            LOGGER.debug("Truncated external bubble OCR retry failed", exc_info=True)
            return None
        if result is None or result.boxes is None or result.txts is None:
            return None

        recovered = []
        scores = result.scores if result.scores is not None else []
        for box, text, score in zip(result.boxes, result.txts, scores):
            text = str(text).strip()
            try:
                confidence = float(score)
                points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
            except (TypeError, ValueError):
                continue
            if (not text or len(points) < 4 or not np.isfinite(points).all()
                    or not np.isfinite(confidence)):
                continue
            points[:, 0] += crop_left
            points[:, 1] += crop_top
            left_x, top_y = float(points[:, 0].min()), float(points[:, 1].min())
            right_x, bottom_y = float(points[:, 0].max()), float(points[:, 1].max())
            recovered.append({
                "text": text, "left": left_x, "top": top_y,
                "width": right_x - left_x, "height": bottom_y - top_y,
                "confidence": confidence, "ocr_polygon": points.tolist(),
                "region_type": "speech_bubble",
            })
        recovered.sort(key=lambda line: (line["top"], line["left"]))
        if not recovered:
            return None
        old_text = " ".join(str(region.get("text", "")).casefold().split())
        new_text = " ".join(line["text"] for line in recovered)
        old_words = re.findall(r"[a-z]+", old_text)
        new_words = re.findall(r"[a-z]+", new_text.casefold())
        stable_prefix = " ".join(old_words[:-1])
        recovered_prefix = " ".join(new_words[:len(old_words) - 1])
        if (len(new_text) < len(str(region.get("text", ""))) + 8
                or not stable_prefix
                or SequenceMatcher(None, stable_prefix, recovered_prefix).ratio() < 0.88
                or not self._looks_like_dialogue(new_text)):
            return None
        return recovered, best

    def extract(self, image_bytes: bytes, reading_direction: str = "ltr",
                include_external: bool = True,
                recover_external_bubble_classification: bool = True) -> dict:
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise ValueError("Image data must be non-empty bytes.")
        if reading_direction not in {"ltr", "rtl"}:
            raise ValueError("reading_direction must be 'ltr' or 'rtl'.")

        started = perf_counter()
        image = decode_color_image(image_bytes)
        if image is None:
            raise ValueError("تعذر فتح صورة الصفحة.")
        bubble_detection_started = perf_counter()
        relaxed_bubbles = []
        try:
            if include_external and recover_external_bubble_classification:
                bubbles, relaxed_bubbles = detect_speech_bubbles(
                    image,
                    PRIMARY_BUBBLE_CONFIDENCE,
                    secondary_confidence_threshold=RELAXED_BUBBLE_CONFIDENCE,
                )
            else:
                bubbles = detect_speech_bubbles(image)
        except RuntimeError:
            if not include_external:
                raise
            # External Manhwa lettering can still be translated even when the
            # optional bubble segmentation model is unavailable.
            bubbles = []

        bubble_detection_elapsed = perf_counter() - bubble_detection_started
        # The secondary comic detector is restricted to large, strongly
        # concave bubble masks. This avoids a full-page RT-DETR pass while
        # allowing confident local proposals to split clearly merged balloons.
        ocr_bubbles = list(enumerate(bubbles, 1))
        bubble_split_detector_seconds = 0.0
        bubble_split_count = 0
        if bubbles:
            (ocr_bubbles, bubble_split_detector_seconds,
             bubble_split_count) = self._refine_suspicious_bubble_splits(image, bubbles)

        lines = []
        regions = []
        bubble_ids_with_primary_ocr = set()
        malformed_bubble_recognition_seconds = 0.0
        malformed_bubble_recognition_count = 0
        bubble_ocr_started = perf_counter()
        for bubble_id, bubble in ocr_bubbles:
            x1, y1, x2, y2 = bubble.bbox
            crop = image[y1:y2, x1:x2].copy()
            safe = bubble.safe_mask
            crop[safe == 0] = 255
            # Small page-fit screenshots and narrow balloons often leave OCR
            # glyphs below 12 pixels high. Upscale only those crops (bounded
            # at 3x) so the detector/recognizer can see small dialogue without
            # running a costly second full-page OCR pass.
            scale = min(3.0, 160.0 / max(crop.shape[:2])) if max(crop.shape[:2]) < 80 else 1.0
            if scale > 1.0:
                crop = cv2.resize(
                    crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
                )
            # RapidOCR accepts OpenCV arrays directly. Avoid PNG encode/decode
            # for every bubble; this is especially costly on long webtoon pages.
            engine = self._get_engine()
            result = engine(crop)
            raw_scores = getattr(result, "scores", None) if result is not None else None
            scores = np.asarray(raw_scores if raw_scores is not None else [], dtype=np.float32)
            finite_scores = scores[np.isfinite(scores)]
            original_usable = finite_scores[finite_scores >= self.min_confidence]
            if not len(finite_scores) or float(np.median(finite_scores)) < 0.62:
                # Low-contrast lettering can disappear in the source-color crop.
                # Retry only uncertain balloons with local contrast enhancement;
                # normal bubbles keep the single-pass path and latency.
                try:
                    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    enhanced = cv2.createCLAHE(
                        clipLimit=2.0, tileGridSize=(8, 8)
                    ).apply(gray)
                    alternate = engine(cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR))
                    raw_alternate_scores = (
                        getattr(alternate, "scores", None) if alternate is not None else None
                    )
                    alternate_scores = np.asarray(
                        raw_alternate_scores if raw_alternate_scores is not None else [],
                        dtype=np.float32,
                    )
                    alternate_scores = alternate_scores[np.isfinite(alternate_scores)]
                    alternate_usable = alternate_scores[
                        alternate_scores >= self.min_confidence
                    ]
                    original_rank = (
                        len(original_usable),
                        float(np.median(original_usable)) if len(original_usable) else 0.0,
                    )
                    alternate_rank = (
                        len(alternate_usable),
                        float(np.median(alternate_usable)) if len(alternate_usable) else 0.0,
                    )
                    if alternate is not None and alternate_rank > original_rank:
                        result = alternate
                except Exception:
                    LOGGER.debug("Contrast-enhanced bubble OCR retry failed", exc_info=True)
            if result is None or result.boxes is None:
                continue
            bubble_lines = []
            for box, text, score in zip(result.boxes, result.txts, result.scores):
                text = str(text).strip()
                try:
                    confidence = float(score)
                    points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
                except (TypeError, ValueError):
                    continue
                if (not text or self._is_chapter_marker(text) or not np.isfinite(confidence)
                        or not np.isfinite(points).all() or len(points) < 4
                        or confidence < self.min_confidence
                        or not self._is_english_source_text(text)):
                    continue
                xs = points[:, 0] / scale
                ys = points[:, 1] / scale
                polygon = [
                    [x1 + float(point[0]), y1 + float(point[1])]
                    for point in zip(xs, ys)
                ]
                left, top = x1 + float(xs.min()), y1 + float(ys.min())
                right, bottom = x1 + float(xs.max()), y1 + float(ys.max())
                if right <= left or bottom <= top:
                    continue
                ix1, iy1 = max(0, int(left)), max(0, int(top))
                ix2, iy2 = min(image.shape[1], int(np.ceil(right))), min(image.shape[0], int(np.ceil(bottom)))
                overlap_left, overlap_top = max(ix1, x1), max(iy1, y1)
                overlap_right, overlap_bottom = min(ix2, x2), min(iy2, y2)
                if overlap_right <= overlap_left or overlap_bottom <= overlap_top:
                    continue
                box_mask = bubble.safe_mask[
                    overlap_top - y1:overlap_bottom - y1,
                    overlap_left - x1:overlap_right - x1,
                ]
                box_area = max(1, (ix2 - ix1) * (iy2 - iy1))
                # Narrow balloons have little safe interior after mask erosion;
                # the crop was already whitened outside the mask, so accept a
                # slightly lower overlap here to retain tiny interjections.
                if np.count_nonzero(box_mask) / box_area < 0.5:
                    continue
                bubble_lines.append({
                    "text": text, "left": round(left, 2), "top": round(top, 2),
                    "width": round(right - left, 2), "height": round(bottom - top, 2),
                    "confidence": round(confidence, 4), "bubble_id": bubble_id,
                    "ocr_polygon": polygon,
                })
            malformed_before = [line.get("text") for line in bubble_lines]
            malformed_started = perf_counter()
            bubble_lines = self._rescue_malformed_bubble_lines(image, bubble_lines)
            bubble_lines = self._rescue_large_stylized_bubble_lines(image, bubble_lines)
            bubble_lines = self._rescue_terminal_short_fragment(image, bubble_lines)
            malformed_bubble_recognition_seconds += perf_counter() - malformed_started
            malformed_bubble_recognition_count += sum(
                old_text != new_line.get("text")
                for old_text, new_line in zip(malformed_before, bubble_lines)
            )
            bubble_lines = self._rescue_fragmented_bubble_line_pairs(image, bubble_lines)
            raw_crop = None
            if self._needs_unmasked_medium_bubble_retry(bubble_lines):
                # Allocate the padded source crop only for bubbles that pass
                # the same strong structural cue gate used by the retry itself.
                retry_pad = max(12, round((y2 - y1) * 0.08))
                retry_x1, retry_y1 = max(0, x1 - retry_pad), max(0, y1 - retry_pad)
                retry_x2 = min(image.shape[1], x2 + retry_pad)
                retry_y2 = min(image.shape[0], y2 + retry_pad)
                raw_crop = image[retry_y1:retry_y2, retry_x1:retry_x2].copy()
                if scale > 1.0:
                    raw_crop = cv2.resize(
                        raw_crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
                    )
            bubble_lines = self._rescue_bubble_lines(crop, bubble_lines, raw_crop)
            bubble_lines = self._rescue_sparse_bubble_lines(
                crop, bubble_lines, origin=(x1, y1), scale=scale
            )
            bubble_lines.sort(key=lambda line: (
                line["top"],
                -line["left"] if reading_direction == "rtl" else line["left"],
            ))
            bubble_lines = self._remove_trailing_orphan_tokens(bubble_lines)
            if not bubble_lines:
                continue
            if not self._looks_like_dialogue(" ".join(line["text"] for line in bubble_lines)):
                continue
            bubble_ids_with_primary_ocr.add(bubble_id)
            lines.extend(bubble_lines)
            clusters = self._split_bubble_columns(bubble_lines, x1, x2, y1, y2)
            for cluster in clusters:
                # Some segmenters join two overlapping neighbor bubbles. Split
                # only clearly separated text columns, then constrain OCR and
                # typesetting to each half of the shared mask.
                clip_left, clip_top, clip_right, clip_bottom = cluster["clip"]
                cluster_lines = cluster["lines"]
                region_box = self._mask_bbox(
                    bubble.safe_mask, (clip_left, clip_top, clip_right, clip_bottom), (x1, y1)
                )
                if region_box is None:
                    continue
                region_left, region_top, region_right, region_bottom = region_box
                region = {
                    "text": " ".join(line["text"] for line in cluster_lines),
                    "region_type": "speech_bubble",
                    "left": region_left, "top": region_top,
                    "width": region_right - region_left, "height": region_bottom - region_top,
                    "confidence": round(sum(line["confidence"] for line in cluster_lines) / len(cluster_lines), 4),
                    "bubble_id": bubble_id,
                    "bubble_confidence": round(bubble.confidence, 4),
                    "bubble_bbox": list(region_box),
                    "ocr_boxes": [[line["left"], line["top"], line["width"], line["height"]] for line in cluster_lines],
                    "ocr_polygons": [line["ocr_polygon"] for line in cluster_lines],
                }
                regions.append(region)

        bubble_ocr_elapsed = perf_counter() - bubble_ocr_started
        bubble_elapsed = (bubble_detection_elapsed + bubble_split_detector_seconds
                          + bubble_ocr_elapsed)
        external_started = perf_counter()
        external_lines = []
        external_annotations = []
        if include_external:
            external_lines, external_annotations = self._extract_external_lines(
                image, bubbles, reading_direction, include_annotations=True,
            )
        external_elapsed = perf_counter() - external_started
        if include_external and external_lines and recover_external_bubble_classification:
            relaxed_bubbles = _relaxed_bubble_candidates(bubbles, relaxed_bubbles)

        detector_elapsed = 0.0
        adjacent_fragment_recovery_seconds = 0.0
        adjacent_fragment_recovery_count = 0
        truncated_external_recovery_seconds = 0.0
        truncated_external_recovery_count = 0
        if include_external and external_lines:
            external_regions = self.group_lines(external_lines)
            for region in external_regions:
                members = [external_lines[index] for index in region.pop("line_indexes")]
                group_angle = float(np.median([line.get("angle", 0.0) for line in members]))
                members = self._refine_rotated_external_group(
                    image, members, group_angle, bubbles,
                )
                left = min(line["left"] for line in members)
                top = min(line["top"] for line in members)
                right = max(line["left"] + line["width"] for line in members)
                bottom = max(line["top"] + line["height"] for line in members)
                region.update({
                    "text": " ".join(line["text"] for line in members),
                    "left": round(left, 2), "top": round(top, 2),
                    "width": round(right - left, 2), "height": round(bottom - top, 2),
                    "confidence": round(sum(line["confidence"] for line in members) / len(members), 4),
                    "region_type": "external_text",
                    "ocr_boxes": [[line["left"], line["top"], line["width"], line["height"]]
                                  for line in members],
                    "ocr_polygons": [line["ocr_polygon"] for line in members],
                    "angle": round(float(np.median([line.get("angle", 0.0) for line in members])), 2),
                })
                best_candidate = None
                best_overlap = 0.0
                for candidate in relaxed_bubbles:
                    overlap = _bubble_region_overlap_fraction(region, candidate)
                    if overlap > best_overlap:
                        best_candidate = candidate
                        best_overlap = overlap
                if best_candidate is not None and best_overlap >= 0.20:
                    region.update({
                        "region_type": "speech_bubble",
                        "bubble_bbox": list(best_candidate.bbox),
                        "bubble_confidence": round(best_candidate.confidence, 4),
                        "bubble_classification_recovered": True,
                    })
                self._add_external_ocr_review_hint(region)
                low_confidence_unknowns = 0
                for line in members:
                    if float(line.get("confidence", 0.0) or 0.0) >= 0.80:
                        continue
                    valid_words, total_words = self._english_word_counts(line.get("text", ""))
                    low_confidence_unknowns += max(0, total_words - valid_words)
                if low_confidence_unknowns:
                    region["ocr_review_required"] = True
                    region["ocr_review_reason"] = "low_confidence_external_ocr_line"
                    region["ocr_unrecognized_word_count"] = low_confidence_unknowns
                if not self._region_duplicates(region, regions):
                    lines.extend(members)
                    regions.append(region)

        (lines, regions, adjacent_recovery_seconds,
         adjacent_recovery_count) = self._recover_adjacent_bubble_continuations(
            image, bubbles, lines, regions
        )
        (lines, regions, detector_elapsed, detector_region_count,
         detector_bubble_count, detector_ocr_line_count,
         edge_crop_recovery_seconds, edge_crop_recovery_count) = self._recover_clipped_bubble_text(
            image, bubbles, lines, regions, reading_direction
        )
        (lines, regions, adjacent_fragment_recovery_seconds,
         adjacent_fragment_recovery_count) = self._recover_adjacent_fragmented_bubble_pair(
            image, lines, regions, reading_direction
        )
        (lines, regions, wide_bubble_recovery_seconds,
         wide_bubble_recovery_count) = self._recover_low_confidence_wide_bubble_region(
            image, lines, regions, reading_direction
        )

        # Some secondary detector crops produce external-text regions only
        # after the primary grouping pass, so apply this narrow retry after all
        # bubble and edge recoveries have finished.
        for region in regions:
            if (region.get("region_type") != "external_text"
                    or not self._has_truncated_external_tail(region.get("text", ""))):
                continue
            recovery_started = perf_counter()
            recovered_result = self._recover_truncated_external_bubble_region(
                image, region, bubbles,
            )
            truncated_external_recovery_seconds += perf_counter() - recovery_started
            if recovered_result is None:
                continue
            recovered_lines, recovered_bubble = recovered_result
            old_left, old_top = float(region["left"]), float(region["top"])
            old_right = old_left + float(region["width"])
            old_bottom = old_top + float(region["height"])
            retained_lines = []
            for line in lines:
                if line.get("bubble_id") is not None:
                    retained_lines.append(line)
                    continue
                line_left, line_top = float(line["left"]), float(line["top"])
                line_right = line_left + float(line["width"])
                line_bottom = line_top + float(line["height"])
                overlap = max(0.0, min(old_right, line_right) - max(old_left, line_left)) * max(
                    0.0, min(old_bottom, line_bottom) - max(old_top, line_top)
                )
                line_area = max(1.0, float(line["width"]) * float(line["height"]))
                if overlap / line_area < 0.5:
                    retained_lines.append(line)
            lines = retained_lines + recovered_lines
            left = min(line["left"] for line in recovered_lines)
            top = min(line["top"] for line in recovered_lines)
            right = max(line["left"] + line["width"] for line in recovered_lines)
            bottom = max(line["top"] + line["height"] for line in recovered_lines)
            region.update({
                "text": " ".join(line["text"] for line in recovered_lines),
                "left": round(left, 2), "top": round(top, 2),
                "width": round(right - left, 2),
                "height": round(bottom - top, 2),
                "confidence": round(sum(line["confidence"] for line in recovered_lines) / len(recovered_lines), 4),
                "region_type": "speech_bubble",
                "bubble_bbox": list(recovered_bubble.bbox),
                "bubble_confidence": round(recovered_bubble.confidence, 4),
                "ocr_boxes": [[line["left"], line["top"], line["width"], line["height"]]
                              for line in recovered_lines],
                "ocr_polygons": [line["ocr_polygon"] for line in recovered_lines],
                "ocr_recovered": True,
                "ocr_recovery_method": "truncated_external_bubble_crop",
            })
            truncated_external_recovery_count += 1

        # Secondary/edge recovery regions are created after the primary bubble
        # OCR loop. Retry only their malformed line crops with the small
        # recognizer; ordinary regions do not incur another model call.
        recovered_bubble_region_started = perf_counter()
        recovered_bubble_region_count = self._rescue_malformed_bubble_regions(
            image, regions, lines
        )
        recovered_bubble_region_seconds = perf_counter() - recovered_bubble_region_started

        # Recovery paths can replace a region after the first OCR pass. Apply
        # casing cleanup and review hints once at the end so recovered text gets
        # the same policy as regions from the primary OCR pass.
        bubble_line_retry_started = perf_counter()
        bubble_line_retry_count = 0
        for region in regions:
            self._normalize_unique_common_digit_confusions(region)
            self._normalize_near_all_caps_bubble(region)
            self._add_speech_bubble_ocr_review_hint(region)
            if self._rescue_suspicious_bubble_region_from_line_boxes(image, region):
                bubble_line_retry_count += 1
                self._add_speech_bubble_ocr_review_hint(region)
        bubble_line_retry_seconds = perf_counter() - bubble_line_retry_started

        regions = self._deduplicate_overlapping_regions(regions)
        lines = self._deduplicate_overlapping_regions(
            lines, minimum_overlap=0.90, allow_fragment=False
        )
        regions.sort(key=lambda region: (
            region["top"], -region["left"] if reading_direction == "rtl" else region["left"]
        ))
        lines.sort(key=lambda line: (
            line["top"], -line["left"] if reading_direction == "rtl" else line["left"]
        ))

        if not lines:
            raise RuntimeError("لم يعثر OCR المحلي على نص واضح داخل الفقاعات أو خارجها.")
        bubble_backend = (
            "directml" if active_bubble_provider() == "DmlExecutionProvider" else "cpu"
        )
        provider = "ppocrv6_manga_v03_onnxruntime_cpu+bubble_onnx_" + bubble_backend
        if not bubbles:
            provider += "+external_page_ocr"
        if adjacent_recovery_count:
            provider += "+adjacent_bubble_crop_recovery"
        if edge_crop_recovery_count:
            provider += "+expanded_bubble_edge_crop_recovery"
        if wide_bubble_recovery_count:
            provider += "+low_confidence_wide_bubble_recovery"
        if detector_region_count:
            provider += "+rtdetr_targeted_rescue"
        return {
            "text": "\n".join(line["text"] for line in lines),
            "lines": lines,
            "regions": regions,
            "nontranslatable_annotations": external_annotations,
            "provider": provider,
            "elapsed_seconds": round(perf_counter() - started, 4),
            "bubble_detection_and_ocr_seconds": round(bubble_elapsed, 4),
            "bubble_detection_seconds": round(bubble_detection_elapsed, 4),
            "bubble_ocr_seconds": round(bubble_ocr_elapsed, 4),
            "malformed_bubble_recognition_seconds": round(malformed_bubble_recognition_seconds, 4),
            "suspicious_bubble_line_retry_seconds": round(bubble_line_retry_seconds, 4),
            "suspicious_bubble_line_retry_count": bubble_line_retry_count,
            "malformed_bubble_recognition_count": malformed_bubble_recognition_count,
            "speech_bubble_mask_count": len(bubbles),
            "speech_bubble_masks_with_primary_ocr": len(bubble_ids_with_primary_ocr),
            "speech_bubble_masks_without_primary_ocr": max(0, len(bubbles) - len(bubble_ids_with_primary_ocr)),
            "bubble_split_detector_seconds": round(bubble_split_detector_seconds, 4),
            "bubble_split_count": bubble_split_count,
            "external_ocr_seconds": round(external_elapsed, 4),
            "external_region_count": sum(region.get("region_type") == "external_text" for region in regions),
            "adjacent_bubble_recovery_seconds": round(adjacent_recovery_seconds, 4),
            "adjacent_bubble_recovery_count": adjacent_recovery_count,
            "adjacent_fragment_recovery_seconds": round(adjacent_fragment_recovery_seconds, 4),
            "adjacent_fragment_recovery_count": adjacent_fragment_recovery_count,
            "wide_bubble_recovery_seconds": round(wide_bubble_recovery_seconds, 4),
            "wide_bubble_recovery_count": wide_bubble_recovery_count,
            "edge_crop_recovery_seconds": round(edge_crop_recovery_seconds, 4),
            "edge_crop_recovery_count": edge_crop_recovery_count,
            "truncated_external_recovery_seconds": round(truncated_external_recovery_seconds, 4),
            "truncated_external_recovery_count": truncated_external_recovery_count,
            "recovered_bubble_recognition_seconds": round(recovered_bubble_region_seconds, 4),
            "recovered_bubble_recognition_count": recovered_bubble_region_count,
            "comic_detector_seconds": round(detector_elapsed, 4),
            "comic_detector_region_count": detector_region_count,
            "comic_detector_bubble_count": detector_bubble_count,
            "comic_detector_ocr_line_count": detector_ocr_line_count,
        }

    @staticmethod
    def _split_bubbles_with_detector(bubbles, detections, image_shape):
        """Split a merged segmentation mask only when RT-DETR finds distinct balloons.

        Detector boxes are crop bounds, never paint/erase masks. The original
        segmentation mask is clipped into each child; every child keeps its
        parent's ID so the renderer can still resolve the original mask.
        """
        _page_height, page_width = image_shape[:2]
        minimum_area = max(250, min(1800, page_width * page_width // 1200))
        output = []
        for parent_id, parent in enumerate(bubbles, 1):
            px1, py1, px2, py2 = parent.bbox
            parent_mask = np.asarray(parent.safe_mask, dtype=np.uint8)
            parent_area = int(np.count_nonzero(parent_mask))
            if parent_area < minimum_area * 2:
                output.append((parent_id, parent))
                continue

            proposals = []
            for detection in detections:
                if detection.label != "bubble" or detection.confidence < 0.70:
                    continue
                dx1, dy1, dx2, dy2 = detection.bbox
                left, top = max(px1, dx1), max(py1, dy1)
                right, bottom = min(px2, dx2), min(py2, dy2)
                if right <= left or bottom <= top:
                    continue
                local = parent_mask[top - py1:bottom - py1, left - px1:right - px1]
                area = int(np.count_nonzero(local))
                detection_area = max(1, (dx2 - dx1) * (dy2 - dy1))
                local_y, local_x = np.nonzero(local)
                if not len(local_x):
                    continue
                mask_width = int(local_x.max() - local_x.min() + 1)
                mask_height = int(local_y.max() - local_y.min() + 1)
                if (area < minimum_area or area / parent_area < 0.08
                        or mask_width / max(1, dx2 - dx1) < 0.78
                        or mask_height / max(1, dy2 - dy1) < 0.78):
                    continue
                proposals.append({
                    "bbox": (left, top, right, bottom),
                    "confidence": float(detection.confidence),
                })

            # Adjacent tile detections can partially overlap one balloon. Keep
            # only the stronger box before considering it a separate instance.
            unique = []
            for candidate in sorted(proposals, key=lambda item: item["confidence"], reverse=True):
                x1, y1, x2, y2 = candidate["bbox"]
                candidate_area = max(1, (x2 - x1) * (y2 - y1))
                duplicate = False
                for existing in unique:
                    a1, b1, a2, b2 = existing["bbox"]
                    intersection = max(0, min(x2, a2) - max(x1, a1)) * max(
                        0, min(y2, b2) - max(y1, b1)
                    )
                    existing_area = max(1, (a2 - a1) * (b2 - b1))
                    if intersection / min(candidate_area, existing_area) >= 0.55:
                        duplicate = True
                        break
                if not duplicate:
                    unique.append(candidate)

            if len(unique) < 2:
                output.append((parent_id, parent))
                continue

            union = np.zeros_like(parent_mask)
            child_areas = []
            for candidate in unique:
                x1, y1, x2, y2 = candidate["bbox"]
                local = parent_mask[y1 - py1:y2 - py1, x1 - px1:x2 - px1]
                union[y1 - py1:y2 - py1, x1 - px1:x2 - px1] |= local
                child_areas.append(int(np.count_nonzero(local)))
            if np.count_nonzero(union) / parent_area < 0.70:
                output.append((parent_id, parent))
                continue

            overlapping_children = False
            for index, first in enumerate(unique):
                x1, y1, x2, y2 = first["bbox"]
                for other_index in range(index + 1, len(unique)):
                    second = unique[other_index]
                    a1, b1, a2, b2 = second["bbox"]
                    left, top = max(x1, a1), max(y1, b1)
                    right, bottom = min(x2, a2), min(y2, b2)
                    if right <= left or bottom <= top:
                        continue
                    overlap_mask = parent_mask[top - py1:bottom - py1,
                                               left - px1:right - px1]
                    overlap_area = int(np.count_nonzero(overlap_mask))
                    if overlap_area / min(child_areas[index], child_areas[other_index]) >= 0.35:
                        overlapping_children = True
                        break
                if overlapping_children:
                    break
            if overlapping_children:
                output.append((parent_id, parent))
                continue

            children = []
            for candidate in unique:
                left, top, right, bottom = candidate["bbox"]
                pad_x = max(3, int(round((right - left) * 0.025)))
                pad_y = max(3, int(round((bottom - top) * 0.025)))
                left, top = max(px1, left - pad_x), max(py1, top - pad_y)
                right, bottom = min(px2, right + pad_x), min(py2, bottom + pad_y)
                mask = parent_mask[top - py1:bottom - py1,
                                   left - px1:right - px1].copy()
                if np.count_nonzero(mask) < minimum_area:
                    children = []
                    break
                children.append(SpeechBubble(
                    (left, top, right, bottom), mask, parent.confidence
                ))
            if len(children) != len(unique):
                output.append((parent_id, parent))
            else:
                output.extend((parent_id, child) for child in children)

        return output

    @staticmethod
    def _should_probe_bubble_split(bubble, page_width):
        """Flag large concave merges, with a narrow low-confidence area grace band."""
        mask = np.asarray(bubble.safe_mask, dtype=np.uint8)
        area = int(np.count_nonzero(mask))
        minimum_area = max(15_000, int(page_width * page_width * 0.22))
        near_floor_low_confidence = (
            bubble.confidence < 0.35 and area >= int(minimum_area * 0.95)
        )
        if area < minimum_area and not near_floor_low_confidence:
            return False
        contours, _ = cv2.findContours(
            (mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
        )
        if not contours:
            return False
        contour = max(contours, key=cv2.contourArea)
        hull = cv2.convexHull(contour)
        hull_area = float(cv2.contourArea(hull))
        if hull_area <= 0 or cv2.contourArea(contour) / hull_area > 0.94:
            return False
        try:
            defects = cv2.convexityDefects(contour, cv2.convexHull(contour, returnPoints=False))
        except cv2.error:
            return False
        if defects is None:
            return False
        deep_defects = sum(1 for defect in defects.reshape(-1, 4) if defect[3] / 256.0 > 12.0)
        return deep_defects >= 2

    def _refine_suspicious_bubble_splits(self, image, bubbles):
        """Use the local comic detector only on large, concave candidate masks.

        The speech-bubble segmenter remains authoritative. RT-DETR boxes may
        split its mask only when two strong, spatially distinct balloon proposals
        cover the parent mask sufficiently; otherwise the original mask is kept.
        Returned IDs refer to the original parent so saved/rendered regions remain
        compatible with the standard bubble detector and renderer.
        """
        page_height, page_width = image.shape[:2]
        candidates = [
            (bubble_id, bubble) for bubble_id, bubble in enumerate(bubbles, 1)
            if self._should_probe_bubble_split(bubble, page_width)
        ]
        if not candidates:
            return list(enumerate(bubbles, 1)), 0.0, 0

        detector = _get_comic_text_detector()
        if not detector.available:
            return list(enumerate(bubbles, 1)), 0.0, 0

        started = perf_counter()
        refined = {bubble_id: bubble for bubble_id, bubble in enumerate(bubbles, 1)}
        split_count = 0
        for bubble_id, bubble in candidates:
            x1, y1, x2, y2 = bubble.bbox
            pad = max(12, int(round(max(x2 - x1, y2 - y1) * 0.025)))
            left, top = max(0, x1 - pad), max(0, y1 - pad)
            right, bottom = min(page_width, x2 + pad), min(page_height, y2 + pad)
            crop = image[top:bottom, left:right]
            if crop.size == 0:
                continue
            try:
                local_detections = detector.detect(crop.copy())
            except Exception:
                LOGGER.warning("Targeted bubble split probe failed", exc_info=True)
                continue
            detections = [
                ComicTextRegion(
                    label=item.label, confidence=item.confidence,
                    bbox=(item.bbox[0] + left, item.bbox[1] + top,
                          item.bbox[2] + left, item.bbox[3] + top),
                )
                for item in local_detections
            ]
            split = self._split_bubbles_with_detector([bubble], detections, image.shape)
            if len(split) > 1:
                refined[bubble_id] = [child for _child_id, child in split]
                split_count += len(split) - 1

        output = []
        for bubble_id in range(1, len(bubbles) + 1):
            value = refined[bubble_id]
            children = value if isinstance(value, list) else [value]
            output.extend((bubble_id, child) for child in children)
        return output, perf_counter() - started, split_count

    def _ocr_detector_regions(self, image, detections, bubbles, existing_lines, include_external):
        """OCR high-confidence detector crops only when RapidOCR left them unread."""
        height, width = image.shape[:2]
        bubble_proposals = []
        external_proposals = []
        for detection in detections:
            if detection.label not in {"text_bubble", "text_free"}:
                continue
            if detection.label == "text_free" and not include_external:
                continue
            x1, y1, x2, y2 = detection.bbox
            if detection.label == "text_free" and self._box_overlaps_bubble(detection.bbox, bubbles):
                continue
            # The first pass already OCRed this page. A modest overlap is
            # enough to skip a redundant crop recognition: detector rectangles
            # intentionally include margins, so requiring 38% overlap caused
            # many near-duplicate crops to be recognized again and rejected
            # only after their OCR had consumed seconds of CPU time. New lines
            # that do not overlap existing OCR still take the supplemental path.
            if self._detector_region_already_ocrd(detection.bbox, existing_lines):
                continue
            # A small margin prevents edge clipping while keeping the detector
            # region authoritative; it is never used as an erase mask directly.
            pad_x = max(2, int(round((x2 - x1) * 0.04)))
            pad_y = max(2, int(round((y2 - y1) * 0.08)))
            left, top = max(0, x1 - pad_x), max(0, y1 - pad_y)
            right, bottom = min(width, x2 + pad_x), min(height, y2 + pad_y)
            crop = image[top:bottom, left:right].copy()
            if crop.size == 0:
                continue
            scale = min(3.0, 480.0 / max(crop.shape[:2])) if max(crop.shape[:2]) < 480 else 1.0
            if scale > 1.0:
                crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            try:
                result = self._get_engine()(crop)
            except Exception:
                LOGGER.debug("RapidOCR detector-crop recognition failed", exc_info=True)
                continue
            if result is None or result.boxes is None:
                continue
            raw_scores = getattr(result, "scores", None)
            raw_texts = getattr(result, "txts", None)
            if raw_scores is None or raw_texts is None:
                continue
            for box, text, score in zip(result.boxes, raw_texts, raw_scores):
                text = str(text).strip()
                try:
                    confidence = float(score)
                    points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
                except (TypeError, ValueError):
                    continue
                if (not text or not np.isfinite(confidence) or confidence < self.min_confidence
                        or not np.isfinite(points).all() or len(points) < 4
                        or not self._is_english_source_text(text)
                        or not self._looks_like_dialogue(text)
                        or self._is_likely_external_sound_effect(text)):
                    continue
                points /= scale
                points[:, 0] += left
                points[:, 1] += top
                points[:, 0] = np.clip(points[:, 0], 0, width - 1)
                points[:, 1] = np.clip(points[:, 1], 0, height - 1)
                box_left, box_top = float(points[:, 0].min()), float(points[:, 1].min())
                box_right, box_bottom = float(points[:, 0].max()), float(points[:, 1].max())
                if box_right - box_left < 5 or box_bottom - box_top < 5:
                    continue
                line = {
                    "text": text, "left": box_left, "top": box_top,
                    "width": box_right - box_left, "height": box_bottom - box_top,
                    "confidence": round(confidence, 4), "ocr_polygon": points.tolist(),
                }
                if self._box_overlaps_ocr(
                    (int(box_left), int(box_top), int(box_right), int(box_bottom)),
                    existing_lines + bubble_proposals + external_proposals,
                ):
                    continue
                if detection.label == "text_bubble":
                    bubble_proposals.append(line)
                else:
                    angle = float(np.degrees(np.arctan2(
                        points[1, 1] - points[0, 1], points[1, 0] - points[0, 0]
                    )))
                    line.update({"region_type": "external_text", "angle": angle})
                    external_proposals.append(line)

        bubble_lines = []
        if bubble_proposals:
            # Reuse the existing segmentation and grouping authority. A detector
            # rectangle alone cannot trigger erasure or define a bubble overlay.
            encoded_ok, encoded = cv2.imencode(".png", image)
            if not encoded_ok:
                encoded = None
            try:
                if encoded is None:
                    raise ValueError("تعذر ترميز الصفحة للتحقق من فقاعات النص المقترحة.")
                bubble_lines, candidate_regions = self.associate_lines_with_bubbles(
                    encoded.tobytes(), bubble_proposals, "ltr"
                )
            except (RuntimeError, ValueError):
                candidate_regions = []
            existing_bubble_regions = [
                region for region in existing_lines
                if region.get("bubble_id") is not None
            ]
            bubble_lines = [
                line for line in bubble_lines
                if not self._box_overlaps_ocr(
                    (int(line["left"]), int(line["top"]),
                     int(line["left"] + line["width"]), int(line["top"] + line["height"])),
                    existing_lines,
                )
            ]
            if existing_bubble_regions:
                known_ids = {region.get("bubble_id") for region in existing_bubble_regions}
                bubble_lines = [line for line in bubble_lines if line.get("bubble_id") not in known_ids]

        external_proposals = [
            line for line in external_proposals
            if not self._box_overlaps_bubble(
                (int(line["left"]), int(line["top"]),
                 int(line["left"] + line["width"]), int(line["top"] + line["height"])),
                bubbles,
            )
            and not self._box_overlaps_ocr(
                (int(line["left"]), int(line["top"]),
                 int(line["left"] + line["width"]), int(line["top"] + line["height"])),
                existing_lines + bubble_lines,
            )
        ]
        return bubble_lines, external_proposals

    @staticmethod
    def _box_overlaps_ocr(box, lines, threshold=0.38):
        x1, y1, x2, y2 = box
        area = max(1, (x2 - x1) * (y2 - y1))
        for line in lines:
            try:
                left, top = float(line["left"]), float(line["top"])
                right = left + float(line["width"])
                bottom = top + float(line["height"])
            except (KeyError, TypeError, ValueError):
                continue
            intersection = max(0.0, min(x2, right) - max(x1, left)) * max(
                0.0, min(y2, bottom) - max(y1, top)
            )
            if intersection / area >= threshold:
                return True
        return False

    @staticmethod
    def _detector_region_already_ocrd(box, lines):
        """Skip duplicate crop OCR only when a confident line covers its area."""
        trusted_lines = []
        for line in lines:
            try:
                confidence = float(line.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            if np.isfinite(confidence) and confidence >= 0.90:
                trusted_lines.append(line)
        return LocalRapidOCR._box_overlaps_ocr(box, trusted_lines, threshold=0.25)

    @staticmethod
    def _box_overlaps_bubble(box, bubbles, threshold=0.10):
        x1, y1, x2, y2 = (int(value) for value in box)
        area = max(1, (x2 - x1) * (y2 - y1))
        for bubble in bubbles:
            bx1, by1, bx2, by2 = bubble.bbox
            left, top, right, bottom = max(x1, bx1), max(y1, by1), min(x2, bx2), min(y2, by2)
            if right <= left or bottom <= top:
                continue
            mask = bubble.safe_mask[top - by1:bottom - by1, left - bx1:right - bx1]
            if np.count_nonzero(mask) / area >= threshold:
                return True
        return False

    @staticmethod
    def _polygon_overlaps_bubble(polygon, bubbles, threshold=0.08):
        """Check a rotated OCR polygon against safe bubble masks.

        Axis-aligned bounds can overlap a balloon substantially even when the
        slanted text itself is outside it. Use the mapped OCR polygon for this
        final check; malformed or degenerate polygons fail closed.
        """
        try:
            points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
        except (TypeError, ValueError):
            return True
        if len(points) < 3 or not np.isfinite(points).all():
            return True
        left = int(np.floor(points[:, 0].min()))
        top = int(np.floor(points[:, 1].min()))
        right = int(np.ceil(points[:, 0].max())) + 1
        bottom = int(np.ceil(points[:, 1].max())) + 1
        if right <= left or bottom <= top or (right - left) * (bottom - top) > 4_000_000:
            return True
        polygon_mask = np.zeros((bottom - top, right - left), dtype=np.uint8)
        local_points = np.rint(points - np.array([left, top], dtype=np.float32)).astype(np.int32)
        try:
            cv2.fillConvexPoly(polygon_mask, local_points, 255)
        except cv2.error:
            return True
        polygon_area = int(np.count_nonzero(polygon_mask))
        if polygon_area == 0:
            return True

        for bubble in bubbles:
            bx1, by1, bx2, by2 = bubble.bbox
            x1, y1 = max(left, bx1), max(top, by1)
            x2, y2 = min(right, bx2), min(bottom, by2)
            if x2 <= x1 or y2 <= y1:
                continue
            candidate = polygon_mask[y1 - top:y2 - top, x1 - left:x2 - left]
            safe = bubble.safe_mask[y1 - by1:y2 - by1, x1 - bx1:x2 - bx1]
            overlap = int(np.count_nonzero(cv2.bitwise_and(candidate, safe)))
            if overlap / polygon_area >= threshold:
                return True
        return False

    @staticmethod
    def _region_duplicates(candidate, regions):
        box = (candidate["left"], candidate["top"],
               candidate["left"] + candidate["width"], candidate["top"] + candidate["height"])
        return LocalRapidOCR._box_overlaps_ocr(box, regions, threshold=0.55)

    @staticmethod
    def _deduplicate_overlapping_regions(regions, minimum_overlap=0.58,
                                         allow_fragment=True):
        """Remove same-class OCR rows that repeat the same words in the same area.

        Bubble segmentation can return two highly overlapping masks for one
        printed utterance. Their OCR regions then contain an exact duplicate
        or a shorter contiguous fragment. Keep the more complete reading only
        when the smaller box is almost fully contained by the other box; nearby
        bubbles and genuine repeated phrases remain separate.
        """
        def normalized_tokens(region):
            return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?",
                              str(region.get("text", "")).casefold())

        def contains_tokens(container, fragment):
            if not fragment or len(fragment) > len(container):
                return False
            for start in range(len(container) - len(fragment) + 1):
                matches = True
                for full, part in zip(container[start:start + len(fragment)], fragment):
                    if full == part:
                        continue
                    if (len(full) >= 4 and len(part) >= 4
                            and SequenceMatcher(None, full, part).ratio() >= 0.80):
                        continue
                    if (not allow_fragment or len(part) < 3
                            or part in {"the", "and", "for", "are", "was", "were",
                                        "not", "but", "you", "one", "her", "him",
                                        "me", "of", "it", "at", "to", "in", "on"}
                            or len(part) / max(1, len(full)) < 0.30
                            or part not in full):
                        matches = False
                        break
                if matches:
                    return True
            return False

        def overlap_of_smaller(first, second):
            try:
                ax1, ay1 = float(first["left"]), float(first["top"])
                ax2, ay2 = ax1 + float(first["width"]), ay1 + float(first["height"])
                bx1, by1 = float(second["left"]), float(second["top"])
                bx2, by2 = bx1 + float(second["width"]), by1 + float(second["height"])
            except (KeyError, TypeError, ValueError):
                return 0.0
            a_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
            b_area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
            smaller_area = min(a_area, b_area)
            if smaller_area <= 0:
                return 0.0
            intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
                0.0, min(ay2, by2) - max(ay1, by1)
            )
            return intersection / smaller_area

        def matched_ocr_box_fraction(first, second):
            def boxes(region):
                parsed = []
                for box in region.get("ocr_boxes", []) or []:
                    try:
                        if len(box) >= 4:
                            parsed.append(tuple(float(value) for value in box[:4]))
                    except (TypeError, ValueError):
                        continue
                return parsed

            first_boxes, second_boxes = boxes(first), boxes(second)
            if not first_boxes or not second_boxes:
                return 0.0
            smaller_boxes, larger_boxes = (
                (first_boxes, second_boxes) if len(first_boxes) <= len(second_boxes)
                else (second_boxes, first_boxes)
            )

            def overlap(first_box, second_box):
                ax, ay, aw, ah = first_box
                bx, by, bw, bh = second_box
                area = min(max(0.0, aw) * max(0.0, ah),
                           max(0.0, bw) * max(0.0, bh))
                if area <= 0:
                    return 0.0
                intersection = max(0.0, min(ax + aw, bx + bw) - max(ax, bx)) * max(
                    0.0, min(ay + ah, by + bh) - max(ay, by)
                )
                return intersection / area

            matched = sum(
                any(overlap(box, candidate) >= 0.80 for candidate in larger_boxes)
                for box in smaller_boxes
            )
            return matched / len(smaller_boxes)

        def completeness(region):
            tokens = normalized_tokens(region)
            try:
                confidence = float(region.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            return (len(tokens), len(" ".join(tokens)),
                    not bool(region.get("ocr_review_required")), confidence)

        kept = []
        for region in regions:
            tokens = normalized_tokens(region)
            if not tokens:
                kept.append(region)
                continue
            duplicate_index = None
            replace_existing = False
            for index, existing in enumerate(kept):
                if region.get("region_type") != existing.get("region_type"):
                    continue
                region_overlap = overlap_of_smaller(region, existing)
                existing_tokens = normalized_tokens(existing)
                duplicate = (
                    contains_tokens(existing_tokens, tokens)
                    or contains_tokens(tokens, existing_tokens)
                )
                aligned_glyph_area_duplicate = (
                    region.get("region_type") == "speech_bubble"
                    and region_overlap >= 0.35
                    and matched_ocr_box_fraction(region, existing) >= 0.90
                )
                if region_overlap < minimum_overlap and not aligned_glyph_area_duplicate:
                    continue
                if not duplicate:
                    # Overlapping bubble masks can distort a few glyphs in one
                    # OCR pass, so exact token containment misses a genuine
                    # duplicate utterance. Use fuzzy matching only when nearly
                    # the entire smaller region lies on top of the larger one.
                    fuzzy_dialogue_duplicate = (
                        region.get("region_type") == "speech_bubble"
                        and len(" ".join(tokens)) >= 28
                        and len(" ".join(existing_tokens)) >= 28
                        and overlap_of_smaller(region, existing) >= 0.85
                        and SequenceMatcher(
                            None, " ".join(existing_tokens), " ".join(tokens)
                        ).ratio() >= 0.88
                    )
                    if not fuzzy_dialogue_duplicate and not aligned_glyph_area_duplicate:
                        continue
                duplicate_index = index
                replace_existing = completeness(region) > completeness(existing)
                break
            if duplicate_index is None:
                kept.append(region)
            elif replace_existing:
                kept[duplicate_index] = region
        return kept

    def _refine_rotated_external_group(self, image, members, angle, bubbles):
        """Retry a skewed caption as one enlarged, deskewed crop.

        Page-level OCR can find only the short/easy lines of an angled title card.
        A block crop gives the recognizer more context and can recover lines it
        missed. Keep the first pass unless the retry adds substantial, confident
        English text; mapped polygons then let the renderer erase those added
        glyphs as well.
        """
        if len(members) < 2 or abs(float(angle)) < 8.0 or abs(float(angle)) > 35.0:
            return members
        height, width = image.shape[:2]
        left = max(0, int(min(line["left"] for line in members)) - 36)
        top = max(0, int(min(line["top"] for line in members)) - 36)
        right = min(width, int(np.ceil(max(line["left"] + line["width"] for line in members))) + 36)
        bottom = min(height, int(np.ceil(max(line["top"] + line["height"] for line in members))) + 36)
        if right - left < 40 or bottom - top < 40 or (right - left) * (bottom - top) > 1_200_000:
            return members

        scale = 1.7 if max(right - left, bottom - top) < 900 else 1.0
        crop = image[top:bottom, left:right]
        if scale != 1.0:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        crop_height, crop_width = crop.shape[:2]
        matrix = cv2.getRotationMatrix2D((crop_width / 2, crop_height / 2), -float(angle), 1.0)
        cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
        rotated_width = max(1, int(np.ceil(crop_height * sine + crop_width * cosine)))
        rotated_height = max(1, int(np.ceil(crop_height * cosine + crop_width * sine)))
        matrix[0, 2] += rotated_width / 2 - crop_width / 2
        matrix[1, 2] += rotated_height / 2 - crop_height / 2
        rectified = cv2.warpAffine(
            crop, matrix, (rotated_width, rotated_height), flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        try:
            result = self._get_engine()(rectified)
        except Exception:
            LOGGER.debug("Deskewed external-text OCR retry failed", exc_info=True)
            return members
        if result is None or result.boxes is None:
            return members

        inverse = cv2.invertAffineTransform(matrix)
        refined = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            text = str(text).strip()
            try:
                confidence = float(score)
                points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
            except (TypeError, ValueError):
                continue
            if (not text or confidence < 0.55 or not np.isfinite(points).all()
                    or len(points) < 4 or not self._is_english_source_text(text)
                    or self._is_likely_external_sound_effect(text)
                    or not self._looks_like_dialogue(text)):
                continue
            mapped = cv2.transform(points[None, :, :], inverse)[0] / scale
            mapped += np.array([left, top], dtype=np.float32)
            x1, y1 = max(0.0, float(mapped[:, 0].min())), max(0.0, float(mapped[:, 1].min()))
            x2, y2 = min(float(width), float(mapped[:, 0].max())), min(float(height), float(mapped[:, 1].max()))
            if x2 <= x1 or y2 <= y1 or self._polygon_overlaps_bubble(mapped, bubbles):
                continue
            refined.append({
                "text": text, "left": round(x1, 2), "top": round(y1, 2),
                "width": round(x2 - x1, 2), "height": round(y2 - y1, 2),
                "confidence": round(confidence, 4), "region_type": "external_text",
                "ocr_polygon": mapped.tolist(), "angle": round(float(angle), 2),
            })

        refined.sort(key=lambda line: (line["top"], line["left"]))
        old_text = " ".join(line["text"] for line in members)
        new_text = " ".join(line["text"] for line in refined)
        old_confidence = float(np.median([line["confidence"] for line in members]))
        new_confidence = float(np.median([line["confidence"] for line in refined])) if refined else 0.0
        old_valid, old_total = self._english_word_counts(old_text)
        new_valid, new_total = self._english_word_counts(new_text)
        lexical_gain = (
            new_valid > old_valid
            and new_valid / max(1, new_total) >= old_valid / max(1, old_total)
        )
        fuller_text = len(new_text) >= max(len(old_text) + 8, int(len(old_text) * 1.2))
        if ((fuller_text or lexical_gain)
                and new_confidence >= max(0.55, old_confidence - 0.15)):
            LOGGER.info(
                "Deskewed external OCR recovered a fuller caption (%s -> %s characters)",
                len(old_text), len(new_text),
            )
            return refined
        return members

    def _rescue_external_line_recognition(self, image, lines):
        """Re-recognize only malformed external text boxes without re-running detection."""
        targets = []
        crops = []
        rescued = list(lines)
        for index, line in enumerate(lines):
            text = str(line.get("text", ""))
            article_hyphen_cleanup = re.sub(
                r"^\s*[-–]([Aa])[-–](?=[A-Za-z]{3,}\b)", r"\1 ", text
            )
            if article_hyphen_cleanup != text:
                rescued[index] = dict(
                    line, text=article_hyphen_cleanup,
                    ocr_rescued=True,
                    ocr_recovery_method="external_article_hyphen_artifact_cleanup",
                )
                continue
            valid, total = self._english_word_counts(text)
            suspicious_question_prefix = bool(re.search(
                r"(?<![A-Za-z])[?ÁÀÂÄáàâä][A-Za-z]{2,}(?![A-Za-z])", text
            ))
            suspicious_accented_uppercase = bool(re.search(
                r"(?<![A-Za-z])[^\x00-\x7F][A-Z]{2,}(?![A-Za-z])", text
            ))
            alpha_count = len(re.findall(r"[A-Za-z]", text))
            try:
                original_confidence = float(line.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                original_confidence = 0.0
            suspicious_short_external = (
                total == 1 and valid == 0 and 3 <= alpha_count <= 7
                and original_confidence <= 0.90
            )
            if (total < 1 or (valid >= total and not suspicious_question_prefix
                                  and not suspicious_accented_uppercase)
                    or (alpha_count < 8 and not suspicious_short_external
                        and not suspicious_accented_uppercase)
                    or abs(float(line.get("angle", 0.0) or 0.0)) > 8):
                continue
            left, top = int(line["left"]), int(line["top"])
            right = int(np.ceil(line["left"] + line["width"]))
            bottom = int(np.ceil(line["top"] + line["height"]))
            pad_x = max(4, round(float(line["height"]) * 0.12))
            pad_y = max(3, round(float(line["height"]) * 0.10))
            crop = image[max(0, top - pad_y):min(image.shape[0], bottom + pad_y),
                         max(0, left - pad_x):min(image.shape[1], right + pad_x)]
            if crop.size == 0:
                continue
            targets.append(index)
            crops.append(crop.copy())
        if not crops:
            return rescued
        try:
            engine = self._get_engine()
            result = engine.recognize_txt(crops)
            texts = getattr(result, "txts", None) or ()
            scores = getattr(result, "scores", None) or ()
        except Exception:
            LOGGER.debug("Direct external-line recognition retry failed", exc_info=True)
            return rescued
        if len(texts) != len(crops) or len(scores) != len(crops):
            return rescued
        for index, candidate, score in zip(targets, texts, scores):
            candidate = str(candidate or "").strip()
            try:
                confidence = float(score)
            except (TypeError, ValueError):
                continue
            original = str(lines[index].get("text", ""))
            old_valid, old_total = self._english_word_counts(original)
            new_valid, new_total = self._english_word_counts(candidate)
            original_alpha_count = len(re.findall(r"[A-Za-z]", original))
            try:
                original_confidence = float(lines[index].get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                original_confidence = 0.0
            suspicious_short_external = (
                old_total == 1 and old_valid == 0 and 3 <= original_alpha_count <= 7
                and original_confidence <= 0.90
            )
            short_fused_article_recovery = False
            if suspicious_short_external:
                fused = re.fullmatch(r"([IA])([A-Za-z]{2,})([.!?…]?)", candidate)
                spellchecker = self._get_spellchecker()
                if fused and spellchecker is not None and fused.group(2).casefold() in spellchecker:
                    candidate = fused.group(1) + " " + fused.group(2).lower() + fused.group(3)
                    new_valid, new_total = self._english_word_counts(candidate)
                    short_fused_article_recovery = new_total > 0 and new_valid == new_total
            question_prefix_full_recovery = (
                bool(re.search(r"(?<![A-Za-z])[?ÁÀÂÄáàâä][A-Za-z]{2,}(?![A-Za-z])", original))
                and not re.search(r"(?<![A-Za-z])[?ÁÀÂÄáàâä][A-Za-z]{2,}(?![A-Za-z])", candidate)
                and new_total > 0 and new_valid == new_total and confidence >= 0.99
            )
            original_deaccented = "".join(
                char for char in unicodedata.normalize("NFKD", original)
                if not unicodedata.combining(char)
            )
            candidate_deaccented = "".join(
                char for char in unicodedata.normalize("NFKD", candidate)
                if not unicodedata.combining(char)
            )
            accented_latin_full_recovery = (
                bool(re.search(r"(?<![A-Za-z])[^\x00-\x7F][A-Z]{2,}(?![A-Za-z])", original))
                and candidate != original
                and re.sub(r"\s+", " ", original_deaccented).casefold()
                    == re.sub(r"\s+", " ", candidate_deaccented).casefold()
                and new_total > 0 and confidence >= 0.94
            )
            full_corruption_recovery = (
                question_prefix_full_recovery or accented_latin_full_recovery
            )
            old_letters = "".join(re.findall(r"[A-Za-z]", original)).casefold()
            new_letters = "".join(re.findall(r"[A-Za-z]", candidate)).casefold()
            visual_similarity = SequenceMatcher(None, old_letters, new_letters).ratio()
            if (not candidate or not np.isfinite(confidence)
                    or confidence < (0.94 if accented_latin_full_recovery else 0.97)
                    or not self._is_english_source_text(candidate)
                    or (new_valid <= old_valid and not full_corruption_recovery)
                    or (suspicious_short_external and (
                        not short_fused_article_recovery or confidence < 0.99
                    ))
                    or (new_valid / max(1, new_total) < 0.80
                        and not accented_latin_full_recovery)
                    or len(candidate) < max(6, int(len(original) * 0.60))
                    or visual_similarity < (0.20 if short_fused_article_recovery else 0.45)):
                continue
            replacement = dict(lines[index], text=candidate, confidence=round(confidence, 4),
                               ocr_rescued=True,
                               ocr_recovery_method="direct_recognition_of_external_line")
            rescued[index] = replacement
        return rescued

    def _rescue_numeric_external_candidate(self, image, line):
        """Recover a short English word that the tile recognizer rendered as digits."""
        original = str(line.get("text", "")).strip()
        if not re.fullmatch(r"\d{3,5}[.,!?…]?", original):
            return None
        try:
            raw_confidence = float(line.get("confidence", 0.0))
            left, top = int(line["left"]), int(line["top"])
            right = int(np.ceil(float(line["left"]) + float(line["width"])))
            bottom = int(np.ceil(float(line["top"]) + float(line["height"])))
        except (KeyError, TypeError, ValueError):
            return None
        line_height = max(1, bottom - top)
        if (not np.isfinite(raw_confidence) or raw_confidence < 0.70
                or line_height < 24 or line_height > 180
                or float(line.get("width", 0.0)) < line_height * 1.5):
            return None
        pad_x = max(4, round(line_height * 0.10))
        pad_y = max(4, round(line_height * 0.10))
        crop = image[max(0, top - pad_y):min(image.shape[0], bottom + pad_y),
                     max(0, left - pad_x):min(image.shape[1], right + pad_x)]
        if crop.size == 0:
            return None
        try:
            result = self._get_engine().recognize_txt([crop])
            texts = getattr(result, "txts", None) or ()
            scores = getattr(result, "scores", None) or ()
            candidate = str(texts[0] or "").strip()
            confidence = float(scores[0])
        except Exception:
            LOGGER.debug("Numeric-looking external OCR crop retry failed", exc_info=True)
            return None
        words = re.findall(r"[A-Za-z]+", candidate)
        if (len(words) != 1 or not re.fullmatch(r"[A-Za-z]{3,7}[.!?…]?", candidate)
                or not np.isfinite(confidence) or confidence < 0.95
                or not self._is_english_source_text(candidate)
                or self._is_likely_external_sound_effect(candidate)):
            return None
        valid_words, total_words = self._english_word_counts(candidate)
        if valid_words != 1 or total_words != 1:
            return None
        spellchecker = self._get_spellchecker()
        if spellchecker is None or words[0].casefold() not in spellchecker:
            return None
        frequency = getattr(spellchecker, "word_usage_frequency", None)
        if callable(frequency) and frequency(words[0].casefold()) < 1e-4:
            return None
        return dict(
            line, text=candidate, confidence=round(confidence, 4),
            ocr_rescued=True,
            ocr_recovery_method="numeric_external_candidate_crop_retry",
        )

    def _extract_external_lines(self, image, bubbles, reading_direction, include_annotations=False):
        """OCR the page in overlapping tiles, excluding detected speech balloons."""
        height, width = image.shape[:2]
        tile_size, overlap = 1800, 192

        def starts(length):
            return _external_ocr_tile_intervals(length, tile_size, overlap)

        found = []
        counter_candidates = []
        # Outside-bubble lettering is often drawn over detailed artwork, so a
        # false positive can damage the page more than a missed caption. Keep
        # this stricter than bubble OCR, while still allowing small stylized
        # text whose recognizer confidence is moderate.
        external_min_confidence = max(self.min_confidence, 0.55)
        protected_bubbles = []
        edge_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (EXTERNAL_BUBBLE_EDGE_MARGIN * 2 + 1, EXTERNAL_BUBBLE_EDGE_MARGIN * 2 + 1),
        )
        for bubble in bubbles:
            bx1, by1, bx2, by2 = bubble.bbox
            protected_mask = cv2.dilate(bubble.safe_mask, edge_kernel, iterations=1)
            protected_bubbles.append(((bx1, by1, bx2, by2), protected_mask))
        # Index by coarse image cell so tile-overlap duplicates with slightly
        # different OCR spelling can still be compared in near-constant time.
        # 512px cells also cover moderate box shifts on large title cards.
        dedupe_cell = 512
        dedupe_index = {}
        for tile_top, tile_bottom in starts(height):
            for tile_left, tile_right in starts(width):
                tile = image[tile_top:tile_bottom, tile_left:tile_right]
                scale = min(1.0, 1800.0 / max(tile.shape[:2]))
                if scale < 1.0:
                    tile = cv2.resize(tile, None, fx=scale, fy=scale,
                                      interpolation=cv2.INTER_AREA)
                # `tile` is already BGR uint8, the native input expected by
                # RapidOCR. Passing it directly avoids a temporary PNG per tile.
                result = self._get_engine()(tile)
                if result is None or result.boxes is None:
                    continue
                for box, text, score in zip(result.boxes, result.txts, result.scores):
                    text = str(text).strip()
                    try:
                        confidence = float(score)
                    except (TypeError, ValueError):
                        continue
                    is_counter = self._is_counter_annotation(text)
                    numeric_retry_candidate = bool(re.fullmatch(r"\d{3,5}[.,!?…]?", text))
                    if (not text or not np.isfinite(confidence)
                            or confidence < external_min_confidence
                            or (not is_counter and not numeric_retry_candidate and (
                                not self._is_english_source_text(text)
                                or self._is_likely_external_sound_effect(text)
                                or not self._looks_like_dialogue(text)))):
                        continue
                    try:
                        points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
                    except (TypeError, ValueError):
                        continue
                    if len(points) < 4 or not np.isfinite(points).all():
                        continue
                    points /= scale
                    points[:, 0] += tile_left
                    points[:, 1] += tile_top
                    # OCR boxes should lie within the source page. Clip small
                    # edge excursions before rasterization, and reject boxes
                    # whose polygon has no visible intersection with the page.
                    if (points[:, 0].max() < 0 or points[:, 1].max() < 0
                            or points[:, 0].min() >= width or points[:, 1].min() >= height):
                        continue
                    points[:, 0] = np.clip(points[:, 0], 0, width - 1)
                    points[:, 1] = np.clip(points[:, 1], 0, height - 1)
                    left, top = float(points[:, 0].min()), float(points[:, 1].min())
                    right, bottom = float(points[:, 0].max()), float(points[:, 1].max())
                    # Drop only tiny isolated lowercase OCR fragments. Larger
                    # single-glyph boxes can be stylized lettering or sound effects.
                    if (len(text) == 1 and text.islower()
                            and right - left <= 24 and bottom - top <= 24):
                        continue
                    if right - left < 6 or bottom - top < 6:
                        continue
                    angle = float(np.degrees(np.arctan2(
                        points[1, 1] - points[0, 1], points[1, 0] - points[0, 0]
                    )))
                    while angle > 90:
                        angle -= 180
                    while angle < -90:
                        angle += 180
                    ix1, iy1 = max(0, int(left)), max(0, int(top))
                    ix2, iy2 = min(width, int(np.ceil(right))), min(height, int(np.ceil(bottom)))
                    text_mask = np.zeros((iy2 - iy1, ix2 - ix1), dtype=np.uint8)
                    local_points = np.rint(points - np.array([ix1, iy1], dtype=np.float32)).astype(np.int32)
                    try:
                        cv2.fillConvexPoly(text_mask, local_points, 255)
                    except cv2.error:
                        text_mask[:] = 255
                    text_area = max(1, int(np.count_nonzero(text_mask)))
                    bubble_overlap = 0.0
                    for (bx1, by1, bx2, by2), protected_mask in protected_bubbles:
                        x1, y1, x2, y2 = max(ix1, bx1), max(iy1, by1), min(ix2, bx2), min(iy2, by2)
                        if x2 <= x1 or y2 <= y1:
                            continue
                        inside = protected_mask[y1 - by1:y2 - by1, x1 - bx1:x2 - bx1] > 0
                        glyph_polygon = text_mask[y1 - iy1:y2 - iy1, x1 - ix1:x2 - ix1] > 0
                        bubble_overlap = max(
                            bubble_overlap,
                            np.count_nonzero(inside & glyph_polygon) / text_area,
                        )
                    # External lettering is rendered across its OCR box. Reject
                    # boxes crossing balloon interiors to avoid painting over
                    # the balloon edge, even when most of the box is outside.
                    if bubble_overlap >= EXTERNAL_BUBBLE_OVERLAP_MAX:
                        continue
                    line = {
                        "text": text, "left": left, "top": top,
                        "width": right - left, "height": bottom - top,
                        "confidence": round(confidence, 4), "region_type": "external_text",
                        "ocr_polygon": points.tolist(), "angle": angle,
                    }
                    if is_counter:
                        counter_candidates.append(line)
                        continue
                    if numeric_retry_candidate:
                        line = self._rescue_numeric_external_candidate(image, line)
                        if line is None:
                            continue
                        text = line["text"]
                    text_key = " ".join(text.casefold().split())
                    cell_x, cell_y = int(left // dedupe_cell), int(top // dedupe_cell)
                    nearby = (
                        dedupe_index.get((cell_x + dx, cell_y + dy), ())
                        for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                    )
                    duplicate = None
                    for bucket in nearby:
                        for item in bucket:
                            if item.get("_dedupe_removed"):
                                continue
                            overlap_w = max(0.0, min(right, item["left"] + item["width"])
                                            - max(left, item["left"]))
                            overlap_h = max(0.0, min(bottom, item["top"] + item["height"])
                                            - max(top, item["top"]))
                            intersection = overlap_w * overlap_h
                            smaller_area = min(
                                max(1.0, (right - left) * (bottom - top)),
                                max(1.0, item["width"] * item["height"]),
                            )
                            if intersection / smaller_area < 0.72:
                                continue
                            old_key = " ".join(item["text"].casefold().split())
                            similarity = SequenceMatcher(None, text_key, old_key).ratio()
                            length_similarity = min(len(text_key), len(old_key)) / max(
                                1, max(len(text_key), len(old_key))
                            )
                            if text_key == old_key or (
                                length_similarity >= 0.72 and similarity >= 0.72
                            ):
                                duplicate = item
                                break
                        if duplicate is not None:
                            break
                    if duplicate is None:
                        found.append(line)
                        dedupe_index.setdefault((cell_x, cell_y), []).append(line)
                    elif confidence > duplicate["confidence"]:
                        duplicate["_dedupe_removed"] = True
                        found.append(line)
                        dedupe_index.setdefault((cell_x, cell_y), []).append(line)
                    else:
                        line["_dedupe_removed"] = True
        found = [line for line in found if not line.get("_dedupe_removed")]
        for line in found:
            line.pop("_dedupe_removed", None)
        found = self._rescue_external_line_recognition(image, found)
        found.sort(key=lambda line: (
            line["top"], -line["left"] if reading_direction == "rtl" else line["left"]
        ))
        annotations = []
        for counter in counter_candidates:
            cy = counter["top"] + counter["height"] / 2
            cx = counter["left"]
            associated = any(
                0 <= cx - (line["left"] + line["width"]) <= max(320, line["height"] * 10)
                and abs(cy - (line["top"] + line["height"] / 2))
                    <= max(24, line["height"] * 0.8)
                for line in found
            )
            if associated:
                annotations.append({**counter, "region_type": "nontranslatable_counter"})
        if include_annotations:
            return found, annotations
        return found

    @staticmethod
    def _split_bubble_columns(lines, left, right, top=None, bottom=None):
        if not lines:
            return []
        top = int(top if top is not None else min(line["top"] for line in lines))
        bottom = int(bottom if bottom is not None else max(line["top"] + line["height"] for line in lines))
        full_clip = (left, top, right, bottom)
        if len(lines) < 4:
            return [{"lines": lines, "clip": full_clip}]

        # A detector can merge vertically stacked bubbles into one mask. Split
        # clearly separated OCR rows before considering side-by-side columns.
        by_y = sorted(
            ((line["top"] + line["height"] / 2, line) for line in lines),
            key=lambda pair: pair[0],
        )
        vertical_gaps = [
            (by_y[index + 1][0] - by_y[index][0], index)
            for index in range(len(by_y) - 1)
        ]
        y_gap, y_split = max(vertical_gaps, default=(0, 0))
        upper = [line for _, line in by_y[:y_split + 1]]
        lower = [line for _, line in by_y[y_split + 1:]]
        typical_height = sum(line["height"] for line in lines) / len(lines)
        text_span = max(1, by_y[-1][0] - by_y[0][0] + typical_height)
        # Two lines are enough to represent a second utterance. Requiring three
        # rejected real webtoon bubbles made of a long balloon followed by a
        # short two-line response/name. The stronger gap prevents splitting a
        # normal paragraph with a modest annotation gap beside its last lines.
        if (len(upper) >= 2 and len(lower) >= 2
                and y_gap >= max(typical_height * 2.4, text_span * 0.32)):
            divider = int(round((by_y[y_split][0] + by_y[y_split + 1][0]) / 2))
            if divider - top >= (bottom - top) * 0.2 and bottom - divider >= (bottom - top) * 0.2:
                ordered = lambda group: sorted(group, key=lambda item: (item["top"], item["left"]))
                return [
                    {"lines": ordered(upper), "clip": (left, top, right, divider)},
                    {"lines": ordered(lower), "clip": (left, divider, right, bottom)},
                ]

        centers = sorted(
            ((line["left"] + line["width"] / 2, line) for line in lines),
            key=lambda pair: pair[0],
        )
        gaps = [(centers[index + 1][0] - centers[index][0], index) for index in range(len(centers) - 1)]
        gap, split_at = max(gaps, default=(0, 0))
        if gap < (right - left) * 0.22:
            return [{"lines": lines, "clip": full_clip}]
        first = [line for _, line in centers[:split_at + 1]]
        second = [line for _, line in centers[split_at + 1:]]
        if min(len(first), len(second)) < 3:
            return [{"lines": lines, "clip": full_clip}]
        first_top = min(line["top"] for line in first)
        first_bottom = max(line["top"] + line["height"] for line in first)
        second_top = min(line["top"] for line in second)
        second_bottom = max(line["top"] + line["height"] for line in second)
        vertical_overlap = max(0, min(first_bottom, second_bottom) - max(first_top, second_top))
        shorter_span = min(first_bottom - first_top, second_bottom - second_top)
        if vertical_overlap < shorter_span * 0.25:
            return [{"lines": lines, "clip": full_clip}]
        divider = int(round((centers[split_at][0] + centers[split_at + 1][0]) / 2))
        if divider - left < (right - left) * 0.28 or right - divider < (right - left) * 0.28:
            return [{"lines": lines, "clip": full_clip}]
        ordered = lambda group: sorted(group, key=lambda item: (item["top"], item["left"]))
        return [
            {"lines": ordered(first), "clip": (left, top, divider, bottom)},
            {"lines": ordered(second), "clip": (divider, top, right, bottom)},
        ]

    @staticmethod
    def _looks_like_dialogue(text):
        words = re.findall(r"[A-Za-z]+", text.upper())
        if not words:
            return False
        # Ignore pure repeated-letter cries/sound marks such as EEE EEE EK,
        # while keeping ordinary short dialogue such as "No, no!".
        return not any(
            len(word) >= 2 and len(set(word)) == 1 and words.count(word) >= 2
            and all(len(item) <= 4 for item in words)
            for word in words
        )

    @staticmethod
    def _is_english_source_text(text):
        """Keep English-source OCR regions, rejecting mixed-script noise."""
        letters = [character for character in text if character.isalpha()]
        if not letters:
            return False
        latin_letters = sum(character.isascii() and character.isalpha() for character in letters)
        return latin_letters / len(letters) >= 0.65 and bool(re.search(r"[A-Za-z]", text))

    @staticmethod
    def _is_chapter_marker(text):
        """Recognize a standalone chapter label that bubble models may mistake for dialogue."""
        return bool(re.fullmatch(
            r"\s*(?:CH|CHAPTER)\s*[-#:]?\s*\d{1,5}\s*", str(text), re.IGNORECASE
        ))

    @staticmethod
    def _is_counter_annotation(text):
        """Recognize compact bracketed progress counters, not arbitrary digits."""
        return bool(re.fullmatch(
            r"\[\s*\d{1,4}\s*/\s*\d{1,4}\s*(?:KM|M|KM\.)?\s*\]",
            str(text).strip(), re.IGNORECASE,
        ))

    @staticmethod
    def is_likely_nonverbal_effect(text):
        """Match a conservative list of standalone all-caps comic effects."""
        words = re.findall(r"[A-Za-z]+", str(text))
        return (
            len(words) == 1
            and words[0].isupper()
            and words[0] in COMMON_EXTERNAL_SOUND_EFFECTS
        )

    @staticmethod
    def _is_likely_external_sound_effect(text):
        """Backward-compatible alias for standalone comic-effect matching."""
        return LocalRapidOCR.is_likely_nonverbal_effect(text)

    @staticmethod
    def _remove_trailing_orphan_tokens(lines):
        # Decorative notes often become a stray OCR letter after a complete
        # sentence inside a cloud-shaped balloon (for example ``! J``).
        cleaned = list(lines)
        while len(cleaned) > 1:
            token = cleaned[-1]["text"].strip()
            previous = cleaned[-2]["text"].rstrip()
            if re.fullmatch(r"[A-Za-z0-9]", token) and previous.endswith((".", "!", "?")):
                cleaned.pop()
            else:
                break
        return cleaned

    @staticmethod
    def _mask_bbox(mask, clip, origin=(0, 0)):
        left, top, right, bottom = (int(value) for value in clip)
        origin_x, origin_y = (int(value) for value in origin)
        crop_left, crop_top = max(left, origin_x), max(top, origin_y)
        crop_right = min(right, origin_x + mask.shape[1])
        crop_bottom = min(bottom, origin_y + mask.shape[0])
        if crop_right <= crop_left or crop_bottom <= crop_top:
            return None
        local = mask[crop_top - origin_y:crop_bottom - origin_y,
                     crop_left - origin_x:crop_right - origin_x]
        ys, xs = np.where(local > 0)
        if not len(xs):
            return None
        return (crop_left + int(xs.min()), crop_top + int(ys.min()),
                crop_left + int(xs.max()) + 1, crop_top + int(ys.max()) + 1)

    @staticmethod
    def associate_lines_with_bubbles(image_bytes: bytes, lines, reading_direction="ltr"):
        """Filter optional external-OCR lines to bubble interiors and group per utterance."""
        image = decode_color_image(image_bytes)
        if image is None:
            raise ValueError("تعذر فتح صورة الصفحة.")
        bubbles = detect_speech_bubbles(image)
        filtered, regions = [], []
        for bubble_id, bubble in enumerate(bubbles, 1):
            x1, y1, x2, y2 = bubble.bbox
            found = []
            for item in lines:
                try:
                    text = str(item.get("text", "")).strip()
                    left, top = float(item.get("left", 0)), float(item.get("top", 0))
                    width, height = float(item.get("width", 0)), float(item.get("height", 0))
                    confidence = float(item.get("confidence", 0.5))
                except (TypeError, ValueError):
                    continue
                if (not text or not np.isfinite([left, top, width, height, confidence]).all()
                        or width <= 0 or height <= 0
                        or not LocalRapidOCR._is_english_source_text(text)):
                    continue
                ix1, iy1 = max(0, int(left)), max(0, int(top))
                ix2, iy2 = min(image.shape[1], int(np.ceil(left + width))), min(image.shape[0], int(np.ceil(top + height)))
                overlap_left, overlap_top = max(ix1, x1), max(iy1, y1)
                overlap_right, overlap_bottom = min(ix2, x2), min(iy2, y2)
                if overlap_right <= overlap_left or overlap_bottom <= overlap_top:
                    continue
                box_mask = bubble.safe_mask[
                    overlap_top - y1:overlap_bottom - y1,
                    overlap_left - x1:overlap_right - x1,
                ]
                box_area = max(1, (ix2 - ix1) * (iy2 - iy1))
                if np.count_nonzero(box_mask) / box_area < 0.5:
                    continue
                found.append({"text": text, "left": left, "top": top, "width": width,
                              "height": height, "confidence": confidence, "bubble_id": bubble_id})
            found.sort(key=lambda line: (line["top"], -line["left"] if reading_direction == "rtl" else line["left"]))
            found = LocalRapidOCR._remove_trailing_orphan_tokens(found)
            if not found:
                continue
            for cluster in LocalRapidOCR._split_bubble_columns(found, x1, x2, y1, y2):
                clip_left, clip_top, clip_right, clip_bottom = cluster["clip"]
                members = cluster["lines"]
                if not LocalRapidOCR._looks_like_dialogue(" ".join(line["text"] for line in members)):
                    continue
                region_box = LocalRapidOCR._mask_bbox(
                    bubble.safe_mask, (clip_left, clip_top, clip_right, clip_bottom), (x1, y1)
                )
                if region_box is None:
                    continue
                region_left, region_top, region_right, region_bottom = region_box
                filtered.extend(members)
                regions.append({
                    "text": " ".join(line["text"] for line in members),
                    "left": region_left, "top": region_top,
                    "width": region_right - region_left, "height": region_bottom - region_top,
                    "confidence": sum(line["confidence"] for line in members) / len(members),
                    "bubble_id": bubble_id, "bubble_confidence": bubble.confidence,
                    "bubble_bbox": list(region_box),
                    "ocr_boxes": [[line["left"], line["top"], line["width"], line["height"]] for line in members],
                })
        return filtered, regions

    @staticmethod
    def group_lines(lines) -> list[dict]:
        """Join nearby external lettering lines that form one caption block."""
        max_vertical_gap_ratio = 2.0
        regions = []
        for line_index, line in enumerate(lines):
            best = None
            for region in reversed(regions):
                last = region["lines"][-1]
                vertical_gap = line["top"] - (last["top"] + last["height"])
                if vertical_gap > max(line["height"], last["height"]) * max_vertical_gap_ratio:
                    # Regions can be interleaved when OCR returns alternating
                    # lines from two nearby caption columns. Their last-line Y
                    # positions are therefore not sorted with region creation
                    # order; a distant newer region must not hide an older,
                    # vertically adjacent match later in this scan.
                    continue
                overlap = max(
                    0,
                    min(line["left"] + line["width"], last["left"] + last["width"])
                    - max(line["left"], last["left"]),
                )
                overlap_ratio = overlap / max(1, min(line["width"], last["width"]))
                # OCR often returns tall/overlapping boxes for outlined comic
                # text. Tolerate modest overlap but require a shared column so
                # adjacent captions are less likely to merge.
                previous_angle = float(last.get("angle", 0.0))
                current_angle = float(line.get("angle", 0.0))
                angle_difference = abs(current_angle - previous_angle) % 180.0
                angle_difference = min(angle_difference, 180.0 - angle_difference)
                same_slanted_block = (
                    abs(previous_angle) >= 8.0
                    and abs(current_angle) >= 8.0
                    and angle_difference <= 6.0
                )
                minimum_gap_ratio = -0.80 if same_slanted_block else -0.35
                crosses_orientation = (
                    vertical_gap > 0 and angle_difference >= 12.0
                    and max(abs(previous_angle), abs(current_angle)) >= 12.0
                    and min(abs(previous_angle), abs(current_angle)) <= 6.0
                )
                if (not crosses_orientation
                        and minimum_gap_ratio <= vertical_gap / max(1, last["height"])
                        <= max_vertical_gap_ratio
                        and overlap_ratio >= 0.35):
                    best = region
                    break
            if best is None:
                regions.append({"lines": [line], "indexes": [line_index]})
            else:
                best["lines"].append(line)
                best["indexes"].append(line_index)

        output = []
        for region in regions:
            members = region["lines"]
            left = min(line["left"] for line in members)
            top = min(line["top"] for line in members)
            right = max(line["left"] + line["width"] for line in members)
            bottom = max(line["top"] + line["height"] for line in members)
            output.append({
                "text": " ".join(line["text"] for line in members),
                "left": round(left, 2),
                "top": round(top, 2),
                "width": round(right - left, 2),
                "height": round(bottom - top, 2),
                "confidence": round(sum(line["confidence"] for line in members) / len(members), 4),
                "line_indexes": region["indexes"],
            })
        return output

    def detect_regions_only(self, image_bytes: bytes, reading_direction: str = "ltr",
                            include_external: bool = True) -> dict:
        """Detect balloon/text crop boxes without running local character recognition."""
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise ValueError("Image data must be non-empty bytes.")
        if reading_direction not in {"ltr", "rtl"}:
            raise ValueError("reading_direction must be 'ltr' or 'rtl'.")
        started = perf_counter()
        image = decode_color_image(image_bytes)
        if image is None:
            raise ValueError("تعذر فتح صورة الصفحة.")

        detections = []
        detector = _get_comic_text_detector()
        if detector.available:
            detections = detector.detect(image)
        # The renderer performs the authoritative bubble-mask pass once, after
        # translations arrive. Re-running segmentation here duplicated that
        # expensive inference on every page before the cloud OCR call.
        bubble_boxes = [item for item in detections if item.label == "bubble"]

        regions = []
        matched_bubbles = set()

        def bubble_for_box(box):
            left, top, right, bottom = box
            area = max(1, (right - left) * (bottom - top))
            best = None
            best_overlap = 0.0
            center_x, center_y = (left + right) // 2, (top + bottom) // 2
            for bubble_index, bubble in enumerate(bubble_boxes, 1):
                bx1, by1, bx2, by2 = bubble.bbox
                ix1, iy1, ix2, iy2 = max(left, bx1), max(top, by1), min(right, bx2), min(bottom, by2)
                overlap = max(0, ix2 - ix1) * max(0, iy2 - iy1) / area
                local_x, local_y = center_x - bx1, center_y - by1
                inside = bx1 <= center_x < bx2 and by1 <= center_y < by2
                score = overlap + (0.25 if inside else 0.0)
                if score > best_overlap:
                    best, best_overlap = (bubble_index, bubble), score
            return best if best_overlap >= 0.30 else None

        for detection in detections:
            if detection.label not in {"text_bubble", "text_free"}:
                continue
            left, top, right, bottom = (int(value) for value in detection.bbox)
            if right <= left or bottom <= top:
                continue
            matched = bubble_for_box((left, top, right, bottom))
            is_bubble_text = detection.label == "text_bubble" or matched is not None
            if not is_bubble_text and not include_external:
                continue
            region = {
                "text": "",
                "left": left, "top": top,
                "width": right - left, "height": bottom - top,
                "confidence": round(float(detection.confidence), 4),
                "region_type": "speech_bubble" if is_bubble_text else "external_text",
                "ocr_boxes": [[left, top, right - left, bottom - top]],
                "ocr_polygons": [],
            }
            if matched is not None:
                bubble_index, bubble = matched
                matched_bubbles.add(bubble_index)
                region.update({
                    "bubble_id": bubble_index,
                    "bubble_bbox": list(bubble.bbox),
                    "bubble_confidence": round(float(bubble.confidence), 4),
                })
            regions.append(region)

        # Preserve coverage for balloons where the text-box detector emits no
        # box: Gemini can inspect the balloon crop, while no local OCR runs.
        for bubble_index, bubble in enumerate(bubble_boxes, 1):
            if bubble_index in matched_bubbles:
                continue
            left, top, right, bottom = bubble.bbox
            if right <= left or bottom <= top:
                continue
            regions.append({
                "text": "",
                "left": left, "top": top,
                "width": right - left, "height": bottom - top,
                "confidence": round(float(bubble.confidence), 4),
                "region_type": "speech_bubble",
                "bubble_id": bubble_index,
                "bubble_bbox": list(bubble.bbox),
                "bubble_confidence": round(float(bubble.confidence), 4),
                "ocr_boxes": [[left, top, right - left, bottom - top]],
                "ocr_polygons": [],
                "detection_fallback": "whole_bubble_crop",
            })

        regions.sort(key=lambda item: (
            item["top"],
            -item["left"] if reading_direction == "rtl" else item["left"],
        ))
        for index, region in enumerate(regions, 1):
            region["region_index"] = index
        return {
            "text": "",
            "lines": [],
            "regions": regions,
            "provider": "comic_text_detector_boxes; bubble segmentation deferred to renderer",
            "elapsed_seconds": round(perf_counter() - started, 4),
            "bubble_detection_seconds": None,
            "comic_text_detection_seconds": round(perf_counter() - started, 4),
            "local_recognition_skipped": True,
        }
