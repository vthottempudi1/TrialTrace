"""Source-only AI labeling after saved blind-20 labels. No evaluated reader calls."""
from evals.label_safety_v5 import *
remaining=[cid for cid in CASES if cid not in blind]
for cid in remaining:empty(cid,'AI assistant: full abstract read; no eligible exact participant safety count/percentage. Efficacy, baseline, occurrence/rate, bounds and unnamed AE totals excluded as applicable.')
LABELS['NCT00856544-29794874']['document']='review'
LABELS['NCT01048541-34699062']['document']='review'
LABELS['NCT00384059-23965217']['document']='review'
LABELS['NCT01048541-21358718']['notes']='One instance of mild urethral burning counts an occurrence, not an explicitly affected participant. Excluded under measure rule.'
LABELS['NCT00031486-25956891']['notes']='Background mortality is approximate/range; no significant study-related AEs is an unnamed, non-serious-total claim. No eligible statement.'
LABELS['NCT00770692-22731653']['notes']='12 serious AEs are occurrences without affected-person count. Unnamed any-AE percentages excluded. Rebound insomnia is a specifically studied efficacy/withdrawal outcome.'
LABELS['NCT00391872-23900047']['notes']='Stent thrombosis is the efficacy/prevention endpoint specifically studied; no separate safety participant number. Do not relabel efficacy events as harms.'
LABELS['NCT00391872-24561148']['notes']='MI is the efficacy outcome; numbers are event occurrences or Kaplan-Meier efficacy rates. No separate eligible death/safety figure.'
LABELS['NCT00409175-32107748']['notes']='Survival percentages are not explicit death percentages. Do not subtract them from 100 to invent mortality. No eligible numeric death statement.'
c='NCT00063635-38828720';LABELS[c]['notes']='Baseline CKD/hyperfiltration prevalence excluded. Exact 19% worsening is a longitudinal harm in a combined NASH-CRN cohort, not assignable to TONIC alone. Approximate 1/5 repetition excluded.';add(c,13,'event','renal dysfunction',percent=19,attribution='pooled',population='children previously participating in NASH-CRN studies',aliases={'term':['renal impairment']})
c='NCT00364182-24418368';LABELS[c]['notes']='Seven SAEs affected five subjects. Count people (5), not events (7). No treatment-period-specific arm; do not add registry arms.';add(c,7,'serious',count=5,detail_level='total')
c='NCT00364182-26196195';LABELS[c]['notes']='Pooled six studies. Named n values in patient AE clauses retained as patient counts. Special-interest event-category counts are explicitly event occurrences, so excluded; withdrawals excluded.'
for term,n in [('pyrexia',63),('nasopharyngitis',53),('cough',52)]:add(c,6,'event',term,arm='nonacog alfa',count=n,attribution='pooled')
for term,n in [('hypersensitivity',6),('urticaria',6),('FIX inhibition',5),('pyrexia',4)]:add(c,7,'event',term,arm='nonacog alfa',count=n,treatment_related=True,attribution='pooled')
add(c,8,'serious',arm='nonacog alfa',count=74,percent=18,attribution='pooled',detail_level='total')
add(c,12,'death',arm='nonacog alfa',count=4,attribution='pooled',detail_level='total')
c='NCT00391872-24016496';LABELS[c]['notes']='Major bleeding percentages retained separately by prior CABG. No evidence that PLATO major bleeding equals the broad registry Haemorrhage serious row. Cox analysis alone does not explicitly identify these percentages as Kaplan-Meier estimates; measure left participant proportion, with no denominator.'
for pop,vals in [('prior CABG',[8.1,8.7]),('without prior CABG',[11.8,11.4])]:
 for arm,aid,pct in zip(['ticagrelor','clopidogrel'],['EG000','EG001'],vals):add(c,9,'event','major bleeding',arm=arm,aid=aid,percent=pct,population=pop,category='safety_unclear',claim_family=f's9-bleeding-{pop}-{arm}')
c='NCT00391872-24682844';LABELS[c]['notes']='All-cause deaths retained by sex. Composite efficacy and stent thrombosis endpoints excluded. Cox analysis does not explicitly make reported percentages Kaplan-Meier estimates. Registry death totals absent.'
for pop,vals in [('women',[5.8,6.8]),('men',[4,5.7])]:
 for arm,aid,pct in zip(['ticagrelor','clopidogrel'],['EG000','EG001'],vals):add(c,4,'death',arm=arm,aid=aid,percent=pct,population=pop,all_cause=True,detail_level='total',category='safety_in_paper_not_registry',claim_family=f's4-death-{pop}-{arm}')
c='NCT00391872-24830710';LABELS[c]['notes']='Explicit Kaplan-Meier event-rate context applies to one-year death and bleeding percentages; these cannot compare to raw AE tables. Composite ischemic efficacy endpoints excluded.'
for arm,aid,pct in [('ticagrelor','EG000',8.7),('clopidogrel','EG001',11.9)]:add(c,5,'death',arm=arm,aid=aid,percent=pct,population='PAD patients',all_cause=True,measure='time_to_event',detail_level='total')
for arm,aid,pct in [('ticagrelor','EG000',14.8),('clopidogrel','EG001',17.9)]:add(c,6,'event','major bleeding',arm=arm,aid=aid,percent=pct,population='PAD patients',measure='time_to_event',aliases={'term':['PLATO-defined major bleeding']})
c='NCT00391872-24952862';LABELS[c]['notes']='Major bleeding retained by CAD extent; no verified equivalent registry bleeding definition. Do not assume that all major bleeding is a regulatory serious event.'
for pop,vals in [('extensive CAD',[25.7,25.5]),('without extensive CAD',[7.3,6.4])]:
 for arm,aid,pct in zip(['ticagrelor','clopidogrel'],['EG000','EG001'],vals):add(c,9,'event','major bleeding',arm=arm,aid=aid,percent=pct,population=pop,category='safety_unclear',claim_family=f's9-bleeding-{pop}-{arm}')
c='NCT00409175-31353964';LABELS[c]['notes']='Integrated trials pooled; THAOS is a separate observational cohort (other). Unnamed TEAE totals excluded; do not borrow denominator from another event clause.'
add(c,5,'serious',arm='tafamidis',count=46,percent=33.6,attribution='pooled',detail_level='total')
for term,pct in [('diarrhoea',26.3),('urinary tract infection',25.5),('influenza',21.2)]:add(c,6,'event',term,arm='tafamidis',percent=pct,attribution='pooled')
add(c,7,'serious',arm='tafamidis',count=96,percent=14.5,attribution='other',population='THAOS',detail_level='total')
add(c,8,'event','urinary tract infection',arm='tafamidis',percent=6.1,attribution='other',population='THAOS',aliases={'term':['UTI']})
c='NCT00410124-20194812';LABELS[c]['notes']='All clinical pneumonitis, grade subsets and explicit grade-4 zero retained. Grade counts refer to patient cases from preceding clinical-pneumonitis context; denominator not borrowed across sentences. Baseline radiological disease and resolution/treatment-management counts excluded. New radiographic pneumonitis findings retained as distinct population/definition. Shared family marks overlapping clinical total/components.'
add(c,5,'event','clinical pneumonitis',arm='everolimus',aid='EG000',count=37,denominator=274,percent=13.5,category='safety_unclear',detail_level='total',claim_family='clinical-pneumonitis',aliases={'term':['pneumonitis']})
add(c,5,'event','clinical pneumonitis',arm='placebo',aid='EG002',count=0,category='safety_unclear',detail_level='total',claim_family='placebo-pneumonitis',aliases={'term':['pneumonitis']})
for n,pct,g in [(9,3.3,'1'),(18,6.6,'2'),(10,3.6,'3')]:add(c,6,'event','pneumonitis',arm='everolimus',aid='EG000',count=n,percent=pct,grade=g,detail_level='component',claim_family='clinical-pneumonitis')
add(c,7,'event','pneumonitis',count=0,grade='4',category='safety_not_comparable',detail_level='component',claim_family='clinical-pneumonitis')
for arm,aid,n,pct in [('everolimus','EG000',245,38.9),('placebo','EG002',132,15.2)]:add(c,11,'event','new radiographic findings',arm=arm,aid=aid,denominator=n,percent=pct,population='available serial radiographic studies',category='safety_unclear',aliases={'term':['radiographic pneumonitis','pneumonitis']})
c='NCT00567996-21227674';LABELS[c]['notes']='Pooled three studies. Active-treatment QTc range excluded; exact placebo percentage with threshold retained. Exposure-adjusted death ratio is not a death count/proportion.';add(c,8,'event','QTc interval increases >60 ms',arm='placebo',percent=0.3,attribution='pooled',aliases={'term':['QTc interval increased']})
c='NCT00618748-24417745';LABELS[c]['notes']='Integrated parent and extension exposure cannot isolate target extension. Weight increased is a named harm; mean lab/weight changes excluded. Explicit zero mania is treatment-emergent safety, not the primary depression-efficacy outcome, despite concluding benefit wording.'
add(c,10,'event','weight increased',arm='olanzapine',percent=47.9,attribution='pooled')
add(c,13,'event','mania',arm='olanzapine',count=0,attribution='pooled')
c='NCT00856544-32816215';LABELS[c]['notes']='CK/myopathy zeros explicitly concern pooled ulcerative-colitis studies, not target RA trial. Patient-year rates and means excluded.'
for term,scope in [('CK elevation','serious'),('myopathy','unspecified')]:add(c,10,'event',term,arm='tofacitinib',count=0,attribution='pooled',population='UC studies',event_scope=scope)
c='NCT00907426-26060513';LABELS[c]['notes']='Postchemotherapy subset retained; abstract does not assign the pooled safety percentages to a specific treatment sequence. Do not select registry group by matching a number.'
for term,pct in [('Conjunctival hyperemia',16.7),('punctate keratitis',9.4)]:add(c,11,'event',term,percent=pct,population='postchemotherapy subjects',category='safety_unclear')
c='NCT00936897-31776637';LABELS[c]['notes']='All atypical fractures explicitly originate in denosumab-versus-zoledronic-acid study, not target ibandronate study. Retain total and arm components in shared family.'
add(c,9,'event','atypical femoral fractures',count=3,attribution='other',detail_level='total',claim_family='s9-fractures')
for arm,n in [('bisphosphonate',1),('denosumab',2)]:add(c,9,'event','atypical femoral fractures',arm=arm,count=n,attribution='other',detail_level='component',claim_family='s9-fractures')
if __name__=='__main__':
 save(remaining)
 print('Remaining 57 saved and source/schema validated.')
