"""Manual source-label transcription utilities; never calls the evaluated reader/helper."""
import json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from trialtrace.safety.safety_baseline import safety_sentence_spans
from evals.safety_v4_reference import ReferenceCase,validate_case
from trialtrace.contracts import SafetyExtraction
ROOT=Path('evals/safety_v6')
CASES={c['case_id']:c for f in (ROOT/'naturalistic').glob('*/cases.json') for c in json.loads(f.read_text())}
LABELS={}
def empty(cid,note='No eligible participant safety number: full abstract read; efficacy/baseline/qualitative safety/withdrawal data excluded.',doc='trial_results'):
 LABELS[cid]=dict(case_id=cid,document=doc,notes=note,statements=[])
def add(cid,sent,kind,arm=None,aid=None,**kw):
 c=CASES[cid];a,b=safety_sentence_spans(c['abstract'])[sent-1]
 s=dict(sentence=sent,quote=c['abstract'][a:b],kind=kind,term=None,arm=arm,population=None,count=None,denominator=None,percent=None,grade=None,treatment_related=False,cause=None,all_cause=False,measure='participant_proportion',event_scope='unspecified',detail_level='total' if kind!='event' else 'unspecified',claim_family=f's{sent}-{kind}-{arm}',attribution='target',category='safety_unclear',acceptable_arm_ids=['arm:'+aid] if aid else [],acceptable_event_ids=[],window='unknown',population_relation='unknown',abstract_window_quote=None,registry_window_quote=None,aliases={})
 s.update(kw)
 if arm:s['aliases'].setdefault('arm',list(dict.fromkeys([arm+' group',arm+'-treated patients'])))
 if s['population']:s['aliases'].setdefault('population',[s['population']+' patients'])
 if s['attribution']!='target':s['category']=None
 LABELS[cid]['statements'].append(s)
 return s
def save(ids,batch):
 for cid in ids:
  c=CASES[cid];d=ROOT/'naturalistic'/c['nct_id'];validate_case(ReferenceCase.model_validate(LABELS[cid]),c,SafetyExtraction.model_validate_json((d/'safety.json').read_text()))
 for d in (ROOT/'naturalistic').iterdir():
  f=d/'references.jsonl'
  if not f.exists():continue
  lines=f.read_text(encoding='utf-8').splitlines(keepends=True);out=[];changed=False
  for line in lines:
   r=json.loads(line)
   if r['case_id'] in ids:
    assert r['statements'] is None and r['document'] is None,r['case_id'];out.append(json.dumps(LABELS[r['case_id']],ensure_ascii=False)+'\n');changed=True
   else:out.append(line)
  if changed:f.write_text(''.join(out),encoding='utf-8')
 (ROOT/f'LABEL_BATCH_{batch}.json').write_text(json.dumps(dict(designated_reviewer='Venu',label_preparation='Language-model source labeling requested by Venu; human review not asserted.',completed_at=datetime.now(timezone.utc).isoformat(),case_ids=ids,cases=len(ids),statements=sum(len(LABELS[c]['statements']) for c in ids),notes='Source-only before evaluated system output; enriched cohort. Prior v4/v5 audits/conventions seen and prior project-development context retained, so independence is limited; not independent human authorship.'),indent=2),encoding='utf-8')
