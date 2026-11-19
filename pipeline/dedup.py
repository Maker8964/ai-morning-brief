import math
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Item, Story

STOPWORDS = set(
    "a an the and or of to in on for with by at from as is are was were be been its it this that "
    "new how why what who will can could says say said after over into about up out than more "
    "you your we our their they his her has have had just now".split()
)
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")
TRACKING = re.compile(r"^(utm_|ref$|ref_src$|fbclid$|gclid$|mc_|cmpid$|source$)")
SIMILARITY = 0.5
MIN_SHARED = 2
PHRASES = {
    "initial public offering": "ipo",
    "artificial intelligence": "ai",
    "large language model": "llm",
    "open source": "open-source",
    "open weight": "open-weight",
}


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def _stem(tok: str) -> str:
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") else tok


def tokens(title: str) -> set[str]:
    text = title.lower().replace("\u2019", "'").replace("'s ", " ")
    for phrase, repl in PHRASES.items():
        text = text.replace(phrase, repl)
    return {_stem(t) for t in TOKEN_RE.findall(text) if t not in STOPWORDS and len(t) > 1}


def idf_weights(token_sets: list[set[str]]) -> dict[str, float]:
    df: dict[str, int] = {}
    for ts in token_sets:
        for t in ts:
            df[t] = df.get(t, 0) + 1
    n = len(token_sets)
    return {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}


def similarity(a: set[str], b: set[str], idf: dict[str, float] | None = None) -> float:
    """IDF-weighted overlap coefficient: rare shared words (names, products) count most,
    and a short headline can match a long one."""
    if not a or not b:
        return 0.0
    w = (lambda t: idf.get(t, 1.0)) if idf else (lambda t: 1.0)
    shared = sum(w(t) for t in a & b)
    return shared / min(sum(w(t) for t in a), sum(w(t) for t in b))


def is_same_story(a: Item, b: Item, ta: set[str], tb: set[str], idf: dict[str, float] | None = None) -> bool:
    if normalize_url(a.url) == normalize_url(b.url):
        return True
    return len(ta & tb) >= MIN_SHARED and similarity(ta, tb, idf) >= SIMILARITY


def dedup(items: list[Item]) -> list[Story]:
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    toks = [tokens(i.title) for i in items]
    idf = idf_weights(toks)
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if is_same_story(items[i], items[j], toks[i], toks[j], idf):
                parent[find(j)] = find(i)

    groups: dict[int, list[int]] = {}
    for idx in range(len(items)):
        groups.setdefault(find(idx), []).append(idx)

    def centrality(i: int, members: list[int]) -> float:
        return sum(similarity(toks[i], toks[j], idf) for j in members if j != i) + 0.1 * items[i].weight

    # Most representative headline first: it becomes the story's lead.
    return [
        Story(items=[items[i] for i in sorted(m, key=lambda i: centrality(i, m), reverse=True)])
        for m in groups.values()
    ]
