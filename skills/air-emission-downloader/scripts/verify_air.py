"""Offline readback against archived air-only official records, not an API call."""
import argparse,hashlib,json
from decimal import Decimal
from pathlib import Path

def pointer(obj,p):
 for k in p.lstrip('/').split('/'):
  k=k.replace('~1','/').replace('~0','~')
  obj=obj[int(k)] if isinstance(obj,list) else obj[k]
 return obj

def verify(folder):
 p=Path(folder);load=lambda q:json.loads(q.read_text(encoding='utf-8'))
 manifest=load(p/'report_manifest.json');rows=load(p/'air_monthly_rows.json');summaries=load(p/'jja_summary.json')
 bodies={};problems=[]
 for m in manifest:
  if m['status']!='downloaded':continue
  # Use local relative report cache to keep verification portable after delivery.
  matches=[q for q in (p/'reports').glob('*/provenance.json') if load(q).get('report_id')==m['report_id']]
  if len(matches)!=1:problems.append(['report_cache_missing_or_duplicate',m['report_id']]);continue
  file=matches[0].parent/'air_report.json';b=file.read_bytes();body=json.loads(b)
  if hashlib.sha256(b).hexdigest()!=m['air_sha256']:problems.append(['air_hash',m['report_id']])
  if body.get('companyInfo',{}).get('permitCode')!=m['permit_code']:problems.append(['identity',m['report_id']])
  bodies[m['report_id']]=body
 for i,r in enumerate(rows):
  body=bodies.get(r['report_id'])
  if body is None:problems.append(['row_no_body',i]);continue
  try:
   source=pointer(body,r['row_json_pointer'])
   if source!=r['raw_row']:problems.append(['raw_row',i])
   if r.get('field_present') and pointer(body,r['json_pointer'])!=r['value_raw']:problems.append(['raw_value',i])
  except (KeyError,IndexError,TypeError):problems.append(['pointer',i])
 complete=0
 for i,g in enumerate(summaries):
  if not g['complete']:
   if g.get('jja') is not None:problems.append(['incomplete_nonnull_total',i])
   continue
  complete+=1;mm=g['months']
  if [m['month'] for m in mm]!=[6,7,8] or any(m['status']!='selected' for m in mm):problems.append(['months',i]);continue
  calculated=sum((Decimal(m['value_decimal']) for m in mm),Decimal(0))
  if calculated!=Decimal(g['jja_decimal']):problems.append(['total_arithmetic',i])
 result={'passed':not problems,'report_files_checked':len(bodies),'monthly_rows_read_back':len(rows),'jja_groups_checked':len(summaries),
         'complete_jja_groups_checked':complete,'problems':problems,
         'boundary':'Tests raw source fidelity and arithmetic, not independent truth of enterprise declarations or unit inference.'}
 (p/'source_readback_qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if not problems else 2

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('folder');a=ap.parse_args();raise SystemExit(verify(a.folder))
