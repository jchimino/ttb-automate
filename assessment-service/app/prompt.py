"""
Compliance Prompts
==================
Two prompt builders — one for the vision path (llava:7b), one for the OCR/reconcile path.

IMPORTANT: Both builders use identical CFR rules and scoring logic, matching the
Anthropic BAM verifier prompt used by the API path. The government health warning
is a hard binary PASS/FAIL — partial presence is never sufficient.
"""

REQUIRED_HEALTH_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should "
    "not drink alcoholic beverages during pregnancy because of the risk of "
    "birth defects. (2) Consumption of alcoholic beverages impairs your "
    "ability to drive a car or operate machinery, and may cause health problems."
)

# Clause fragments that MUST both be present for health_warning to PASS
_WARNING_CLAUSE_1 = "surgeon general"          # pregnancy / birth defects clause
_WARNING_CLAUSE_2 = "impairs your ability"     # driving / machinery clause

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
      "note": "<cite the valid TTB Standard of Identity if FAIL>"
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
      "status": "PASS" | "FAIL",
      "found_on_label": "<full warning text as found, or null>",
      "reference_value": "statutory text per 27 CFR Part 16",
      "note": "<state exactly which clause(s) are missing or incomplete>"
    }
  ]
}
"""

_TTB_RULES = f"""
TTB mandatory label requirements (27 CFR Parts 4, 5, 7):
  1. brand_name      — producer/winery/distillery name
  2. class_type      — Standard of Identity per BAM (see rules below)
  3. alcohol_content — ABV percentage (e.g. 13.5% by volume)
  4. net_contents    — metric volume (e.g. 750 mL) — may be embossed on bottle
  5. bottler_info    — producer name AND city/state or country
  6. health_warning  — FULL statutory text, both clauses, per 27 CFR Part 16

═══════════════════════════════════════════════════════
CLASS & TYPE RULES — Standard of Identity (CRITICAL)
═══════════════════════════════════════════════════════
Wine (27 CFR Part 4):
  VALID designations: Table Wine, Red Wine, White Wine, Rosé Wine, Pink Wine,
    Red Table Wine, White Table Wine, Rosé Table Wine, Dessert Wine, Sparkling
    Wine, Champagne, Port, Sherry, Merlot, Cabernet Sauvignon, Chardonnay,
    Pinot Noir (varietal = class if ≥75% of stated grape)
  NOTE: "Red Wine", "White Wine", "Rosé Wine", and "Pink Wine" ARE valid type
    designations under 27 CFR 4.21(a)(1) — they describe a still grape wine by
    color. A label showing "Red Wine" satisfies the class/type requirement.
    Do NOT fail "Red Wine" as a class/type designation.
  INVALID: Generic terms like "Wine" alone without a color or class qualifier,
    or fabricated style names with no TTB Standard of Identity.

Spirits (27 CFR Part 5):
  VALID: Bourbon Whisky, Straight Bourbon Whisky, Vodka, Gin, Rum, Tequila,
    Brandy, Cognac, Scotch Whisky, Irish Whiskey, Liqueur, Cordial, etc.
  INVALID: Generic terms like "Spirit" or "Distilled Spirit" without a class.

Malt Beverages (27 CFR Part 7):
  VALID: Beer, Ale, Lager, Stout, Porter, Pilsner, Malt Liquor, India Pale Ale
  INVALID: "Malt Beverage" alone without a specific class.

═══════════════════════════════════════════════════════
GOVERNMENT WARNING — HARD BINARY RULE (27 CFR Part 16)
═══════════════════════════════════════════════════════
The COMPLETE statutory text is required. BOTH clauses must appear:

  Clause 1: "According to the Surgeon General, women should not drink
             alcoholic beverages during pregnancy because of the risk of
             birth defects."
  Clause 2: "Consumption of alcoholic beverages impairs your ability to
             drive a car or operate machinery, and may cause health problems."

  PASS: Both clauses are FULLY and COMPLETELY readable in the label image.
    Both the pregnancy/birth defects clause AND the impairment/machinery clause
    must be legible in their entirety.
  FAIL: Either clause is absent, truncated, rotated to the point of being
    unreadable, obscured, or cannot be confirmed as complete. "Contains Sulfites"
    alone is always FAIL. If you cannot confirm both complete clauses are
    present, mark FAIL.
  CRITICAL: "GOVERNMENT WARNING: Contains Sulfites" is a sulfite declaration,
    NOT the health warning — always FAIL.
  CRITICAL: If the health warning text is rotated, sideways, or only partially
    visible and you cannot read both full clauses — always FAIL.

  ⚠ NEVER mark health_warning REVIEW — it is always PASS or FAIL.
  ⚠ "Contains Sulfites" is a separate sulfite declaration, NOT the health warning.
  ⚠ A label that only shows "GOVERNMENT WARNING: Contains Sulfites" is a FAIL.

═══════════════════════════════════════════════════════
DECISION RULES
═══════════════════════════════════════════════════════
  APPROVE — all mandatory fields present and fully compliant with 27 CFR
  REVIEW  — a field is genuinely ambiguous (partially visible, conflicting
             data); do NOT use REVIEW to soften a clear compliance failure
  DENY    — a mandatory field is absent, incomplete, or uses an invalid
             designation; OR health_warning is FAIL; OR class_type is FAIL

  A non-empty critical_failures list (health_warning FAIL, class_type FAIL,
  or any mandatory field FAIL) forces DENY regardless of other scores.

═══════════════════════════════════════════════════════
FIELD STATUS RULES
═══════════════════════════════════════════════════════
  PASS   — field is present and meets the TTB Standard of Identity
           (minor capitalization differences OK; paraphrasing is NOT OK)
  REVIEW — field is genuinely ambiguous (conflicting data, genuinely
           partially visible — not just rotated or small print)
  FAIL   — field is absent, uses an invalid designation, or (for
           health_warning) either statutory clause is missing

  health_warning has only two states: PASS or FAIL — never REVIEW.
"""


def build_prompt_vision(n_labels: int, has_form: bool, submission_id: str, ocr_supplement: str = "", rag_context: str = "") -> str:
    """
    Primary path — llava:7b reads the label images directly.
    If OCR text is provided as a supplement, it is appended as confirmatory
    context so llava can cross-check its visual read against extracted text.
    llava's visual interpretation always takes precedence.
    """
    prompt = "You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance specialist.\n\n"
    prompt += _TTB_RULES

    if has_form:
        prompt += (
            f"\nYou are shown {n_labels + 1} images. The FIRST image is the TTB application "
            f"form — use it as your reference. The remaining {n_labels} image(s) are the label(s)."
        )
    else:
        prompt += f"\nYou are shown {n_labels} label image(s). No application form was provided — assess against TTB requirements only."

    # Inject RAG-retrieved CFR context (authoritative regulatory text)
    if rag_context.strip():
        prompt += rag_context

    if ocr_supplement.strip():
        prompt += f"""

--- SUPPLEMENTARY OCR TEXT (use to confirm your visual read) ---
{ocr_supplement}
--- END OCR TEXT ---
Note: OCR is provided as a cross-check only. Your primary source is the image.
If OCR and visual read conflict, trust your visual interpretation of the image.
"""

    prompt += f"\nSubmission ID: {submission_id}\n"
    prompt += _FIELD_SCHEMA
    return prompt


def build_prompt_ocr(ocr_text: str, n_labels: int, has_form: bool, submission_id: str, rag_context: str = "") -> str:
    """
    Fallback path — OCR text is the primary input (no GPU / llava unavailable).
    Uses identical CFR rules as the vision path and the Anthropic BAM verifier.
    """
    prompt = "You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance specialist.\n\n"
    prompt += _TTB_RULES

    # Inject RAG-retrieved CFR context (authoritative regulatory text)
    if rag_context.strip():
        prompt += rag_context

    prompt += f"""
The following text was extracted from the submission images by OCR (four rotation
passes were run to capture sideways text like the government warning).
Use this as your PRIMARY source. The images are also provided as visual backup.

--- OCR EXTRACTED TEXT ---
{ocr_text}
--- END OCR TEXT ---

OCR GUIDANCE: OCR output may be imperfect (fragmented lines, misread characters).
Use your best judgment to reconstruct what the label likely says — but only mark
PASS if the evidence actually supports it. Do NOT assume a field is present because
it would be expected on this type of label. Evaluate what is actually in the text.
"""

    if has_form:
        prompt += f"\nThe first image/OCR block is the TTB application form — use it as reference values. The remaining {n_labels} image(s)/block(s) are the label(s)."
    else:
        prompt += "\nNo application form was provided. Assess against TTB requirements only."

    prompt += f"\nSubmission ID: {submission_id}\n"
    prompt += _FIELD_SCHEMA
    return prompt
