"""Flag explicit source conflicts before attributing harms to a registry trial.

These flags require review; they do not prove that a paper belongs to another trial.
Participant-count differences alone are deliberately not used (subsets are valid).
"""
import re


def identity_issues(abstract, safety, registry_title=None):
    if not abstract or safety is None:
        return []
    issues = []
    # A historical placebo-to-active cohort is not a current placebo arm.
    # An abstract may also describe a blinded phase plus an open-label extension.
    placebo_arm = any(re.fullmatch(r"placebo(?: group| arm)?", arm.title.strip(), re.IGNORECASE)
                      for arm in safety.arms)
    if (re.search(r"\bopen[- ]label\b", abstract, re.IGNORECASE) and placebo_arm
            and not re.search(r"\bplacebo\b", abstract, re.IGNORECASE)):
        issues.append("The abstract describes an open-label study while the registry supplies a placebo arm. Confirm original-study versus extension/substudy identity and scope.")
    if re.search(r"\bfocal (?:seizures?|epilepsy)\b", abstract, re.IGNORECASE) and re.search(
            r"\bPGTC\b|primary generalized", registry_title or "", re.IGNORECASE):
        issues.append("The abstract describes focal seizures while the registry title describes primary generalized seizures.")
    return issues


def check_reading_identity(reading, abstract, safety, registry_title=None):
    issues = identity_issues(abstract, safety, registry_title)
    if not issues or reading.status != "ok":
        return reading
    return reading.model_copy(update={
        "status": "failed", "error_code": "trial_identity_unverified", "reason": " ".join(issues),
        "statements": [], "rejected": reading.rejected + [s.model_copy(update={
            "error_code": "trial_identity_unverified"}) for s in reading.statements]})
