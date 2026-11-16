import pymupdf as fitz, json, re, html, sys
doc=fitz.open(sys.argv[1] if len(sys.argv)>1 else 'pse.pdf'); pages=json.load(open(sys.argv[2] if len(sys.argv)>2 else 'out/pages.json'))
R=fitz.Rect
bad=[]
for k,v in pages.items():
    p=doc[int(k)-1]
    ys=[R(d['rect']) for d in p.get_drawings() if d.get('fill') and d['fill']==(1.0,1.0,0.0)]
    truth=''; tot=''
    if ys:
        for b in p.get_text('rawdict')['blocks']:
            for l in b.get('lines',[]):
                for s in l['spans']:
                    for ch in s['chars']:
                        cb=R(ch['bbox']); c=fitz.Point((cb.x0+cb.x1)/2,(cb.y0+cb.y1)/2)
                        if ch["c"].strip() and any(y.x0<=c.x<=y.x1 and min(cb.y1,y.y1)-max(cb.y0,y.y0)>=0.3*max(cb.height,.1) for y in ys): truth+=ch["c"]
    marks=''.join(re.findall(r'<mark>(.*?)</mark>',v,re.S))
    marks=html.unescape(re.sub(r'<[^>]+>','',marks))
    t=re.sub(r'\s','',truth).replace('•',''); m=re.sub(r'\s','',marks)
    if t!=m:
        bad.append((k,len(t),len(m)))
print(len(bad)); print(bad[:60])
import difflib
for k in sys.argv[3:]:
    v=pages[k]; p=doc[int(k)-1]
    ys=[R(d['rect']) for d in p.get_drawings() if d.get('fill') and d['fill']==(1.0,1.0,0.0)]
    truth=''
    for b in p.get_text('rawdict')['blocks']:
        for l in b.get('lines',[]):
            for s in l['spans']:
                for ch in s['chars']:
                    cb=R(ch['bbox']); c=fitz.Point((cb.x0+cb.x1)/2,(cb.y0+cb.y1)/2)
                    if ch["c"].strip() and any(y.x0<=c.x<=y.x1 and min(cb.y1,y.y1)-max(cb.y0,y.y0)>=0.3*max(cb.height,.1) for y in ys): truth+=ch["c"]
    marks=html.unescape(re.sub(r'<[^>]+>','',''.join(re.findall(r'<mark>(.*?)</mark>',v,re.S))))
    t=re.sub(r'\s','',truth); m=re.sub(r'\s','',marks)
    sm=difflib.SequenceMatcher(None,t,m,autojunk=False)
    for op,a1,a2,b1,b2 in sm.get_opcodes():
        if op!='equal': print(k,op,repr(t[max(0,a1-15):a2+5]),'->',repr(m[b1:b2]))
