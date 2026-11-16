import pymupdf as fitz, re, html, json, sys, os, io
from collections import Counter
from PIL import Image

PDF, OUT = sys.argv[1], sys.argv[2]
os.makedirs(os.path.join(OUT, "img"), exist_ok=True)
doc = fitz.open(PDF)
R = fitz.Rect
BULLET_FONTS = ("SymbolMT", "CourierNewPSMT", "Wingdings")
BULLET_CLASS = {"•": "b-disc", "o": "b-circ", "§": "b-sq", "q": "b-box", "Ø": "b-arr", "ü": "b-chk", "·": "b-disc"}


def col(c):
    return "#%06x" % c


def is_white(f):
    return f is not None and all(v > 0.97 for v in f)


def near(a, b, tol):
    return not (a.x1 + tol < b.x0 or b.x1 + tol < a.x0 or a.y1 + tol < b.y0 or b.y1 + tol < a.y0)


def merge_rects(rects, tol):
    rects = [R(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out = []
        while rects:
            r = rects.pop()
            i = 0
            while i < len(rects):
                if near(r, rects[i], tol):
                    r |= rects.pop(i)
                    changed = True
                    i = 0
                else:
                    i += 1
            out.append(r)
        rects = out
    return rects


def center(b):
    return fitz.Point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def in_yellow(cb, yellow):
    """A character is highlighted when its horizontal centre is inside a yellow box and it overlaps
    that box vertically (superscripts sit above the line but are highlighted with it)."""
    cx = (cb.x0 + cb.x1) / 2
    h = max(cb.height, 0.1)
    for y in yellow:
        if y.x0 <= cx <= y.x1:
            ov = min(cb.y1, y.y1) - max(cb.y0, y.y0)
            if ov >= 0.3 * h:
                return True
    return False


def page_spans(p, td, yellow=(), ulines=(), bgfills=()):
    """Spans as lists per line. Spans are split where a yellow highlight starts or ends (char level)."""
    spans = []
    raw = p.get_text("rawdict")
    for b in raw["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            ls = []
            for s in l["spans"]:
                chars = s.get("chars") or []
                if not chars:
                    continue
                runs = []
                for ch in chars:
                    cb = R(ch["bbox"])
                    hl = bool(yellow) and ch["c"].strip() != "" and in_yellow(cb, yellow)
                    oy = ch["origin"][1]
                    cx = (cb.x0 + cb.x1) / 2
                    ul = bool(ulines) and any(oy - 0.5 <= g.y0 <= oy + 2.5 and g.x0 - 0.5 <= cx <= g.x1 + 0.5 for g in ulines)
                    bg = None
                    if bgfills and ch["c"].strip() and not hl:
                        cc_ = fitz.Point(cx, (cb.y0 + cb.y1) / 2)
                        cand_ = [(fr.get_area(), fc) for fr, fc in bgfills if cc_ in fr]
                        if cand_:
                            bg = min(cand_)[1]
                    key = (hl, ul, bg)
                    if runs and (runs[-1][0] == key or (ch["c"].strip() == "" and not (runs[-1][0][1] and not ul))):
                        runs[-1][1].append(ch)
                    else:
                        runs.append([key, [ch]])
                for (hl, ul, bg), chs in runs:
                    txt = "".join(c["c"] for c in chs)
                    if txt == "":
                        continue
                    bb = R(chs[0]["bbox"])
                    for c in chs[1:]:
                        bb |= R(c["bbox"])
                    txt = re.sub(r"(?<=[a-zA-Zﬀﬁﬂﬃﬄ])\u2022(?=[a-z])", "ti", txt)
                    ls.append(dict(text=txt, bbox=bb, font=s["font"], size=s["size"],
                                   color=s["color"], flags=s["flags"], origin=s["origin"], line=id(l), hl=hl, ul=ul, bg=bg))
            if ls:
                spans.append(ls)
    return spans  # list of lines (list of spans)


def build_rows(lines):
    """lines: list of span lists -> rows (visual lines) sorted top->bottom"""
    items = []
    for ls in lines:
        txt_ = [s for s in ls if s["text"].strip()] or ls
        mx = max(s["size"] for s in txt_)
        core = [s for s in txt_ if s["size"] >= 0.8 * mx]
        bb = R(core[0]["bbox"])
        for s in core[1:]:
            bb |= s["bbox"]
        # ignore lines that are only whitespace for row geometry but keep them
        items.append([bb, list(ls)])
    items.sort(key=lambda it: (round((it[0].y0 + it[0].y1) / 2), it[0].x0))
    rows = []
    for bb, ls in items:
        placed = False
        for row in rows[-3:]:
            rb = row[0]
            ov = min(rb.y1, bb.y1) - max(rb.y0, bb.y0)
            hgap = max(bb.x0 - rb.x1, rb.x0 - bb.x1)
            if ov > 0.5 * min(rb.height, bb.height) and ov > 0 and hgap < 25:
                row[0] = rb | bb
                row[1].extend(ls)
                placed = True
                break
        if not placed:
            rows.append([R(bb), list(ls)])
    out = []
    split_rows = []
    for bb, ls in rows:
        ls.sort(key=lambda s: s["bbox"].x0)
        cur = []
        last_c = None
        for s in ls:
            if s["text"].strip():
                if last_c is not None:
                    gap = s["bbox"].x0 - last_c["bbox"].x1
                    is_bul = s["font"].startswith(BULLET_FONTS) and len(s["text"].strip()) == 1
                    cur_txt = "".join(x["text"] for x in cur).strip()
                    label = gap > 10 and cur_txt and len(cur_txt.split()) == 1 and len(cur_txt) > 3 \
                        and not cur[0]["font"].startswith(BULLET_FONTS) and s["text"].strip()[:1].isupper()
                    if gap > 25 or (is_bul and gap > 6) or label:
                        split_rows.append(cur)
                        cur = []
                last_c = s
            cur.append(s)
        if cur:
            split_rows.append(cur)
    for ls in split_rows:
        content = [s for s in ls if s["text"].strip()]
        if not content:
            continue
        # recompute geometry from content spans only
        cb = R(content[0]["bbox"])
        for s in content[1:]:
            cb |= s["bbox"]
        out.append(dict(bbox=cb, spans=ls, content=content))
    out.sort(key=lambda r: (r["bbox"].y0, r["bbox"].x0))
    return order_columns(out)


def vov(a, b):
    return min(a.y1, b.y1) - max(a.y0, b.y0) > 0.3 * min(a.height, b.height)


def order_columns(rows):
    n = len(rows)
    if n < 3:
        return rows
    # side-by-side pairs
    pairs = []
    for i in range(n):
        for j in range(i + 1, min(n, i + 6)):
            a, b = rows[i]["bbox"], rows[j]["bbox"]
            if vov(a, b) and (b.x0 > a.x1 + 6 or a.x0 > b.x1 + 6):
                pairs.append((i, j))
    if not pairs:
        return rows
    # bands: merge y-intervals of pairs
    bands = []
    ivs = []
    for i, j in pairs:
        y0 = min(rows[i]["bbox"].y0, rows[j]["bbox"].y0)
        y1 = max(rows[i]["bbox"].y1, rows[j]["bbox"].y1)
        rx = max(rows[i]["bbox"].x0, rows[j]["bbox"].x0)
        ivs.append((y0, y1, rx))
    ivs.sort()
    for y0, y1, rx in ivs:
        if bands and y0 <= bands[-1][1] + 14:
            bands[-1][1] = max(bands[-1][1], y1)
            bands[-1][2] = min(bands[-1][2], rx)
        else:
            bands.append([y0, y1, rx])
    keyed = []
    for idx, r in enumerate(rows):
        bb = r["bbox"]
        key = (bb.y0, 0, bb.y0, bb.x0)
        for y0, y1, rx in bands:
            if bb.y0 >= y0 - 1 and bb.y1 <= y1 + 1:
                key = ("band", y0, rx)
                break
        keyed.append((key, idx, r))
    # decide per band: pairs mode (every left row has a right partner) or column mode
    final = []
    for y0, y1, rx in bands:
        members = [r for k, i, r in keyed if k == ("band", y0, rx)]
        left = [r for r in members if r["bbox"].x0 < rx - 5]
        right = [r for r in members if r["bbox"].x0 >= rx - 5]
        def nwords(r):
            return len("".join(x["text"] for x in r["content"]).split())
        has_bul = any(x["font"].startswith(BULLET_FONTS) for r in members for x in r["content"])
        paired = (not has_bul) and all(nwords(l) <= 5 for l in left)
        for r in members:
            if paired:
                r["_k"] = (y0, 1, r["bbox"].y0, r["bbox"].x0)
            else:
                r["_k"] = (y0, 1 if r in left else 2, r["bbox"].y0, r["bbox"].x0)
                if r not in left:
                    r["dx"] = max(0, rx - 57)
                    r["coldx"] = True
    res = []
    for k, i, r in keyed:
        if k[0] == "band":
            res.append((r["_k"], r))
        else:
            res.append(((k[0], 0, k[2], k[3]), r))
    res.sort(key=lambda t: t[0])
    return [r for k, r in res]


def classify(row, H, pageno):
    c = row["content"]
    first = c[0]
    bullet = None
    if first["font"].startswith(BULLET_FONTS) and len(first["text"].strip()) == 1 and len(c) > 1:
        bullet = first
    elif len(c) > 1 and re.fullmatch(r"\d{1,2}[.)]", first["text"].strip()) and c[1]["bbox"].x0 - first["bbox"].x1 > 4:
        bullet = first
        row["num"] = True
    main = max((s for s in c if s is not bullet), key=lambda s: len(s["text"].strip()))
    f, sz, colr = main["font"], main["size"], main["color"]
    txt = "".join(s["text"] for s in c).strip()
    bb = row["bbox"]
    row["main"] = main
    row["bullet"] = bullet
    if bb.y0 > H - 65 and txt.isdigit() and sz >= 9.5:
        return "page"
    if f.startswith("Marianne"):
        return "h1"
    if sz >= 22:
        return "title"
    if 14.5 < sz < 17.5:
        if "Light" not in f:
            return "ref"
        if colr == 0x808080 or "Italic" in f:
            return "h4"
        return "h3"
    if "Light" in f and 10.5 < sz < 11.5:
        return "h5"
    if "LightItalic" in f and 11.5 < sz < 12.5:
        return "cap"
    if bullet is not None:
        return "li"
    c0t = c[0]["text"].strip()
    fn_start = bool(re.match(r"^\d{1,2}$", c0t) and len(c) > 1 and c[0]["size"] < 0.8 * sz) or bool(re.match(r"^\d{1,2}\s+\S", c[0]["text"].lstrip()) and sz <= 8.6)
    row["fn_start"] = fn_start
    if (sz <= 8.6 or (fn_start and sz <= 9.6)) and bb.y0 > H * 0.62:
        return "fn"
    if sz <= 9.6:
        return "small"
    return "p"


def hard_break(prev, row, rb, punct=True):
    """True when the previous line ended early although the next word would have fitted (explicit line break)."""
    c = [x for x in row["content"] if x is not row.get("bullet")]
    if not c:
        return False
    f = c[0]
    t = f["text"].lstrip()
    w = t.split(" ")[0] if t else ""
    wlen = f["bbox"].width * len(w) / max(1, len(t)) if t else 0
    px1 = max(x["bbox"].x1 for x in prev["content"])
    if prev.get("coldx") or row.get("coldx"):
        return False
    rb = min(rb, max(prev.get("rb", rb), row.get("rb", rb)) + 2)
    return px1 + wlen + 0.3 * f["size"] < rb - 1.5 or (punct and t[:1] in ("-", "–", "•"))


RECENT_LI = []


def build_blocks(rows, H, pageno, ctx="flow", rb=None):
    blocks = []
    pitch_min = None
    if ctx == "cell" and len(rows) > 2:
        def _by(r):
            return max(x["origin"][1] for x in r["content"])
        ps = [_by(rows[i + 1]) - _by(rows[i]) for i in range(len(rows) - 1)
              if 0 < _by(rows[i + 1]) - _by(rows[i]) < 30]
        if ps:
            pitch_min = min(ps)
    if rb and ctx == "flow":
        # local right edge: lines starting at the same x nearby (text boxes are narrower than the page)
        for r in rows:
            bb = r["bbox"]
            near_x1 = [o["bbox"].x1 for o in rows if abs(o["bbox"].x0 - bb.x0) < 4 and abs(o["bbox"].y0 - bb.y0) < 90]
            r["rb"] = min(rb, max(near_x1)) if near_x1 else rb
    for r in rows:
        k = classify(r, H, pageno)
        if ctx != "flow" and k in ("page",):
            k = "p"
        if ctx == "cell" and k == "fn":
            k = "small"
        dx = r.get("dx", 0)
        bb = r["bbox"] + (-dx, 0, -dx, 0)
        sz = r["main"]["size"]
        text_x = None
        if k == "li":
            nxt = [s for s in r["content"] if s is not r["bullet"]]
            text_x = nxt[0]["bbox"].x0 - dx
        merge = False
        b = None
        for cand in reversed(blocks[-1:]):
            if cand["x1"] < bb.x0 - 2 or bb.x1 < cand["x0"] - 2:
                continue  # no horizontal overlap: another column
            b = cand
            break
        if blocks and b is not blocks[-1] and b is not None:
            # only allow merging into an older block if every newer block is in another column
            newer = blocks[blocks.index(b) + 1:]
            if any(not (n["x1"] < bb.x0 - 2 or bb.x1 < n["x0"] - 2) for n in newer):
                b = None
        if b is not None and pitch_min and k in ("p", "small") and \
                max(x["origin"][1] for x in r["content"]) - max(x["origin"][1] for x in b["rows"][-1]["content"]) > pitch_min * 1.25 + 1:
            b = None
        if b is not None and ctx == "cell" and k in ("p", "small") and b["kind"] in ("p", "small", "li"):
            t_new = "".join(x["text"] for x in r["content"]).lstrip()
            t_prev = "".join(x["text"] for x in b["rows"][-1]["content"]).rstrip()
            if re.match(r"^[A-ZÀ-Ý][a-zà-ÿ]", t_new) and t_prev and t_prev[-1] not in ",(-" and \
                    not re.search(r"\b(et|ou|de|du|des|la|le|les|à|au|en|par|pour|sur|un|une|d’|l’)$", t_prev):
                b = None
        if b is not None:
            gap = bb.y0 - b["y1"]
            if k == b["kind"] and k in ("title", "ref", "h3", "h4", "h5", "h1", "cap") and gap < 0.6 * sz \
                    and r["main"]["color"] == b["color"]:
                merge = True
            elif k == "p" and b["kind"] == "li" and abs(bb.x0 - b["text_x"]) < 7 and gap < 4.5 \
                    and not (rb and hard_break(b["rows"][-1], r, rb, ctx != "flow")):
                merge = True
            elif k == "p" and b["kind"] == "p" and gap < 3.2 and bb.x0 >= b["x0"] - 8 and abs(sz - b["size"]) < 1.6 \
                    and not (rb and hard_break(b["rows"][-1], r, rb, ctx != "flow")):
                merge = True
            elif k == "small" and b["kind"] == "small" and gap < 3 and abs(sz - b["size"]) < 1.6 \
                    and not (rb and hard_break(b["rows"][-1], r, rb, ctx != "flow")):
                merge = True
            elif k == "fn" and b["kind"] == "fn" and gap < 3:
                c0 = r["content"][0]
                starts_num = ((c0["size"] < 6.6 or c0["flags"] & 1) and c0["text"].strip()[:1].isalnum()) or r.get("fn_start")
                if not starts_num:
                    merge = True
        if merge:
            b["rows"].append(r)
            b["y1"] = max(b["y1"], bb.y1)
            b["x1"] = max(b["x1"], bb.x1)
        else:
            blocks.append(dict(kind=k, rows=[r], y0=bb.y0, y1=bb.y1, x0=bb.x0, x1=bb.x1, size=sz,
                               color=r["main"]["color"], text_x=text_x,
                               bullet=r["bullet"], bx=r["bullet"]["bbox"].x0 - dx if r["bullet"] else None))
    if ctx in ("flow", "cell"):
        if ctx == "cell":
            RECENT_LI.clear()
        for b in blocks:
            k = b["kind"]
            if k in ("title", "ref", "h1", "h3", "h4", "h5"):
                RECENT_LI.clear()
            elif k == "li":
                lvl = max(0, min(6, round((b["bx"] - 57) / 18)))
                RECENT_LI[:] = [t for t in RECENT_LI if abs(t[0] - b["text_x"]) >= 3][-8:] + [(b["text_x"], lvl)]
            elif k in ("p", "small"):
                m = [t for t in RECENT_LI if abs(t[0] - b["x0"]) < 5]
                if m:
                    b["cont"] = m[-1][1]
                elif b["x0"] < 66:
                    RECENT_LI.clear()
    return blocks


GREYS = {0x0, 0x212121, 0x181818, 0x111111}


def span_html(s, base_size, yellow, kind, maxsz=0, base_y=None, bcolor=None):
    t = html.escape(s["text"], quote=False)
    if not t:
        return ""
    cls = []
    style = ""
    f = s["font"]
    if "Bold" in f or s["flags"] & 16:
        cls.append("b")
    if "Italic" in f or s["flags"] & 2:
        cls.append("i")
    if kind not in ("title", "ref", "h1", "h3", "h4", "h5"):
        if s["color"] not in GREYS:
            style = "color:%s" % col(s["color"])
    elif bcolor is not None and s["color"] != bcolor and s["text"].strip():
        style = "color:%s" % col(s["color"])
    sup = (s["flags"] & 1) or (s["size"] < 0.78 * base_size and s["text"].strip() != "")
    tt = s["text"].strip()
    if not sup and maxsz and s["size"] < 0.86 * maxsz and len(tt) > 1 and tt.isupper():
        cls.append("sc")
    c = center(s["bbox"])
    hi = s.get("hl", any(c in y for y in yellow)) and s["text"].strip() != ""
    out = t
    if cls or style:
        out = '<span%s%s>%s</span>' % (' class="%s"' % " ".join(cls) if cls else "",
                                        ' style="%s"' % style if style else "", out)
    if s.get("bg") is not None and s["text"].strip() and kind not in ("title", "ref", "h1", "h3", "h4", "h5"):
        out = '<span class="bgf" style="background:#%02x%02x%02x">%s</span>' % (*[int(v * 255) for v in s["bg"]], out)
    if s.get("ul") and s["text"].strip():
        out = "<u>%s</u>" % out
    if sup:
        ref = base_y.get(s["line"]) if isinstance(base_y, dict) else None
        if ref is not None and (s["bbox"].y0 + s["bbox"].y1) / 2 > (ref.y0 + ref.y1) / 2 + 0.5:
            out = "<sub>%s</sub>" % out
        else:
            out = "<sup>%s</sup>" % out
    if hi:
        out = "<mark>%s</mark>" % out
    if s["text"].strip().startswith(("http://", "https://", "www.")):
        url = s["text"].strip()
        if url.startswith("www."):
            url = "https://" + url
        out = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (html.escape(url), out)
    return out


def block_inner(b, yellow, skip_bullet=True):
    parts = []
    prev_txt = ""
    base = b["size"]
    maxsz = max(x["size"] for r in b["rows"] for x in r["content"])
    for ri, r in enumerate(b["rows"]):
        spans = r["spans"]
        if r["bullet"] is not None and skip_bullet and not r.get("num"):
            # drop the bullet glyph and the whitespace spans right after it
            idx = spans.index(r["bullet"])
            spans = spans[:idx] + spans[idx + 1:]
            # strip leading whitespace spans
        while spans and not spans[0]["text"].strip():
            spans = spans[1:]
        if ri > 0:
            if prev_txt.endswith("-") and not prev_txt.endswith(" -") and len(prev_txt) > 1:
                pass
            elif not prev_txt.endswith((" ", " ")) and not (spans and spans[0]["text"].startswith(" ")):
                parts.append(" ")
        last = None
        big = [x for x in r["content"] if x is not r["bullet"]]
        base_y = {}
        for x in big:
            cur = base_y.get(x["line"])
            if x["size"] >= base * 0.85 and (cur is None or x["bbox"].height > cur.height):
                base_y[x["line"]] = x["bbox"]
        for s in spans:
            if last is not None:
                gap = s["bbox"].x0 - last["bbox"].x1
                if gap > 0.25 * max(s["size"], last["size"]) and not last["text"].endswith(" ") \
                        and not s["text"].startswith(" "):
                    parts.append(" ")
            parts.append(span_html(s, base, yellow, b["kind"], maxsz, base_y, b["color"]))
            last = s
        if spans:
            prev_txt = spans[-1]["text"]
    h = "".join(parts).strip()
    # merge adjacent identical wrappers
    for tag in ("mark", "sup"):
        h = h.replace("</%s><%s>" % (tag, tag), "")
    h = re.sub(r'</span>(<span( class="[^"]*")?( style="[^"]*")?>)',
               lambda m: "" if False else m.group(0), h)
    return h


LEADER = re.compile(r"(_{3,})")


def toc_split(inner):
    """'title ____ page' -> three balanced parts so the line can be laid out as title / leader / page."""
    m = LEADER.search(inner)
    a, b = m.start(), m.end()
    stack = []
    for t in re.finditer(r"<(/?)([a-z0-9]+)([^>]*)>", inner[:a]):
        if t.group(1):
            if stack:
                stack.pop()
        else:
            stack.append((t.group(2), t.group(0)))
    close = "".join("</%s>" % n for n, _ in reversed(stack))
    reopen = "".join(o for _, o in stack)
    return ('<span class="tt">%s%s</span><span class="lead">%s%s%s</span><span class="tp">%s%s</span>'
            % (inner[:a], close, reopen, m.group(1), close, reopen, inner[b:]))


def block_html(b, yellow, extra_id=None):
    k = b["kind"]
    inner = block_inner(b, yellow)
    idattr = ' id="%s"' % extra_id if extra_id else ""
    if k == "li":
        lvl = max(0, min(6, round((b["bx"] - 57) / 18)))
        g = b["bullet"]["text"].strip()
        r0 = b["rows"][0]
        cls = "b-num" if r0.get("num") else BULLET_CLASS.get(g, "b-disc")
        if not r0.get("num") and b["bullet"].get("hl"):
            cls += " hlb"
        return '<div class="li l%d %s"%s>%s</div>' % (lvl, cls, idattr, inner)
    if k == "page":
        return None
    tag = {"h1": "h2", "title": "h3", "ref": "div", "h3": "h4", "h4": "h5", "h5": "h6", "cap": "div",
           "fn": "div", "small": "div", "p": "p"}[k]
    cls = {"h1": "chap", "title": "ftitle", "ref": "ref", "h3": "sec", "h4": "sub", "h5": "sub2", "cap": "cap",
           "fn": "fn", "small": "small", "p": ""}[k]
    style = ""
    if k in ("title", "ref", "h3", "h4", "h5") and b["color"] not in (0x0,):
        style = ' style="--c:%s"' % col(b["color"])
    if k in ("p", "small") and b.get("cont") is not None:
        return '<div class="li li-c l%d%s"%s>%s</div>' % (b["cont"], " small" if k == "small" else "", idattr, inner)
    lvl = max(0, min(6, round((b["x0"] - 57) / 18)))
    if k in ("p", "small") and lvl:
        cls += " l%d" % lvl
    if k == "p" and LEADER.search(inner):
        inner = toc_split(inner)
        cls += " toc"
    return '<%s%s%s%s>%s</%s>' % (tag, ' class="%s"' % cls.strip() if cls.strip() else "", style, idattr, inner, tag)


def border_segments(dr):
    """Visible horizontal/vertical segments from drawings: (orient, fixed, a, b, color, width)."""
    segs = []
    for d in dr:
        colr = d.get("color")
        fill = d.get("fill")
        for it in d["items"]:
            if it[0] == "l" and colr is not None and not is_white(colr):
                p1, p2 = it[1], it[2]
                w = d.get("width") or 0.5
                if abs(p1.y - p2.y) < 0.8:
                    segs.append(("h", p1.y, min(p1.x, p2.x), max(p1.x, p2.x), colr, w))
                elif abs(p1.x - p2.x) < 0.8:
                    segs.append(("v", p1.x, min(p1.y, p2.y), max(p1.y, p2.y), colr, w))
            elif it[0] == "re":
                r = R(it[1])
                if fill is not None and not is_white(fill) and (r.height <= 2.2 or r.width <= 2.2):
                    if r.height <= 2.2 and r.width > 2:
                        segs.append(("h", (r.y0 + r.y1) / 2, r.x0, r.x1, fill, max(r.height, .5)))
                    elif r.width <= 2.2 and r.height > 2:
                        segs.append(("v", (r.x0 + r.x1) / 2, r.y0, r.y1, fill, max(r.width, .5)))
                elif colr is not None and not is_white(colr) and d.get("type") in ("s", "fs"):
                    w = d.get("width") or 0.5
                    segs += [("h", r.y0, r.x0, r.x1, colr, w), ("h", r.y1, r.x0, r.x1, colr, w),
                             ("v", r.x0, r.y0, r.y1, colr, w), ("v", r.x1, r.y0, r.y1, colr, w)]
    return segs


def edge_style(segs, orient, fixed, a, b):
    L = b - a
    if L <= 0:
        return None
    cov = []
    for o, f, s0, s1, c, w in segs:
        if o == orient and abs(f - fixed) <= 2.2:
            lo, hi = max(a, s0), min(b, s1)
            if hi > lo:
                cov.append((lo, hi, c, w))
    if not cov:
        return None
    cov.sort()
    tot, end = 0, a
    for lo, hi, c, w in cov:
        if hi > end:
            tot += hi - max(lo, end)
            end = hi
    if tot < 0.55 * L:
        return None
    c, w = cov[0][2], cov[0][3]
    px = 2 if w >= 1.4 else 1
    if 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2] < 0.35:
        return "%dpx solid var(--tb)" % px
    return "%dpx solid #%02x%02x%02x" % (px, *[int(v * 255) for v in c])


def add_style(tag_open, st):
    if ' style="' in tag_open:
        return tag_open.replace(' style="', ' style="%s;' % st, 1)
    return tag_open + ' style="%s"' % st


def block_paint(b, fills, segs, page_area):
    """Background fill and underline rule drawn behind/under a block in the PDF."""
    rows = b["rows"]
    bb = R(rows[0]["bbox"])
    for r in rows[1:]:
        bb |= r["bbox"]
    c = center(bb)
    st = []
    cand = [(fr.get_area(), fc) for fr, fc in fills
            if c in fr and fr.width > 40 and fr.height > 4 and fr.get_area() < 0.35 * page_area
            and not (fc[0] > .95 and fc[1] > .95 and fc[2] < .1)]
    if cand:
        fc = min(cand)[1]
        st.append("background:#%02x%02x%02x" % tuple(int(v * 255) for v in fc))
        st.append("padding:2px 6px")
    if b["kind"] in ("title", "h3", "h4", "h5", "ref"):
        for o, f, s0, s1, colr, w in segs:
            if o == "h" and bb.y1 - 3 <= f <= bb.y1 + 9 and (s1 - s0) > 0.5 * 480:
                st.append("border-bottom:%dpx solid #%02x%02x%02x" % (2 if w >= 1.4 else 1, *[int(v * 255) for v in colr]))
                st.append("padding-bottom:3px")
                break
    return ";".join(st)


class _Row:
    def __init__(self, cells, bbox):
        self.cells = cells
        self.bbox = bbox


class _Tab:
    def __init__(self, rows, ncol, bbox):
        self.rows = rows
        self.col_count = ncol
        self.row_count = len(rows)
        self.bbox = bbox
        self.cells = [c for r in rows for c in r.cells if c]

    def extract(self):
        return [[""]]


RULE_FILLS = []


def grid_tables(segs, page_rect):
    """Tables drawn as thin border segments that find_tables misses."""
    H0 = sorted([s for s in segs if s[0] == "h"], key=lambda s: (round(s[1]), s[2]))
    # merge collinear touching horizontal pieces
    H = []
    for s in H0:
        if H and abs(H[-1][1] - s[1]) < 0.8 and s[2] <= H[-1][3] + 1.5:
            p_ = H[-1]
            H[-1] = (p_[0], p_[1], p_[2], max(p_[3], s[3]), p_[4], p_[5])
        else:
            H.append(s)
    H = [s for s in H if (s[3] - s[2]) > 8]
    V = [s for s in segs if s[0] == "v" and (s[3] - s[2]) > 6]
    # ruled tables: same-extent grey rules without outer verticals -> add virtual outer borders
    def grey(c):
        return max(c) - min(c) < 0.08
    def heading_rule(s):
        return any(fr.width > 400 and 12 < fr.height < 30 and abs(fr.y1 - s[1]) < 1.5 for fr, fc in RULE_FILLS)
    H = [s for s in H if not heading_rule(s)]
    wide = [s for s in H if (s[3] - s[2]) > 250 and grey(s[4])]
    groups = []
    for s in sorted(wide, key=lambda s: s[1]):
        g = next((g for g in groups if abs(g[-1][2] - s[2]) < 2.5 and abs(g[-1][3] - s[3]) < 2.5 and 0 < s[1] - g[-1][1] < 70
                  and not any(fr.width > 400 and 12 < fr.height < 30 and g[-1][1] < fr.y0 < s[1] for fr, fc in RULE_FILLS)), None)
        if g:
            g.append(s)
        else:
            groups.append([s])
    for g in groups:
        x0, x1 = g[0][2], g[0][3]
        ytop, ybot = g[0][1], g[-1][1]
        for fr, fc in RULE_FILLS:
            if abs(fr.x0 - x0) < 2.5 and abs(fr.y1 - g[0][1]) < 2 and fr.height < 200:
                ytop = min(ytop, fr.y0)
            if abs(fr.x0 - x0) < 2.5 and abs(fr.y0 - g[-1][1]) < 2 and fr.height < 200:
                ybot = max(ybot, fr.y1)
        if len(g) < 2 and ytop == g[0][1] and ybot == g[-1][1]:
            continue
        has_v = any(x0 + 3 < v[1] < x1 - 3 and v[2] >= ytop - 2 and v[3] <= ybot + 2 for v in V)
        if not has_v:
            continue
        if ytop < g[0][1]:
            H.append(("h", ytop, x0, x1, (1, 1, 1), 0))
        if ybot > g[-1][1]:
            H.append(("h", ybot, x0, x1, (1, 1, 1), 0))
        V.append(("v", x0, ytop, ybot, (1, 1, 1), 0))
        V.append(("v", x1, ytop, ybot, (1, 1, 1), 0))
    if len(V) < 2 or len(H) < 2:
        return []
    items = [("h", s) for s in H] + [("v", s) for s in V]
    rects = []
    for k, s in items:
        if k == "h":
            rects.append(R(s[2], s[1] - 1, s[3], s[1] + 1))
        else:
            rects.append(R(s[1] - 1, s[2], s[1] + 1, s[3]))
    clusters = merge_rects(rects, 2)
    out = []
    for cb in clusters:
        hs = [s for s in H if (cb + (-1, -1, 1, 1)).contains(R(s[2], s[1] - 1, s[3], s[1] + 1))]
        vs = [s for s in V if (cb + (-1, -1, 1, 1)).contains(R(s[1] - 1, s[2], s[1] + 1, s[3]))]
        if len(hs) < 2 or len(vs) < 2 or cb.width < 60 or cb.height < 12:
            continue

        def uniq(vals):
            vals = sorted(vals)
            o = []
            for v in vals:
                if not o or v - o[-1] > 2:
                    o.append(v)
            return o
        xs = uniq([s[1] for s in vs])
        ys = uniq([s[1] for s in hs])
        if len(xs) < 2 or len(ys) < 2:
            continue

        def hcover(y, x0, x1):
            L = x1 - x0
            tot = sum(max(0, min(x1, s[3]) - max(x0, s[2])) for s in hs if abs(s[1] - y) <= 2)
            return tot >= 0.6 * L

        def vcover(x, y0, y1):
            L = y1 - y0
            tot = sum(max(0, min(y1, s[3]) - max(y0, s[2])) for s in vs if abs(s[1] - x) <= 2)
            return tot >= 0.6 * L
        rows = []
        covered = set()
        for i in range(len(ys) - 1):
            y0, y1 = ys[i], ys[i + 1]
            if y1 - y0 < 4:
                continue
            cells = [None] * (len(xs) - 1)
            j = 0
            while j < len(xs) - 1:
                if (i, j) in covered:
                    j += 1
                    continue
                k = j + 1
                while k < len(xs) - 1 and not vcover(xs[k], y0, y1):
                    k += 1
                # extend downwards while no horizontal border below
                ii = i + 1
                while ii < len(ys) - 1 and not hcover(ys[ii], xs[j], xs[k]):
                    ii += 1
                for a in range(i, ii):
                    for b in range(j, k):
                        covered.add((a, b))
                cells[j] = (xs[j], y0, xs[k], ys[ii])
                j = k
            rows.append(_Row(cells, (xs[0], y0, xs[-1], y1)))
        if rows:
            out.append(_Tab(rows, len(xs) - 1, (xs[0], ys[0], xs[-1], ys[-1])))
    return out


def table_html(t, flow_rows_spans, fills, yellow, H, pageno, segs, p, img_rects):
    rows = t.rows
    if not rows:
        return None, []
    ncol = t.col_count
    from collections import Counter as _C
    xb = []
    for j in range(ncol):
        xs_ = [round(r.cells[j][0], 1) for r in rows if j < len(r.cells) and r.cells[j]]
        xb.append(_C(xs_).most_common(1)[0][0] if xs_ else None)
    tb = R(t.bbox)
    # fill unknown column starts by interpolation
    for j in range(ncol):
        if xb[j] is None:
            xb[j] = xb[j - 1] + 1 if j else tb.x0
    xb.append(tb.x1)
    yb = [r.bbox[1] for r in rows] + [tb.y1]
    # --- simplify columns: when the table draws vertical borders, keep only boundaries that have one
    minrow = min((yb[i + 1] - yb[i]) for i in range(len(rows))) if rows else 10
    vsegs = [sg for sg in segs if sg[0] == "v" and tb.x0 - 3 <= sg[1] <= tb.x1 + 3 and sg[3] > tb.y0 - 2 and sg[2] < tb.y1 + 2
             and (sg[3] - sg[2]) >= 0.8 * minrow]
    inner_v = [sg for sg in vsegs if tb.x0 + 3 < sg[1] < tb.x1 - 3]
    keep = list(range(ncol + 1))
    if inner_v:
        keep = [0] + [k for k in range(1, ncol) if any(abs(sg[1] - xb[k]) <= 2.5 for sg in inner_v)] + [ncol]
    else:
        nr = len(rows)
        def edge_rows(x):
            return sum(1 for r in rows if any(c and abs(c[0] - x) <= 1.5 for c in r.cells))
        keep = [0] + [k for k in range(1, ncol) if edge_rows(xb[k]) >= max(2 if nr > 1 else 1, 0.4 * nr)] + [ncol]
    NB = [xb[k] for k in keep]

    def c_start(x):
        return max(q for q in range(len(NB) - 1) if NB[q] <= x + 1.5) if NB[0] <= x + 1.5 else 0

    def c_end(x):
        cands = [q for q in range(1, len(NB)) if NB[q] >= x - 1.5]
        return min(cands) if cands else len(NB) - 1

    used = []
    seen = set()
    raw = []
    for i, r in enumerate(rows):
        for j, c in enumerate(r.cells):
            if not c:
                continue
            key = tuple(round(v, 1) for v in c)
            if key in seen:
                continue
            seen.add(key)
            c = R(c)
            c0, c1 = c_start(c.x0), c_end(c.x1)
            if c1 <= c0:
                c1 = c0 + 1
            raw.append([i, c0, c1, c])
    # merge cells of the same row that now fall in overlapping new columns
    merged = []
    for it in sorted(raw, key=lambda z: (z[0], z[1])):
        m = next((g for g in merged if g[0] == it[0] and not (it[2] <= g[1] or g[2] <= it[1])), None)
        if m:
            m[1] = min(m[1], it[1]); m[2] = max(m[2], it[2]); m[3] = m[3] | it[3]
        else:
            merged.append(list(it))
    # drop cells fully covered by an earlier (row-spanning) cell
    grid = []
    for i, c0, c1, c in merged:
        rs = 1 + sum(1 for k in range(i + 1, len(rows)) if yb[k] < c.y1 - 2)
        grid.append((i, c0, rs, c1 - c0, c))
    occ = {}
    final = []
    for g in sorted(grid, key=lambda z: (z[0], z[1])):
        i, j, rs, cs, c = g
        if any((i, jj) in occ for jj in range(j, j + cs)):
            continue
        for ii in range(i, i + rs):
            for jj in range(j, j + cs):
                occ[(ii, jj)] = 1
        final.append(g)
    # fill holes left by the detector (rows without their own cells) with plain cells
    for i in range(len(rows)):
        for j in range(len(NB) - 1):
            if (i, j) not in occ:
                occ[(i, j)] = 1
                final.append((i, j, 1, 1, R(NB[j], yb[i], NB[j + 1], yb[i + 1])))
    grid = final
    ncol = len(NB) - 1
    xb = NB
    by_row = {}
    for g in grid:
        by_row.setdefault(g[0], []).append(g)
    widths = [max(0.5, xb[j + 1] - xb[j]) for j in range(ncol)]
    tw = sum(widths)
    cols = "".join('<col style="width:%.2f%%">' % (100 * w / tw) for w in widths)
    trs = []
    for i in range(len(rows)):
        tds = []
        for (_, j, rs, cs, c) in sorted(by_row.get(i, []), key=lambda g: g[1]):
            sp = [s for s in flow_rows_spans if center(s["bbox"]) in c + (-0.5, -0.5, 0.5, 0.5) and id(s) not in used]
            for s in sp:
                used.append(id(s))
            lines = {}
            for s in sp:
                lines.setdefault(s["line"], []).append(s)
            rws = build_rows(list(lines.values()))
            for rw in rws:
                rw["dx"] = rw.get("dx", 0) + (c.x0 + 3 - 57)
            blks = build_blocks(rws, H, pageno, ctx="cell", rb=c.x1 - 5)
            parts = [(b["y0"], block_html(b, yellow)) for b in blks]
            # images inside this cell
            for k, ir in enumerate(img_rects):
                if center(ir) in c:
                    name = "p%03d_t%d_c%d_%d.webp" % (pageno, int(tb.y0), i, k)
                    w, h = render_fig(p, ir, name)
                    parts.append((ir.y0, '<img class="cimg" loading="lazy" src="img/%s" width="%d" height="%d" alt="" style="width:%.0f%%">' % (name, w, h, min(100, 100 * ir.width / max(c.width, 1)))))
            parts.sort(key=lambda x: x[0])
            inner = "".join(x[1] for x in parts if x[1])
            st = []
            # background
            cc = center(c)
            cand = [(fr.get_area(), fc) for fr, fc in fills if cc in fr and fr.get_area() < c.get_area() * 4
                    and fr.width > 3 and fr.height > 3
                    and (not (fc[0] > .95 and fc[1] > .95 and fc[2] < .1) or fr.get_area() > 0.6 * c.get_area())]
            if cand:
                fc = min(cand)[1]
                st.append("background:#%02x%02x%02x" % tuple(int(v * 255) for v in fc))
                lum = 0.3 * fc[0] + 0.59 * fc[1] + 0.11 * fc[2]
                st.append("color:%s" % ("#111" if lum > 0.55 else "#fff"))
            # alignment from text position
            if sp:
                cont = [s for s in sp if s["text"].strip()]
                if cont:
                    def tx(s_):
                        t_ = s_["text"]; n = max(1, len(t_)); w_ = s_["bbox"].width
                        lead = len(t_) - len(t_.lstrip()); trail = len(t_) - len(t_.rstrip())
                        return s_["bbox"].x0 + w_ * lead / n, s_["bbox"].x1 - w_ * trail / n
                    ls_ = {}
                    for s in cont:
                        ls_.setdefault(s["line"], []).append(s)
                    centered = True
                    multi = len(ls_) > 1
                    for row_ in ls_.values():
                        x0 = min(tx(s)[0] for s in row_); x1 = max(tx(s)[1] for s in row_)
                        lg, rg = x0 - c.x0, c.x1 - x1
                        if not (abs(lg - rg) < max(4, 0.07 * c.width) and lg > (7 if multi else 10)):
                            centered = False
                            break
                    if centered:
                        st.append("text-align:center")
                        inner = re.sub(r'(class="[^"]*?)\s*\bl\d\b', r'\1', inner)
                    ty0 = min(s["bbox"].y0 for s in cont); ty1 = max(s["bbox"].y1 for s in cont)
                    tg, bg_ = ty0 - c.y0, c.y1 - ty1
                    if tg > 1.5 and abs(tg - bg_) < max(3, 0.2 * c.height):
                        st.append("vertical-align:middle")
            # borders
            for side, args in (("top", ("h", c.y0, c.x0, c.x1)), ("bottom", ("h", c.y1, c.x0, c.x1)),
                               ("left", ("v", c.x0, c.y0, c.y1)), ("right", ("v", c.x1, c.y0, c.y1))):
                e = edge_style(segs, *args)
                if e:
                    st.append("border-%s:%s" % (side, e))
            if not any(x.startswith("border-top") for x in st):
                st.append("padding-top:1px")
            if not any(x.startswith("border-bottom") for x in st):
                st.append("padding-bottom:1px")
            attrs = (' rowspan="%d"' % rs if rs > 1 else "") + (' colspan="%d"' % cs if cs > 1 else "")
            tds.append('<td%s style="%s">%s</td>' % (attrs, ";".join(st), inner))
        row_empty = all(re.search(r'<td[^>]*>\s*</td>', td_) for td_ in tds)
        if row_empty and tds:
            hpx = max(2, round((yb[i + 1] - yb[i]) * 1.05))
            trs.append('<tr class="e" style="height:%dpx">%s</tr>' % (hpx, "".join(tds)))
        else:
            trs.append("<tr>%s</tr>" % "".join(tds))
    return ('<div class="tw" data-bb="%d,%d,%d,%d"><table style="width:%dpx"><colgroup>%s</colgroup>%s</table></div>'
            % (tb.x0, tb.y0, tb.x1, tb.y1, round(tb.width * 1.05), cols, "".join(trs))), used


def render_fig(p, rect, name):
    pix = p.get_pixmap(clip=rect, dpi=150)
    im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    im.save(os.path.join(OUT, "img", name), "WEBP", quality=72, method=6)
    return im.size


def convert_page(pno):
    p = doc[pno]
    pageno = pno + 1
    W, H = p.rect.width, p.rect.height
    page_area = W * H
    dr = p.get_drawings()
    yellow = [R(d["rect"]) + (-0.3, -0.3, 0.3, 0.3) for d in dr if d.get("fill") and d["fill"][0] > .95 and d["fill"][1] > .95 and d["fill"][2] < .1]
    fills = [(R(d["rect"]), d["fill"]) for d in dr if d.get("fill") and not is_white(d["fill"])]
    tabs = p.find_tables().tables
    # a "table" holding decorative diagonal shapes (arrows, triangles) is a layout, not a data table
    diag_shapes = [R(d["rect"]) for d in dr if any(i[0] == "l" and abs(i[1].x - i[2].x) > 1 and abs(i[1].y - i[2].y) > 1 for i in d["items"])]
    tabs = [t for t in tabs if sum(1 for r in diag_shapes if r.intersects(R(t.bbox))) < 2]
    # sommaire pages: rows of "title ____ page" with coloured bands are not data tables
    def _leader_table(t):
        txt = [c for row in t.extract() for c in row if c and c.strip()]
        return txt and sum(1 for c in txt if "____" in c) > 0.5 * len(txt)
    tabs = [t for t in tabs if not _leader_table(t)]
    segs = border_segments(dr)
    fnsep = [sg for sg in segs if sg[0] == "h" and 90 < sg[3] - sg[2] < 230 and 54 < sg[2] < 62 and sg[1] > H * 0.45 and max(sg[4]) < 0.3]
    # text underlines are not table borders
    _bl = [(sp["origin"][1], R(sp["bbox"])) for b_ in p.get_text("dict")["blocks"] if b_["type"] == 0 for l_ in b_["lines"] for sp in l_["spans"] if sp["text"].strip()]
    def _is_ul(sg):
        if sg[0] != "h" or max(sg[4]) > 0.3 or sg[3] - sg[2] > 350:
            return False
        return any(oy - 0.8 <= sg[1] <= oy + 2.8 and bb_.x0 - 2 <= sg[2] and sg[3] <= bb_.x1 + 2 for oy, bb_ in _bl)
    segs = [sg for sg in segs if not _is_ul(sg)]
    RULE_FILLS[:] = [(R(d_["rect"]), d_["fill"]) for d_ in dr if d_.get("fill")]
    for gt in grid_tables(segs, p.rect):
        gb = R(gt.bbox)
        over = [t for t in tabs if (gb & R(t.bbox)).get_area() > 0.2 * min(gb.get_area(), R(t.bbox).get_area())]
        if over:
            if all(gb.contains(R(t.bbox) + (2, 2, -2, -2)) and gb.get_area() > 1.15 * R(t.bbox).get_area() for t in over):
                tabs = [t for t in tabs if t not in over]
            else:
                continue
        if any(r.intersects(gb) for r in diag_shapes):
            continue
        txt_in = [w for w in p.get_text("words") if R(w[:4]).intersects(gb)]
        if len(txt_in) < 2:
            continue
        img_area = sum((R(ii["bbox"]) & gb).get_area() for ii in p.get_image_info())
        ncell = sum(1 for r_ in gt.rows for c_ in r_.cells if c_)
        filled = sum(1 for r_ in gt.rows for c_ in r_.cells if c_ and any(center(R(w[:4])) in R(c_) for w in txt_in))
        if filled < max(1, 0.5 * ncell):
            continue
        if img_area > 0.35 * gb.get_area():
            continue
        tabs.append(gt)
    trects = [R(t.bbox) for t in tabs]
    td = p.get_text("dict")
    ulines = []
    for d_ in dr:
        for it in d_["items"]:
            if it[0] == "re":
                r_ = R(it[1])
                if r_.height < 1.2 and 5 < r_.width < 350 and d_.get("fill") and max(d_["fill"]) < 0.5:
                    ulines.append(r_)
            elif it[0] == "l" and abs(it[1].y - it[2].y) < 0.5 and 5 < abs(it[1].x - it[2].x) < 350 and d_.get("color") and max(d_["color"]) < 0.5:
                ulines.append(R(min(it[1].x, it[2].x), it[1].y, max(it[1].x, it[2].x), it[1].y + 0.5))
    bgfills = [(R(d_["rect"]), tuple(d_["fill"])) for d_ in dr if d_.get("fill") and not is_white(d_["fill"])
               and R(d_["rect"]).height <= 18 and R(d_["rect"]).width > 20
               and not (d_["fill"][0] > .95 and d_["fill"][1] > .95 and d_["fill"][2] < .1)]
    lines = page_spans(p, td, yellow, ulines, bgfills)
    allspans = [s for ls in lines for s in ls]
    has_marianne = any(s["font"].startswith("Marianne") for s in allspans)

    # ---- pages that are a full-page picture (cover, chapter pages, back cover): keep them as a picture
    big = [R(b["bbox"]) & p.rect for b in td["blocks"] if b["type"] == 1]
    if any(r.get_area() > 0.5 * page_area for r in big):
        name = "p%03d_page.webp" % pageno
        w, h = render_fig(p, p.rect, name)
        bl = build_blocks(build_rows(lines), H, pageno, ctx="fig")
        inner = "".join(x for x in (block_html(b, yellow) for b in bl) if x and 'class="pg"' not in x)
        txt = '<div class="ft">%s</div>' % inner if inner.strip() else ""
        body = '<figure class="pagefig"><img loading="lazy" src="img/%s" width="%d" height="%d" alt="">%s</figure>' % (name, w, h, txt)
        return body + '\n<div class="pg" id="p%d"></div>' % pageno, dict(figs=1, tables=0)

    # ---- figure candidates
    imgs = [R(b["bbox"]) & p.rect for b in td["blocks"] if b["type"] == 1]
    imgs = [r for r in imgs if r.get_area() > 30]
    vecs = []
    for d in dr:
        kinds = {i[0] for i in d["items"]}
        if not (kinds & {"l", "c", "qu"}):
            continue
        visible = (d.get("color") is not None and not is_white(d.get("color"))) or (d.get("fill") is not None and not is_white(d["fill"]))
        if not visible:
            continue
        r = R(d["rect"])
        if any((r & (t + (-3, -3, 3, 3))).get_area() >= 0.6 * max(r.get_area(), 0.01) or r in t + (-3, -3, 3, 3) for t in trects):
            continue
        vecs.append(r + (-0.5, -0.5, 0.5, 0.5))
    vclusters = merge_rects(vecs, 8)
    vclusters = [c for c in vclusters if sum(1 for v in vecs if v in c + (-1, -1, 1, 1)) >= 3 and c.get_area() > 300]
    groups = merge_rects(imgs + vclusters, 10)
    figs = []
    for g in groups:
        has_vec = any(v.intersects(g) for v in vclusters)
        inside_text = [s for s in allspans if s["text"].strip() and center(s["bbox"]) in g]
        if g.get_area() > 0.5 * page_area:
            if inside_text and len(inside_text) > 0:
                continue  # background image behind text (chapter pages)
        if not has_vec and g.get_area() < 4500 and inside_text:
            continue  # small decorative background behind text
        if any((g & t).get_area() > 0.5 * g.get_area() for t in trects):
            continue  # inside a table: kept via table text; image rendered with table below
        figs.append(R(g))
    # widen each figure to the drawings right next to it (side labels, arrows) and give it some margin
    for i_, g in enumerate(figs):
        changed = True
        while changed:
            changed = False
            for v in vecs:
                if not v in g and near(g, v, 20) and not any((v & t).get_area() > 0 for t in trects):
                    g |= v
                    changed = True
        figs[i_] = (g + (-6, -6, 6, 6)) & p.rect
    # captions ("Figure n: ...") just below a figure become part of the picture; neighbouring figures merge
    big_figs = [g for g in figs if g.height >= 24]
    small_figs = [g for g in figs if g.height < 24]
    if big_figs:
        caps = []
        lb = []
        for ls in lines:
            cb_ = R(ls[0]["bbox"])
            for x in ls[1:]:
                cb_ |= x["bbox"]
            lb.append((cb_, "".join(x["text"] for x in ls).strip(), ls[0]["size"]))
        lb.sort(key=lambda z: z[0].y0)
        for cb_, t_, sz_ in lb:
            if re.match(r"^Figure\s*\d", t_):
                cap = R(cb_)
                grow = True
                while grow:
                    grow = False
                    for cb2, t2, sz2 in lb:
                        if cb2 in cap or not t2:
                            continue
                        if abs(sz2 - sz_) < 0.6 and -3 <= cb2.y0 - cap.y1 < 6 and cb2.y0 > cap.y0 + 2 and min(cap.x1, cb2.x1) - max(cap.x0, cb2.x0) > 0.3 * cb2.width:
                            cap |= cb2
                            grow = True
                caps.append(cap)
        changed = True
        while changed:
            changed = False
            for i_, g in enumerate(big_figs):
                for cb_ in caps:
                    if not cb_ in g and g.y1 - 4 <= cb_.y0 <= g.y1 + 28 and min(g.x1, cb_.x1) - max(g.x0, cb_.x0) > 0.3 * cb_.width:
                        big_figs[i_] = g | (cb_ + (-3, -2, 3, 3))
                        changed = True
                        g = big_figs[i_]
        if len(big_figs) >= 4:
            u = R(big_figs[0])
            for g in big_figs[1:]:
                u |= g
            big_figs = [u]
        merged = True
        while merged:
            merged = False
            for a in range(len(big_figs)):
                for b_ in range(a + 1, len(big_figs)):
                    A, B = big_figs[a], big_figs[b_]
                    vov_ = min(A.y1, B.y1) - max(A.y0, B.y0)
                    hov_ = min(A.x1, B.x1) - max(A.x0, B.x0)
                    gap_ = max(A.y0, B.y0) - min(A.y1, B.y1)
                    if vov_ > 0.3 * min(A.height, B.height) or (hov_ > 0.3 * min(A.width, B.width) and gap_ < 14):
                        big_figs[a] = A | B
                        del big_figs[b_]
                        merged = True
                        break
                if merged:
                    break
        figs = big_figs + small_figs

    # tables that contain images -> render a picture of the table too (keep pictures)
    tab_pics = []
    for t in trects:
        if any((R(i) & t).get_area() > 0.3 * R(i).get_area() for i in imgs):
            tab_pics.append(t)

    # ---- assign spans
    flow_lines, fig_spans, tab_spans = [], {i: [] for i in range(len(figs))}, {i: [] for i in range(len(trects))}
    for ls in lines:
        keep = []
        for s in ls:
            c = center(s["bbox"])
            fi = next((i for i, g in enumerate(figs) if c in g), None)
            if fi is not None:
                fig_spans[fi].append(s)
                continue
            ti = next((i for i, t in enumerate(trects) if c in t), None)
            if ti is not None:
                tab_spans[ti].append(s)
                continue
            keep.append(s)
        if keep:
            flow_lines.append(keep)

    elems = []  # (y0, html)
    rows = build_rows(flow_lines)
    blocks = build_blocks(rows, H, pageno, rb=W - 55.5)
    fiche = None
    mono = -1
    for b in blocks:
        mono = max(mono, b["y0"])
        b["y0"] = mono
        eid = None
        if b["kind"] == "ref":
            txt = "".join(s["text"] for r in b["rows"] for s in r["content"])
            m = re.search(r"\[(\d\d[A-Z]{2}\d\d)", txt)
            if m:
                eid = "f-" + m.group(1)
                fiche = m.group(1)
        if b["kind"] == "page":
            elems.append((b["y0"], '<div class="pg" id="p%d">%s</div>' % (pageno, html.escape(
                "".join(s["text"] for r in b["rows"] for s in r["spans"]).strip()))))
            continue
        hh = block_html(b, yellow, eid)
        if hh:
            st = block_paint(b, fills, segs, page_area)
            if st:
                hh = re.sub(r"^<([a-z0-9]+)((?: [a-z-]+=\"[^\"]*\")*)", lambda m_: add_style(m_.group(0), st), hh, count=1)
            elems.append((b["y0"], hh, b))

    for ti, t in enumerate(tabs):
        t_imgs = [R(i) for i in imgs if center(R(i)) in trects[ti]]
        th, used = table_html(t, tab_spans[ti], fills, yellow, H, pageno, segs, p, t_imgs)
        left = [s for s in tab_spans[ti] if id(s) not in used]
        if th:
            tr_ = trects[ti]
            side = tr_.width < 0.45 * W and (tr_.x0 > 0.45 * W or tr_.x1 < 0.55 * W)
            if side:
                th = th.replace('<div class="tw"', '<div class="tw side"', 1)
            elems.append((tr_.y1 if side else tr_.y0, th))
        if left:
            lmap = {}
            for s in left:
                lmap.setdefault(s["line"], []).append(s)
            for b in build_blocks(build_rows(list(lmap.values())), H, pageno, ctx="cell"):
                hh = block_html(b, yellow)
                if hh:
                    elems.append((b["y0"], hh))

    for fi, g in enumerate(figs):
        name = "p%03d_f%d.webp" % (pageno, fi)
        w, h = render_fig(p, g, name)
        txt = ""
        if fig_spans[fi]:
            lmap = {}
            for s in fig_spans[fi]:
                lmap.setdefault(s["line"], []).append(s)
            bl = build_blocks(build_rows(list(lmap.values())), H, pageno, ctx="fig")
            inner = "".join(x for x in (block_html(b, yellow) for b in bl) if x)
            if inner.strip():
                txt = '<div class="ft">%s</div>' % inner
        if g.height < 24 and not txt:
            elems.append((g.y0, '<img class="inl" loading="lazy" src="img/%s" width="%d" height="%d" alt="" style="height:%.2fem">' % (name, w, h, g.height / 11), g, 1))
        else:
            elems.append((g.y0, '<figure><img loading="lazy" src="img/%s" width="%d" height="%d" alt="">%s</figure>' % (name, w, h, txt)))

    # small pictures sitting on a text line (e.g. "CHAPITRE 01" labels): put them inline in that line
    small = [e for e in elems if len(e) == 4]
    elems = [e for e in elems if len(e) != 4]
    for (y0, html_img, g, _) in small:
        tgt = None
        for i_, e in enumerate(elems):
            if len(e) == 3:
                bb_ = e[2]
                if bb_["y0"] - 3 <= (g.y0 + g.y1) / 2 <= bb_["y1"] + 3:
                    tgt = i_
                    break
        if tgt is None:
            elems.append((y0, '<p>%s</p>' % html_img))
        else:
            e = elems[tgt]
            elems[tgt] = (e[0], re.sub(r"^(<[^>]+>)", lambda m_: m_.group(1) + html_img, e[1], count=1), e[2])
    elems = [(e[0], e[1]) for e in elems]
    for sg in fnsep[:1]:
        elems.append((sg[1], '<hr class="fnsep">'))
    elems.sort(key=lambda e: e[0])
    # page marker placement: keep at end
    pg = [e for e in elems if e[1].startswith('<div class="pg"')]
    rest = [e for e in elems if not e[1].startswith('<div class="pg"')]
    body = "\n".join(e[1] for e in rest + pg)
    if not pg:
        body += '\n<div class="pg" id="p%d"></div>' % pageno
    return body, dict(figs=len(figs), tables=len(tabs))


if __name__ == "__main__":
    pages = range(len(doc))
    if len(sys.argv) > 3:
        a, b = sys.argv[3].split("-")
        pages = range(int(a) - 1, int(b))
    res = {}
    stats = Counter()
    for pno in pages:
        body, st = convert_page(pno)
        res[pno + 1] = body
        stats.update(st)
    json.dump(res, open(os.path.join(OUT, "pages.json"), "w"), ensure_ascii=False)
    print(stats)
