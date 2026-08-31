"""Read-only, high-level thesis report projection and renderers."""
from __future__ import annotations
import html, io, json, re
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from markdown_it import MarkdownIt
from app.db.database import db

SCORES=(("Correctness","correctness",3),("Completeness","completeness",3),("Source-reference accuracy","source_reference_accuracy",2),("Evidence discipline","evidence_discipline",3),("Explanation quality","explanation_quality",3),("Usefulness","usefulness",3))

def _json(v:Any, fallback:Any)->Any:
    if v is None:return fallback
    if not isinstance(v,str):return v
    try:return json.loads(v)
    except (TypeError,json.JSONDecodeError):return fallback

def normalize_text_items(v:Any)->list[str]:
    if v is None:return []
    if isinstance(v,str):return [v] if v.strip() else []
    if isinstance(v,(list,tuple,set)):return [str(x) for x in v if x is not None and str(x).strip()]
    return [str(v)]

def _evaluation(row:dict|None)->dict|None:
    if not row or not any(row.get(k) is not None for k in ("correctness_score","completeness","source_reference_accuracy_score","evidence_discipline_score","explanation_quality","usefulness","hallucination_flag","verdict","evaluator_comment","human_comment")):return None
    return {"id":row.get("id"),"correctness":row.get("correctness_score"),"completeness":row.get("completeness"),"source_reference_accuracy":row.get("source_reference_accuracy_score"),"evidence_discipline":row.get("evidence_discipline_score"),"explanation_quality":row.get("explanation_quality"),"usefulness":row.get("usefulness"),"hallucination":None if row.get("hallucination_flag") is None else bool(row["hallucination_flag"]),"verdict":row.get("verdict"),"notes":row.get("evaluator_comment") or row.get("human_comment"),"saved_at":row.get("evaluated_at") or row.get("created_at")}

def _iso(v:Any)->str|None:
    if not v:return None
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:return str(v)

def _usage(row:dict|None)->dict:
    r=row or {}
    return {"execution_id":r.get("execution_id"),"provider":r.get("provider"),"model":r.get("model"),"input_tokens":r.get("provider_reported_input_tokens"),"output_tokens":r.get("provider_reported_output_tokens"),"total_tokens":r.get("provider_reported_total_tokens"),"cached_tokens":r.get("provider_reported_cached_input_tokens"),"reasoning_tokens":r.get("provider_reported_reasoning_tokens"),"duration_ms":r.get("request_duration_ms"),"status":r.get("status"),"warnings":_json(r.get("warnings_json"),[]),"model_configuration":_json(r.get("model_configuration_json"),{})}

def build_project_report(project_id:str,auditor_name:str="Unknown")->dict:
    with db() as c:
        project=c.execute("SELECT * FROM projects WHERE id=?",(project_id,)).fetchone()
        if not project:raise ValueError("Project not found")
        runs=[dict(x) for x in c.execute("SELECT * FROM formal_runs WHERE project_id=? ORDER BY timestamp,run_id",(project_id,))]
        usages=[dict(x) for x in c.execute("SELECT * FROM model_usage WHERE project_id=? ORDER BY created_at,execution_id",(project_id,))]
        evals=[dict(x) for x in c.execute("SELECT * FROM evaluations WHERE project_id=? ORDER BY created_at,id",(project_id,))]
    by_run=defaultdict(list)
    for x in usages:by_run[x.get("run_id") or ""].append(x)
    by_eval={x["id"]:x for x in evals}; groups=OrderedDict(); asks=compares=results_count=0
    for run in runs:
        q=run.get("question") or "Untitled question"; group=groups.setdefault(q,{"question_text":q,"executions":[]})
        op=str(run.get("operation") or "ask").lower(); answers=_json(run.get("answer_json"),[] if op=="compare" else ""); providers=_json(run.get("provider_model_json"),{}); evidence=_json(run.get("supplied_source_evidence_json"),_json(run.get("primary_evidence_json"),[])); meta=_json(run.get("comparison_metadata_json"),{}); ru=by_run.get(run["run_id"],[])
        completed=_iso(run.get("completed_at") or run.get("timestamp") or (ru[-1].get("created_at") if ru else None)); started=_iso(run.get("started_at"))
        if not started and completed and ru and ru[0].get("request_duration_ms") is not None:started=(datetime.fromisoformat(completed)-timedelta(milliseconds=ru[0]["request_duration_ms"])).isoformat()
        ex={"operation":op,"started_at":started,"completed_at":completed,"status":run.get("execution_status"),"evidence":evidence,"results":[],"technical":{"execution_id":run["run_id"],"run_purpose":run.get("run_purpose"),"evidence_package_id":meta.get("shared_evidence_package_id"),"evidence_hash":run.get("supplied_source_package_hash") or meta.get("shared_evidence_hash"),"evidence_package_match":meta.get("primary_evidence_match"),"effective_context_valid":meta.get("effective_context_valid"),"comparison_metadata":meta}}
        if op=="compare":
            compares+=1
            for i,r in enumerate(answers if isinstance(answers,list) else []):
                u=next((x for x in ru if x.get("execution_id")==((r.get("execution") or {}).get("execution_id"))),None) or (ru[i] if i<len(ru) else None)
                ex["results"].append({"provider":r.get("provider"),"model":r.get("model"),"status":r.get("validation_status"),"display_status":r.get("display_status"),"answer":r.get("full_answer") or r.get("answer") or "","raw_answer_preserved":True,"warnings":r.get("warnings") or [],"error":r.get("error"),"usage":_usage(u),"human_evaluation":_evaluation(by_eval.get(r.get("evaluation_id"))),"evaluation_id":r.get("evaluation_id"),"technical":{"evidence_package_match":r.get("evidence_package_match")}});results_count+=1
        else:
            asks+=1; provider=providers if isinstance(providers,dict) else {}; ev=_evaluation(by_eval.get(run.get("human_evaluation_id")))
            if ev is None:ev=_evaluation(next((x for x in evals if x.get("question")==q and x.get("model_provider")==provider.get("provider") and x.get("model_name")==provider.get("model")),None))
            ex["results"].append({"provider":provider.get("provider"),"model":provider.get("model"),"status":run.get("execution_status"),"answer":answers if isinstance(answers,str) else json.dumps(answers,ensure_ascii=False),"raw_answer_preserved":True,"warnings":_json((ru[0] if ru else {}).get("warnings_json"),[]),"error":None,"usage":_usage(ru[0] if ru else None),"human_evaluation":ev,"evaluation_id":run.get("human_evaluation_id"),"technical":{}});results_count+=1
        group["executions"].append(ex)
    return {"project":dict(project),"generated_at":datetime.now(timezone.utc).isoformat(),"timezone":"UTC","auditor_name":auditor_name,"questions":list(groups.values()),"counts":{"questions":len(groups),"executions":len(runs),"ask_runs":asks,"compare_runs":compares,"model_results":results_count}}

def _value(v):return "Unavailable" if v is None or v=="" else str(v)
def _time(v):
    if not v:return "Unavailable"
    try:return datetime.fromisoformat(v.replace("Z","+00:00")).astimezone(timezone.utc).strftime("%d %b %Y, %H:%M")
    except ValueError:return v
def _name(provider,model):
    raw=(model or "Model result").strip(); aliases={"gemini-2.5-flash":"Gemini 2.5 Flash","models/gemini-2.5-flash":"Gemini 2.5 Flash","openai/gpt-5.1":"GPT-5.1","gpt-5.1":"GPT-5.1","openai/gpt-4o-mini":"GPT-4o Mini","gpt-4o-mini":"GPT-4o Mini","qwen3.5:9b":"Qwen 3.5 9B","openai/gpt-oss-20b":"GPT-OSS 20B","nemotron-3.5-lightning":"Nemotron 3.5 Lightning"}
    return aliases.get(raw.lower()," ".join(x.upper() if x.lower() in {"gpt","oss","ai"} else x.capitalize() for x in raw.split("/",1)[-1].replace(":"," ").replace("-"," ").split()))
def _provider(v):return {"gemini":"Google Gemini API","google":"Google Gemini API","openrouter":"via OpenRouter","ollama":"Local via Ollama","groq":"via Groq","openai":"OpenAI API"}.get((v or "").lower(),_value(v))
def _status(v,error=None):
    k=(v or "").lower()
    if "rate" in k or "capacity" in k or k in {"provider_unavailable","provider_error"}:return "Provider unavailable"
    if k in {"response_parse_failed","parse_failed","processing_failed"}:return "Processing failed"
    if k=="completed_with_warnings":return "Completed with warnings"
    if k in {"valid_json","accepted_plain_text","completed","success","ready"}:return "Completed"
    return "Provider unavailable" if error else _value(v).replace("_"," ").capitalize()
def _duration(ms):
    if ms is None:return "Unavailable"
    sec=float(ms)/1000
    return f"{sec:.1f} s" if sec<60 else f"{int(sec//60)} min {sec%60:.1f} s"
def _tokens(u):
    n=lambda v:"Unavailable" if v is None else f"{int(v):,}"
    return f"{n(u.get('input_tokens'))} input / {n(u.get('output_tokens'))} output / {n(u.get('total_tokens'))} provider total"
def _total(ev):
    if not ev or any(ev.get(k) is None for _,k,_ in SCORES):return None
    return sum(int(ev[k]) for _,k,_ in SCORES),sum(m for _,_,m in SCORES)
def _eval_md(ev,level):
    h="#"*level
    if not ev:return [f"{h} Human Evaluation","","Not evaluated"]
    out=[f"{h} Human Evaluation","","| Metric | Result |","|---|---:|"]+[f"| {n} | {'Not scored' if ev.get(k) is None else f'{ev[k]}/{m}'} |" for n,k,m in SCORES]; total=_total(ev)
    if total:out.append(f"| **Total** | **{total[0]}/{total[1]}** |")
    hall="Not evaluated" if ev.get("hallucination") is None else "Yes" if ev["hallucination"] else "No";out += [f"| Hallucination | {hall} |",f"| Verdict | {_value(ev.get('verdict'))} |"]
    if ev.get("notes"):out += ["",f"**Evaluator notes:** {ev['notes']}"]
    return out
def _shift(s):return re.sub(r"^(#{1,6})(\s+)",lambda m:"#"*min(6,len(m.group(1))+4)+m.group(2),s,flags=re.M)

def _compare(results):
    out=["#### Comparison Summary","","| Model | Correctness | Completeness | Evidence discipline | Hallucination | Time | Tokens | Verdict |","|---|---:|---:|---:|---|---:|---:|---|"]
    for r in results:
        e,u=r.get("human_evaluation"),r["usage"]; score=lambda k,m:"Not evaluated" if not e or e.get(k) is None else f"{e[k]}/{m}"; hall="Not evaluated" if not e or e.get("hallucination") is None else "Yes" if e["hallucination"] else "No"; tok=u.get("total_tokens")
        out.append("| "+" | ".join([_name(r.get("provider"),r.get("model")),score("correctness",3),score("completeness",3),score("evidence_discipline",3),hall,_duration(u.get("duration_ms")),"Unavailable" if tok is None else f"{int(tok):,}",_value(e.get("verdict")) if e else "Not evaluated"])+" |")
    return out
def _sources(q):
    cited=set(); all=[]
    for ex in q["executions"]:
        for r in ex["results"]:cited.update(int(x) for x in re.findall(r"\[E(\d+)\]",r.get("answer") or "",re.I))
        for i,e in enumerate(ex.get("evidence") or [],1):all.append({**e,"number":i,"cited":i in cited})
    candidates=[x for x in all if x["cited"]] if cited else all; seen=set();out=[]
    for x in candidates:
        key=(x.get("file_path"),x.get("symbol_name"),x.get("start_line"),x.get("end_line"))
        if key not in seen:seen.add(key);out.append(x)
    return out
def _overall(report):
    data=defaultdict(lambda:{"n":0,"scores":[],"hall":0,"verified":0,"lat":[]})
    for q in report["questions"]:
      for ex in q["executions"]:
       for r in ex["results"]:
        e=r.get("human_evaluation")
        if not e:continue
        d=data[_name(r.get("provider"),r.get("model"))];d["n"]+=1;t=_total(e)
        if t:d["scores"].append(t[0]/t[1]*100)
        d["hall"]+=int(e.get("hallucination") is True);d["verified"]+=int(str(e.get("verdict") or "").lower()=="verified")
        if r["usage"].get("duration_ms") is not None:d["lat"].append(r["usage"]["duration_ms"])
    if not data:return []
    out=["","## Overall Model Results","","Only persisted human evaluations are included; unevaluated results are not scored.","","| Model | Questions evaluated | Average normalized score | Hallucination count | Verified answers | Average latency |","|---|---:|---:|---:|---:|---:|"]
    for n,d in data.items():out.append(f"| {n} | {d['n']} | {'Not available' if not d['scores'] else f'{sum(d['scores'])/len(d['scores']):.1f}%'} | {d['hall']} | {d['verified']} | {'Unavailable' if not d['lat'] else _duration(sum(d['lat'])/len(d['lat']))} |")
    return out

def render_markdown(report:dict)->str:
    p,c=report["project"],report["counts"]; out=["# SecurityCodeWiki Evaluation Report","","## Project Summary","",f"**Project:** {_value(p.get('name'))}",f"**Repository:** {_value(p.get('repo_url') or p.get('local_path'))}",f"**Generated:** {_time(report['generated_at'])} UTC",f"**Questions:** {c['questions']}",f"**Ask runs:** {c['ask_runs']}",f"**Compare runs:** {c['compare_runs']}",f"**Model results:** {c['model_results']}"]
    if report["questions"]:out += ["","## Question Index",""]+[f"- [Question {i} - {q['question_text']}](#question-{i})" for i,q in enumerate(report["questions"],1)]
    for qi,q in enumerate(report["questions"],1):
        out += ["","---","",f"## Question {qi}","",q["question_text"]]; asks=[e for e in q["executions"] if e["operation"]!="compare"]; comps=[e for e in q["executions"] if e["operation"]=="compare"]
        for i,ex in enumerate(asks,1):
            r=ex["results"][0];u=r["usage"];title="Initial Ask" if len(asks)==1 else f"Ask Run {i} - {_time(ex['completed_at'])} UTC"
            out += ["",f"### {title}","",f"#### {_name(r.get('provider'),r.get('model'))}","",f"{_provider(r.get('provider'))}  ",f"Status: {_status(r.get('display_status') or r.get('status'),r.get('error'))}  ",f"Response time: {_duration(u.get('duration_ms'))}  ",f"Tokens: {_tokens(u)}","","#### Answer","",_shift(r.get("answer") or "Not produced"),""]+_eval_md(r.get("human_evaluation"),4)
        for i,ex in enumerate(comps,1):
            title="Model Comparison" if len(comps)==1 else f"Compare Run {i} - {_time(ex['completed_at'])} UTC";out += ["",f"### {title}",""]+_compare(ex["results"])
            for r in ex["results"]:
                u,e=r["usage"],r.get("human_evaluation");t=_total(e);label="Not evaluated" if not e else _value(e.get("verdict"))+(f" - {t[0]}/{t[1]}" if t else "");hall="Not evaluated" if not e or e.get("hallucination") is None else "Yes" if e["hallucination"] else "No"
                out += ["",f"#### {_name(r.get('provider'),r.get('model'))}","",f"{_provider(r.get('provider'))}  ",f"Status: {_status(r.get('display_status') or r.get('status'),r.get('error'))}  ",f"Response time: {_duration(u.get('duration_ms'))}  ",f"Tokens: {_tokens(u)}  ",f"Evaluation: {label}  ",f"Hallucination: {hall}","","##### Answer","",_shift(r.get("answer") or "Not produced"),""]+_eval_md(e,5)
        out += ["","### Supporting Sources","","| Evidence | Source | Lines | Purpose |","|---|---|---:|---|"]
        sources=_sources(q)
        for i,e in enumerate(sources,1):
            path=str(e.get("file_path") or "Unavailable").replace("|","\\|");symbol=str(e.get("symbol_name") or "").replace("|","\\|");start,end=e.get("start_line"),e.get("end_line");rng=str(start or "") if start==end or not end else f"{start or ''}-{end}";out.append(f"| E{i} | {path}{' - '+symbol if symbol else ''} | {rng} | {'Cited by a model answer' if e.get('cited') else 'Supplied supporting source'} |")
        if not sources:out.append("| - | No supporting source references stored | - | - |")
        out += ["","Full evidence remains available in the SecurityCodeWiki project."]
    out += _overall(report)+["","---","","All report timestamps are UTC."];return "\n".join(out)+"\n"

def render_html(report:dict)->str:
    body=MarkdownIt("commonmark",{"html":False,"linkify":True}).enable("table").render(render_markdown(report));body=re.sub(r"<h2>Question (\d+)</h2>",r'<h2 id="question-\1">Question \1</h2>',body);css="body{font-family:Arial,sans-serif;max-width:1080px;margin:auto;padding:36px;color:#17202a;line-height:1.55}h1{color:#17365d;border-bottom:2px solid #17365d}h2{color:#244f78;margin-top:38px}h3,h4{color:#35658f;margin-top:26px}table{width:100%;border-collapse:collapse;margin:14px 0;font-size:13px}th,td{border:1px solid #cbd5e1;padding:8px;vertical-align:top;overflow-wrap:break-word}th{background:#eaf0f6}pre{background:#f4f6f8;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere}@media print{h1,h2,h3,h4{break-after:avoid}thead{display:table-header-group}body{padding:0}}";return f'<!doctype html><html><head><meta charset="utf-8"><title>SecurityCodeWiki Evaluation Report</title><style>{css}</style></head><body>{body}</body></html>'

def render_pdf(report:dict)->bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle,getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether,Paragraph,Preformatted,SimpleDocTemplate,Spacer,Table,TableStyle
    output=io.BytesIO();doc=SimpleDocTemplate(output,pagesize=A4,rightMargin=15*mm,leftMargin=15*mm,topMargin=16*mm,bottomMargin=17*mm,title="SecurityCodeWiki Evaluation Report");styles=getSampleStyleSheet();styles.add(ParagraphStyle(name="BodyR",parent=styles["BodyText"],fontSize=8.6,leading=11.2,spaceAfter=4,splitLongWords=False));styles.add(ParagraphStyle(name="Cell",parent=styles["BodyText"],fontSize=6.5,leading=8,splitLongWords=False))
    for n,parent,size,color in (("H1R","Heading1",18,"#17365d"),("H2R","Heading2",14,"#244f78"),("H3R","Heading3",11.5,"#35658f")):styles.add(ParagraphStyle(name=n,parent=styles[parent],fontSize=size,textColor=colors.HexColor(color),keepWithNext=True,spaceBefore=8))
    lines=[x for x in render_markdown(report).splitlines() if not x.startswith("<a id=")];story=[];i=0;incode=False;code=[]
    def inline(v):
        s=html.escape(v).replace("/","/<wbr/>").replace("\\","\\<wbr/>");s=re.sub(r"\*\*(.*?)\*\*",r"<b>\1</b>",s);s=re.sub(r"(?<!\*)\*([^*]+)\*",r"<i>\1</i>",s);return re.sub(r"`([^`]+)`",r"<font name='Courier'>\1</font>",s)
    while i<len(lines):
        line=lines[i]
        if line.startswith("```"):
            if incode:story.append(Preformatted("\n".join(code),styles["Code"]));code=[]
            incode=not incode;i+=1;continue
        if incode:code.append(line);i+=1;continue
        if not line.strip():story.append(Spacer(1,2));i+=1;continue
        h=re.match(r"^(#{1,6})\s+(.*)$",line)
        if h:story.append(Paragraph(inline(h.group(2)),styles["H1R" if len(h.group(1))==1 else "H2R" if len(h.group(1))==2 else "H3R"]))
        elif line.startswith("|") and i+1<len(lines) and re.match(r"^\|?\s*:?-+",lines[i+1]):
            rows=[[x.strip() for x in line.strip("|").split("|")]];i+=2
            while i<len(lines) and lines[i].startswith("|"):rows.append([x.strip() for x in lines[i].strip("|").split("|")]);i+=1
            cells=[[Paragraph(inline(x),styles["Cell"]) for x in row] for row in rows];avail=A4[0]-30*mm;cols=len(rows[0]);widths=([28,21,22,28,24,21,20,26] if cols==8 else None)
            if widths:
                scale=avail/sum(widths);widths=[x*scale for x in widths]
            elif cols==4:widths=[16*mm,94*mm,18*mm,avail-128*mm]
            table=Table(cells,repeatRows=1,colWidths=widths,hAlign="LEFT");table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0f6")),("GRID",(0,0),(-1,-1),.3,colors.HexColor("#94a3b8")),("VALIGN",(0,0),(-1,-1),"TOP"),("PADDING",(0,0),(-1,-1),3)]))
            if story and isinstance(story[-1],Spacer) and len(story)>1 and isinstance(story[-2],Paragraph):
                spacer=story.pop();heading=story.pop();story.append(KeepTogether([heading,spacer,table]))
            else:story.append(table)
            continue
        elif line.startswith("- "):story.append(Paragraph("&#8226; "+inline(line[2:]),styles["BodyR"]))
        elif not line.startswith("---"):story.append(Paragraph(inline(line),styles["BodyR"]))
        i+=1
    def footer(canvas,d):
        canvas.saveState();canvas.setFont("Helvetica",7.5);canvas.setFillColor(colors.HexColor("#64748b"));canvas.drawString(15*mm,9*mm,"SecurityCodeWiki Evaluation Report - All timestamps UTC");canvas.drawRightString(A4[0]-15*mm,9*mm,f"Page {d.page}");canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer);return output.getvalue()
