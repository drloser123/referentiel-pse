import json, re, html, sys, os

pages = json.load(open(sys.argv[1]))
# stray hidden character behind the back-cover picture (removed at the user's request)
pages["389"] = pages["389"].replace("<p>b</p>", "")
OUT = sys.argv[2]
tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html"), encoding="utf-8").read()

# Sections: (first page, last page, title as printed in the sommaire)
MODS = [
    (1, 8, ""),
    (9, 33, "ATTITUDE ET COMPORTEMENT"),
    (34, 41, "PROTECTION ET SECURITE"),
    (42, 63, "HYGIENE ET ASEPSIE"),
    (64, 108, "BILANS"),
    (109, 173, "URGENCES VITALES"),
    (174, 199, "MALAISES ET AFFECTIONS SPECIFIQUES"),
    (200, 266, "TRAUMATISMES"),
    (267, 322, "ATTEINTES CIRCONSTANCIELLES ET ENVIRONNEMENTALES"),
    (323, 339, "SOUFFRANCE PSYCHIQUE ET COMPORTEMENTS INHABITUELS"),
    (340, 371, "RELEVAGE ET BRANCARDAGE"),
    (372, 376, "SITUATIONS PARTICULIERES"),
    (377, 383, "DIVERS"),
    (384, 389, "FORMATIONS"),
]
assert sum(b - a + 1 for a, b, _ in MODS) == len(pages)

def plain(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()

def first_page(h, default):
    m = re.search(r'<div class="pg" id="p(\d+)"', h)
    return int(m.group(1)) if m else default

secs, items, tmpls = [], [], []
# section 0: préface + sommaire, split by pages
pre = "\n".join(pages[str(p)] for p in (1, 2))
som = "\n".join(pages[str(p)] for p in range(3, 9))
for key, title, body, a, b in (("preface", "PREFACE", pre, 1, 2), ("sommaire", "SOMMAIRE ET CONTENUS DE FORMATION", som, 3, 8)):
    items.append(dict(c=key, r="", t=title, s=0, a=a, b=b))
    tmpls.append('<template id="t-%s">%s</template>' % (key, body))
secs.append(dict(t="", a=1, b=8))

for si, (a, b, title) in enumerate(MODS[1:], start=1):
    secs.append(dict(t=title, a=a, b=b))
    body = "\n".join(pages[str(p)] for p in range(a, b + 1))
    starts = [m.start() for m in re.finditer(r'<div class="ref"[^>]*id="f-[0-9A-Z]+"', body)]
    assert starts, title
    cuts = starts[:]
    cuts[0] = 0  # content before the first fiche (chapter page) goes with the first fiche
    cuts.append(len(body))
    for i in range(len(starts)):
        seg = body[cuts[i]:cuts[i + 1]]
        m = re.search(r'<div class="ref"[^>]*id="f-([0-9A-Z]+)">(.*?)</div>\s*<h3 class="ftitle"[^>]*>(.*?)</h3>', seg, re.S)
        code, ref, t = m.group(1), plain(m.group(2)), plain(m.group(3))
        # pages covered
        pgs = [int(x) for x in re.findall(r'<div class="pg" id="p(\d+)"', seg)]
        fa = pgs[0] if i else first_page(body[starts[0]:], a)
        if i == 0:
            p2 = [int(x) for x in re.findall(r'<div class="pg" id="p(\d+)"', body[starts[0]:cuts[1]])]
            fa = (p2[0] if p2 else a)
            fb = p2[-1] if p2 else a
        else:
            fa, fb = (pgs[0], pgs[-1]) if pgs else (a, a)
        # a fiche that starts mid-page: its first page is the page whose marker follows its start
        th = m.group(3)
        th = re.sub(r'<span class="[^"]*\bi\b[^"]*"[^>]*>(.*?)</span>', lambda m_: "\x01" + m_.group(1) + "\x02", th, flags=re.S)
        th = html.escape(html.unescape(re.sub(r"<[^>]+>", "", th)).strip(), quote=False).replace("\x01", "<i>").replace("\x02", "</i>")
        th = th.replace("</i><i>", "")
        items.append(dict(c=code, r=ref, t=t, h=th, s=si, a=fa, b=fb))
        tmpls.append('<template id="t-%s">%s</template>' % (code, seg))

out = tpl.replace("/*SECS*/[]", json.dumps(secs, ensure_ascii=False)) \
         .replace("/*ITEMS*/[]", json.dumps(items, ensure_ascii=False)) \
         .replace("<!--TEMPLATES-->", "\n".join(tmpls))
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(out)
print(len(out), "bytes;", len(items), "entries")
