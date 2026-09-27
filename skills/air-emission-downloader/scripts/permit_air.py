"""MEE public air-report acquisition. No accounts, inferred IDs or access bypass.

Self-contained CLI shared byte-for-byte by the region and company skills.
Official public response fields are untrusted data, never executable commands.
"""
from __future__ import annotations
import argparse,base64,csv,hashlib,json,re,sys,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin,urlparse,parse_qs
import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from air_extract import extract_air,summarize_jja

BASE='https://permit.mee.gov.cn'
SEARCH=BASE+'/perxxgkinfo/syssb/xkgg/xkgg!licenseInformation.action'
REGIONS=BASE+'/perxxgkinfo/syssb/xkgg/xkgg!getRegions.action'
INDEX=BASE+'/perxxgkinfo/syssb/wysb/hpsp/hpsp-company-sewage!getZxbgByYear.action'
VIEW=BASE+'/permitrep/report/'
BODY=VIEW+'repShare/pdf/getReportInfo'
VERSION='1.0.0'

def now(): return datetime.now(timezone.utc).isoformat()
def digest(b): return hashlib.sha256(b).hexdigest()
def canonical(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def save(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 q=p.with_suffix(p.suffix+'.tmp');q.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8');q.replace(p)
def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def text(s): return BeautifulSoup(str(s or ''),'html.parser').get_text(' ',strip=True)
def csv_save(p,rows):
 if not rows: Path(p).write_text('',encoding='utf-8-sig');return
 cols=list(dict.fromkeys(k for r in rows for k in r))
 with Path(p).open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
  for r in rows:w.writerow({k:canonical(v) if isinstance(v,(dict,list)) else v for k,v in r.items()})

class StopRun(RuntimeError):pass
class UnsupportedPublicFormat(ValueError):pass
class Net:
 def __init__(self,out,budget,delay):
  self.out=Path(out);self.out.mkdir(parents=True,exist_ok=True)
  self.cache=self.out/'http';self.cache.mkdir(exist_ok=True)
  self.s=requests.Session();self.s.headers['User-Agent']='PublicAirReportResearch/1.0 (single-thread; reproducible public records)'
  self.budget=budget;self.delay=max(1.,delay);self.calls=0;self.last=0
 def request(self,method,url,data=None,params=None,headers=None,cache=True):
  if urlparse(url).hostname!='permit.mee.gov.cn' or urlparse(url).scheme!='https':raise StopRun('Non-allowlisted URL')
  filtered={k:v for k,v in (data or {}).items() if k!='tempReportKey'}
  key=digest(canonical([method,url,filtered,params]).encode())
  fp=self.cache/(key+'.bin');mp=self.cache/(key+'.json')
  if cache and fp.exists() and mp.exists():
   m=load(mp);b=fp.read_bytes()
   if m.get('sha256')!=digest(b):raise StopRun('Cache hash mismatch: '+str(fp))
   return b,m
  if self.calls>=self.budget:raise StopRun('Request budget reached; resume only within authorized budget')
  time.sleep(max(0,self.delay-(time.monotonic()-self.last)))
  self.calls+=1;self.last=time.monotonic()
  m={'method':method,'url':url,'form':filtered,'params':params,'access_utc':now(),'call':self.calls}
  try:
   r=self.s.request(method,url,data=data,params=params,headers=headers,timeout=(15,60),allow_redirects=False)
   m.update(status=r.status_code,bytes=len(r.content),sha256=digest(r.content))
   if r.status_code in (401,403,429) or r.is_redirect:raise StopRun('Access/rate restriction or redirect; stop without bypass')
   r.raise_for_status();b=r.content
   if cache:fp.write_bytes(b);save(mp,m)
   return b,m
  except requests.RequestException as e:
   m['error']=type(e).__name__;raise StopRun('Network failure; inspect before bounded resume: '+str(e)) from e
  finally:
   with (self.out/'network.jsonl').open('a',encoding='utf-8') as f:f.write(canonical(m)+'\n')
 def html(self,*a,**k):return self.request(*a,**k)[0].decode('utf-8')
 def json(self,*a,**k):
  try:return json.loads(self.request(*a,**k)[0])
  except (ValueError,UnicodeDecodeError) as e:raise StopRun('Expected JSON; possible service error or verification page') from e

def resolve_region(net,name,parent):
 r=net.json('POST',REGIONS,data={'parentCode':parent})
 vals=r.get('regions')
 if not isinstance(vals,list):raise StopRun('Region response schema changed')
 matches=[v for v in vals if v.get('regionname')==name]
 if len(matches)!=1:raise StopRun('Use exact official region name; choices: '+','.join(v.get('regionname','') for v in vals))
 return matches[0]['regioncode']

def parse_search(html):
 sp=BeautifulSoup(html,'html.parser');form=sp.select_one('#mainForm')
 if not form:raise StopRun('Official search form absent')
 fields={x['name']:x.get('value','') for x in form.select('input[name]')}
 pm=re.search(r'\btotalPages\s*=\s*(\d+)',html)
 if not pm:raise StopRun('Cannot verify pagination')
 table=sp.select_one('table.tabtd')
 if not table:raise StopRun('Official result table absent; not evidence of zero enterprises')
 rows=[]
 for tr in table.select('tr')[1:]:
  td=tr.select('td');a=tr.select_one('a[href*="getxxgkContent"]')
  if not a:
   if td:raise StopRun('Unrecognized result row; verify zero-results/changed schema manually')
   continue
  if len(td)<9:raise StopRun('License table schema changed')
  vals=[v.get_text(' ',strip=True) for v in td]
  u=urljoin(BASE,a['href']);ids=parse_qs(urlparse(u).query).get('dataid',[])
  if len(ids)!=1 or not re.fullmatch('[0-9a-f]{32}',ids[0]):raise StopRun('Unexpected observed enterprise ID')
  rows.append(dict(zip(['province','city','permit_code','name','industry','validity','issued','management'],vals[:8]),enterprise_id=ids[0],detail_url=u))
 if not rows:raise StopRun('No recognized license rows; manually confirm official zero-results before declaring absence')
 return fields,int(pm.group(1)),rows

def detail(net,row):
 h=net.html('GET',row['detail_url']);t=text(h)
 if row['permit_code'] not in t:raise StopRun('Observed detail does not contain expected permit identity')
 region=re.search(r'所在地区[：:]\s*(.*?)\s*发证机关',t)
 address=re.search(r'生产经营场所地址[：:]\s*(.*?)\s*行业类别',t)
 if not region or not address:raise StopRun('Facility region/address missing in detail; no name-based substitution')
 row=dict(row,official_region=region.group(1).strip(),facility_address=address.group(1).strip(),detail_sha256=digest(h.encode()))
 row['district']=re.split(r'[-－—]',row['official_region'])[-1].strip()
 return row

def discover(net,args):
 initial=net.html('GET',SEARCH);form,_,_=parse_search(initial)
 filters={'province':'','city':'','management':'','registerentername':args.name or '', 'xkznum':args.permit or ''}
 if args.province:
  filters['province']=resolve_region(net,args.province,'000000000000')
 if args.city:
  if not args.province:raise StopRun('City requires province')
  filters['city']=resolve_region(net,args.city,filters['province'])
 h=net.html('POST',SEARCH,data={**form,**filters,'page.pageNo':'1'})
 form,pages,rows=parse_search(h)
 if pages<1 or not 1<=len(rows)<=10 or (pages>1 and len(rows)!=10):raise StopRun('First result page incomplete or page size changed')
 if pages>3000:raise StopRun('Official 3000-page access limit; narrow approved server filters, never bypass')
 target=pages if args.max_pages==0 else min(pages,args.max_pages)
 ledger={'query':filters,'server_pages':pages,'pages_fetched':1,'page_rows':[len(rows)],'duplicate_rows':0,'snapshot_utc':now(),
         'scope':'current public license catalog, not all historical canceled/revoked permits',
         'county_filter':'official district in facility detail; not enterprise name','exact_server_total':None}
 for page in range(2,target+1):
  h=net.html('POST',SEARCH,data={**form,**filters,'page.pageNo':str(page)})
  form,np,rr=parse_search(h)
  if np!=pages:raise StopRun('Page count changed during acquisition; reconcile snapshot before completion')
  if not 1<=len(rr)<=10 or (page<pages and len(rr)!=10):raise StopRun('Page size changed or incomplete')
  rows+=rr;ledger['page_rows'].append(len(rr));ledger['pages_fetched']=page
  save(net.out/'discovery_progress.json',ledger)
 unique={}
 for row in rows:
  key=(row['permit_code'],row['enterprise_id'])
  if key in unique:ledger['duplicate_rows']+=1
  unique[key]=row
 rows=list(unique.values());save(net.out/'catalog_rows.json',rows)
 for r in rows:
  if (args.province and r['province']!=args.province) or (args.city and r['city']!=args.city):raise StopRun('Directory province/city differs from requested server filter')
 if args.mode=='company':
  if target!=pages:
   save(net.out/'company_candidates.json',rows);raise StopRun('Company search pagination incomplete; identity cannot be declared unique')
  # Exact name or explicit permit disambiguation only, no first-fuzzy-match selection.
  if args.permit:pool=[r for r in rows if r['permit_code']==args.permit]
  else:pool=[r for r in rows if r['name']==args.name]
  if len(pool)!=1:
   save(net.out/'company_candidates.json',rows);raise StopRun('Company identity ambiguous or absent; select official exact name / permit from company_candidates.json')
 else:pool=rows
 inspected=[];selected=[]
 for row in pool[:args.max_details or None]:
  r=detail(net,row);inspected.append(r)
  hierarchy=re.split(r'[-－—]',r['official_region'])
  if len(hierarchy)<3 or (args.province and hierarchy[0].strip()!=args.province) or (args.city and hierarchy[1].strip()!=args.city):raise StopRun('Facility administrative hierarchy differs from requested province/city')
  if args.mode=='company' or r['district']==args.county:selected.append(r)
 save(net.out/'inspected_enterprises.json',inspected)
 ledger.update(catalog_unique=len(rows),details_inspected=len(inspected),county_matches=len(selected),
               catalog_pages_complete=target==pages,details_complete=len(inspected)==len(pool))
 ledger['discovery_complete']=ledger['catalog_pages_complete'] and ledger['details_complete'] and not ledger['duplicate_rows']
 if args.mode=='company':ledger['discovery_complete']=len(selected)==1 and target==pages and not ledger['duplicate_rows']
 if args.limit_companies:selected=selected[:args.limit_companies]
 ledger['selected_for_reports']=len(selected)
 ledger['report_sample_limited']=len(selected)<ledger['county_matches']
 save(net.out/'discovery.json',ledger);save(net.out/'enterprises.json',selected);csv_save(net.out/'enterprises.csv',selected)
 return selected,ledger

def is_jja(r,year):
 t=str(r.get('reportTime',''))
 if not t.startswith(str(year)):return False
 if '月' in t:return bool(re.search(r'年0?[678]月',t))
 return bool(re.search(r'第0?[23]季',t))

def viewer_script(net):
 h=net.html('GET',VIEW);sp=BeautifulSoup(h,'html.parser')
 sources=[urljoin(VIEW,s['src']) for s in sp.select('script[src]') if '/assets/index-' in s['src']]
 if len(sources)!=1:raise StopRun('Public viewer entry changed')
 app=net.html('GET',sources[0]);match=re.search(r'path:"/pubView",.{0,100}?import\("([^\"]+)"\)',app)
 if not match:raise StopRun('Public viewer route changed')
 u=urljoin(sources[0],match.group(1));script=net.html('GET',u)
 match=re.search(r'U\.hashStr\(p\+f\+y\+A\+"([^"]+)"\)',script)
 if not match:raise StopRun('Public display checksum schema changed; human review required')
 save(net.out/'viewer_version.json',{'entry':sources[0],'public_component':u,'component_sha256':digest(script.encode()),'observed_utc':now(),
                                  'route':'official anonymous /pubView','purpose':'replay public display formatting; not an authentication credential'})
 return match.group(1)

def air_projection(body):
 # Keep air sections without retaining water/waste/noise operational records.
 projected={'companyInfo':body.get('companyInfo',{})}
 def recur(obj):
  out={}
  if isinstance(obj,dict):
   for k,v in obj.items():
    if k.lower().startswith('air'):out[k]=v
    elif isinstance(v,dict):
     sub=recur(v)
     if sub:out[k]=sub
  return out
 projected.update(recur({k:v for k,v in body.items() if k!='companyInfo'}))
 return projected

def get_body(net,r,ent,secret):
 u=r['docUrl'];parsed=urlparse(u)
 if parsed.hostname!='permit.mee.gov.cn' or '/pubView?' not in u:raise UnsupportedPublicFormat('unsupported_public_format; preserve index URL; do not invent PDF conversion')
 params={k:v[0] for k,v in parse_qs(parsed.fragment.split('?',1)[1]).items()}
 for field in ['reportId','provinceSharding','yearSharding']:
  if not re.fullmatch('[0-9a-f]{32,64}',params.get(field,'')):raise StopRun('invalid observed public report identifier')
 rid=params['reportId'];cachekey=digest(canonical([ent['permit_code'],rid,params['provinceSharding'],params['yearSharding']]).encode())
 d=net.out/'reports'/cachekey;bp=d/'air_report.json';mp=d/'provenance.json'
 if bp.exists() and mp.exists():
  m=load(mp)
  if m['official_public_url']!=u or m['permit_code']!=ent['permit_code'] or m['air_sha256']!=digest(bp.read_bytes()):raise StopRun('Report cache identity/hash mismatch')
  return load(bp),m
 ts=str(int(time.time()*1000));parts=params['reportId']+params['provinceSharding']+params['yearSharding']
 md5=lambda s:hashlib.md5(s.encode()).hexdigest()
 sign=md5(parts+ts+secret)
 b,wire=net.request('GET',BODY,params={k:params[k] for k in ['reportId','yearSharding','provinceSharding']},headers={'sign':sign,'timestamp':ts,'Referer':VIEW},cache=False)
 try:payload=json.loads(b)
 except (ValueError,UnicodeDecodeError) as e:raise StopRun('Report is not JSON; possible validation/error page') from e
 if not isinstance(payload.get('data'),str):raise StopRun('Unexpected public report envelope; no retry/bypass')
 try:body=json.loads(unpad(AES.new(md5(parts+sign+ts)[8:24].encode(),AES.MODE_ECB).decrypt(base64.b64decode(payload['data'])),16))
 except (ValueError,UnicodeDecodeError) as e:raise StopRun('Public report decoding schema changed') from e
 ci=body.get('companyInfo',{})
 if ci.get('permitCode')!=ent['permit_code']:raise StopRun('Report permit identity mismatch')
 air=air_projection(body);save(bp,air)
 meta={'enterprise_id':ent['enterprise_id'],'permit_code':ent['permit_code'],'report_id':rid,'report_period':r.get('reportTime'),
       'report_type':r.get('type'),'official_public_url':u,'source_url':u,'access_utc':wire['access_utc'],
       'wire_sha256':wire['sha256'],'air_sha256':digest(bp.read_bytes()),'path':str(bp),
       'format':'air-only JSON projection of official anonymous viewer; NOT an original PDF',
       'public_template_unit':'air emission amount table in tons except explicitly non-mass/row-unit indicators',
       'companyInfo':ci}
 save(mp,meta);return air,meta

def collect(net,args,ents,discovery):
 manifest=[];indices=[];allrows=[];secret=None
 # Initialize this run even when zero enterprises or zero relevant reports.
 save(net.out/'report_manifest.json',manifest);save(net.out/'index_coverage.json',indices)
 for ent in ents:
  for year in args.years:
   rs=net.json('POST',INDEX,data={'reportYear':str(year),'dataid':ent['enterprise_id']})
   if not isinstance(rs,list):raise StopRun('Report index schema changed')
   save(net.out/'indices'/f"{ent['enterprise_id']}_{year}.json",rs)
   relevant=[r for r in rs if is_jja(r,year)]
   indices.append({'enterprise_id':ent['enterprise_id'],'permit_code':ent['permit_code'],'year':year,'index_reports':len(rs),'jja_reports':len(relevant),'status':'ok'})
   for r in relevant:
    entry={'enterprise_id':ent['enterprise_id'],'permit_code':ent['permit_code'],'period':r.get('reportTime'),'source_url':r.get('docUrl')}
    try:
     if secret is None:secret=viewer_script(net)
     body,meta=get_body(net,r,ent,secret)
     extracted=extract_air(body,meta,ent);allrows.extend(extracted)
     entry.update(meta,status='downloaded',extracted_rows=len(extracted))
    except UnsupportedPublicFormat as e:entry.update(status='unsupported_or_invalid',error=str(e))
    manifest.append(entry);save(net.out/'report_manifest.json',manifest)
   save(net.out/'index_coverage.json',indices)
 summary=summarize_jja(allrows)
 save(net.out/'air_monthly_rows.json',allrows);csv_save(net.out/'air_monthly_rows.csv',allrows)
 save(net.out/'jja_summary.json',summary)
 if isinstance(summary,list):csv_save(net.out/'jja_summary.csv',summary)
 coverage={'mode':args.mode,'years':args.years,'months':[6,7,8],'enterprises':len(ents),'indexes_completed':len(indices),
   'reports_requested':len(manifest),'reports_downloaded':sum(m['status']=='downloaded' for m in manifest),
   'reports_unsupported':sum(m['status']!='downloaded' for m in manifest),'air_monthly_rows':len(allrows),
   'jja_groups':len(summary),'jja_complete_groups':sum(g.get('complete',False) for g in summary),
   'jja_conflict_groups':sum(g.get('status')=='conflict' for g in summary),
   'monthly_vs_quarterly_disagreement_groups':sum(any(m.get('lower_priority_disagreement',False) for m in g.get('months',[])) for g in summary),
   'jja_incomplete_groups':sum(not g.get('complete',False) for g in summary),
   'monthly_missing_or_invalid_rows':sum(r.get('value') is None for r in allrows),
   'unknown_or_conflicting_unit_rows':sum(r.get('unit_status') in ['unknown','conflict'] for r in allrows),
   'network_calls_this_run':net.calls,
   'report_acquisition_complete_for_selected':bool(ents) and len(indices)==len(ents)*len(args.years) and all(m['status']=='downloaded' for m in manifest),
   'selected_identity_discovery_complete':discovery.get('discovery_complete',False),
   'geographic_completeness':(discovery.get('discovery_complete',False) and not discovery.get('report_sample_limited',False)) if args.mode=='region' else None,
   'historical_all_permit_enterprises_complete':False,
   'caveats':['Current public catalog is not an archive of all 2023/2024 canceled or revoked permits.',
    'No available monthly/quarterly source means missing, not zero. Annual and quarter totals are never prorated.',
    'Whole-enterprise, main outlet, minor-group and fugitive-group amounts remain separate; not added together.',
    'No reported zero is silently corrected. Unknown units, conflicts and duplicate rows require review.']}
 save(net.out/'coverage.json',coverage);print(json.dumps(coverage,ensure_ascii=False,indent=2),flush=True)
 return coverage

def compare_dirs(a,b,out):
 # Numeric/source identity equivalence, not date/cache-path equivalence.
 def records(p):
  rr=load(Path(p)/'air_monthly_rows.json')
  ignored={'access_utc','path','retrieved_at','download_path'}
  return sorted(canonical({k:v for k,v in r.items() if k not in ignored}) for r in rr)
 aa,bb=records(a),records(b)
 def reportset(p):return sorted((r['permit_code'],r.get('report_id',''),r.get('air_sha256','')) for r in load(Path(p)/'report_manifest.json') if r['status']=='downloaded')
 result={'compared_utc':now(),'left':str(a),'right':str(b),'left_rows':len(aa),'right_rows':len(bb),'monthly_records_equal':aa==bb,
         'air_report_hashes_equal':reportset(a)==reportset(b),'left_only':list(set(aa)-set(bb))[:10],'right_only':list(set(bb)-set(aa))[:10]}
 result['nonempty_evidence']=bool(aa and bb and reportset(a) and reportset(b))
 result['left_status']=load(Path(a)/'run_status.json')
 result['right_status']=load(Path(b)/'run_status.json')
 result['both_runs_finished']=all(result[k].get('state')=='completed_with_declared_scope' for k in ['left_status','right_status'])
 save(out,result);print(json.dumps(result,ensure_ascii=False,indent=2))
 return 0 if result['nonempty_evidence'] and result['both_runs_finished'] and result['monthly_records_equal'] and result['air_report_hashes_equal'] else 2

def main():
 ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
 run=sub.add_parser('run');run.add_argument('--mode',choices=['region','company'],required=True)
 run.add_argument('--province');run.add_argument('--city');run.add_argument('--county');run.add_argument('--name');run.add_argument('--permit')
 run.add_argument('--years',type=int,nargs='+',default=[2023,2024]);run.add_argument('--out',required=True)
 run.add_argument('--max-pages',type=int,default=1,help='0 means all, subject to request budget')
 run.add_argument('--max-details',type=int,default=10,help='0 means all')
 run.add_argument('--limit-companies',type=int,default=0)
 run.add_argument('--max-requests',type=int,default=50);run.add_argument('--delay',type=float,default=1.25)
 run.add_argument('--bulk-approved',action='store_true',help='Use only after explicit human approval >100 requests')
 run.add_argument('--discover-only',action='store_true')
 c=sub.add_parser('compare');c.add_argument('--left',required=True);c.add_argument('--right',required=True);c.add_argument('--out',required=True)
 a=ap.parse_args()
 if a.command=='compare':return compare_dirs(a.left,a.right,a.out)
 if a.max_requests>100 and not a.bulk_approved:ap.error('Request budget above100 requires documented human approval and --bulk-approved')
 if a.mode=='region' and (not a.province or not a.city or not a.county or a.name or a.permit):ap.error('Region requires province/city/county, and prohibits name/permit narrowing')
 if a.mode=='company' and not (a.name or a.permit):ap.error('Company requires exact name or permit')
 if any(v<0 for v in [a.max_pages,a.max_details,a.limit_companies,a.max_requests]):ap.error('Nonnegative limits required')
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 contract={'version':VERSION,'created_utc':now(),'arguments':vars(a),'months':[6,7,8],
           'source':'MEE public permit platform','intended_scope':'air only; public current license catalog; explicit gaps for historic canceled entities',
           'server_filters':['province','city','registerentername','xkznum'],'local_filters':['facility district','report year','JJA months'],
           'stop_on':['budget','HTTP401/403/429','redirect','schema change','identity mismatch']}
 cp=out/'contract.json'
 if cp.exists():
  prior=load(cp)['arguments']
  for k in ['mode','province','city','county','name','permit','years']:
   if prior.get(k)!=getattr(a,k):ap.error('Output directory belongs to a different query: '+k)
 save(cp,contract);net=Net(out,a.max_requests,a.delay)
 try:
  ents,ledger=discover(net,a)
  if not a.discover_only:collect(net,a,ents,ledger)
  save(out/'run_status.json',{'state':'completed_with_declared_scope','utc':now(),'network_calls':net.calls,'discovery_complete':ledger['discovery_complete']})
  return 0
 except (StopRun,Exception) as e:
  save(out/'run_status.json',{'state':'stopped_incomplete','utc':now(),'network_calls':net.calls,'error':str(e),'exception':type(e).__name__})
  print('STOPPED:',str(e),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
