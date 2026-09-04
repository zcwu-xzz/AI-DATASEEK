from __future__ import annotations
import base64,json,sys
from pathlib import Path
from collections import Counter,defaultdict

def fail(msg): print(json.dumps({'success':False,'error':msg},ensure_ascii=False)); raise SystemExit(0)
def emit(data,out=None):
 r={'success':True,**data}
 if out: Path(out).parent.mkdir(parents=True,exist_ok=True); Path(out).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(r,ensure_ascii=False,default=str))
def lines(path):
 p=Path(path)
 if not p.is_file(): fail('输入文件不存在')
 return p.read_text(encoding='utf-8',errors='replace').splitlines()
def vcf(path):
 hs=[]; rows=[]
 for l in lines(path):
  if l.startswith('#'): hs.append(l); continue
  x=l.split('\t')
  if len(x)>=8: rows.append(x)
 return hs,rows
def vtype(ref,alts):
 return 'SNP' if len(ref)==1 and all(len(a)==1 for a in alts) else ('INDEL' if any(len(a)!=len(ref) for a in alts) else 'OTHER')
def gff(path):
 out=[]
 for l in lines(path):
  if not l or l.startswith('#'): continue
  x=l.split('\t')
  if len(x)>=9:
   try: out.append(x)
   except: pass
 return out
def bed(path):
 out=[]
 for l in lines(path):
  if not l or l.startswith('#') or l.startswith('track'): continue
  x=l.split('\t')
  if len(x)>=3:
   try: out.append((x[0],int(x[1]),int(x[2]),x))
   except ValueError: pass
 return out
def main():
 if len(sys.argv)!=3: fail('参数错误')
 try: a=json.loads(base64.urlsafe_b64decode(sys.argv[2]+'='*(-len(sys.argv[2])%4)))
 except Exception: fail('参数编码无效')
 op=sys.argv[1]; p=a.get('input_path')
 if op.startswith('vcf_'):
  hs,rs=vcf(p)
  if op=='vcf_inspect': emit({'header_lines':len(hs),'records':len(rs),'samples':hs[-1].split('\t')[9:] if hs and hs[-1].startswith('#CHROM') else [],'chromosomes':sorted({r[0] for r in rs}),'position_range':{'min':min((int(r[1]) for r in rs),default=None),'max':max((int(r[1]) for r in rs),default=None)}}); return
  if op=='vcf_validate':
   issues=[]
   for i,r in enumerate(rs):
    if len(r)<8: issues.append({'line':i+1,'error':'字段不足'}); continue
    try:
     if int(r[1])<1: issues.append({'line':i+1,'error':'坐标必须为正数'})
    except: issues.append({'line':i+1,'error':'坐标无效'})
    if not r[3] or r[4]=='.': issues.append({'line':i+1,'error':'参考或替代等位基因为空'})
   emit({'valid':not issues,'checked':len(rs),'issues':issues[:500]}); return
  if op=='vcf_variant_summary':
   c=Counter(); quals=[]; filters=Counter(); gt=Counter()
   for r in rs:
    c[vtype(r[3],r[4].split(','))]+=1
    try: quals.append(float(r[5]))
    except: pass
    filters[r[6]]+=1
    for s in r[9:]: gt[s.split(':')[0]]+=1
   emit({'records':len(rs),'variant_types':dict(c),'quality':{'min':min(quals) if quals else None,'max':max(quals) if quals else None,'mean':sum(quals)/len(quals) if quals else None},'filters':dict(filters),'genotypes':dict(gt)}); return
  if op in {'vcf_filter','vcf_region_extract'}:
   out=Path(a['output_path']); out.parent.mkdir(parents=True,exist_ok=True); kept=[]
   for r in rs:
    ok=True
    if op=='vcf_filter':
     if a.get('chromosome') and r[0]!=a['chromosome']: ok=False
     try: ok=ok and float(r[5])>=float(a.get('min_qual',0))
     except: ok=False
     if a.get('variant_type') and vtype(r[3],r[4].split(','))!=a['variant_type']: ok=False
    else: ok=r[0]==a['chromosome'] and int(a['start'])<=int(r[1])<=int(a['end'])
    if ok: kept.append(r)
   out.write_text('\n'.join(hs+['\t'.join(r) for r in kept])+'\n',encoding='utf-8'); emit({'output_path':str(out),'records_written':len(kept)}); return
  if op=='vcf_density_visualize':
   import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
   w=int(a.get('window_size',1000000)); c=Counter((r[0],(int(r[1])-1)//w) for r in rs); keys=sorted(c); counts=[c[k] for k in keys]; fig,ax=plt.subplots(figsize=(12,5)); ax.bar(range(len(keys)),counts,color='#7c3aed',alpha=.85); ax.set(title=f'VCF 变异密度（窗口 {w:,} bp）',xlabel='染色体窗口（按染色体排序）',ylabel='变异数量'); ax.grid(axis='y',alpha=.2); fig.tight_layout(pad=1.3); Path(a['output_path']).parent.mkdir(parents=True,exist_ok=True); fig.savefig(a['output_path'],dpi=220,bbox_inches='tight'); plt.close(fig); html=str(Path(a['output_path']).with_suffix('.html'))
   try:
    import plotly.express as px
    px.bar(x=[f'{k[0]}:{k[1]*w+1:,}' for k in keys],y=counts,labels={'x':'染色体窗口','y':'变异数量'},title=f'VCF 变异密度（窗口 {w:,} bp）').write_html(html,include_plotlyjs='inline',full_html=True)
   except Exception: html=None
   emit({'output_path':a['output_path'],'interactive_output_path':html,'windows':len(keys),'window_size':w}); return
 if op.startswith('gff_'):
  rs=gff(p)
  if op=='gff_inspect': emit({'features':len(rs),'feature_types':dict(Counter(r[2] for r in rs)),'chromosomes':sorted({r[0] for r in rs}),'sources':sorted({r[1] for r in rs})}); return
  if op=='gff_validate':
   issues=[]
   for i,r in enumerate(rs):
    try:
     if int(r[3])<1 or int(r[4])<int(r[3]): issues.append({'line':i+1,'error':'坐标范围无效'})
     if r[6] not in ('+','-','.') : issues.append({'line':i+1,'error':'链方向无效'})
    except: issues.append({'line':i+1,'error':'坐标不是整数'})
   emit({'valid':not issues,'checked':len(rs),'issues':issues[:500]}); return
  if op=='gff_feature_summary': emit({'features':len(rs),'counts':dict(Counter(r[2] for r in rs)),'lengths':{k:{'min':min((int(r[4])-int(r[3])+1 for r in rs if r[2]==k),default=0),'max':max((int(r[4])-int(r[3])+1 for r in rs if r[2]==k),default=0)} for k in set(r[2] for r in rs)}}); return
  if op=='gff_region_extract':
   kept=[r for r in rs if r[0]==a['chromosome'] and int(r[3])<=int(a['end']) and int(r[4])>=int(a['start'])]; out=Path(a['output_path']); out.parent.mkdir(parents=True,exist_ok=True); out.write_text('\n'.join('\t'.join(r) for r in kept)+'\n',encoding='utf-8'); emit({'output_path':str(out),'features_written':len(kept)}); return
 if op.startswith('bed_'):
  rs=bed(p)
  if op=='bed_inspect': emit({'intervals':len(rs),'chromosomes':sorted({r[0] for r in rs}),'range':{'min':min((r[1] for r in rs),default=None),'max':max((r[2] for r in rs),default=None)},'columns':max((len(r[3]) for r in rs),default=0)}); return
  if op=='bed_merge_intervals':
   out=[]
   for chrom in sorted({r[0] for r in rs}):
    cur=None
    for _,s,e,x in sorted([r for r in rs if r[0]==chrom],key=lambda r:r[1]):
     if cur is None: cur=[chrom,s,e]
     elif s<=cur[2]+(1 if a.get('merge_adjacent',True) else 0): cur[2]=max(cur[2],e)
     else: out.append(cur); cur=[chrom,s,e]
    if cur: out.append(cur)
   Path(a['output_path']).parent.mkdir(parents=True,exist_ok=True); Path(a['output_path']).write_text('\n'.join('\t'.join(map(str,x)) for x in out)+'\n'); emit({'output_path':a['output_path'],'intervals_written':len(out)}); return
  if op=='bed_intersect':
   other=bed(a['other_path']); out=[]
   for c,s,e,x in rs:
    for c2,s2,e2,y in other:
     if c==c2 and max(s,s2)<min(e,e2): out.append([c,max(s,s2),min(e,e2)])
   Path(a['output_path']).parent.mkdir(parents=True,exist_ok=True); Path(a['output_path']).write_text('\n'.join('\t'.join(map(str,x)) for x in out)+'\n'); emit({'output_path':a['output_path'],'intersections':len(out)}); return
 if op in {'vcf_sample_qc','vcf_allele_frequency','vcf_titv_profile','vcf_genotype_matrix_export'}:
  hs,rs=vcf(p); column=next((line for line in reversed(hs) if line.startswith('#CHROM')),None); samples=column.split('\t')[9:] if column else []
  def parsed_gt(row):
   keys=row[8].split(':') if len(row)>8 else []; gt_index=keys.index('GT') if 'GT' in keys else 0; values=[]
   for sample in row[9:]:
    fields=sample.split(':'); values.append(fields[gt_index] if gt_index<len(fields) else './.')
   return values
  if op=='vcf_sample_qc':
   counters=[Counter() for _ in samples]
   for row in rs:
    for index,gt in enumerate(parsed_gt(row)):
     alleles=gt.replace('|','/').split('/')
     if not alleles or any(x=='.' for x in alleles): counters[index]['missing']+=1
     elif len(set(alleles))>1: counters[index]['heterozygous']+=1
     elif all(x=='0' for x in alleles): counters[index]['hom_ref']+=1
     else: counters[index]['hom_alt']+=1
   result=[]
   for name,c in zip(samples,counters):
    total=sum(c.values()); called=total-c['missing']; result.append({'sample':name,'records':total,'called':called,'call_rate':called/total if total else 0,'missing_rate':c['missing']/total if total else 0,'heterozygosity':c['heterozygous']/called if called else 0,'hom_ref':c['hom_ref'],'hom_alt':c['hom_alt']})
   emit({'sample_count':len(samples),'variant_count':len(rs),'samples':result}); return
  if op=='vcf_allele_frequency':
   records_out=[]; limit=max(1,min(1000000,int(a.get('max_records',100000))))
   for row in rs[:limit]:
    alt_count=called=0
    for gt in parsed_gt(row):
     for allele in gt.replace('|','/').split('/'):
      if allele=='.': continue
      called+=1
      if allele!='0': alt_count+=1
    frequency=alt_count/called if called else None
    records_out.append({'chromosome':row[0],'position':int(row[1]),'id':row[2],'ref':row[3],'alt':row[4],'alternate_allele_count':alt_count,'called_alleles':called,'alternate_allele_frequency':frequency,'minor_allele_frequency':min(frequency,1-frequency) if frequency is not None else None})
   emit({'variant_count':len(rs),'returned_records':len(records_out),'truncated':len(rs)>limit,'variants':records_out}); return
  if op=='vcf_titv_profile':
   transitions={('A','G'),('G','A'),('C','T'),('T','C')}; spectrum=Counter(); ti=tv=0
   for row in rs:
    ref=row[3].upper()
    for alt in row[4].upper().split(','):
     if len(ref)!=1 or len(alt)!=1 or ref not in 'ACGT' or alt not in 'ACGT': continue
     spectrum[f'{ref}>{alt}']+=1
     if (ref,alt) in transitions: ti+=1
     else: tv+=1
   emit({'transitions':ti,'transversions':tv,'ti_tv_ratio':ti/tv if tv else None,'substitution_spectrum':dict(sorted(spectrum.items()))}); return
  if op=='vcf_genotype_matrix_export':
   import csv
   destination=Path(a['output_path']); destination.parent.mkdir(parents=True,exist_ok=True); encoding=a.get('encoding','dosage')
   with destination.open('w',newline='',encoding='utf-8-sig') as handle:
    writer=csv.writer(handle); writer.writerow(['variant_id','chromosome','position','ref','alt',*samples])
    for row in rs:
     values=[]
     for gt in parsed_gt(row):
      if encoding=='genotype': values.append(gt); continue
      alleles=gt.replace('|','/').split('/'); values.append('' if not alleles or any(x=='.' for x in alleles) else sum(x!='0' for x in alleles))
     writer.writerow([row[2] if row[2]!='.' else f'{row[0]}:{row[1]}:{row[3]}:{row[4]}',row[0],row[1],row[3],row[4],*values])
   emit({'output_path':destination.name,'variant_count':len(rs),'sample_count':len(samples),'encoding':encoding}); return
 fail(f'未知工具: {op}')
if __name__=='__main__': main()
