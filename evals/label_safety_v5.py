"""AI-authored source labels; no reader/helper outputs or API calls. Not human-independent gold."""
import json, hashlib
from pathlib import Path
from datetime import datetime, timezone
from trialtrace.safety.safety_baseline import safety_sentence_spans
from evals.safety_v4_reference import ReferenceCase, validate_case
from trialtrace.contracts import SafetyExtraction
ROOT=Path('evals/safety_v5')
CASES={c['case_id']:c for p in (ROOT/'naturalistic').glob('*/cases.json') for c in json.loads(p.read_text())}
LABELS={}
def empty(cid,note,doc='trial_results'):
 LABELS[cid]=dict(case_id=cid,document=doc,notes=note,statements=[])
def add(cid,sentence,kind,term=None,arm=None,count=None,percent=None,denominator=None,aid=None,eid=None,category=None,**kw):
 c=CASES[cid]; spans=safety_sentence_spans(c['abstract']); start,end=spans[sentence-1]
 st=dict(sentence=sentence,quote=c['abstract'][start:end],kind=kind,term=term,arm=arm,population=None,count=count,denominator=denominator,percent=percent,grade=None,treatment_related=False,cause=None,all_cause=False,measure='participant_proportion',event_scope='unspecified',detail_level='unspecified',claim_family=f's{sentence}-{kind}-{arm or "combined"}',attribution='target',category=category or ('safety_not_comparable' if aid else 'safety_unclear'),acceptable_arm_ids=[f'arm:{aid}'] if aid else [],acceptable_event_ids=[eid] if eid else [],window='unknown',population_relation='unknown',abstract_window_quote=None,registry_window_quote=None,aliases={})
 st.update(kw)
 if st['attribution']!='target':st['category']=None
 LABELS[cid]['statements'].append(st)
def save(ids):
 for cid in ids:
  c=CASES[cid]; d=ROOT/'naturalistic'/c['nct_id']; safety=SafetyExtraction.model_validate_json((d/'safety.json').read_text());validate_case(ReferenceCase.model_validate(LABELS[cid]),c,safety)
 for d in (ROOT/'naturalistic').iterdir():
  p=d/'references.jsonl'
  if not p.exists():continue
  rows=[json.loads(x) for x in p.read_text().splitlines() if x.strip()]
  changed=False
  for i,r in enumerate(rows):
   if r['case_id'] in ids: rows[i]=LABELS[r['case_id']];changed=True
  if changed:p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
blind=json.loads((ROOT/'blind_subset.json').read_text())['case_ids']
for cid in blind:empty(cid,'AI assistant: read full abstract and saved registry; no eligible exact participant safety statement.')
LABELS['NCT00063635-19761871']['document']='protocol'
LABELS['NCT01634256-23497020']['notes']='Severe is not serious; unnamed severe AE zero is outside named-event/serious-total scope. Mixed withdrawal reasons and unspecified lab abnormalities are excluded.'
c='NCT00856544-24026258';LABELS[c]['notes']='SAE rates per patient-year and cardiovascular event occurrences excluded. Disease cases counted as people; combined tofacitinib doses cannot map to one registry period/arm.'
for term,n in [('tuberculosis',2),('other opportunistic infections',2)]:add(c,12,'event',term,arm='tofacitinib groups',count=n)
add(c,12,'death',arm='tofacitinib groups',count=4,detail_level='total')
c='NCT00107120-19465881';LABELS[c]['notes']='Bounded symptom percentages and AE discontinuations excluded. Exact influenza-like symptom percentages retained; broad scope cannot select only non-serious row.'
for arm,pct,aid in [('escitalopram',7.1,'EG000'),('placebo',3.2,'EG001')]:add(c,7,'event','influenza-like symptoms',arm=arm,percent=pct,aid=aid,category='safety_unclear')
for arm,pct,aid in [('escitalopram',2.6,'EG000'),('placebo',1.3,'EG001')]:add(c,9,'serious',arm=arm,percent=pct,aid=aid,detail_level='total')
c='NCT00604214-22616830';LABELS[c]['notes']='Death endpoint included despite efficacy purpose. Registry death totals absent for identified arms; subgroup deaths retained. Broad serious bleeding cannot map to a single anatomical bleeding row.'
for sent,vals in [(4,[(223,846,26.4),(202,834,24.2)]),(5,[(287,842,34.1),(269,822,32.7)]),(6,[(98,342,28.7),(102,331,30.8)])]:
 for (arm,aid),(n,d,pct) in zip([('DrotAA','EG000'),('placebo','EG001')],vals):add(c,sent,'death',arm=arm,aid=aid,count=n,denominator=d,percent=pct,all_cause=True,detail_level='total',population='severe protein C deficiency at baseline' if sent==6 else None,category='safety_in_paper_not_registry')
for arm,n,aid in [('DrotAA',10,'EG000'),('placebo',8,'EG001')]:add(c,8,'event','bleeding',arm=arm,count=n,aid=aid,event_scope='serious',category='safety_unclear',aliases={'term':['serious bleeding']})
c='NCT00410124-18653228';LABELS[c]['notes']='No denominators borrowed from randomization. Everolimus registry arm includes open-label exposure. Unrestricted named events cannot select one seriousness stratum. Grade-3 subset and any-grade total share family.'
for term,vals in [('Stomatitis',[(107,40),(11,8)]),('rash',[(66,25),(6,4)]),('fatigue',[(53,20),(22,16)])]:
 for (arm,aid),(n,pct) in zip([('everolimus','EG000'),('placebo','EG002')],vals):add(c,11,'event',term,arm=arm,aid=aid,count=n,percent=pct,category='safety_unclear')
add(c,12,'event','Pneumonitis',arm='everolimus',aid='EG000',count=22,percent=8,event_scope='all',grade='any grade',detail_level='total',category='safety_unclear',claim_family='s12-pneumonitis')
add(c,12,'event','pneumonitis',arm='everolimus',aid='EG000',count=8,grade='3',detail_level='component',claim_family='s12-pneumonitis')
c='NCT00540423-22409309';LABELS[c]['notes']='Day-9 TIA is in early active treatment, not the long-term exposure arm; no denominator borrowed from enrollment.';add(c,9,'event','transient ischemic attack',arm='eltrombopag',count=1,aid='EG001',eid='event:serious:1',category='safety_unclear')
c='NCT00048932-29329602';LABELS[c]['notes']='Three trials have separately reported SAE percentages, but abstract supplies no trial-name-to-NCT mapping and saved registry has no ASSURE acronym. Keep identity unresolved rather than infer from numbers. Unnamed treatment-related AE percentages excluded.'
for trial,vals in [('ATTAIN',[2.4,2.3]),('ASSURE',[4.2,1.7]),('ARRIVE',[0.6,2.9])]:
 for arm,pct in zip(['abatacept plus one non-MTX csDMARD','abatacept plus MTX'],vals):add(c,11,'serious',arm=arm,percent=pct,population=trial,attribution='unresolved',detail_level='total',claim_family=f's11-{trial}-{arm}')
c='NCT00249795-21388310';LABELS[c]['notes']='Efficacy composite/person-year rates excluded. Symptomatic hypotension and renal dysfunction retained; unrestricted scope cannot select a serious-only or non-serious-only row.'
for term,vals in [('symptomatic hypotension',[127,64]),('renal dysfunction',[43,24])]:
 for arm,aid,n in zip(['irbesartan','placebo'],['EG000','EG001'],vals):add(c,12,'event',term,arm=arm,aid=aid,count=n,category='safety_unclear')
c='NCT00936897-23812464';LABELS[c]['notes']='Unnamed any-AE totals excluded. SAE percentages lack same-sentence denominator.'
for arm,pct,aid in [('denosumab',9.5,'EG001'),('ibandronate',5.4,'EG000')]:add(c,7,'serious',arm=arm,percent=pct,aid=aid,detail_level='total')
if __name__=='__main__':
 save(blind)
 record=dict(author='AI assistant',authored_at=datetime.now(timezone.utc).isoformat(),case_ids=blind,method='Full abstract and saved registry sources; blind 20 before remaining labels; no reader/helper outputs or calls. Not independent human labeling.',reference_hashes={n:hashlib.sha256(json.dumps(LABELS[n],sort_keys=True).encode()).hexdigest() for n in blind})
 (ROOT/'AI_BLIND_LABEL_RECORD.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
 print('Blind 20 saved and source/schema validated.')
