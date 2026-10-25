"""Local visual contact sheets and structural checks; does not edit the PDFs."""
import json
import re
from pathlib import Path
from PIL import Image, ImageDraw
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
TMP=ROOT/'tmp'/'pdfs'
for lang in ['en','ar']:
    manifest=json.loads((TMP/f'manifest_{lang}.json').read_text(encoding='utf-8'))
    pdf=PdfReader(manifest['file'])
    assert len(pdf.pages)==manifest['pages']
    assert sum(float(p.mediabox.width)>float(p.mediabox.height) for p in pdf.pages)==6
    assert not re.search(r'sk-or-v1-[A-Za-z0-9]{20,}', ''.join(p.extract_text() or '' for p in pdf.pages))
    for i,p in enumerate(pdf.pages,1):
        assert (p.extract_text() or '').strip(),(lang,i,'empty page')
        assert len(p['/Resources']['/Font'].get_object())>0
    images=sorted(TMP.glob(f'{lang}-[0-9][0-9].png'))
    assert len(images)==manifest['pages'],(lang,len(images))
    for start in range(0,len(images),12):
        sheet=Image.new('RGB',(1200,1280),'#d9e0e6')
        draw=ImageDraw.Draw(sheet)
        for j,path in enumerate(images[start:start+12]):
            im=Image.open(path).convert('RGB');im.thumbnail((284,389))
            xx=10+(j%4)*300;yy=12+(j//4)*421
            sheet.paste(im,(xx+(284-im.width)//2,yy))
            draw.text((xx+5,yy+393),path.stem,fill='black')
        sheet.save(TMP/f'contact_{lang}_{start//12+1}.png')
    print(lang,manifest['pages'],'pages checked;',len(images),'renders')
