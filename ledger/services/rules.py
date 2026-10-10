"""Match imported rows to tracked funds and spending categories."""

import re

from ..models import WorktagType

CODE_PATTERN = re.compile(r"(?<![A-Z0-9])([A-Z]{2,4}\d{4,10})(?![A-Z0-9])")
# Worktag-like IDs that are never a fund you track (fund, class, function, spend category, documents).
IGNORED_CODE_PREFIXES = {"FD", "CL", "FN", "SC", "EXP", "PO", "REQ", "INV", "JE", "SI", "PR"}
# Project and program worktags can belong to any kind of fund, so they are left for you to choose.
PREFIX_TYPES = {
    "AWD": WorktagType.GRANT,
    "GR": WorktagType.GRANT,
    "GF": WorktagType.GIFT,
    "CC": WorktagType.STATE,
}
LABEL_TYPES = [
    ("cost center", WorktagType.STATE),
    ("grant", WorktagType.GRANT),
    ("award", WorktagType.GRANT),
    ("gift", WorktagType.GIFT),
]


def keyword_regex(keywords: str) -> re.Pattern | None:
    """'travel, per diem, 5161*' -> regex matching whole words/phrases (or word starts for *)."""
    parts = []
    for keyword in keywords.split(","):
        keyword = keyword.strip()
        is_prefix = keyword.endswith("*")
        keyword = keyword.rstrip("*").strip()
        if not keyword:
            continue
        body = r"\s+".join(re.escape(word) for word in keyword.split())
        parts.append(rf"(?<!\w){body}" + ("" if is_prefix else r"(?!\w)"))
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


class CategoryMatcher:
    def __init__(self, rules):
        self.compiled = [(regex, rule.category) for rule in rules if (regex := keyword_regex(rule.keywords))]

    def match(self, primary: str, secondary: str = ""):
        """Try every rule on the file's category text first, then on the description."""
        for text in (primary, secondary):
            if not text:
                continue
            for regex, category in self.compiled:
                if regex.search(text):
                    return category
        return None


class WorktagMatcher:
    def __init__(self, worktags):
        self.entries = [
            (re.compile(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", re.IGNORECASE), worktag)
            for worktag in worktags
            for code in worktag.all_codes
        ]
        self.known_codes = {code.upper() for worktag in worktags for code in worktag.all_codes}

    def match(self, text: str):
        """Return (worktag or None, reason). The most specific fund type wins; ties are ambiguous."""
        if not text:
            return None, "no worktag in row"
        found = {worktag.pk: worktag for regex, worktag in self.entries if regex.search(text)}
        if not found:
            return None, "no tracked fund"
        top = min(worktag.priority for worktag in found.values())
        best = [worktag for worktag in found.values() if worktag.priority == top]
        if len(best) > 1:
            return None, "several tracked funds: " + ", ".join(sorted(w.code for w in best))
        return best[0], ""

    def unknown_codes(self, text: str) -> set[str]:
        codes = set()
        for code in CODE_PATTERN.findall(text.upper()):
            letters = re.match(r"[A-Z]+", code).group()
            if code not in self.known_codes and letters not in IGNORED_CODE_PREFIXES:
                codes.add(code)
        return codes


def describe_code(code: str, text: str) -> tuple[str, str]:
    """Guess (worktag type, name) for an unknown code from text like 'Grant: GR00012345 Solar Study'."""
    letters = re.match(r"[A-Z]+", code).group()
    worktag_type = PREFIX_TYPES.get(letters, "")
    name = ""
    for line in re.split(r"[\n;|]", text):
        position = line.upper().find(code)
        if position < 0:
            continue
        label = line[:position].lower()
        if not worktag_type:
            worktag_type = next((t for word, t in LABEL_TYPES if word in label), "")
        rest = line[position + len(code) :]
        rest = re.split(r"\b[A-Z][A-Za-z ]{2,24}:\s", rest)[0]
        name = rest.strip(" -–:()[],")[:120]
        break
    return worktag_type, name
