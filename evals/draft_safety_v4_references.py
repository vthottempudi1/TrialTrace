"""AI-authored reference transcription, not a reader run or independent review.

All clinical/attribution choices below are explicit authoring decisions. Helpers
copy exact source sentences and validate structure; they do not infer gold from
TrialTrace predictions. Never overwrite human edits or a frozen packet.
"""
import hashlib
import json
from pathlib import Path

from evals.run_safety_v4 import items
from evals.safety_v4_reference import ReferenceCase, ReferenceStatement, validate_case
from trialtrace.safety.safety_baseline import safety_sentence_spans

ROOT = Path("evals/safety_v4")
SOURCES = {}
for item in items(ROOT, require_labels=False):
    SOURCES[item["case"]["case_id"]] = item
LABELS = {}
CURRENT = None
BLIND = set(json.loads((ROOT/"blind_subset.json").read_text())["case_ids"])


def case(cid, document="trial_results", notes=""):
    global CURRENT
    if cid in LABELS:
        raise ValueError("Case authored twice: " + cid)
    CURRENT = cid
    provenance = "Based on Venu's independent notes; AI transcription/check pending review. " if cid in BLIND else "AI-authored draft, not independent human gold. "
    LABELS[cid] = {"case_id": cid, "document": document, "statements": [], "notes": provenance + notes}


def row(sentence, kind, term=None, arm=None, count=None, denominator=None, percent=None,
        category="safety_not_comparable", aid=None, eids=(), attribution="target", **extra):
    source = SOURCES[CURRENT]["case"]
    start, end = safety_sentence_spans(source["abstract"])[sentence-1]
    record = dict(sentence=sentence, quote=source["abstract"][start:end], kind=kind, term=term,
        arm=arm, population=None, count=count, denominator=denominator, percent=percent, grade=None,
        treatment_related=False, cause=None, all_cause=False, measure="participant_proportion",
        event_scope="unspecified", detail_level="total" if kind in ("death", "serious") else "unspecified",
        claim_family=f"s{sentence}-{kind}-{term or ''}-{arm or 'all'}", attribution=attribution,
        category=category if attribution == "target" else None, acceptable_arm_ids=["arm:"+aid] if aid else [],
        acceptable_event_ids=list(eids), window="unknown", population_relation="unknown",
        abstract_window_quote=None, registry_window_quote=None, aliases={})
    record.update(extra)
    # Predeclared surface variants preserve meaning; never remove a subgroup or
    # replace a source number with a registry value.
    if arm:
        variants = [arm[:-6]] if arm.endswith(" group") else [arm+" group"]
        record["aliases"].setdefault("arm", []).extend(variants)
    variants = {"diarrhea":["diarrhoea"], "diarrhoea":["diarrhea"], "anemia":["anaemia"],
        "anaemia":["anemia"], "cancers":["cancer"], "adrenal crises":["adrenal crisis"],
        "injection-site reaction":["injection site reaction"],
        "spontaneous abortions":["spontaneous abortion"], "thrombotic microangiopathies":["thrombotic microangiopathy"],
        "Gastrointestinal adverse events":["gastrointestinal adverse events","gastrointestinal events","gastrointestinal"],
        "injection reactions":["IRs","injection-related reactions"]}
    if term in variants:
        record["aliases"].setdefault("term", []).extend(variants[term])
    LABELS[CURRENT]["statements"].append(record)


def remaining_naturalistic():
    # Every empty case listed here was read in full; no harm-vocabulary filter.
    for cid in ["NCT01917331-30880943", "NCT02360215-32058845", "NCT03334253-37440213",
        "NCT03334253-39480129", "NCT03615326-34912120", "NCT03701399-38814532",
        "NCT03701399-39806095", "NCT03734029-36780610", "NCT03734029-37597578",
        "NCT03734029-40349139", "NCT03734029-41062831", "NCT03745287-40857358",
        "NCT03785964-37347393", "NCT03785964-41115259", "NCT04242498-40153232",
        "NCT04242498-40652434", "NCT04242498-41107641", "NCT04242498-42247110",
        "NCT04255433-41406444", "NCT04760626-37748181", "NCT04760626-39368488",
        "NCT04760626-39699848", "NCT04816643-36929216", "NCT05110001-42348235"]:
        case(cid, notes="No eligible participant safety statement; efficacy, measurements, unnumbered safety or occurrence/composite counts excluded.")
    for cid in ["NCT03138733-31918579", "NCT03615326-33167735", "NCT04551222-35392963", "NCT05110001-39192339"]:
        case(cid, "protocol")
    for cid in ["NCT03734029-37207306", "NCT03734029-38442809", "NCT03745287-38866240",
                "NCT04255433-40746703", "NCT04816643-39197299"]:
        case(cid, "review")
    for cid in ["NCT03701399-34115419", "NCT03734029-42340017", "NCT03745287-36860093", "NCT03745287-36975201"]:
        case(cid, "other", "Survey/instrument-development/retrospective cohort, not prospective trial safety results.")

    case("NCT03138733-37754204", notes="Do not borrow AE-clause denominators for the SAE clause.")
    for arm, aid, pct, count_sae, pct_sae in [("ceftobiprole group", "EG000", 9.0, 36, 18.8), ("daptomycin group", "EG001", 9.1, 45, 22.7)]:
        row(11, "death", arm=arm, percent=pct, aid=aid)
        row(12, "serious", arm=arm, count=count_sae, percent=pct_sae, aid=aid)
    case("NCT03615326-37871604", notes="Related-only findings; any-grade named events retain unrestricted seriousness. Japan/second-course registry arms are not interchangeable with global first-course arms.")
    for arm, aid, n in [("pembrolizumab group", "EG000", 4), ("placebo group", "EG001", 3)]:
        row(17, "death", arm=arm, count=n, percent=1, aid=aid, treatment_related=True)
    for term, values in [("diarrhoea", [(165,47),(145,42)]), ("nausea", [(154,44),(152,44)]), ("anaemia", [(109,31),(113,33)])]:
        for (n,pct), arm, aid in zip(values, ["pembrolizumab group", "placebo group"], ["EG000", "EG001"]):
            row(18,"event",term,arm,n,percent=pct,aid=aid,treatment_related=True,event_scope="all",aliases={"grade":["any grade"]})
    case("NCT03734029-35665782", notes="ILD/pneumonitis is one combined class; grade 5 is a subset of that class, not an inferred death count.")
    row(11,"event","interstitial lung disease or pneumonitis","trastuzumab deruxtecan",percent=12.1,aid="EG000",treatment_related=True,claim_family="ild-tdxd")
    row(11,"event","interstitial lung disease or pneumonitis","trastuzumab deruxtecan",percent=.8,aid="EG000",treatment_related=True,grade="grade 5",detail_level="component",claim_family="ild-tdxd")
    case("NCT03734029-38884900", notes="Asian subgroup is preserved. Majority grade 1-2 does not restrict the entire ILD count.")
    text = SOURCES[CURRENT]["case"]["abstract"]
    grade_text = text[text.index("grade"):text.index("drug-related")].strip()
    for term,pct in [("neutropenia",16.3),("anemia",12.9),("leukopenia",11.6)]:
        row(7,"event",term,"T-DXd",percent=pct,aid="EG000",population="Asian patients",grade=grade_text,treatment_related=True,
            aliases={"grade":["grade ≥ 3","grade 3 or higher"]})
    row(8,"event","interstitial lung disease or pneumonitis","T-DXd",percent=14.3,aid="EG000",population="Asian patients",treatment_related=True)
    case("NCT03745287-38661449")
    row(12,"event","cancers",count=0,category="safety_unclear",aid="EG000")
    case("NCT04255433-41903177", notes="GI class cannot match one registry component.")
    row(12,"event","Gastrointestinal adverse events","tirzepatide",2827,percent=42.5,category="safety_unclear",aid="EG000")
    row(12,"event","Gastrointestinal adverse events","dulaglutide",2387,percent=35.9,category="safety_unclear",aid="EG001")
    case("NCT04760626-40186685", notes="Positive episode proportions and ≤3% bounds excluded. Explicit zero recurrences imply zero affected people in the specified period; trial identities are mapped in the abstract.")
    for attribution,pop in [("other","ONWARDS 1"),("other","ONWARDS 3"),("target","ONWARDS 5")]:
        row(14,"event","recurrent clinically significant or severe hypoglycaemic episodes",count=0,population=pop,
            attribution=attribution,category="safety_unclear",aliases={"term":["recurrent clinically significant or severe hypoglycaemia"]})
    row(15,"event","additional severe episodes",count=0,attribution="pooled",population="any participants across the trials",
        aliases={"term":["additional severe hypoglycaemic episodes","recurrent severe hypoglycaemia"]})
    case("NCT04760626-40465144", notes="Hospitalization itself excluded. Six named hypoglycaemia rows include overlapping arm totals and T2D/T1D components; T1D belongs to ONWARDS 6, not the target.")
    for arm, n, a, b in [("icodec",6,1,5),("once-daily comparators",3,1,2)]:
        family="hypo-"+arm
        row(10,"event","clinically significant or severe hypoglycaemia",arm,n,attribution="pooled",claim_family=family,detail_level="total")
        row(10,"event","clinically significant or severe hypoglycaemia",arm,a,population="T2D",attribution="pooled",claim_family=family,detail_level="component")
        row(10,"event","clinically significant or severe hypoglycaemia",arm,b,population="T1D",attribution="other",claim_family=family,detail_level="component")
    case("NCT04816643-34752019")
    row(12,"serious",count=0,treatment_related=True,population="5-to-11-year-olds")
    case("NCT04816643-36791162", notes="Grade-4 unnamed reactogenicity is not a named harm. Placebo fever percentages are ranges. Vaccinated fever is not assigned to a single dose-period registry group.")
    row(11,"event","fever","BNT162b2",percent=7,population="children 6 months to <2 years of age",category="safety_unclear")
    row(11,"event","fever","BNT162b2",percent=5,population="those 2 to 4 years of age",category="safety_unclear")
    case("NCT05110001-41561665", notes="Unresolved attribution: prospective tacrolimus/TPK case series versus randomized rose-bengal/sham registry. Reinfection is a postoperative complication; urgent surgery is not an explicit glaucoma count.")
    row(10,"event","reinfection",count=3,attribution="unresolved")
    row(11,"event","endophthalmitis",count=0,attribution="unresolved")
    case("NCT05232825-40245351", notes="Post-SC subgroup is not established as the complete registry population. Combined-arm IR totals cannot be mapped to one arm.")
    row(12,"serious",arm="OCR IV/SC",percent=5.9,aid="EG000",population="Patients receiving at least 1 dose of OCR SC 920 mg")
    row(12,"serious",arm="OCR SC/SC",percent=2.5,aid="EG001",population="Patients receiving at least 1 dose of OCR SC 920 mg")
    row(13,"event","injection reactions",percent=51.5,category="safety_unclear",population="Patients receiving at least 1 dose of OCR SC 920 mg",claim_family="irs",detail_level="total")
    row(13,"event","local",count=117,denominator=233,percent=50.2,category="safety_unclear",population="Patients receiving at least 1 dose of OCR SC 920 mg",claim_family="irs",detail_level="component",aliases={"term":["local IRs","local injection reactions"]})
    row(13,"event","systemic IRs",count=27,denominator=233,percent=11.6,category="safety_unclear",population="Patients receiving at least 1 dose of OCR SC 920 mg",claim_family="irs",detail_level="component",aliases={"term":["systemic","systemic injection reactions"]})
    case("NCT05595642-42150581", notes="No abstract mapping from the two IDs to ALIENTO/ARNASA; do not infer identity from registry numbers. The treatment-related total is pooled.")
    row(12,"death",count=40,denominator=1301,percent=3.1,population="ALIENTO",attribution="unresolved")
    row(12,"death",count=44,denominator=1375,percent=3.2,population="ARNASA",attribution="unresolved")
    row(13,"death",count=3,percent=.1,attribution="pooled",treatment_related=True)


def submitted_twenty():
    """AI transcription/check of Venu's independent prose; changes logged separately."""
    case("NCT03999996-40099800", notes="Transcribed from Venu notes: three clinical families yield five source rows, all pooled.")
    row(6,"serious",arm="TAK-003",count=6,population="participants who received TAK-003 in the exposure window",attribution="pooled")
    row(6,"serious",count=2,population="neonates",attribution="pooled")
    for arm,n,level in [(None,5,"total"),("TAK-003",3,"component"),("placebo",2,"component")]:
        row(5,"event","spontaneous abortions",arm,n,population="inside the exposure window",attribution="pooled",claim_family="spontaneous-abortions",detail_level=level)
    for cid in ["NCT05595642-42148875","NCT05767021-42112567","NCT06109311-42251769",
                "NCT04028492-38237696","NCT05232825-39460719","NCT00424463-28872914"]:
        case(cid, notes="Transcribed from Venu's no-eligible-statement judgment; source rechecked.")
    case("NCT05110001-39011096","protocol")
    case("NCT04255433-35949358","review")
    case("NCT04242498-38795716", notes="Source trial names cannot be mapped to the two NCT IDs from the abstract. No inference from registry death counts.")
    row(14,"serious",arm="bimekizumab",count=40,percent=8,population="BE HEARD I",attribution="unresolved")
    row(14,"serious",arm="bimekizumab",count=24,percent=5,population="BE HEARD II",attribution="unresolved")
    row(16,"death",count=1,attribution="pooled",claim_family="be-heard-death")
    row(16,"death",arm="bimekizumab every 2 weeks",count=1,cause="congestive heart failure",population="BE HEARD I",attribution="unresolved",detail_level="component",claim_family="be-heard-death")
    case("NCT04121221-41705515", notes="Injection-site reaction class does not equal one listed component.")
    row(9,"event","injection site reactions","GA Depot",percent=46.1,category="safety_unclear",aid="EG000")
    row(9,"event","injection site reactions","placebo",percent=28.7,category="safety_unclear",aid="EG001")
    case("NCT06045221-41765029")
    for arm,aid,n,N,pct in [("orforglipron 12 mg","EG000",249,424,59),("orforglipron 36 mg","EG001",245,423,58),
                           ("semaglutide 7 mg","EG002",157,426,37),("semaglutide 14 mg","EG003",193,425,45)]:
        row(14,"event","gastrointestinal events",arm,n,N,pct,category="safety_unclear",aid=aid)
    row(16,"death",count=4,category="safety_unclear",claim_family="achieve-deaths")
    for arm,aid,n in [("orforglipron 12 mg group","EG000",1),("orforglipron 36 mg group","EG001",1),("semaglutide 7 mg group","EG002",2)]:
        row(16,"death",arm=arm,count=n,aid=aid,claim_family="achieve-deaths",detail_level="component")
    case("NCT01917331-27598678", notes="Venu's two roles retained as one shared clinical family: the explicitly stated overall serious-related count and its named atrial-fibrillation detail; one participant, not two observations.")
    row(17,"serious",arm="BDP/FF/GB group",count=1,aid="EG000",treatment_related=True,claim_family="trilogy-serious-related")
    row(17,"event","atrial fibrillation","BDP/FF/GB group",1,aid="EG000",eids=["event:serious:52"],treatment_related=True,event_scope="serious",detail_level="component",claim_family="trilogy-serious-related")
    case("NCT03062280-33527139", notes="AI correction requiring review: explicit zero is eligible source evidence, but the record is DIUR-006, an extension enrolling DIUR-005 completers. The sentence's zero belongs to the preceding randomized phase-3 period, not this extension. See saved registry identity evidence.")
    row(9,"event","adrenal crises","MR-HC",0,population="phase 3",attribution="other")
    case("NCT04158648-36716761", notes="Venu's judgments transcribed; occurrence count ten is not used for the eight SAE participants.")
    for term,n,pct,eid in [("headache",12,17,"event:other:2"),("injection-site reaction",12,17,"event:other:12"),("arthralgia",11,15,"event:other:0")]:
        row(15,"event",term,count=n,percent=pct,aid="EG000",eids=[eid])
    row(17,"serious",count=8,percent=11,aid="EG000")
    row(18,"death",count=0,aid="EG000")
    row(18,"event","thrombotic microangiopathies",count=0,aid="EG000",category="safety_unclear")
    row(19,"event","thrombosed haemorrhoids",count=1,aid="EG000",grade="grade 1")
    case("NCT05134974-41743463")
    row(10,"serious",arm="POS",count=0,population="pediatric subjects aged 3-17 years",attribution="pooled")
    case("NCT03740165-42705254", notes="No denominator copied from the unnamed grade-3+ AE clause. The single pembrolizumab death's explicit GI-bleeding cause is retained; four unnumbered causes are not split into invented counts.")
    arms=["pembrolizumab-olaparib group","pembrolizumab group","control group"]
    aids=["EG000","EG001","EG002"]
    for term,values in [("neutropenia",[(102,23),(78,17),(89,20)]),("anaemia",[(100,22),(59,13),(48,11)]),
                        ("neutrophil count decreased",[(66,15),(66,15),(68,15)])]:
        for arm,aid,(n,pct) in zip(arms,aids,values):
            row(16,"event",term,arm,n,percent=pct,aid=aid,grade="grade 3 or higher",treatment_related=True)
    for arm,aid,n,pct,fn,fp in zip(arms,aids,[110,96,39],[24,21,9],[19,16,9],[4,4,2]):
        family="serious-related-"+arm
        row(17,"serious",arm=arm,count=n,percent=pct,aid=aid,treatment_related=True,claim_family=family)
        row(17,"event","febrile neutropenia",arm,fn,percent=fp,aid=aid,eids=["event:serious:6"],treatment_related=True,event_scope="serious",detail_level="component",claim_family=family)
    row(18,"death",arm=arms[0],count=4,percent=1,aid=aids[0],treatment_related=True)
    row(18,"death",arm=arms[1],count=1,aid=aids[1],treatment_related=True,cause="gastrointestinal haemorrhage",detail_level="component")
    case("NCT02838420-30981696", notes="Equal counts are not confirmed agreement: abstract does not establish the same collection window/population.")
    row(19,"serious",arm="alectinib",count=19,denominator=125,percent=15,aid="EG000",category="safety_unclear")
    row(19,"serious",arm="crizotinib",count=16,denominator=62,percent=26,aid="EG001",category="safety_unclear")
    case("NCT03785964-36884323", notes="AI check: rash occurs in both seriousness strata (the notes mentioned only one). Ovarian dysfunction has no verified equivalent class row; unclear is used before the subgroup/denominator comparison.")
    for term,pct,eids in [("diarrhea",84,[]),("nausea",54,["event:other:10"]),("fatigue",51,["event:other:14"]),
                          ("hypophosphatemia",42,["event:other:33"]),("maculopapular rash",32,[])]:
        row(10,"event",term,"nirogacestat",percent=pct,aid="EG000",eids=eids)
    row(11,"event","ovarian dysfunction","nirogacestat",27,36,75,aid="EG000",population="women of childbearing potential",category="safety_unclear",aliases={"term":["adverse events consistent with ovarian dysfunction"]})
    case("NCT05305547-39960062")
    row(11,"serious",count=0,treatment_related=True)
    row(11,"death",count=0,treatment_related=True)


def numeric_scope():
    source, safety = SOURCES[CURRENT]["case"], SOURCES[CURRENT]["safety"]
    start,end = safety_sentence_spans(source["abstract"])[0]
    return dict(window="same", population_relation="same",
        abstract_window_quote=source["abstract"][start:end], registry_window_quote=safety.time_frame,
        abstract_population_quote="all treated participants", registry_population_quote=safety.arms[0].description)


def constructed():
    # Explicit transcription of each constructed source, not comparator output.
    for cid,n,category in [("T01",4,"safety_consistent"),("T02",5,"safety_consistent"),
        ("T03",6,"safety_consistent"),("T04",7,"safety_consistent"),
        ("T05",8,"safety_number_differs"),("T06",9,"safety_number_differs"),
        ("T07",10,"safety_number_differs"),("T08",11,"safety_number_differs"),
        ("T29",12,"safety_consistent"),("T30",24,"safety_number_differs"),
        ("T31",13,"safety_consistent"),("T32",25,"safety_number_differs"),
        ("T33",14,"safety_consistent"),("T34",26,"safety_number_differs"),
        ("T35",15,"safety_consistent"),("T36",27,"safety_number_differs"),
        ("T37",16,"safety_consistent"),("T38",28,"safety_number_differs"),
        ("T39",17,"safety_consistent"),("T40",29,"safety_number_differs")]:
        case(cid)
        row(2,"serious",arm="Drug group",count=n,denominator=120,category=category,aid="EG0",**numeric_scope())
    case("T09")
    row(1,"serious",arm="Drug",count=2,denominator=40,population="AYAs",aid="EG0",population_relation="different",
        abstract_population_quote="40 AYAs",registry_population_quote=SOURCES[CURRENT]["safety"].arms[0].description)
    case("T10")
    row(1,"event","hypertension","Drug group",2,40,population="AYAs",aid="EG0",category="safety_unclear")
    row(1,"event","hypertension","Drug group",8,80,population="non-AYAs",aid="EG0",category="safety_unclear")
    case("T11")
    row(1,"serious",arm="Drug",count=6,denominator=120,aid="EG0",category="safety_unclear",
        population_relation="same",abstract_population_quote=SOURCES[CURRENT]["case"]["abstract"],
        registry_population_quote=SOURCES[CURRENT]["safety"].arms[0].description)
    case("T12")
    row(1,"serious",arm="Drug",count=2,denominator=40,population="participants older than 70 years",aid="EG0",
        population_relation="different",abstract_population_quote="40 participants older than 70 years",
        registry_population_quote=SOURCES[CURRENT]["safety"].arms[0].description)
    case("T13",notes="Open-label focal-seizure study conflicts with placebo-controlled PGTC record: unresolved trial identity.")
    row(2,"event","DRESS",count=0,attribution="unresolved")
    case("T14",notes="A placebo-controlled phase plus extension is not an identity contradiction.")
    row(2,"serious",arm="Drug",count=6,denominator=120,aid="EG0",category="safety_unclear")
    case("T15")
    row(1,"serious",arm="Drug",count=6,denominator=120,attribution="other")
    case("T16")
    row(1,"serious",arm="Drug",count=6,denominator=120,aid="EG0",category="safety_unclear")
    case("T17",notes="Unrestricted event cannot be compared by adding overlapping seriousness strata.")
    row(1,"event","Hypertension","Drug",10,120,aid="EG0")
    case("T18")
    row(1,"event","hypertension","Drug",2,120,aid="EG0",category="safety_unclear",event_scope="serious",eids=["event:serious:0"])
    case("T19")
    row(1,"event","hypertension","Drug",8,120,aid="EG0",category="safety_unclear",event_scope="non_serious",eids=["event:other:0"])
    for cid in ("T20","T26"):
        case(cid)
        row(1,"event","cardiovascular or thrombotic events","Drug",0,aid="EG0",category="safety_unclear",event_scope="serious",
            aliases={"term":["serious cardiovascular or thrombotic events"]})
    for cid in ("T21","T22"):
        case(cid)
        row(1,"death",arm="Drug group",percent=10,aid="EG0",measure="time_to_event")
    case("T23")
    source,safety=SOURCES[CURRENT]["case"],SOURCES[CURRENT]["safety"]
    row(1,"death",arm="Drug group",count=12,denominator=120,aid="EG0",all_cause=True,category="safety_consistent",
        window="same",population_relation="same",abstract_window_quote=source["abstract"],registry_window_quote=safety.time_frame,
        abstract_population_quote="120 treated participants in the Drug group",registry_population_quote=safety.arms[0].description)
    case("T24",notes="About 10% is approximate; risk ratio is not a participant count.")
    case("T25")
    row(1,"death",arm="Drug",count=6,aid="EG0",claim_family="deaths-drug")
    row(1,"death",arm="Drug",count=2,aid="EG0",cause="pneumonia",detail_level="component",claim_family="deaths-drug")
    row(1,"death",arm="Drug",count=4,aid="EG0",cause="disease progression",detail_level="component",claim_family="deaths-drug")
    case("T27",notes="The trailing Drug modifier is explicit for vomiting; nausea's arm is not repeated. Single registry group still identifies a counterpart, not the absent event row.")
    row(1,"event","Nausea",count=3,aid="EG0",category="safety_unclear")
    row(1,"event","vomiting","Drug",2,aid="EG0",category="safety_unclear")
    case("T28")
    row(1,"death",arm="Drug",count=3,aid="EG0",claim_family="deaths-drug")
    row(1,"death",arm="Drug",count=1,aid="EG0",cause="pneumonia",detail_level="component",claim_family="deaths-drug")
    row(1,"death",arm="placebo",count=3,aid="EG1")


def main():
    if (ROOT/"freeze.json").exists() or (ROOT/"model_run_started.json").exists():
        raise ValueError("Do not author references after freeze/model output")
    remaining_naturalistic()
    submitted_twenty()
    constructed()
    if set(LABELS) != set(SOURCES):
        raise ValueError("Case authoring incomplete: " + str(set(SOURCES)-set(LABELS)))
    references = {cid:ReferenceCase(**value) for cid,value in LABELS.items()}
    for cid,ref in references.items():
        validate_case(ref,SOURCES[cid]["case"],SOURCES[cid]["safety"])
    provenance_path=ROOT/"label_amendments"/"structured_draft_hashes.json"
    prior=json.loads(provenance_path.read_text()) if provenance_path.exists() else {}
    files={}
    for path in ROOT.rglob("references.jsonl"):
        existing=path.read_bytes()
        rows=[json.loads(line) for line in existing.decode('utf-8').splitlines() if line.strip()]
        if any(r.get('statements') is not None for r in rows) and hashlib.sha256(existing).hexdigest()!=prior.get(str(path)):
            raise ValueError("Reference edited outside authoring script; preserve it: "+str(path))
        files[path]=[references[r['case_id']] for r in rows]
    for path,refs in files.items():
        path.write_text(''.join(r.model_dump_json()+'\n' for r in refs),encoding='utf-8')
    provenance_path.write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},indent=2)+'\n',encoding='utf-8')
    print('Wrote',len(references),'case references;',sum(len(r.statements) for r in references.values()),'source statements. No model called.')


if __name__ == "__main__":
    main()
