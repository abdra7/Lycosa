"""Generate bilingual, vector-rich academic reports with measured page layout."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import itertools

import arabic_reshaper
from bidi.algorithm import get_display
import networkx as nx
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon, Ellipse
from reportlab.graphics import renderPDF
from pypdf import PdfReader

from report_content import FRONT, CORE, DESIGN, APPENDIX, REFERENCES
from requirements_content import PAGES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output' / 'pdf'
TMP = ROOT / 'tmp' / 'pdfs'
NAVY = colors.HexColor('#12243B')
INK = colors.HexColor('#253448')
TEAL = colors.HexColor('#087F8C')
GOLD = colors.HexColor('#C5A268')
MUTED = colors.HexColor('#56677A')
PALE = colors.HexColor('#F1F5F8')
BORDER = colors.HexColor('#CFD9E2')
WHITE = colors.white
FONTDIR = Path('C:/Windows/Fonts')
for name, filename in [('Body','georgia.ttf'),('Head','segoeuib.ttf'),('Sans','segoeui.ttf'),('AR','arial.ttf'),('ARB','arialbd.ttf')]:
    pdfmetrics.registerFont(TTFont(name, str(FONTDIR / filename)))


def clean(s):
    return s.replace('\u2013', '-').replace('\u2014', '-').replace('\u2011', '-').replace('\u00a0',' ')


def vis(s, lang):
    s = clean(s)
    # Preserve LTR code and multiplicity notation inside Arabic figures.
    # Applying RTL to a pure identifier reverses brackets and e.g. 0..20.
    return get_display(arabic_reshaper.reshape(s), base_dir='R') if lang == 'ar' and re.search(r'[\u0600-\u06ff]',s) else s


def font(lang, head=False):
    return ('ARB' if head else 'AR') if lang == 'ar' else ('Head' if head else 'Body')


def lines(s, width, size, lang, head=False):
    result = []
    for para in clean(s).split('\n'):
        line = ''
        for word in para.split():
            trial = (line + ' ' + word).strip()
            if pdfmetrics.stringWidth(vis(trial,lang),font(lang,head),size) > width and line:
                result.append(line)
                line = word
            else:
                line = trial
        result.append(line)
    return result


def text(c, s, x, y, w, size=11.2, lang='en', head=False, color=INK, leading=None):
    leading = leading or (size*1.5 if lang=='ar' else size*1.46)
    c.setFillColor(color)
    c.setFont(font(lang,head),size)
    for line in lines(s,w,size,lang,head):
        if lang == 'ar':
            c.drawRightString(x+w,y,vis(line,lang))
        else:
            c.drawString(x,y,line)
        y -= leading
    return y


def dtext(d, s, x, y, lang, size=10, color=INK, anchor='middle', bold=False):
    d.add(String(x,y,vis(s,lang),fontName=font(lang,bold),fontSize=size,
                 fillColor=color,textAnchor=anchor))


class Diagram:
    """NetworkX semantics, fixed presentation geometry, ReportLab vector renderer."""
    def __init__(self,lang):
        self.lang=lang
        self.d=Drawing(740,350)
        self.g=nx.DiGraph()
        self.drawnodes=[]

    def node(self,key,x,y,w,h,en,ar,kind='box',fill=PALE):
        self.g.add_node(key,x=x,y=y,w=w,h=h)
        self.drawnodes.append((key,en if self.lang=='en' else ar,kind,fill))

    def region(self,x,y,w,h,en,ar):
        self.d.add(Rect(x,y,w,h,fillColor=WHITE,strokeColor=BORDER,strokeDashArray=[4,3],rx=8,ry=8))
        dtext(self.d,en if self.lang=='en' else ar,x+12 if self.lang=='en' else x+w-12,y+h-15,self.lang,9,TEAL,'start' if self.lang=='en' else 'end',True)

    def edge(self,a,b,label='',arabic='',dash=False,bends=None):
        self.g.add_edge(a,b)
        u,v=self.g.nodes[a],self.g.nodes[b]
        ux,uy=u['x']+u['w']/2,u['y']+u['h']/2
        vx,vy=v['x']+v['w']/2,v['y']+v['h']/2
        if bends:
            tx,ty=bends[0]
            ex,ey=bends[-1]
        else:
            tx,ty=vx,vy
            ex,ey=ux,uy
        def port(n,xx,yy):
            cx,cy=n['x']+n['w']/2,n['y']+n['h']/2
            dx,dy=xx-cx,yy-cy
            t=min((n['w']/2)/abs(dx) if dx else 1e6,(n['h']/2)/abs(dy) if dy else 1e6)
            return cx+dx*t,cy+dy*t
        start=port(u,tx,ty);end=port(v,ex,ey)
        pts=[start]+(bends or [])+[end]
        for p,q in zip(pts,pts[1:]):
            self.d.add(Line(*p,*q,strokeColor=MUTED,strokeWidth=1.05,strokeDashArray=[4,3] if dash else None))
        p,q=pts[-2:]
        a1=math.atan2(q[1]-p[1],q[0]-p[0]);r=5
        self.d.add(Polygon([q[0],q[1],q[0]-r*math.cos(a1-.5),q[1]-r*math.sin(a1-.5),q[0]-r*math.cos(a1+.5),q[1]-r*math.sin(a1+.5)],fillColor=MUTED,strokeColor=MUTED))
        if label:
            pp,qq=pts[len(pts)//2-1:len(pts)//2+1]
            xx,yy=(pp[0]+qq[0])/2,(pp[1]+qq[1])/2
            lab=label if self.lang=='en' else (arabic or label)
            tw=pdfmetrics.stringWidth(vis(lab,self.lang),font(self.lang),8)+8
            self.d.add(Rect(xx-tw/2,yy+2,tw,12,fillColor=WHITE,strokeColor=None))
            dtext(self.d,lab,xx,yy+5,self.lang,8)

    def finish(self):
        for key, label, kind, fill in self.drawnodes:
            n=self.g.nodes[key];x,y,w,h=[n[k] for k in ('x','y','w','h')]
            if kind=='ellipse':
                self.d.add(Ellipse(x+w/2,y+h/2,w/2,h/2,fillColor=fill,strokeColor=BORDER,strokeWidth=1))
            else:
                self.d.add(Rect(x,y,w,h,rx=5,ry=5,fillColor=fill,strokeColor=BORDER,strokeWidth=1))
                if kind=='store':
                    self.d.add(Line(x+8,y,x+8,y+h,strokeColor=TEAL,strokeWidth=2))
            size=10.1 if self.lang=='en' else 11.3
            ll=label.split('\n')
            while any(pdfmetrics.stringWidth(vis(l,self.lang),font(self.lang),size)>w-10 for l in ll):
                size-=.2
            assert size >= 7.5,(key,size)
            top=y+h/2+(len(ll)-1)*7-3
            for i,l in enumerate(ll):dtext(self.d,l,x+w/2,top-i*14,self.lang,size)
        return self.d


def architecture(lang):
    z=Diagram(lang)
    z.region(0,98,145,243,'Operator + MCP host','المشغل ومضيف MCP')
    z.region(175,98,220,243,'Controller boundary','حدود خادم التحكم')
    z.region(427,98,148,243,'LAN node','العقدة المحلية')
    z.region(605,98,135,243,'External cloud','السحابة الخارجية')
    for args in [
        ('ui',10,251,125,52,'Flutter desktop\nREST + events','سطح مكتب Flutter\nطلبات وأحداث'),
        ('host',10,186,125,45,'Trusted MCP host\noptional','مضيف MCP موثوق\nاختياري'),
        ('api',192,251,186,52,'FastAPI / RBAC\nRegistry + orchestration','FastAPI / الصلاحيات\nالسجل والتنسيق'),
        ('svc',192,157,186,52,'Knowledge + workflows\nProvider credentials','المعرفة وسير العمل\nاعتمادات المزود'),
        ('agent',439,251,124,52,'Agent HTTP API\nUsage + execution','واجهة الوكيل\nالاستخدام والتنفيذ'),
        ('ollama',439,128,124,50,'Ollama runtime\nlocal inference','بيئة Ollama\nاستدلال محلي'),
        ('cloud',613,251,119,52,'OpenRouter\nNVIDIA free model','OpenRouter\nنموذج NVIDIA مجاني'),
        ('mcp',10,114,125,43,'MCP companion\nseparate local process','مرافق MCP\nعملية محلية مستقلة')]:z.node(*args)
    for args in [('pg',7,12,150,50,'PostgreSQL\nstate + audit','PostgreSQL\nالحالة والتدقيق'),('qd',185,12,150,50,'Qdrant\nvectors + passages','Qdrant\nالمتجهات والمقاطع'),('redis',365,12,160,50,'Redis (optional)\ncoordination, not jobs','Redis اختياري\nتنسيق لا طابور وظائف'),('obs',555,12,180,50,'Prometheus / Grafana\nmetrics + visualization','Prometheus / Grafana\nالمؤشرات والرصد')]:z.node(*args,kind='store')
    z.edge('ui','api','REST / WS','REST / WS')
    z.edge('api','agent','dispatch','تنفيذ')
    z.edge('api','cloud',bends=[(410,320),(671,320)])
    z.edge('api','svc')
    z.edge('agent','ollama')
    z.edge('host','mcp',dash=True)
    z.edge('mcp','ollama',dash=True,bends=[(157,135),(157,87),(420,87),(420,153)])
    z.edge('svc','pg',bends=[(164,183),(164,82),(82,82)])
    z.edge('svc','qd',bends=[(405,183),(405,77),(260,77)])
    z.edge('api','redis',dash=True,bends=[(414,276),(414,82),(445,82)])
    z.edge('obs','api',dash=True,bends=[(730,76),(754,76),(754,348),(285,348)])
    return z.finish()


def dfd(lang):
    z=Diagram(lang)
    for args in [('user',2,265,122,55,'E1 Operator\nrequests / uploads','E1 المشغل\nطلبات ووثائق'),('p1',176,265,160,55,'P1 Access + registry\nauth / enrollment','P1 الوصول والسجل\nالهوية وضم العقد'),('p2',412,265,160,55,'P2 Task + workflow\nroute / approve','P2 المهمة وسير العمل\nتوجيه وموافقة'),('cloud',625,265,112,55,'E2 Runtime\nAgent or cloud','E2 بيئة التنفيذ\nوكيل أو سحابة'),('p3',176,132,160,55,'P3 Ingestion\nextract / embed','P3 إدخال المعرفة\nاستخراج وتمثيل'),('p4',412,132,160,55,'P4 Retrieval\nquery / rank / context','P4 الاسترجاع\nسؤال وترتيب وسياق')]:z.node(*args,kind='ellipse' if args[0].startswith('p') else 'box')
    for args in [('db',4,10,170,55,'D1 PostgreSQL\nmetadata / audit','D1 PostgreSQL\nالبيانات والتدقيق'),('vec',280,10,170,55,'D2 Qdrant\nvectors / chunk text','D2 Qdrant\nمتجهات ونصوص'),('trans',561,10,175,55,'D3 Redis (optional)\ntransient coordination','D3 Redis اختياري\nتنسيق عابر')]:z.node(*args,kind='store')
    z.edge('user','p1','identity','الهوية');z.edge('p1','p2','authorized task','طلب مخول')
    z.edge('p2','cloud','prompt','طلب')
    z.edge('cloud','p2','result','نتيجة',bends=[(681,341),(492,341)])
    z.edge('user','p3','document','وثيقة',bends=[(63,160)])
    z.edge('p3','vec','chunks','مقاطع');z.edge('p4','vec','query vector','متجه السؤال')
    z.edge('vec','p4','hits','نتائج',bends=[(536,82),(536,114)])
    z.edge('p2','p4','query','سؤال')
    z.edge('p4','p2','context','سياق',bends=[(591,160),(591,240),(492,240)])
    z.edge('p1','db',bends=[(150,291),(150,90),(89,90)])
    z.edge('p3','db','job state','حالة الإدخال')
    z.edge('p2','trans','events','أحداث',bends=[(608,291),(608,90),(648,90)])
    z.edge('p2','db',bends=[(371,290),(371,235),(20,235),(20,81)])
    return z.finish()


def usecase(lang):
    z=Diagram(lang);z.region(173,5,389,340,'Lycosa system boundary','حدود نظام ليـكوسا')
    for args in [('op',5,260,128,50,'Operator','المشغل'),('admin',5,93,128,50,'Administrator\nalso an operator','المدير\nيمارس دور المشغل'),('node',603,260,130,50,'Local Agent','الوكيل المحلي'),('host',603,35,130,50,'Trusted MCP host','مضيف MCP موثوق')]:z.node(*args)
    for args in [('task',205,265,148,44,'Submit task','إرسال مهمة'),('workflow',389,265,148,44,'Run / approve flow','تشغيل وموافقة'),('knowledge',205,181,148,44,'Manage knowledge','إدارة المعرفة'),('monitor',389,181,148,44,'Inspect / report Usage','فحص الاستخدام وإرساله'),('adminops',205,98,148,44,'Keys / decommission','المفاتيح وإلغاء العقد'),('auth',389,98,148,44,'Authorize operation','تفويض العملية'),('evidence',300,21,195,44,'Answer from evidence','إجابة من الأدلة')]:z.node(*args,kind='ellipse')
    z.edge('op','task');z.edge('op','knowledge',bends=[(151,285),(151,204)])
    z.edge('op','workflow',bends=[(140,327),(463,327)])
    z.edge('admin','adminops');z.edge('node','monitor');z.edge('host','evidence')
    z.edge('op','monitor',bends=[(156,285),(156,238),(462,238)])
    z.edge('task','auth','include','تضمين',True,bends=[(368,287),(368,120)])
    z.edge('adminops','auth','include','تضمين',True)
    return z.finish()


def classes(lang):
    z=Diagram(lang)
    # Identifiers are intentionally preserved in both language editions.
    for args in [('req',5,249,212,89,'GroundedRequest\nmodel, question, evidence\ncontext_chars, max_tokens','GroundedRequest\nالنموذج والسؤال والأدلة\nحدود السياق والمخرجات'),('ev',265,249,208,89,'Evidence\nsource: str / text: str\nscore: float','Evidence\nالمصدر والنص\nدرجة عددية'),('run',524,249,211,89,'RuntimeRequest\nmodel / messages\ntemperature / max_tokens','RuntimeRequest\nالنموذج والرسائل\nالحرارة وحد المخرجات'),('fn',5,112,212,89,'knowledge.answer()\nfilter + prompt + validate\nasync function','knowledge.answer()\nتصفية وطلب وتحقق\nدالة غير متزامنة'),('out',265,112,208,89,'GroundedResponse\noutput / sources / citations\nstatus / truncation','GroundedResponse\nالنص والمصادر والاستشهادات\nالحالة والاقتطاع'),('ad',524,112,211,89,'OllamaAdapter\nlist_models() / complete()\naclose()','OllamaAdapter\nlist_models() / complete()\naclose()'),('api',5,10,212,50,'HTTP /rag/answer\nFastAPI route function','HTTP /rag/answer\nدالة مسار FastAPI'),('mcp',265,10,208,50,'MCP answer_from_context\nFastMCP tool function','MCP answer_from_context\nدالة أداة FastMCP'),('resp',524,10,211,50,'RuntimeResponse\noutput / usage / model','RuntimeResponse\nالمخرج والاستخدام والنموذج')]:z.node(*args)
    z.edge('req','ev','0..20','0..20')
    z.edge('req','fn',dash=True);z.edge('fn','out','returns','يعيد',True)
    z.edge('fn','ad','uses','يستخدم',True,bends=[(111,217),(631,217)])
    z.edge('ad','run','accepts','يقبل',True);z.edge('ad','resp','returns','يعيد',True)
    z.edge('api','fn',dash=True);z.edge('mcp','fn',dash=True,bends=[(245,35),(245,90),(111,90)])
    return z.finish()


def sequence(lang):
    d=Drawing(740,350)
    xs=[53,210,368,529,687]
    names=[('Desktop','سطح المكتب'),('API / orchestrator','الخادم والمنسق'),('PostgreSQL','PostgreSQL'),('Knowledge / Qdrant','المعرفة وQdrant'),('OpenRouter','OpenRouter')]
    for x,(en,ar) in zip(xs,names):
        d.add(Rect(x-50,307,100,35,rx=4,fillColor=PALE,strokeColor=BORDER))
        dtext(d,en if lang=='en' else ar,x,320,lang,9)
        d.add(Line(x,14,x,305,strokeColor=BORDER,strokeDashArray=[3,3]))
    g=nx.DiGraph()
    def msg(i,j,y,en,ar,dashed=False):
        g.add_edge(str(i)+str(y),str(j)+str(y))
        a,b=xs[i],xs[j]
        d.add(Line(a,y,b,y,strokeColor=TEAL,strokeWidth=1,strokeDashArray=[4,3] if dashed else None))
        dx=1 if b>a else -1
        d.add(Polygon([b,y,b-dx*5,y+3,b-dx*5,y-3],fillColor=TEAL,strokeColor=None))
        dtext(d,en if lang=='en' else ar,(a+b)/2,y+5,lang,8.5)
    msg(0,1,282,'1 Submit task','1 إرسال المهمة')
    msg(1,2,251,'2 Record task + audit','2 حفظ المهمة والتدقيق')
    d.add(Rect(168,211,108,23,fillColor=colors.HexColor('#E9F3F2'),strokeColor=None))
    dtext(d,'3 Privacy gate' if lang=='en' else '3 فحص الخصوصية',222,219,lang,9)
    msg(1,3,184,'4 Retrieve scoped evidence','4 استرجاع أدلة مقيدة')
    msg(3,1,155,'5 Context; empty / error returns early','5 سياق؛ الغياب أو الخطأ ينهي الطلب',True)
    d.add(Rect(175,84,553,66,fillColor=None,strokeColor=GOLD,strokeDashArray=[3,2]))
    dtext(d,'opt: privacy allowed + successful nonempty retrieval' if lang=='en' else 'شرط: السماح بالخصوصية ونجاح الاسترجاع بأدلة',451,137,lang,8,TEAL)
    msg(1,4,117,'6 HTTPS + exact free model + zero-price cap','6 HTTPS ونموذج مجاني محدد وسقف سعر صفري')
    msg(4,1,96,'7 Text + finish reason + usage','7 النص وسبب الإنهاء والاستخدام',True)
    msg(1,2,66,'8 Persist terminal outcome','8 حفظ النتيجة النهائية')
    msg(1,0,32,'9 Result + event','9 النتيجة والحدث',True)
    return d


def er(lang):
    z=Diagram(lang)
    columns=[20,215,410,605];ys=[267,155,43]
    entities=[
        ('roles',0,0,'Role\nid PK / name','الدور\nid PK / name'),
        ('users',0,1,'User\nid PK / role_id FK','المستخدم\nid PK / role_id FK'),
        ('sessions',0,2,'Session\nid PK / user_id FK','الجلسة\nid PK / user_id FK'),
        ('nodes',1,0,'Node\nid PK / profile / metrics','العقدة\nid PK / profile / metrics'),
        ('tasks',1,1,'Task\nid PK / node_id FK?','المهمة\nid PK / node_id FK?'),
        ('exec',1,2,'TaskExecution\ntask_id FK / node_id FK','محاولة تنفيذ\ntask_id FK / node_id FK'),
        ('col',2,0,'KnowledgeCollection\nid PK / node_id FK?','مجموعة معرفة\nid PK / node_id FK?'),
        ('docs',2,1,'Document\nid PK / collection_id FK','الوثيقة\nid PK / collection_id FK'),
        ('jobs',2,2,'EmbeddingJob\nid PK / document_id FK','وظيفة التضمين\nid PK / document_id FK'),
        ('wf',3,0,'Workflow\nid PK / definition JSON','تعريف سير العمل\nid PK / definition JSON'),
        ('run',3,1,'WorkflowRun\nid PK / workflow_id FK','تشغيل سير العمل\nid PK / workflow_id FK'),
        ('step',3,2,'WorkflowStepRun\nrun_id FK / task_id FK?','محاولة خطوة\nrun_id FK / task_id FK?')]
    for key,col,row,en,ar in entities:z.node(key,columns[col],ys[row],115 if col==3 else 155,64,en,ar)
    for a,b in [('roles','users'),('users','sessions'),('tasks','exec'),('col','docs'),('docs','jobs'),('wf','run'),('run','step')]:z.edge(a,b,'1 : 0..*')
    z.edge('nodes','tasks','0..1 : 0..*',dash=True)
    z.edge('tasks','step',dash=True,bends=[(390,187),(390,127),(662,127)])
    dtext(z.d,'FK? = nullable; selected core entities' if lang=='en' else 'العلامة ? تعني ارتباطاً اختيارياً؛ عرض الكيانات الأساسية',370,17,lang,10,TEAL)
    return z.finish()


DIAGRAMS={'architecture':architecture,'dfd':dfd,'usecase':usecase,'classes':classes,'sequence':sequence,'er':er}


def block_height(b,lang,w,size):
    return len(lines(b['heading'][lang],w,12.0,lang,True))*17 + 8 + len(lines(b[lang],w,size,lang))*size*(1.5 if lang=='ar' else 1.46)+19


def prepare(lang):
    raw=FRONT+CORE+PAGES+DESIGN+APPENDIX
    planned=[]
    for p in raw:
        if p.get('diagram'):
            planned.append(p);continue
        blocks=p['blocks']
        heights=[block_height(b,lang,A4[0]-108,11.8 if lang=='ar' else 11.2) for b in blocks]
        count=max(1,math.ceil(sum(heights)/626))
        while True:
            choices=[]
            for cuts in itertools.combinations(range(1,len(blocks)),count-1):
                ends=(0,)+cuts+(len(blocks),)
                hh=[sum(heights[a:b]) for a,b in zip(ends,ends[1:])]
                if max(hh)<=626:choices.append((sum((v-sum(hh)/count)**2 for v in hh),ends))
            if choices:break
            count+=1
        _,ends=min(choices)
        for i,(a,b) in enumerate(zip(ends,ends[1:])):
            planned.append({**p,'blocks':blocks[a:b],'continued':i>0})
    return planned


def page_base(c,number,total,lang,title,w,h):
    c.setPageSize((w,h))
    c.setFillColor(WHITE);c.rect(0,0,w,h,fill=1,stroke=0)
    c.setStrokeColor(GOLD);c.setLineWidth(1);c.line(54,h-41,w-54,h-41)
    if lang=='en':
        c.setFont('Head',8);c.setFillColor(TEAL);c.drawString(54,h-31,'LYCOSA / SYSTEMS ENGINEERING')
    else:
        text(c,'LYCOSA / معمارية وهندسة النظم',54,h-31,200,8,lang,True,TEAL)
    text(c,'تقرير هندسة النظم' if lang=='ar' else 'ARCHITECTURE REPORT',w-234,h-31,180,8,lang,True,MUTED)
    c.setStrokeColor(BORDER);c.line(54,43,w-54,43)
    c.setFont('Sans',8);c.setFillColor(MUTED);c.drawString(54,29,'abdra7  /  2026-09-18  /  6064535')
    c.drawRightString(w-54,29,f'{number:02d} / {total:02d}')
    titlelines=lines(title,w-108,21,lang,True)
    return text(c,title,54,h-80,w-108,21,lang,True,NAVY,27)-24


def draw_cover(c,lang,total):
    w,h=A4;c.setPageSize(A4)
    c.setFillColor(NAVY);c.rect(0,0,w,h,fill=1,stroke=0)
    c.setFillColor(TEAL);c.rect(0,0,19,h,fill=1,stroke=0)
    c.setStrokeColor(colors.HexColor('#23465A'));c.setLineWidth(.8)
    for i in range(8):
        c.circle(w-20,185,80+i*24,stroke=1,fill=0)
    c.setFillColor(GOLD);c.rect(54,h-101,65,4,fill=1,stroke=0)
    c.setFillColor(WHITE);c.setFont('Head',44);c.drawString(54,h-174,'LYCOSA')
    title='Systems Architecture\nand Engineering Report' if lang=='en' else 'تقرير معمارية النظم\nوالهندسة'
    text(c,title,54,h-251,w-108,28,lang,True,WHITE,40)
    subtitle='A source-grounded study of local AI orchestration,\nretrieval and controlled cloud execution' if lang=='en' else 'دراسة مستندة إلى الكود لتنسيق الذكاء الاصطناعي المحلي\nوالاسترجاع والتنفيذ السحابي المضبوط'
    text(c,subtitle,54,h-367,w-108,13,lang,False,colors.HexColor('#CCDCE7'),22)
    text(c,'Prepared for the Lycosa project' if lang=='en' else 'أُعد لمشروع ليـكوسا',54,250,w-108,12,lang,True,GOLD)
    c.setFont('Head',17);c.setFillColor(WHITE);c.drawString(54,212,'abdra7')
    text(c,'18 September 2026 | Revision 1.0 | English edition' if lang=='en' else '18 سبتمبر 2026 | الإصدار 1.0 | النسخة العربية',54,174,w-108,10.5,lang,False,WHITE)
    text(c,'Independent academic-style report.\nNo institutional affiliation or endorsement is claimed.' if lang=='en' else 'تقرير مستقل بصياغة أكاديمية.\nلا يُدعى أي انتساب مؤسسي أو تأييد جامعي.',54,80,w-108,9,lang,False,colors.HexColor('#B4C7D5'),15)
    c.bookmarkPage('cover');c.addOutlineEntry('LYCOSA','cover',0);c.showPage()


def toc(c,plans,lang,total):
    w,h=A4;y=page_base(c,3,total,lang,'Contents and figure guide' if lang=='en' else 'المحتويات ودليل الأشكال',w,h)
    entries=[];seen=set()
    # Abstract is page 2; remaining planned narrative begins at page 4.
    for i,p in enumerate(plans[1:],4):
        sec=p['section']
        if sec not in seen:
            entries.append((sec,p['title'][lang],i));seen.add(sec)
    entries.append((16,'References and source map' if lang=='en' else 'المراجع وخريطة المصادر',len(plans)+3))
    for sec,title,pnum in entries:
        prefix=f'{sec:02d}' if sec<=13 else ('A' if sec==14 else 'B' if sec==15 else 'R')
        if lang=='en':
            text(c,prefix,54,y,34,10.2,lang,True,TEAL)
            text(c,title,94,y,w-210,10.2,lang)
            c.setFont('Sans',10);c.drawRightString(w-54,y,str(pnum))
        else:
            text(c,prefix,w-89,y,35,11,lang,True,TEAL)
            text(c,title,88,y,w-190,11,lang)
            c.setFont('Sans',10);c.drawString(54,y,str(pnum))
        c.linkRect('',f'p{pnum}',(54,y-4,w-54,y+13),relative=0,thickness=0)
        y-=25
    y-=13
    text(c,'READING THE DIAGRAMS' if lang=='en' else 'قراءة المخططات',54,y,w-108,10,lang,True,TEAL);y-=24
    text(c,'Figures 1-6 are full-page vector diagrams in Sections 8-13. Diagram pages use landscape orientation for legibility. Technical class, API and database identifiers are preserved across both editions.' if lang=='en' else 'الأشكال 1-6 مخططات متجهية في الأقسام 8-13. تستخدم صفحاتها الاتجاه الأفقي لتيسير القراءة. وتُحفظ معرفات الأصناف والواجهات وقاعدة البيانات كما هي في النسختين.',54,y,w-108,11,lang)
    c.bookmarkPage('contents');c.addOutlineEntry('Contents' if lang=='en' else 'المحتويات','contents',0);c.showPage()


def bodypage(c,p,number,total,lang):
    diagram=p.get('diagram');w,h=landscape(A4) if diagram else A4
    title=p['title'][lang]+((' (continued)' if lang=='en' else ' (تابع)') if p.get('continued') else '')
    if p['section']<=13 and p['section']>0:title=f"{p['section']:02d}  "+title
    y=page_base(c,number,total,lang,title,w,h)
    c.bookmarkPage(f'p{number}');c.addOutlineEntry(title,f'p{number}',0)
    if diagram:
        d=DIAGRAMS[diagram](lang)
        renderPDF.draw(d,c,50,142)
        b=p['blocks'][0]
        y=text(c,b['heading'][lang],54,121,w-108,11,lang,True,TEAL)-3
        y=text(c,b[lang],54,y,w-108,9.7 if lang=='en' else 10.5,lang,leading=13)
        assert y>=46,(diagram,lang,y)
    else:
        for b in p['blocks']:
            y=text(c,b['heading'][lang],54,y,w-108,12,lang,True,TEAL,17)-8
            y=text(c,b[lang],54,y,w-108,11.8 if lang=='ar' else 11.2,lang)-19
        assert y>=46,(p['title'],lang,y)
    c.showPage()


def refs(c,start,total,lang):
    for part in range(2):
        n=start+part;w,h=A4
        y=page_base(c,n,total,lang,('References | '+('Project evidence' if part==0 else 'External scholarship')) if lang=='en' else ('المراجع | '+('أدلة المشروع' if part==0 else 'المصادر العلمية والتقنية')),w,h)
        c.bookmarkPage(f'p{n}');c.addOutlineEntry('References '+str(part+1) if lang=='en' else 'المراجع '+str(part+1),f'p{n}',0)
        for key,en,ar,url,detail in REFERENCES[part*6:part*6+6]:
            y=text(c,key+'  '+(en if lang=='en' else ar),54,y,w-108,10.4 if lang=='en' else 11.4,lang)-5
            # URLs and repository paths are identifiers, kept LTR.
            c.setFillColor(TEAL);c.setFont('Sans',8)
            assert pdfmetrics.stringWidth(url,'Sans',8)<w-108,url
            c.drawString(54,y,url);c.linkURL(url,(54,y-2,w-54,y+10),relative=0);y-=17
            if part==0:
                y=text(c,detail,54,y,w-108,8.2,'en',color=MUTED,leading=12)-17
            else:
                y=text(c,'Accessed 18 September 2026' if lang=='en' else 'تاريخ الاطلاع: 18 سبتمبر 2026',54,y,w-108,9,lang,color=MUTED)-20
        assert y>48,('references',lang,y)
        c.showPage()


def build(lang):
    plans=prepare(lang);total=len(plans)+4
    filename=OUT/('Lycosa_Systems_Architecture_English.pdf' if lang=='en' else 'Lycosa_Systems_Architecture_Arabic.pdf')
    c=canvas.Canvas(str(filename),pagesize=A4,pageCompression=1)
    c.setTitle('Lycosa - Systems Architecture and Engineering Report' if lang=='en' else 'ليـكوسا - تقرير معمارية النظم والهندسة')
    c.setAuthor('abdra7');c.setSubject('Source-grounded architectural study, 18 September 2026; source revision 6064535')
    c.setCreator('Lycosa Python report builder / ReportLab + NetworkX')
    draw_cover(c,lang,total)
    bodypage(c,plans[0],2,total,lang)
    toc(c,plans,lang,total)
    for n,p in enumerate(plans[1:],4):bodypage(c,p,n,total,lang)
    refs(c,len(plans)+3,total,lang)
    c.save()
    pdf=PdfReader(str(filename))
    assert len(pdf.pages)==total
    assert len([p for p in plans if p.get('diagram')])==6
    manifest={'language':lang,'file':str(filename),'pages':total,'diagrams':6,'source_revision':'6064535','page_map':[{ 'page':i,'title':p['title'][lang],'diagram':p.get('diagram')} for i,p in [(2,plans[0])]+list(enumerate(plans[1:],4))]}
    (TMP/f'manifest_{lang}.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'language':lang,'pages':total,'file':str(filename)}))


if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True);TMP.mkdir(parents=True,exist_ok=True)
    for language in ['en','ar']:build(language)
