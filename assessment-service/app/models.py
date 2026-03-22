"""
Response Models
===============
Pydantic schemas for the structured LLM verdict.
Graceful fallback to REVIEW if response can't be parsed —
a submission is never silently dropped.
"""

import json
import re
from typing import Optional
from pydantic import BaseModel


class FieldResult(BaseModel):
    name:            str
    status:          str             # PASS | REVIEW | FAIL
    found_on_label:  Optional[str]
    reference_value: Optional[str]
    note:            Optional[str]


class AssessmentResult(BaseModel):
    submission_id: str
    decision:      str              # APPROVE | REVIEW | DENY
    brand_name:    Optional[str]
    reasoning:     Optional[str]
    fields:        list[FieldResult]
    model:         str

    @classmethod
    def from_llm_response(cls, raw: str, submission_id: str, model: str) -> "AssessmentResult":
        try:
            text = raw.strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
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
            # Never drop a submission — route to human if parsing fails
            return cls(
                submission_id = submission_id,
                decision      = "REVIEW",
                brand_name    = None,
                reasoning     = f"Response could not be parsed — human review required. Error: {e}",
                fields        = [],
                model         = model,
            )
