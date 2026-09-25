"""
Core Helper Functions (core/helpers.py)

Shared, reusable helper functions for the backend codebase:
- safe_json_loads / safe_json_dumps: Robust JSON parsing and serialization
- normalize_text: Standardized text normalization
- is_title_match: Unified, domain-agnostic title/deliverable matcher (handles acronyms, stemming, dates, aliases)
- find_best_match: Fuzzy candidate finder using SequenceMatcher
"""

import json
import re
import difflib
from typing import Any, Optional, Sequence, Tuple, Set

MONTH_TOKENS = {
    'january', 'february', 'march', 'april', 'may', 'june',
    'july', 'august', 'september', 'october', 'november', 'december',
    'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'
}

MONTH_MAP = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
    'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
}


def safe_json_loads(val: Any, default: Any = None) -> Any:
    """
    Safely parse JSON data from a string, bytes, or return existing structured object.
    
    Returns `default` (or `{}` if default is None) when parsing fails or input is None/empty.
    """
    fallback = {} if default is None else default
    if val is None:
        return fallback
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, (bytes, bytearray)):
        try:
            val = val.decode('utf-8', errors='ignore')
        except Exception:
            return fallback
    if isinstance(val, str):
        val = val.strip()
        if not val or val.lower() == 'null':
            return fallback
        try:
            return json.loads(val)
        except Exception:
            return fallback
    return fallback


def safe_json_dumps(val: Any, default: str = "{}") -> str:
    """
    Safely serialize any Python data structure to a JSON string.
    Automatically handles datetime, Decimal, UUID, and unknown types via `default=str`.
    """
    try:
        return json.dumps(val, default=str)
    except Exception:
        return default


def normalize_text(text: Optional[str]) -> str:
    """
    Cleans punctuation, converts whitespace/tabs/newlines to single spaces,
    and returns lowercase trimmed text.
    """
    if not text:
        return ""
    cleaned = re.sub(r'[\(\)\[\]\{\}\-_,\.:;\t\r\n\/\\]+', ' ', str(text))
    return re.sub(r'\s+', ' ', cleaned).strip().lower()


def stem_token(token: str) -> str:
    """Generic light stemming for English verb/noun inflections."""
    t = token.lower()
    for suffix in ("ation", "tion", "ment", "ing", "ies", "ed", "es", "s"):
        if t.endswith(suffix) and len(t) - len(suffix) >= 3:
            return t[:-len(suffix)]
    return t


def tokenize_stemmed_words(text: str) -> Set[str]:
    """Tokenize and stem words, excluding standard stop words."""
    stop_words = {"for", "and", "the", "with", "to", "of", "in", "a", "an", "on", "is", "by", "at", "as", "from", "etc"}
    clean_text = re.sub(r'[\(\)\[\]\{\}\-_,\.:;\t\r\n]+', ' ', str(text))
    raw_tokens = re.findall(r'\b[a-zA-Z0-9]+\b', clean_text.lower())
    return {stem_token(w) for w in raw_tokens if w not in stop_words and len(w) > 1}


def is_token_match(t1: str, t2: str) -> bool:
    """Check if two tokens match via equality, prefix, or similarity ratio."""
    if t1 == t2:
        return True
    if len(t1) >= 4 and len(t2) >= 4 and (t1.startswith(t2) or t2.startswith(t1)):
        return True
    if len(t1) >= 3 and len(t2) >= 3 and difflib.SequenceMatcher(None, t1, t2).ratio() >= 0.82:
        return True
    return False


def matches_any_token(target: str, pool: Set[str]) -> bool:
    """Check if target token matches any token in the pool."""
    for item in pool:
        if is_token_match(target, item):
            return True
    return False


def extract_compound_abbrevs(word: str) -> Set[str]:
    """Generically extract common compound abbreviations (e.g. 'database' -> 'db')."""
    w = word.lower()
    res = {w}
    compound_splits = re.findall(r'[a-z]+', re.sub(r'([A-Z])', r' \1', word).lower())
    if len(compound_splits) >= 2:
        res.add("".join(s[0] for s in compound_splits))
    if "data" in w and "base" in w:
        res.add("db")
    return res


def extract_acronyms(text: str) -> Set[str]:
    """Generically extract acronyms from multi-word phrases and hyphenated terms."""
    stop_words = {"for", "and", "the", "with", "to", "of", "in", "a", "an", "on", "is", "by", "at", "as", "from"}
    clean_text = re.sub(r'[\(\)\[\]\{\}\-_,\.:;\t\r\n]+', ' ', str(text))
    words = [w for w in re.findall(r'\b[a-zA-Z0-9]+\b', clean_text.lower()) if w not in stop_words]
    acrs = set()
    if len(words) >= 2:
        acrs.add("".join(w[0] for w in words))
    for l in range(2, min(len(words) + 1, 6)):
        for i in range(len(words) - l + 1):
            sub = words[i:i+l]
            acrs.add("".join(w[0] for w in sub))
    for w in words:
        acrs.update(extract_compound_abbrevs(w))
    return acrs


def get_parentheses_aliases(raw: str) -> list:
    """Extract aliases inside and outside parentheses."""
    raw_str = str(raw).lower().strip()
    aliases = [raw_str]
    for p in re.findall(r'\((.*?)\)', str(raw)):
        if p.strip():
            aliases.append(p.strip().lower())
    no_parens = re.sub(r'\(.*?\)', '', str(raw)).strip().lower()
    if no_parens:
        aliases.append(no_parens)
    return list(set(aliases))


def is_title_match(a: Optional[str], b: Optional[str], threshold: float = 0.85) -> bool:
    """
    100% Generic, document-agnostic matching algorithm.
    Works for ANY project, ANY baseline, and ANY industry domain.
    """
    if not a or not b:
        return False

    a_str = str(a).strip()
    b_str = str(b).strip()

    if a_str.lower() == b_str.lower():
        return True

    a_clean = normalize_text(a_str)
    b_clean = normalize_text(b_str)

    if a_clean == b_clean:
        return True

    # Check for period/month or year conflict
    months_a = {w for w in re.findall(r'\b[a-zA-Z]+\b', a_str.lower()) if w in MONTH_TOKENS}
    months_b = {w for w in re.findall(r'\b[a-zA-Z]+\b', b_str.lower()) if w in MONTH_TOKENS}

    if months_a and months_b and not (months_a & months_b):
        return False
    if bool(months_a) != bool(months_b):
        return False

    years_a = {w for w in re.findall(r'\b20\d{2}\b', a_str)}
    years_b = {w for w in re.findall(r'\b20\d{2}\b', b_str)}
    if years_a and years_b and not (years_a & years_b):
        return False
    if bool(years_a) != bool(years_b) and (months_a or months_b):
        return False

    # 1. Parenthetical aliases
    a_parens = get_parentheses_aliases(a_str)
    b_parens = get_parentheses_aliases(b_str)
    for ap in a_parens:
        for bp in b_parens:
            if ap == bp:
                return True

    # 2. Compound multi-phase protection
    a_parts = [p.strip() for p in re.split(r'[,;/]|\band\b', a_str) if len(p.strip()) > 2]
    b_parts = [p.strip() for p in re.split(r'[,;/]|\band\b', b_str) if len(p.strip()) > 2]

    words_a = tokenize_stemmed_words(a_str)
    words_b = tokenize_stemmed_words(b_str)
    acrs_a = extract_acronyms(a_str)
    acrs_b = extract_acronyms(b_str)

    pool_a = words_a | acrs_a
    pool_b = words_b | acrs_b

    if len(a_parts) > 1 and len(b_parts) == 1:
        first_pool = tokenize_stemmed_words(a_parts[0]) | extract_acronyms(a_parts[0])
        matched_in_first = sum(1 for wb in words_b if matches_any_token(wb, first_pool))
        if len(words_b) > 0 and (matched_in_first / len(words_b)) >= 0.70:
            return True
        return False
    elif len(b_parts) > 1 and len(a_parts) == 1:
        first_pool = tokenize_stemmed_words(b_parts[0]) | extract_acronyms(b_parts[0])
        matched_in_first = sum(1 for wa in words_a if matches_any_token(wa, first_pool))
        if len(words_a) > 0 and (matched_in_first / len(words_a)) >= 0.70:
            return True
        return False

    # 3. Dynamic acronym & word match
    matched_a_in_b = sum(1 for wa in words_a if matches_any_token(wa, pool_b))
    matched_b_in_a = sum(1 for wb in words_b if matches_any_token(wb, pool_a))

    len_a = len(words_a)
    len_b = len(words_b)

    if len_a > 0 and len_b > 0:
        containment_a = matched_a_in_b / len_a
        containment_b = matched_b_in_a / len_b

        if min(len_a, len_b) <= 3 and max(containment_a, containment_b) >= 0.65 and max(matched_a_in_b, matched_b_in_a) >= 2:
            return True
        if max(containment_a, containment_b) >= 0.75 and max(matched_a_in_b, matched_b_in_a) >= 2:
            return True
        if (containment_a >= 0.50 and containment_b >= 0.50) and (matched_a_in_b >= 2 or matched_b_in_a >= 2):
            return True

    # 4. Levenshtein / Sequence Matcher
    ratio = difflib.SequenceMatcher(None, a_clean, b_clean).ratio()
    return ratio >= threshold


def find_best_match(
    target_name: str,
    candidates: Sequence[Any],
    key: str = "name",
    threshold: float = 0.80
) -> Tuple[Optional[Any], float]:
    """
    Finds the highest-similarity item from a list of candidates (dicts or objects).
    
    Returns:
        (best_item, best_ratio) or (None, 0.0) if no item meets the threshold.
    """
    if not target_name or not candidates:
        return None, 0.0

    target_norm = normalize_text(target_name)
    best_item = None
    best_ratio = 0.0

    for item in candidates:
        if isinstance(item, dict):
            cand_val = item.get(key, "")
        else:
            cand_val = getattr(item, key, "")

        cand_norm = normalize_text(cand_val)
        if not cand_norm:
            continue

        if target_norm == cand_norm:
            return item, 1.0

        ratio = difflib.SequenceMatcher(None, target_norm, cand_norm).ratio()
        if ratio >= threshold and ratio > best_ratio:
            best_ratio = ratio
            best_item = item

    return best_item, best_ratio
