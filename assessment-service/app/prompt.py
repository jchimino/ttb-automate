"""
Compliance Prompts
==================
Two prompt builders — one for the vision path (GPU), one for the OCR path (CPU).
Same output schema either way.
"""

REQUIRED_HEALTH_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should "
    "not drink alcoholic beverages during pregnancy because of the risk of "
    "birth defects. (2) Consumption of alcoholic beverages impairs your "
    "ability to drive a car or operate machinery, and may cause health problems."
)

_FIELD_SCHEMA = """
Return ONLY this JSON — no markdown, no explanation outside it:

{
  "decision": "APPROVE" | "REVIEW" | "DENY",
  "brand_name": "<brand name or null>",
  "reasoning": "<one or two plain-English sentences explaining the decision>",
  "fields": [
    {
      "name": "brand_name",
      "status": "PASS" | "REVIEW" | "FAIL",
      "found_on_label": "<exact text from label or null>",
      "reference_value": "<value from application form or null>",
      "note": "<explanation if not PASS, otherwise null>"
    },
    {
      "name": "class_type",
      "status": "PASS" | "REVIEW" | "FAIL",
      "found_on_label": "<text or null>",
      "reference_value": "<text or null>",
      "note": null
    },
    {
      "name": "alcohol_content",
      "status": "PASS" | "REVIEW" | "FAIL",
      "found_on_label": "<text or null>",
      "reference_value": "<text or null>",
      "note": null
    },
    {
      "name": "net_contents",
      "status": "PASS" | "REVIEW" | "FAIL",
      "found_on_label": "<text or null>",
      "reference_value": "<text or null>",
      "note": "<if absent from label, note it may be embossed on the bottle>"
    },
    {
      "name": "bottler_info",
      "status": "PASS" | "REVIEW" | "FAIL",
      "found_on_label": "<text or null>",
      "reference_value": "<text or null>",
      "note": null
    },
    {
      "name": "health_warning",
      "status": "PASS" | "REVIEW" | "FAIL",
      "found_on_label": "<full warning text or null>",
      "reference_value": "statutory text",
      "note": "<note if rotated, small, truncated, or caps not confirmed>"
    }
  ]
}
"""

_TTB_RULES = f"""
TTB mandatory label requirements (27 CFR Parts 4, 5, 7):
  1. brand_name       — producer/winery/distillery name
  2. class_type       — beverage category (e.g. Red Wine, Bourbon Whiskey, Beer)
  3. alcohol_content  — ABV percentage (e.g. 11.5% by volume)
  4. net_contents     — volume (e.g. 750mL) — may be embossed on bottle, not label
  5. bottler_info     — producer name and address
  6. health_warning   — exact statutory text required:
     "{REQUIRED_HEALTH_WARNING}"

Decision rules:
  APPROVE — all mandatory fields present and consistent with the application form
  REVIEW  — a field is ambiguous or needs a human look; do NOT use REVIEW when
            text is clearly present but OCR quality is uncertain
  DENY    — a mandatory field is definitively absent or clearly conflicts

Field status rules — apply TTB standards accurately:
  PASS    — field is present on the label (even if OCR quality was uncertain,
            even if capitalization differs, even if wording is paraphrased)
  REVIEW  — field is genuinely ambiguous (conflicting information, partially
            visible, or the label appears to be for a different product)
  FAIL    — field is completely absent with no trace

Specific guidance:
  - brand_name: if ANY brand or producer name is found, mark PASS
  - net_contents: if absent from label text, mark PASS with note "may be
    embossed on bottle" — this is standard practice and not a defect
  - health_warning: the FULL statutory text is required per 27 CFR §16. Both
    clauses must be present: (1) Surgeon General/pregnancy/birth defects AND
    (2) impairs your ability/drive/machinery. If either clause is missing or
    truncated, mark FAIL. Partial presence is NOT sufficient.
  - "750ML", "750 ml", "750 MILLILITERS" are equivalent
  - class_type must match a valid TTB Standard of Identity. "Red Wine" is a
     color designation only — it is NOT a valid class. Valid wine classes include
     "Table Wine", "Red Table Wine", "Dessert Wine", "Sparkling Wine", etc.
     If the label shows only "Red Wine" without a valid class designation, mark FAIL
  - Minor capitalization and punctuation differences are never failures
  - If OCR text is blurry/unclear but a field appears to be present, PASS it
"""


def build_prompt_vision(n_labels: int, has_form: bool, submission_id: str) -> str:
    """GPU path — LLM reads the images directly."""
    prompt = "You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance specialist.\n\n"
    prompt += _TTB_RULES

    if has_form:
        prompt += (
            f"\nYou are shown {n_labels + 1} images. The FIRST image is the TTB application "
            f"form — use it as your reference. The remaining {n_labels} image(s) are the label(s)."
        )
    else:
        prompt += f"\nYou are shown {n_labels} label image(s). No application form was provided — assess against TTB requirements only."

    prompt += f"\nSubmission ID: {submission_id}\n"
    prompt += _FIELD_SCHEMA
    return prompt


def build_prompt_ocr(ocr_text: str, n_labels: int, has_form: bool, submission_id: str) -> str:
    """
    CPU path — OCR text is the primary input.
    Images are still sent to the model as visual backup for anything OCR missed.
    """
    prompt = "You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance specialist.\n\n"
    prompt += _TTB_RULES

    prompt += f"""
The following text was extracted from the submission images by OCR (four rotation
passes were run to capture sideways text like the government warning).
Use this as your PRIMARY source. The images are also provided as visual backup.

--- OCR EXTRACTED TEXT ---
{ocr_text}
--- END OCR TEXT ---

IMPORTANT: OCR on printed labels is imperfect. Use the extracted text as your
primary evidence. Do NOT assume a field is present because of the label type —
only mark PASS if the text evidence actually supports it. A missing or incomplete
field must be marked FAIL regardless of label category.
"""

    if has_form:
        prompt += f"\nThe first image/OCR block is the TTB application form — use it as reference values. The remaining {n_labels} image(s)/block(s) are the label(s)."
    else:
        prompt += "\nNo application form was provided. Assess against TTB requirements only."

    prompt += f"\nSubmission ID: {submission_id}\n"
    prompt += _FIELD_SCHEMA
    return prompt
