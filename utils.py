import re, unicodedata

def normalize_email(s: str) -> str:
    if not s:
        return ""
    # Unicode-normalize, strip “format” chars (incl. zero-width), trim spaces, lowercase
    s = unicodedata.normalize("NFKC", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    s = s.strip().lower()
    # Emails shouldn’t have spaces—just in case, collapse/remove any whitespace
    s = re.sub(r"\s+", "", s)
    return s