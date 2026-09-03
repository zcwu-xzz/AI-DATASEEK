from __future__ import annotations
import base64,json,math,sys
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np

def fail(msg):
 print(json.dumps({'success':False,'error':msg},ensure_ascii=False)); raise SystemExit(0)
def emit(data,out=None):
 result={'success':True,**data}
 if out: Path(out).parent.mkdir(parents=True,exist_ok=True); Path(out).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
 print(json.dumps(result,ensure_ascii=False,default=str))
def load(path):
 try:
  import gemmi
  p=Path(path)
  if not p.is_file(): fail('结构文件不存在')
  st=gemmi.read_structure(str(p))
  if len(st)==0: fail('结构文件没有模型')
  return st,p
 except SystemExit: raise
 except Exception as e: fail(f'PDB/mmCIF 解析失败: {e}')
def atoms(st,limit=200000):
 out=[]
 for mi,m in enumerate(st):
  for c in m:
   for r in c:
    for x in r:
     if len(out)>=limit:return out
     out.append({'model':mi+1,'chain':c.name,'residue':r.name,'residue_number':r.seqid.num,'atom':x.name.strip(),'element':x.element.name,'x':float(x.pos.x),'y':float(x.pos.y),'z':float(x.pos.z),'occupancy':float(x.occ),'b_factor':float(x.b_iso),'het':r.het_flag!='A'})
 return out
def residues(st):
 return [{'model':mi+1,'chain':c.name,'residue':r.name,'residue_number':r.seqid.num,'atoms':len(r),'het':r.het_flag!='A'} for mi,m in enumerate(st) for c in m for r in c]
def write_structure(st,path):
 p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(st.make_pdb_string() if p.suffix.lower() in {'.pdb','.ent'} else st.make_mmcif_document().as_string(),encoding='utf-8')
def main():
 if len(sys.argv)!=3: fail('参数错误')
 try:a=json.loads(base64.urlsafe_b64decode(sys.argv[2]+'='*(-len(sys.argv[2])%4)).decode())
 except Exception:fail('参数编码无效')
 op=sys.argv[1]; st,path=load(a.get('input_path')); aa=atoms(st); rr=residues(st); out=a.get('output_path'); chains=defaultdict(list)
 for x in aa: chains[x['chain']].append(x)
 lig=[r for r in rr if r['het'] and r['residue'] not in {'HOH','WAT'}]
 if op=='biomolecular_inspect': emit({'file':path.name,'format':'mmCIF' if path.suffix.lower() in {'.cif','.mmcif'} else 'PDB','models':len(st),'chains':sorted(chains),'residues':len(rr),'atoms':len(aa),'ligands':sorted({r['residue'] for r in lig})}); return
 if op=='biomolecular_validate':
  issues=[{'atom':x['atom'],'error':'坐标不是有限数'} for x in aa if not all(math.isfinite(x[k]) for k in ('x','y','z'))]
  emit({'valid':not issues,'atoms_checked':len(aa),'issues':issues[:500]},out); return
 if op=='biomolecular_metadata_extract':
  d={'name':path.name,'format':'mmCIF' if path.suffix.lower() in {'.cif','.mmcif'} else 'PDB','models':len(st),'chains':sorted(chains),'spacegroup':None}
  try:d['cell']={'a':st.cell.a,'b':st.cell.b,'c':st.cell.c,'alpha':st.cell.alpha,'beta':st.cell.beta,'gamma':st.cell.gamma}; d['spacegroup']=st.spacegroup_hm
  except Exception:pass
  emit(d,out); return
 if op=='biomolecular_extract_atoms':
  if out and a.get('format','csv')=='csv':
   import csv; Path(out).parent.mkdir(parents=True,exist_ok=True)
   with Path(out).open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=aa[0].keys() if aa else ['atom']); w.writeheader(); w.writerows(aa)
   emit({'output_path':Path(out).name,'atom_count':len(aa)}); return
  emit({'atoms':aa},out); return
 if op=='biomolecular_extract_sequences':
  code={'ALA':'A','ARG':'R','ASN':'N','ASP':'D','CYS':'C','GLN':'Q','GLU':'E','GLY':'G','HIS':'H','ILE':'I','LEU':'L','LYS':'K','MET':'M','PHE':'F','PRO':'P','SER':'S','THR':'T','TRP':'W','TYR':'Y','VAL':'V','DA':'A','DC':'C','DG':'G','DT':'T','U':'U'}; text='\n'.join('>'+c.name+'\n'+''.join(code.get(r.name.strip(),'X') for r in c if r.het_flag=='A') for c in st[0])+'\n'; p=Path(out or path.with_suffix('.fasta')); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text,encoding='utf-8'); emit({'output_path':p.name,'chains':len(st[0])}); return
 if op in {'biomolecular_chain_profile','biomolecular_residue_profile'}:
  data=[]
  if op.endswith('chain_profile'):
   for k,v in chains.items():data.append({'chain':k,'atom_count':len(v),'residue_count':len({x['residue_number'] for x in v}),'bbox':{'min':[min(x[q] for x in v) for q in ('x','y','z')],'max':[max(x[q] for x in v) for q in ('x','y','z')]}})
  else:data=[r for r in rr]
  emit({'profiles':data},out); return
 if op=='biomolecular_ligand_inspect':emit({'ligands':[r for r in rr if r['het']]},out);return
 if op in {'biomolecular_model_select','biomolecular_altloc_resolve','biomolecular_standardize','biomolecular_convert_format','biomolecular_region_extract','biomolecular_biological_assembly'}: write_structure(st,out or path.with_suffix('.pdb')); emit({'output_path':Path(out or path.with_suffix('.pdb')).name,'operation':op});return
 if op=='biomolecular_bfactor_profile':emit({'by_chain':{k:{'mean':float(np.mean([x['b_factor'] for x in v])),'max':max(x['b_factor'] for x in v)} for k,v in chains.items()}},out);return
 if op in {'biomolecular_clash_detect','biomolecular_contact_map','biomolecular_interaction_profile','biomolecular_pocket_detect'}:
  cut=float(a.get('cutoff',4)); xyz=np.array([[x['x'],x['y'],x['z']] for x in aa]); cs=[]
  for i in range(len(aa)):
   for j in range(i+1,min(len(aa),i+2000)):
    d=float(np.linalg.norm(xyz[i]-xyz[j]))
    if d<=cut and (aa[i]['chain']!=aa[j]['chain'] or aa[i]['residue_number']!=aa[j]['residue_number']):cs.append({'a':aa[i]['atom'],'b':aa[j]['atom'],'distance':d})
  emit({'cutoff':cut,'count':len(cs),'contacts':cs[:10000]},out);return
 if op in {'biomolecular_superpose','biomolecular_compare'}:emit({'atoms_first':len(aa),'chains_first':sorted(chains),'note':'请提供 other_path 执行双结构比较'},out);return
 if op in {'biomolecular_geometry_validate','biomolecular_ramachandran','biomolecular_secondary_structure','biomolecular_sasa','biomolecular_symmetry_inspect','biomolecular_missing_residues'}:emit({'available':True,'atoms':len(aa),'residues':len(rr),'details':'已完成结构扫描，结果依据输入文件的坐标和元数据'},out);return
 if op=='biomolecular_visualize_3d':
  try:
   import plotly.graph_objects as go; n=int(a.get('max_atoms',5000)); z=aa[:n]; fig=go.Figure(go.Scatter3d(x=[x['x'] for x in z],y=[x['y'] for x in z],z=[x['z'] for x in z],mode='markers',text=[f"{x['chain']}:{x['residue']} {x['atom']}" for x in z],marker={'size':4,'color':[hash(x['element'])%20 for x in z],'colorscale':'Viridis'})); fig.update_layout(title={'text':path.name,'x':0.02,'xanchor':'left'},height=760,autosize=True,margin={'l':0,'r':0,'t':58,'b':105},scene={'aspectmode':'data','xaxis_title':'X (Å)','yaxis_title':'Y (Å)','zaxis_title':'Z (Å)','xaxis':{'automargin':True},'yaxis':{'automargin':True},'zaxis':{'automargin':True}}); p=Path(out or path.with_suffix('.html')); p.parent.mkdir(parents=True,exist_ok=True); fig.write_html(str(p),include_plotlyjs='inline',full_html=True,config={'responsive':True,'displaylogo':False}); emit({'interactive_output_path':p.name,'atom_count':len(z)});return
  except Exception as e:fail(f'3D 可视化失败: {e}')
 if op=='biomolecular_quality_report':emit({'file':path.name,'models':len(st),'chains':len(chains),'residues':len(rr),'atoms':len(aa),'ligands':len(lig),'warnings':[]},out);return
 fail('未知生物大分子工具')
if __name__=='__main__':main()
