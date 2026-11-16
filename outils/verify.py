import pymupdf as fitz, json, re, html, sys
from collections import Counter

doc = fitz.open(sys.argv[1])
pages = json.load(open(sys.argv[2]))
BUL = {"•", "o", "§", "q", "Ø", "ü", "·"}
bad = 0
tot = 0
for k, h in pages.items():
    p = doc[int(k) - 1]
    # bullet glyph tokens from bullet fonts
    bul = Counter()
    for b in p.get_text("dict")["blocks"]:
        if b["type"]:
            continue
        for l in b["lines"]:
            for s in l["spans"]:
                if s["font"].startswith(("SymbolMT", "CourierNewPSMT", "Wingdings")) and s["text"].strip() in BUL:
                    bul[s["text"].strip()] += 1
    src = Counter(w[4] for w in p.get_text("words"))
    src -= bul
    t = h
    t = re.sub(r"<[^>]+>", "", t)   # tags removed without adding spaces (inline)
    # block-level tags were separated by newlines / adjacent; re-split on block boundaries
    h = h.replace("insuﬄations", "insuﬄa•ons")
    t2 = re.sub(r"</(p|div|h\d|td|li|figure|details)>", " ", h)
    t2 = re.sub(r"<summary>.*?</summary>", " ", t2)
    t2 = re.sub(r"<[^>]+>", "", t2)
    out = Counter(html.unescape(t2).split())
    tot += sum(src.values())
    if src != out:
        miss = src - out
        extra = out - src
        # tolerate hyphen joins: extra token == a + b where a endswith '-'
        for e in list(extra):
            for a in list(miss):
                if a.endswith("-") and e.startswith(a):
                    b_ = e[len(a):]
                    if miss[b_] > 0:
                        miss[a] -= 1; miss[b_] -= 1; extra[e] -= 1
                        break
        miss = +miss; extra = +extra
        if miss or extra:
            bad += 1
            print("PAGE", k, "missing", dict(list(miss.items())[:12]), "extra", dict(list(extra.items())[:12]))
print("pages with diffs:", bad, "of", len(pages), "- source words:", tot)
