"""
Response Models
===============
Pydantic schemas for the structured LLM verdict.

Graceful fallback to REVIEW if response can't be parsed -- a submission is
never silently dropped.

post_process() applies hard, Python-enforced compliance rules AFTER the LLM
responds. This is the safety net that catches cases where the LLM ignores
the prompt rules (e.g. llava passing "Contains Sulfites" as the health
warning). These rules are binary and deterministic:

  - health_warning PASS requires both statutory clauses in the found text.
    If either clause is absent, status is forced to FAIL and decision to DENY.
  - Any field with status FAIL forces the overall decision to DENY.
  - "Contains Sulfites" alone (without the Surgeon General clause) is always
    a health_warning FAIL regardless of what the LLM returned.
"""
import json
import re
from typing import Optional

from pydantic import BaseModel

# -- Statutory health warning clauses (27 CFR Part 16) ----------------------
# Both must be present (case-insensitive substring match) for PASS.
_HW_CLAUSE_1 = "surgeon general"       # pregnancy / birth defects
_HW_CLAUSE_2 = "impairs your ability"  # driving / machinery

# Phrases that are definitively NOT the health warning.
_HW_FALSE_POSITIVES = [
    "contains sulfites",
    "contains sulphites",
    "contains a sulfiting agent",
    "contains sulfiting agents",
]



def _extract_json(raw: str) -> str:
    """
    Robustly extract a valid JSON object from raw LLM output.
    Handles: markdown fences, preamble text, invalid escapes,
    JS comments, trailing commas -- all common llava/qwen quirks.
    """
    text = raw.strip()
    # 1. Strip markdown code fences
    text = re.sub(r"^`{1,3}(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"`{1,3}\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()
    # 2. Extract outermost { ... } block
    b0 = text.find("{")
    b1 = text.rfind("}")
    if b0 != -1 and b1 > b0:
        text = text[b0:b1+1]
    # 3. Remove JS single-line comments
    text = re.sub(r"//[^\n]*", "", text)
    # 4. Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\\]])", r"\1", text)
    # 5. Fix invalid JSON escape sequences
    #    Valid: \" \\ \/ \b \f \n \r \t \uXXXX
    #    Everything else: drop the backslash
    valid_esc = set('"\\/bfnrtu')
    result = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            nxt = text[i+1]
            if nxt in valid_esc:
                result.append(c)
                result.append(nxt)
            else:
                result.append(nxt)  # drop the backslash
            i += 2
        else:
            result.append(c)
            i += 1
    return "".join(result)

class FieldResult(BaseModel):
    name: str
    status: str                   # PASS | REVIEW | FAIL
    found_on_label: Optional[str]
    reference_value: Optional[str]
    note: Optional[str]


class AssessmentResult(BaseModel):
    submission_id: str
    decision: str                 # APPROVE | REVIEW | DENY
    brand_name: Optional[str]
    reasoning: Optional[str]
    fields: list[FieldResult]
    model: str

    @classmethod
    def from_llm_response(
        cls, raw: str, submission_id: str, model: str
    ) -> "AssessmentResult":
        """Parse LLM response with robust JSON extraction."""
        try:
            text = _extract_json(raw)
            data = json.loads(text)
            return cls(
                submission_id = submission_id,
                decision      = data.get("decision", "REVIEW"),
                brand_name    = data.get("brand_name"),
                reasoning     = data.get("reasoning"),
                fields        = [FieldResult(**f) for f in data.get("fields", [])],
                model         = model,
            )
        except Exception as e:
            return cls(
                submission_id = submission_id,
                decision      = "REVIEW",
                brand_name    = None,
                reasoning     = f"Response could not be parsed -- human review required. Error: {e}",
                fields        = [],
                model         = model,
            )
    def post_process(self) -> "AssessmentResult":
        """
        Enforce hard compliance rules after the LLM responds.

        Rules:
        1. health_warning: if found_on_label lacks both statutory clauses,
           force status=FAIL. "Contains Sulfites" alone is always FAIL.
        2. Any field status==FAIL forces decision=DENY.
        3. Prepend enforcement note to reasoning.
        """
        enforced_fails: list[str] = []

        for field in self.fields:
            if field.name == "health_warning":
                field, fail_reason = _enforce_health_warning(field)
                if fail_reason:
                    enforced_fails.append(fail_reason)
            # Any field already FAIL (including LLM-marked) forces DENY
            if field.status == "FAIL":
                label = field.name + ": FAIL"
                if not any(label.startswith(f.split(":")[0]) for f in enforced_fails):
                    enforced_fails.append(label)

        if enforced_fails:
            self.decision = "DENY"
            summary = "; ".join(enforced_fails)
            self.reasoning = f"[Enforcement] Forced DENY -- {summary}. " + (self.reasoning or "")

        return self


def _enforce_health_warning(field: FieldResult):
    """
    Check a health_warning FieldResult against the statutory text.
    Returns (field, fail_reason_or_None). Mutates field in place if violated.
    """
    found = (field.found_on_label or "").lower().strip()

    has_clause1 = _HW_CLAUSE_1 in found
    has_clause2 = _HW_CLAUSE_2 in found
    is_false_pos = any(fp in found for fp in _HW_FALSE_POSITIVES)

    if has_clause1 and has_clause2:
        # Both clauses confirmed -- PASS is legitimate.
        return field, None

    # Missing at least one clause -> FAIL
    if is_false_pos and not has_clause1 and not has_clause2:
        reason = (
            "health_warning: label shows sulfite declaration only "
            "('Contains Sulfites') -- not the statutory 27 CFR Part 16 warning"
        )
    elif not has_clause1:
        reason = "health_warning: Surgeon General / pregnancy clause absent"
    else:
        reason = "health_warning: impairment / machinery clause absent"

    field.status = "FAIL"
    field.note = (
        f"[Python enforcement] {reason}. "
        f"Required: both 27 CFR Part 16 clauses. "
        f"Found on label: {field.found_on_label!r}"
    )
    return field, reason
