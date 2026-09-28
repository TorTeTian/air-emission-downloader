"""Offline readback against archived air-only official records, not an API call."""
import argparse,hashlib,json
from decimal import Decimal
from pathlib import Path
from air_extract import extract_air,requested_rows,summarize_period,summarize_native_periods

def pointer(obj,p):
 for k in p.lstrip('/').split('/'):
  k=k.replace('~1','/').replace('~0','~')
  obj=obj[int(k)] if isinstance(obj,list) else obj[k]
 return obj

def verify(folder):
 p=Path(folder);load=lambda q:json.loads(q.read_text(encoding='utf-8'))
 manifest=load(p/'report_manifest.json');rows=load(p/'air_records.json');summaries=load(p/'period_summary.json')
 selection=load(p/'contract.json')['time_selection']
 native_summaries=load(p/'native_period_summary.json')
 enterprises={e['enterprise_id']:e for e in load(p/'enterprises.json')}
 bodies={};problems=[];reparsed=[]
 for m in manifest:
  if m['status']!='downloaded':continue
  # Use local relative report cache to keep verification portable after delivery.
  matches=[q for q in (p/'reports').glob('*/provenance.json') if (load(q).get('report_id'),load(q).get('permit_code'))==(m['report_id'],m['permit_code'])]
  if len(matches)!=1:problems.append(['report_cache_missing_or_duplicate',m['report_id']]);continue
  file=matches[0].parent/'air_report.json';b=file.read_bytes();body=json.loads(b)
  if hashlib.sha256(b).hexdigest()!=m['air_sha256']:problems.append(['air_hash',m['report_id']])
  if body.get('companyInfo',{}).get('permitCode')!=m['permit_code']:problems.append(['identity',m['report_id']])
  bodies[(m['permit_code'],m['report_id'])]=body
  reparsed.extend(requested_rows(extract_air(body,m,enterprises.get(m['enterprise_id'],{})),selection))
 for i,r in enumerate(rows):
  body=bodies.get((r['permit_code'],r['report_id']))
  if body is None:problems.append(['row_no_body',i]);continue
  try:
   source=pointer(body,r['row_json_pointer'])
   if source!=r['raw_row']:problems.append(['raw_row',i])
   if r.get('field_present') and pointer(body,r['json_pointer'])!=r['value_raw']:problems.append(['raw_value',i])
  except (KeyError,IndexError,TypeError):problems.append(['pointer',i])
 complete=0
 for i,g in enumerate(summaries):
  if not g['complete']:
   if g.get('period_total') is not None:problems.append(['incomplete_nonnull_total',i])
   continue
  complete+=1;mm=g['months']
  if not selection['whole_months'] or [m['period'] for m in mm]!=selection['periods'] or any(m['status']!='selected' for m in mm):problems.append(['months',i]);continue
  calculated=sum((Decimal(m['value_decimal']) for m in mm),Decimal(0))
  if calculated!=Decimal(g['period_total_decimal']):problems.append(['total_arithmetic',i])
 canonical=lambda rr:sorted(json.dumps(r,sort_keys=True,ensure_ascii=False) for r in rr)
 if canonical(rows)!=canonical(reparsed):problems.append(['records_differ_from_reparsed_sources'])
 if summarize_period(rows,selection)!=summaries:problems.append(['period_summary_recompute_mismatch'])
 if summarize_native_periods(rows,selection)!=native_summaries:problems.append(['native_period_summary_recompute_mismatch'])
 for name,kinds in [('air_monthly_rows',{'month'}),('air_native_period_rows',{'quarter','year'})]:
  if canonical(load(p/(name+'.json')))!=canonical([r for r in rows if r.get('period_kind') in kinds]):problems.append(['record_partition_mismatch',name])
 result={'passed':not problems,'report_files_checked':len(bodies),'air_rows_read_back':len(rows),'period_groups_checked':len(summaries),
         'complete_period_groups_checked':complete,'native_period_groups_checked':len(native_summaries),'time_selection':selection,'nonempty_evidence':bool(bodies),'problems':problems,
         'boundary':'Tests raw source fidelity and arithmetic, not independent truth of enterprise declarations or unit inference.'}
 (p/'source_readback_qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if not problems else 2

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('folder');a=ap.parse_args();raise SystemExit(verify(a.folder))
