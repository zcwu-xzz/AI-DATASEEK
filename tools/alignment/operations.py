from __future__ import annotations
import base64, json, re, sys
from pathlib import Path
from collections import Counter, defaultdict
import numpy as np

def plot_style():
    import matplotlib.pyplot as plt
    plt.rcParams.update({'figure.facecolor':'white','axes.facecolor':'#fbfcfe','axes.edgecolor':'#cbd5e1','font.size':10})
    return plt
def html_output(out): return str(Path(out).with_suffix('.html'))

def fail(msg):
    print(json.dumps({"success": False, "error": msg}, ensure_ascii=False)); raise SystemExit(0)
def emit(data, out=None):
    result={"success":True, **data}
    if out: Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,default=str))
def is_binary(path): return Path(path).suffix.lower() in {".bam",".cram"}
def reader(path):
    p=Path(path)
    if not p.is_file(): fail("比对文件不存在")
    if is_binary(p):
        try:
            import pysam
            return pysam.AlignmentFile(str(p), "rb"), True
        except Exception as e: fail(f"BAM/CRAM 读取失败，请确认镜像已安装 pysam: {e}")
    try: return p.open(encoding="utf-8",errors="replace"), False
    except OSError as e: fail(str(e))
def sam_records(path, limit=None):
    h=[]; rows=[]; f,b=reader(path)
    try:
        if b:
            h=[f.text] if getattr(f,"text","") else []
            for i,r in enumerate(f.fetch(until_eof=True)):
                if limit and i>=limit: break
                rows.append(r)
        else:
            for line in f:
                if line.startswith("@"): h.append(line.rstrip()); continue
                if not line.strip(): continue
                x=line.rstrip("\n").split("\t")
                if len(x)>=11: rows.append(x)
    finally: f.close()
    return h,rows,b
def fields(r):
    if isinstance(r,list):
        q,n,flag,ref,pos,mapq,cigar,seq=r[0],r[1],int(r[1]),r[2],int(r[3]),int(r[4]),r[5],r[9]
        tags=r[11:]; return dict(qname=q,flag=flag,reference=ref,pos=pos,mapq=mapq,cigar=cigar,seq=seq,tags=tags)
    return dict(qname=r.query_name,flag=r.flag,reference=r.reference_name,pos=(r.reference_start or -1)+1,mapq=r.mapping_quality,cigar=r.cigarstring or "*",seq=r.query_sequence or "",tags=[] , aligned=r)
def stats(rows):
    total=len(rows); unmapped=sum(bool(fields(r)["flag"]&4) for r in rows); mapped=total-unmapped
    mapq=[fields(r)["mapq"] for r in rows if not fields(r)["flag"]&4]
    return {"records":total,"mapped":mapped,"unmapped":unmapped,"mapping_rate":mapped/total if total else 0,"mapq":{"mean":float(np.mean(mapq)) if mapq else None,"min":min(mapq) if mapq else None,"max":max(mapq) if mapq else None}}
def main():
    if len(sys.argv)!=3: fail("参数错误")
    try: a=json.loads(base64.urlsafe_b64decode(sys.argv[2]+"="*(-len(sys.argv[2])%4)))
    except Exception: fail("参数编码无效")
    op=sys.argv[1]; path=a.get("input_path"); h,rows,b=sam_records(path, 200000)
    fs=[fields(r) for r in rows]
    if op=="alignment_inspect":
        refs=[]
        for x in h:
            if x.startswith("@SQ"):
                m=re.search(r"SN:([^\t]+)",x); n=re.search(r"LN:(\d+)",x)
                if m: refs.append({"name":m.group(1),"length":int(n.group(1)) if n else None})
        emit({"format":Path(path).suffix.lower().lstrip('.').upper(),"header_lines":len(h),"references":refs,"records_sampled":len(rows),"statistics":stats(rows)}); return
    if op=="alignment_validate":
        issues=[]
        for x in fs:
            if x["flag"]&4: continue
            if x["reference"] in (None,"*") or x["pos"]<1: issues.append({"read":x["qname"],"error":"无效参考序列或坐标"})
            if x["cigar"]=="*": issues.append({"read":x["qname"],"error":"缺少 CIGAR"})
            if x["seq"]!="*" and x["cigar"]!="*" and sum(int(n) for n,c in re.findall(r"(\d+)([MIDNSHP=X])",x["cigar"]) if c in "MIS=X")!=len(x["seq"]): issues.append({"read":x["qname"],"error":"序列长度与 CIGAR 不一致"})
        emit({"valid":not issues,"checked":len(fs),"issues":issues[:500]}); return
    if op=="alignment_summary": emit(stats(rows)); return
    if op=="alignment_mismatch_profile":
        c=Counter();
        for x in fs:
            for n,k in re.findall(r"(\d+)([MIDNSHP=X])",x["cigar"]): c[{"I":"insertions","D":"deletions","X":"mismatches","M":"matches"}.get(k,k)]+=int(n)
        emit({"records_sampled":len(fs),"cigar_operations":dict(c)}); return
    if op in {"alignment_coverage_profile","alignment_depth_visualize"}:
        depth=defaultdict(int)
        for x in fs:
            if x["flag"]&4 or x["reference"] in (None,"*"): continue
            pos=x["pos"]
            for n,k in re.findall(r"(\d+)([MIDNSHP=X])",x["cigar"]):
                n=int(n)
                if k in "M=X":
                    for i in range(pos,pos+n): depth[(x["reference"],i)]+=1
                    pos+=n
                elif k in "DN": pos+=n
        values=list(depth.values()); result={"positions":len(depth),"mean_depth":float(np.mean(values)) if values else 0,"max_depth":max(values) if values else 0,"covered_bases":sum(v>0 for v in values),"depth_histogram":dict(Counter(values))}
        if op=="alignment_depth_visualize":
            import matplotlib; matplotlib.use("Agg"); plt=plot_style()
            pts=sorted(depth.items(),key=lambda z:(z[0][0],z[0][1])); lim=max(1,int(a.get("max_points",5000))); pts=pts[::max(1,len(pts)//lim)]
            values_plot=[v for k,v in pts]; fig,ax=plt.subplots(figsize=(12,4.8)); ax.plot(range(len(pts)),values_plot,color='#0f766e',lw=1.3); ax.fill_between(range(len(pts)),values_plot,0,color='#14b8a6',alpha=.12); ax.set(title='比对测序深度曲线',xlabel='参考序列位置（按顺序抽样）',ylabel='深度'); ax.grid(alpha=.2); fig.tight_layout(pad=1.3); Path(a["output_path"]).parent.mkdir(parents=True,exist_ok=True); fig.savefig(a["output_path"],dpi=220,bbox_inches='tight'); plt.close(fig); result["output_path"]=a["output_path"]
            try:
                import plotly.graph_objects as go
                chart=go.Figure(go.Scatter(x=list(range(len(pts))),y=values_plot,mode='lines',fill='tozeroy',line={'color':'#0f766e'},hovertemplate='位置 %{x}<br>深度 %{y}<extra></extra>')); chart.update_layout(title='比对测序深度曲线',xaxis_title='参考序列位置（抽样）',yaxis_title='深度'); chart.write_html(html_output(a["output_path"]),include_plotlyjs='inline',full_html=True); result["interactive_output_path"]=html_output(a["output_path"])
            except Exception: pass
        else:
            result["by_reference"]={}
            for ref in sorted({k[0] for k in depth}):
                v=[n for (r,_),n in depth.items() if r==ref]; result["by_reference"][ref]={"positions":len(v),"mean_depth":float(np.mean(v)) if v else 0,"max_depth":max(v) if v else 0}
        emit(result,a.get("output_path") if op.endswith("profile") else None); return
    if op in {"alignment_region_extract","alignment_filter"}:
        out=Path(a["output_path"]); out.parent.mkdir(parents=True,exist_ok=True); kept=0
        with out.open("w",encoding="utf-8") as w:
            for line in h: w.write(line+"\n")
            for r,x in zip(rows,fs):
                ok=not (x["flag"]&4) or a.get("include_unmapped",False)
                if op=="alignment_filter": ok=ok and x["mapq"]>=int(a.get("min_mapq",20))
                else: ok=ok and x["reference"]==a["reference"] and x["pos"]<=int(a["end"]) and x["pos"]+len(x["seq"])-1>=int(a["start"])
                if ok: w.write("\t".join(r) if isinstance(r,list) else r.to_string()); w.write("\n"); kept+=1
        emit({"output_path":str(out),"records_written":kept}); return
    fail(f"未知工具: {op}")
if __name__=="__main__": main()
