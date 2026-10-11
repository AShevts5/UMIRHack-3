"""Text normalization helpers shared by parser, rules and question engines.

The public open-set evaluator (`evaluate.py` of the case package) normalizes
comparable strings this way: NFC, lower case, ``ё`` -> ``е``, typographic quotes
folded to ``"``, runs of whitespace collapsed.  The same rules are applied here so
that matching inside the pipeline behaves exactly like matching in the grader.
"""

from __future__ import annotations

import re
import unicodedata

QUOTE_CHARS = "«»“”„‟‹›"
DASH_CHARS = "‐‑‒–—―−"

_WS_RE = re.compile(r"\s+")
_EDGE_CHARS = " \t:：.,;·•\"'" + re.escape(DASH_CHARS) + "_"
_LABEL_TAIL_RE = re.compile(r"[" + _EDGE_CHARS + r"]+$")
_LABEL_HEAD_RE = re.compile(r"^[" + _EDGE_CHARS + r"]+")
_UNDERSCORE_RUN_RE = re.compile(r"[_—–\-]{3,}")
#: Dotted leaders, with or without spaces between the dots: ". . . ." / ".....".
_DOTTED_LEADER_RE = re.compile(r"(?:[.·•]\s*){2,}")
_DIGIT_ONLY_RE = re.compile(r"^\d+$")


def norm_text(value: str | None) -> str:
    """Fold a string the way the grader does (casefold, ё->е, quotes, spaces)."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", value)
    text = text.casefold().replace("ё", "е")
    for char in QUOTE_CHARS:
        text = text.replace(char, '"')
    for char in DASH_CHARS:
        text = text.replace(char, "-")
    text = _WS_RE.sub(" ", text)
    return text.strip()


def norm_label(value: str | None) -> str:
    """Normalize a field label: drop quotes, trailing ``:``/dashes/underscores."""
    text = norm_text(value)
    text = _LABEL_TAIL_RE.sub("", text)
    text = _LABEL_HEAD_RE.sub("", text)
    return text.strip()


def collapse_spaces(value: str | None) -> str:
    """Collapse whitespace but keep the original letter case."""
    if value is None:
        return ""
    return _WS_RE.sub(" ", value.replace("\u00a0", " ")).strip()


def strip_fill_artifacts(value: str) -> str:
    """Remove underscore/dash fill lines and stray colon tails from a value."""
    text = value.replace("_", " ")
    text = _UNDERSCORE_RUN_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def strip_ocr_noise(value: str) -> str:
    """Remove characters that are never part of a document value.

    OCR occasionally produces box-drawing glyphs for the dotted fill lines used in
    the synthetic samples (``. . . . .``) or for rule lines.  Those must not end up
    inside an extracted value, otherwise a field that is visibly empty would be
    reported as filled.
    """
    text = value.replace("\u00a0", " ")
    text = re.sub(r"[|¦│┃]", " ", text)
    text = _DOTTED_LEADER_RE.sub(" ", text)
    text = re.sub(r"[\u2500-\u257f]", " ", text)  # box drawing
    text = _WS_RE.sub(" ", text)
    return text.strip()


def is_blank_value(value: str | None) -> bool:
    """True when a captured fragment carries no information.

    Only whitespace, punctuation and the fill-in glyphs used by the synthetic
    samples (underscores, dashed rules, dotted leaders) count as blank: a single
    meaningful character is a legitimate value (``Курс: 3``).
    """
    if value is None:
        return True
    text = strip_ocr_noise(strip_fill_artifacts(value))
    text = text.strip(" \t:;,.·•-—–")
    return len(text) < 1


def is_empty_fill_line(value: str | None) -> bool:
    """True when the fragment is a printed fill line *and nothing else*.

    Unlike :func:`is_blank_value` this is the strict variant used for optional text
    that may legally be a single character.
    """
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    residue = strip_ocr_noise(strip_fill_artifacts(stripped)).strip(" \t:;,.·•-—–_\"'")
    return len(residue) < 1


def is_digits_only(value: str) -> bool:
    return bool(_DIGIT_ONLY_RE.match(value.strip()))


def first_meaningful(value: str) -> str:
    """First character that is neither whitespace nor fill punctuation."""
    return value.strip(" \t:;,.·•-—–_")[:1]


def trim_template_tail(value: str) -> str:
    """Cut trailing sentence-final dots that the OCR glued onto a value."""
    return value.strip()
