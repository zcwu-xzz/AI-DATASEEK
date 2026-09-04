from __future__ import annotations
import base64,json,sys,re
from pathlib import Path
from collections import Counter
import numpy as np

def plot_style():
 import matplotlib.pyplot as plt
 plt.rcParams.update({'figure.facecolor':'white','axes.facecolor':'#fbfcfe','axes.edgecolor':'#cbd5e1','axes.labelcolor':'#334155','xtick.color':'#475569','ytick.color':'#475569','font.size':10,'axes.titleweight':'bold'})
 return plt
def finish_plot(fig, out):
 # Keep pyplot scoped here so callers cannot accidentally rely on a local import.
 import matplotlib.pyplot as plt
 fig.tight_layout(pad=1.4); Path(out).parent.mkdir(parents=True,exist_ok=True); fig.savefig(out,dpi=220,bbox_inches='tight',facecolor='white'); plt.close(fig)
def html_output(out): return str(Path(out).with_suffix('.html'))

def fail(m): print(json.dumps({'success':False,'error':m},ensure_ascii=False)); raise SystemExit(0)
def emit(d,out=None):
 p={'success':True,**d}
 if out: Path(out).parent.mkdir(parents=True,exist_ok=True); Path(out).write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(p,ensure_ascii=False,default=str))
def records(path):
 p=Path(path)
 if not p.is_file(): fail('序列文件不存在')
 try:
  from Bio import SeqIO
  return list(SeqIO.parse(str(p),'fastq' if p.suffix.lower() in {'.fastq','.fq'} or (p.read_text(errors='ignore').lstrip().startswith('@')) else 'fasta'))
 except Exception as e: fail(f'序列解析失败: {e}')
def summary(rs):
 lengths=np.array([len(r.seq) for r in rs],int); gc=np.array([(str(r.seq).upper().count('G')+str(r.seq).upper().count('C'))/max(len(r.seq),1)*100 for r in rs])
 return {'sequence_count':len(rs),'length':{'min':int(lengths.min()) if len(lengths) else 0,'max':int(lengths.max()) if len(lengths) else 0,'mean':float(lengths.mean()) if len(lengths) else 0},'gc_percent':{'mean':float(gc.mean()) if len(gc) else 0,'min':float(gc.min()) if len(gc) else 0,'max':float(gc.max()) if len(gc) else 0},'n_fraction_mean':float(np.mean([str(r.seq).upper().count('N')/max(len(r.seq),1) for r in rs])) if rs else 0}
def write_records(rs,path,fastq=False,quality=40):
 from Bio import SeqIO
 Path(path).parent.mkdir(parents=True,exist_ok=True)
 if fastq:
  for r in rs:
   if not r.letter_annotations.get('phred_quality'): r.letter_annotations['phred_quality']=[quality]*len(r.seq)
 SeqIO.write(rs,path,'fastq' if fastq else 'fasta')
def blast_rows(path):
    rows=[]
    fields=['qseqid','sseqid','pident','length','mismatch','gapopen','qstart','qend','sstart','send','evalue','bitscore']
    for line in Path(path).read_text(errors='replace').splitlines():
        if not line.strip() or line.startswith('#'): continue
        parts=line.split('\t')
        if len(parts)<12: continue
        try: rows.append(dict(zip(fields,[parts[0],parts[1],float(parts[2]),int(parts[3]),int(parts[4]),int(parts[5]),int(parts[6]),int(parts[7]),int(parts[8]),int(parts[9]),float(parts[10]),float(parts[11])])))
        except ValueError: continue
    return rows
def main():
 if len(sys.argv)!=3: fail('参数错误')
 op=sys.argv[1]
 try: a=json.loads(base64.urlsafe_b64decode(sys.argv[2]+'='*(-len(sys.argv[2])%4)).decode())
 except Exception: fail('参数编码无效')
 path=a.get('input_path'); out=a.get('output_path'); rs=records(path) if path else []
 if op in {'sequence_identify_format','sequence_inspect','sequence_stats','sequence_quality_encoding','sequence_quality_report'}:
  s=summary(rs); fmt='FASTQ' if Path(path).suffix.lower() in {'.fq','.fastq'} or Path(path).read_text(errors='ignore').lstrip().startswith('@') else 'FASTA'; result={'format':fmt,**s}
  if fmt=='FASTQ':
   q=[q for r in rs for q in r.letter_annotations.get('phred_quality',[])]; result['quality']={'min':min(q) if q else None,'max':max(q) if q else None,'mean':float(np.mean(q)) if q else None}
  emit(result,out); return
 if op=='sequence_validate':
  issues=[]; valid_chars=set('ACGTUNRYSWKMBDHV-')
  for r in rs[:100000]:
   bad=set(str(r.seq).upper())-valid_chars
   if bad: issues.append({'id':r.id,'invalid_bases':sorted(bad)})
   if r.letter_annotations.get('phred_quality') and len(r.letter_annotations['phred_quality'])!=len(r.seq): issues.append({'id':r.id,'error':'质量值长度不一致'})
  emit({'valid':not issues,'checked':len(rs),'issues':issues[:500]},out); return
 if op=='sequence_pair_validate':
  r1,r2=records(a['read1_path']),records(a['read2_path']); n=min(len(r1),len(r2)); mismatches=[]
  def key(r): return re.sub(r'([/ _])?[12]$','',r.id)
  for i in range(min(n,100000)):
   if key(r1[i])!=key(r2[i]): mismatches.append({'index':i,'read1':r1[i].id,'read2':r2[i].id})
  emit({'read1_count':len(r1),'read2_count':len(r2),'paired_count':n,'match':len(r1)==len(r2) and not mismatches,'mismatches':mismatches[:1000]},out); return
 if op=='sequence_position_quality':
  rows=[]
  for i in range(max((len(r) for r in rs),default=0)):
   q=[r.letter_annotations.get('phred_quality',[])[i] for r in rs if i<len(r) and r.letter_annotations.get('phred_quality')]
   if q: rows.append({'position':i+1,'mean':float(np.mean(q)),'q25':float(np.percentile(q,25)),'q75':float(np.percentile(q,75)),'low_fraction':float(np.mean(np.array(q)<20))})
  emit({'positions':rows},out); return
 if op=='sequence_position_bases':
  rows=[]
  for i in range(max((len(r) for r in rs),default=0)):
   c=Counter(str(r.seq).upper()[i] for r in rs if i<len(r)); total=sum(c.values()); rows.append({'position':i+1,**{b: c[b]/total if total else 0 for b in 'ACGTN'}})
  emit({'positions':rows},out); return
 if op in {'sequence_filter_quality','sequence_trim_adapters','sequence_trim_length','sequence_deduplicate','sequence_convert_fasta_fastq','sequence_reverse_complement','sequence_extract_unmapped'}:
  from Bio.Seq import Seq
  if op=='sequence_filter_quality': rs=[r for r in rs if len(r)>=int(a.get('min_length',30)) and str(r.seq).upper().count('N')/max(len(r),1)<=float(a.get('max_n_fraction',.1)) and (not r.letter_annotations.get('phred_quality') or np.mean(r.letter_annotations['phred_quality'])>=float(a.get('min_quality',20)))]
  elif op=='sequence_trim_adapters':
   for r in rs:
    for ad in a['adapters']:
     i=str(r.seq).upper().find(ad.upper()); r.seq=r.seq[:i] if i>=0 else r.seq
  elif op=='sequence_trim_length':
   for r in rs: r.seq=r.seq[int(a.get('start',0)):int(a['end']) if a.get('end') is not None else None]
  elif op=='sequence_deduplicate':
   seen=set(); rs=[r for r in rs if not (str(r.seq) in seen or seen.add(str(r.seq)))]
  elif op=='sequence_reverse_complement':
   for r in rs: r.seq=r.seq.reverse_complement()
  elif op=='sequence_extract_unmapped':
   names={x.strip().split()[0] for x in Path(a['names_path']).read_text().splitlines() if x.strip()}; rs=[r for r in rs if r.id in names]
  write_records(rs,out,Path(out).suffix.lower() in {'.fq','.fastq'},int(a.get('default_quality',40))); emit({'output_path':Path(out).name,'sequence_count':len(rs)}); return
 if op=='sequence_kmer_profile':
  k=int(a.get('k',5)); c=Counter(s[i:i+k] for r in rs for s in [str(r.seq).upper()] for i in range(max(0,len(s)-k+1))); emit({'k':k,'distinct_kmers':len(c),'total_kmers':sum(c.values()),'top_kmers':[{'kmer':x,'count':n} for x,n in c.most_common(100)]}); return
 if op=='sequence_complexity_check':
  result=[]
  for r in rs:
   s=str(r.seq).upper(); counts=Counter(s); entropy=-sum((n/len(s))*np.log2(n/len(s)) for n in counts.values()) if s else 0; hom=max(counts.values())/len(s) if s else 0
   if entropy<1.5 or hom>.8: result.append({'id':r.id,'entropy':float(entropy),'homopolymer_fraction':hom})
  emit({'low_complexity_count':len(result),'sequences':result[:1000]}); return
 if op=='sequence_batch_profile': emit({'files':[{'path':Path(p).name,**summary(records(p))} for p in a['input_paths']]}); return
 if op=='sequence_gc_visualize':
  import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
  vals=[(str(r.seq).upper().count('G')+str(r.seq).upper().count('C'))/max(len(r),1)*100 for r in rs]; fig,ax=plt.subplots(figsize=(10,5)); ax.hist(vals,bins=30,color='#2563eb',alpha=.88,edgecolor='white'); ax.axvline(np.mean(vals) if vals else 0,color='#dc2626',ls='--',label='平均值'); ax.set(title='GC 含量分布',xlabel='GC 含量 (%)',ylabel='序列数量'); ax.grid(axis='y',alpha=.2); ax.legend(); finish_plot(fig,out); emit({'output_path':Path(out).name,'sequence_count':len(rs),'mean_gc_percent':float(np.mean(vals)) if vals else None}); return
 if op=='sequence_fastqc_report':
  d=Path(a['output_dir']); d.mkdir(parents=True,exist_ok=True); payload={'source':Path(path).name,**summary(rs)}; (d/'sequence_quality.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)); emit({'output_dir':d.name,'files':['sequence_quality.json']}); return
 if op=='reference_fasta_inspect': emit({'reference':Path(path).name,'sequences':[{'id':r.id,'length':len(r.seq),'gc_percent':(str(r.seq).upper().count('G')+str(r.seq).upper().count('C'))/max(len(r),1)*100} for r in rs]}); return
 if op=='sequence_extract_bed':
  refs={r.id:str(r.seq) for r in rs}; selected=[]
  for line in Path(a['bed_path']).read_text().splitlines():
   if not line or line.startswith('#'): continue
   chrom,start,end,*rest=line.split(); selected.append((rest[0] if rest else f'{chrom}:{start}-{end}',refs.get(chrom,'')[int(start):int(end)]))
  from Bio.SeqRecord import SeqRecord
  from Bio.Seq import Seq
  write_records([SeqRecord(Seq(s),id=n,description='') for n,s in selected],out); emit({'output_path':Path(out).name,'sequence_count':len(selected)}); return
 if op=='sequence_alignment_summary':
  txt=Path(path).read_text(errors='ignore'); nums=[int(x) for x in re.findall(r'(\d+)\s+(?:mapped|aligned)',txt,re.I)]; emit({'file':Path(path).name,'numeric_counts':nums,'text_lines':len(txt.splitlines())}); return
 if op=='sequence_coverage_profile':
  import pandas as pd; df=pd.read_csv(path,sep=None,engine='python'); col=next((c for c in df.columns if str(c).lower() in {'depth','coverage','cov'}),df.columns[-1]); v=df[col].to_numpy(float); emit({'column':str(col),'count':len(v),'mean_depth':float(np.mean(v)),'covered_fraction':float(np.mean(v>0)),'percentiles':{str(p):float(np.percentile(v,p)) for p in (1,25,50,75,95)}}); return
 if op=='sequence_depth_visualize':
  import pandas as pd,matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
  df=pd.read_csv(path,sep=None,engine='python'); col=df.columns[-1]; values=df[col].to_numpy(float); fig,ax=plt.subplots(figsize=(12,4.8)); ax.plot(np.arange(1,len(values)+1),values,color='#0f766e',lw=1.4); ax.fill_between(np.arange(1,len(values)+1),values,0,color='#14b8a6',alpha=.12); ax.set(title='测序深度曲线',xlabel='基因组位置',ylabel='深度'); ax.grid(alpha=.2); finish_plot(fig,out); emit({'output_path':Path(out).name,'positions':len(values),'mean_depth':float(np.mean(values)) if len(values) else 0}); return
 if op=='blast_result_inspect':
  rows=blast_rows(path); emit({'file':Path(path).name,'hit_count':len(rows),'query_count':len({r['qseqid'] for r in rows}),'subject_count':len({r['sseqid'] for r in rows}),'identity_mean':float(np.mean([r['pident'] for r in rows])) if rows else None,'top_hits':sorted(rows,key=lambda r:(r['bitscore'],-r['evalue']),reverse=True)[:100]}); return
 if op in {'blast_hit_visualize','blast_dotplot_visualize'}:
  rows=blast_rows(path)
  if not rows: fail('BLAST结果中没有可解析的 outfmt 6/7 命中记录')
  import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
  Path(out).parent.mkdir(parents=True,exist_ok=True); fig,ax=plt.subplots(figsize=(12,7))
  if op=='blast_dotplot_visualize':
   for r in rows[:10000]: ax.plot([r['qstart'],r['qend']],[r['sstart'],r['send']],alpha=.35,linewidth=1)
   ax.set_xlabel('查询序列位置'); ax.set_ylabel('参考序列位置'); ax.set_title('BLAST 命中点阵图')
  else:
   top=sorted(rows,key=lambda r:(r['bitscore'],-r['evalue']),reverse=True)[:int(a.get('max_hits',100))]; subjects={s:i for i,s in enumerate(dict.fromkeys(r['sseqid'] for r in top))}
   for r in top:
    y=subjects[r['sseqid']]; ax.plot([r['qstart'],r['qend']],[y,y],linewidth=max(1,min(6,r['pident']/20)),alpha=.8); ax.scatter([r['qstart'],r['qend']],[y,y],s=10)
   ax.set_yticks(list(subjects.values()),list(subjects.keys())); ax.set_xlabel('查询序列位置'); ax.set_ylabel('参考序列'); ax.set_title('BLAST 命中区间图')
  ax.grid(alpha=.2); finish_plot(fig,out)
  try:
   import plotly.graph_objects as go
   top=rows[:int(a.get('max_hits',100))]; x=[]; y=[]; text=[]
   if op=='blast_dotplot_visualize':
    for r in top: x += [r['qstart'],r['qend'],None]; y += [r['sstart'],r['send'],None]; text += [f"{r['qseqid']} → {r['sseqid']} 相似度 {r['pident']:.1f}%"]*2+[None]
    chart=go.Figure(go.Scatter(x=x,y=y,mode='lines',text=text,hoverinfo='text')); chart.update_layout(title='BLAST 命中点阵图',xaxis_title='查询序列位置',yaxis_title='参考序列位置')
   else:
    subjects={s:i for i,s in enumerate(dict.fromkeys(r['sseqid'] for r in top))}
    for r in top: x += [r['qstart'],r['qend'],None]; y += [subjects[r['sseqid']]]*2+[None]; text += [f"{r['sseqid']} | 相似度 {r['pident']:.1f}% | E-value {r['evalue']:.2g}"]*2+[None]
    chart=go.Figure(go.Scatter(x=x,y=y,mode='lines',text=text,hoverinfo='text')); chart.update_layout(title='BLAST 命中区间图',xaxis_title='查询序列位置',yaxis_title='参考序列')
   chart.write_html(html_output(out),include_plotlyjs='inline',full_html=True)
  except Exception: pass
  emit({'output_path':Path(out).name,'interactive_output_path':Path(html_output(out)).name,'hit_count':len(rows),'rendered_hits':min(len(rows),int(a.get('max_hits',10000)))}); return
 if op in {'sequence_quality_boxplot','sequence_quality_heatmap','sequence_base_composition_visualize'}:
  import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
  if not rs or not any(r.letter_annotations.get('phred_quality') for r in rs): fail('输入中没有 FASTQ 质量值')
  Path(out).parent.mkdir(parents=True,exist_ok=True); fig,ax=plt.subplots(figsize=(12,7))
  if op=='sequence_quality_boxplot':
   q=[ [r.letter_annotations['phred_quality'][i] for r in rs if i<len(r) and r.letter_annotations.get('phred_quality')] for i in range(max(map(len,rs))) ]; ax.boxplot(q,showfliers=False,patch_artist=True,boxprops={'facecolor':'#bfdbfe','color':'#2563eb'},medianprops={'color':'#dc2626'}); ax.axhspan(0,20,color='#fee2e2',alpha=.35); ax.set_xlabel('测序位置'); ax.set_ylabel('Phred 质量值'); ax.set_title('FASTQ 位点质量分布')
  elif op=='sequence_quality_heatmap':
   max_reads=max(1,int(a.get('max_reads',1000)))
   max_positions=max(1,int(a.get('max_positions',150)))
   sampled=rs[:max_reads]
   # Pad short reads with NaN so variable-length FASTQ records remain rectangular.
   matrix=np.full((len(sampled),max_positions),np.nan,dtype=float)
   for row,record in enumerate(sampled):
    qualities=np.asarray(record.letter_annotations.get('phred_quality',()),dtype=float)[:max_positions]
    matrix[row,:len(qualities)]=qualities
   valid_counts=np.sum(~np.isnan(matrix),axis=0)
   mean_quality=np.divide(np.nansum(matrix,axis=0),valid_counts,out=np.full(max_positions,np.nan),where=valid_counts>0)
   valid_positions=np.flatnonzero(~np.isnan(mean_quality))
   last_position=int(valid_positions[-1]+1) if len(valid_positions) else 0
   display_matrix=matrix[:,:last_position] if last_position else matrix[:,:0]
   im=ax.imshow(display_matrix,aspect='auto',interpolation='nearest',cmap='RdYlGn',vmin=0,vmax=40); ax.set_xlabel('测序位置'); ax.set_ylabel('抽样读段'); ax.set_title(f'FASTQ 质量热图（抽样 {len(sampled)} 条）'); fig.colorbar(im,ax=ax,label='Phred 质量值')
   # Overlay the per-position mean and the conventional Q20 threshold.
   if last_position:
    positions=np.arange(last_position)
    ax.plot(positions,mean_quality[:last_position],color='#111827',linewidth=1.6,label='逐位平均质量')
    ax.axhline(20,color='#dc2626',linestyle='--',linewidth=1.1,label='Q20')
    ax.legend(loc='upper right',frameon=True,facecolor='white',framealpha=.9)
  else:
   positions=[]
   for i in range(max(map(len,rs))):
    c=Counter(str(r.seq).upper()[i] for r in rs if i<len(r)); total=sum(c.values()); positions.append([c[b]/total if total else 0 for b in 'ACGTN'])
   ax.stackplot(range(1,len(positions)+1),np.array(positions).T,labels=list('ACGTN'),colors=['#2563eb','#16a34a','#f59e0b','#dc2626','#64748b']); ax.set_xlabel('测序位置'); ax.set_ylabel('碱基比例'); ax.set_ylim(0,1); ax.set_title('FASTQ 位点碱基组成'); ax.legend(loc='upper center',ncol=5,frameon=False)
  finish_plot(fig,out)
  interactive=None
  if op=='sequence_quality_heatmap':
   try:
    import plotly.express as px
    interactive=html_output(out)
    chart=px.imshow(display_matrix,aspect='auto',color_continuous_scale='RdYlGn',zmin=0,zmax=40,labels={'x':'测序位置','y':'抽样读段','color':'Phred 质量值'},title='FASTQ 质量热图')
    if last_position:
     chart.add_scatter(x=list(range(last_position)),y=mean_quality[:last_position].tolist(),mode='lines',name='逐位平均质量',line={'color':'#111827','width':2})
     chart.add_hline(y=20,line_dash='dash',line_color='#dc2626',annotation_text='Q20')
    chart.write_html(interactive,include_plotlyjs='inline',full_html=True)
   except Exception: pass
  emit({'output_path':Path(out).name,'interactive_output_path':Path(interactive).name if interactive else None,'sequence_count':len(rs),'sampled_reads':len(sampled) if op=='sequence_quality_heatmap' else None,'positions':last_position if op=='sequence_quality_heatmap' else None,'mean_quality':mean_quality[:last_position].tolist() if op=='sequence_quality_heatmap' else None}); return
 if op=='sequence_subsequence_extract':
  from Bio.SeqRecord import SeqRecord
  found=next((r for r in rs if r.id==a['sequence_id']),None)
  if found is None: fail(f"未找到序列: {a['sequence_id']}")
  start=max(1,int(a.get('start',1))); end=min(len(found),int(a['end']) if a.get('end') is not None else len(found))
  if end<start: fail('提取坐标范围无效')
  seq=found.seq[start-1:end]; strand=a.get('strand','+')
  if strand=='-': seq=seq.reverse_complement()
  result=SeqRecord(seq,id=f'{found.id}:{start}-{end}({strand})',description='')
  write_records([result],out); emit({'output_path':Path(out).name,'sequence_id':found.id,'start':start,'end':end,'strand':strand,'length':len(seq)}); return
 if op=='sequence_motif_search':
  motif=str(a.get('motif','')).upper()
  if not motif: fail('motif 不能为空')
  if a.get('mode','iupac')=='iupac':
   codes={'A':'A','C':'C','G':'G','T':'T','U':'[TU]','R':'[AG]','Y':'[CT]','S':'[GC]','W':'[AT]','K':'[GT]','M':'[AC]','B':'[CGT]','D':'[AGT]','H':'[ACT]','V':'[ACG]','N':'[ACGT]'}
   try: pattern=''.join(codes[c] for c in motif)
   except KeyError as e: fail(f'IUPAC 基序包含无效字符: {e.args[0]}')
  else: pattern=motif
  try: regex=re.compile(f'(?=({pattern}))',re.I)
  except re.error as e: fail(f'正则表达式无效: {e}')
  from Bio.Seq import Seq
  reverse=str(Seq(motif).reverse_complement()) if a.get('mode','iupac')=='iupac' else None
  reverse_regex=re.compile(f'(?=({"".join(codes[c] for c in reverse)}))',re.I) if reverse and reverse!=motif and a.get('both_strands',True) else None
  limit=max(1,min(100000,int(a.get('max_hits',10000)))); hits=[]; total=0
  for record in rs:
   sequence=str(record.seq).upper()
   for strand,matcher in [('+',regex),('-',reverse_regex)]:
    if matcher is None: continue
    for match in matcher.finditer(sequence):
     total+=1
     if len(hits)<limit: hits.append({'sequence_id':record.id,'start':match.start()+1,'end':match.start()+len(match.group(1)),'strand':strand,'match':match.group(1)})
  emit({'motif':motif,'hit_count':total,'returned_hits':len(hits),'truncated':total>len(hits),'hits':hits}); return
 if op in {'sequence_orf_find','sequence_translate'}:
  from Bio.Seq import Seq
  from Bio.SeqRecord import SeqRecord
  table=int(a.get('genetic_code',1)); proteins=[]
  if op=='sequence_translate':
   frame=max(1,min(6,int(a.get('frame',1))))
   for record in rs:
    nucleotide=record.seq if frame<=3 else record.seq.reverse_complement(); offset=(frame-1)%3; translated=nucleotide[offset:].translate(table=table,to_stop=bool(a.get('to_stop',False)))
    proteins.append(SeqRecord(translated,id=f'{record.id}|frame={frame}',description=''))
  else:
   minimum=max(1,int(a.get('min_amino_acids',30)))
   for record in rs:
    for strand,nucleotide in [('+',record.seq),('-',record.seq.reverse_complement())]:
     for offset in range(3):
      translated=str(nucleotide[offset:].translate(table=table)); cursor=0
      for peptide in translated.split('*'):
       aa_start=cursor; cursor+=len(peptide)+1
       methionine=peptide.find('M')
       if methionine<0 or len(peptide)-methionine<minimum: continue
       protein=peptide[methionine:]; nt_start=offset+(aa_start+methionine)*3
       nt_end=nt_start+len(protein)*3
       if strand=='-': start=len(record)-nt_end+1; end=len(record)-nt_start
       else: start=nt_start+1; end=nt_end
       proteins.append(SeqRecord(Seq(protein),id=f'{record.id}|orf={strand}{offset+1}|{start}-{end}',description=''))
  write_records(proteins,out); emit({'output_path':Path(out).name,'protein_count':len(proteins),'genetic_code':table}); return
 if op=='sequence_codon_usage':
  from Bio.Data import CodonTable
  frame=max(1,min(3,int(a.get('frame',1)))); table=CodonTable.unambiguous_dna_by_id[int(a.get('genetic_code',1))]; codons=Counter()
  for record in rs:
   seq=str(record.seq).upper().replace('U','T')[frame-1:]
   codons.update(seq[index:index+3] for index in range(0,len(seq)-2,3) if set(seq[index:index+3])<=set('ACGT'))
  total=sum(codons.values()); amino=Counter(table.forward_table.get(codon,'Stop') for codon,count in codons.items() for _ in range(count))
  emit({'frame':frame,'total_codons':total,'stop_codons':sum(codons[c] for c in table.stop_codons),'codons':[{'codon':c,'count':n,'fraction':n/total if total else 0,'amino_acid':table.forward_table.get(c,'Stop')} for c,n in codons.most_common()],'amino_acids':dict(amino)}); return
 if op=='sequence_pairwise_identity':
  other=records(a['other_path']); left=next((r for r in rs if r.id==a.get('sequence_id')),rs[0] if rs else None); right=next((r for r in other if r.id==a.get('other_sequence_id')),other[0] if other else None)
  if left is None or right is None: fail('两个输入文件都必须包含至少一条序列')
  from Bio.Align import PairwiseAligner
  aligner=PairwiseAligner(mode='global'); alignment=aligner.align(left.seq,right.seq)[0]; coordinates=alignment.coordinates; aligned=matches=mismatches=gaps=0
  for index in range(coordinates.shape[1]-1):
   l0,l1=coordinates[0,index:index+2]; r0,r1=coordinates[1,index:index+2]
   if l1==l0 or r1==r0: gaps+=max(l1-l0,r1-r0); aligned+=max(l1-l0,r1-r0); continue
   span=min(l1-l0,r1-r0); aseq=str(left.seq[l0:l0+span]).upper(); bseq=str(right.seq[r0:r0+span]).upper(); same=sum(x==y for x,y in zip(aseq,bseq)); matches+=same; mismatches+=span-same; aligned+=span
  emit({'sequence_id':left.id,'other_sequence_id':right.id,'aligned_length':int(aligned),'matches':int(matches),'mismatches':int(mismatches),'gap_bases':int(gaps),'identity_percent':matches/max(aligned-gaps,1)*100,'alignment_score':float(alignment.score)}); return
 fail('未知序列工具')
if __name__=='__main__': main()
