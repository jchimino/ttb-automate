REQUIRED_HEALTH_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should "
    "not drink alcoholic beverages during pregnancy because of the risk of "
    "birth defects. (2) Consumption of alcoholic beverages impairs your "
    "ability to drive a car or operate machinery, and may cause health problems."
)

_FIELD_SCHEMA = """
Return ONLY this JSON -- no markdown, no explanation outside it:

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

_TTB_RULES = (
    "TTB mandatory label requirements (27 CFR Parts 4, 5, 7):\n"
    "  1. brand_name      -- producer/winery/distillery name\n"
    "  2. class_type      -- Standard of Identity per BAM (see rules below)\n"
    "  3. alcohol_content -- ABV percentage (e.g. 13.5% by volume)\n"
    "  4. net_contents    -- metric volume (e.g. 750 mL) -- may be embossed on bottle\n"
    "  5. bottler_info    -- producer name AND city/state or country\n"
    "  6. health_warning  -- FULL statutory text, both clauses, per 27 CFR Part 16\n"
    "\n"
    "CLASS & TYPE RULES -- Standard of Identity (CRITICAL)\n"
    "\n"
    "Wine (27 CFR Part 4):\n"
    "  VALID: Table Wine, Red Wine, White Wine, Rose Wine, Pink Wine,\n"
    "    Red Table Wine, White Table Wine, Rose Table Wine, Dessert Wine,\n"
    "    Sparkling Wine, Champagne, Port, Sherry, Merlot, Cabernet Sauvignon,\n"
    "    Chardonnay, Pinot Noir (varietal = class if 75%+ of stated grape)\n"
    "  NOTE: Red Wine, White Wine, Rose Wine, and Pink Wine ARE valid type\n"
    "    designations under 27 CFR 4.21(a)(1). Do NOT fail these designations.\n"
    "  INVALID: Generic 'Wine' alone without a color or class qualifier.\n"
    "\n"
    "Spirits (27 CFR Part 5):\n"
    "  VALID: Bourbon Whisky, Straight Bourbon Whisky, Vodka, Gin, Rum,\n"
    "    Tequila, Brandy, Cognac, Scotch Whisky, Irish Whiskey, Liqueur, etc.\n"
    "  INVALID: Generic 'Spirit' or 'Distilled Spirit' without a class.\n"
    "\n"
    "Malt Beverages (27 CFR Part 7):\n"
    "  VALID: Beer, Ale, Lager, Stout, Porter, Pilsner, Malt Liquor, IPA\n"
    "  INVALID: 'Malt Beverage' alone without a specific class.\n"
    "\n"
    "GOVERNMENT WARNING -- HARD BINARY RULE (27 CFR Part 16)\n"
    "\n"
    "The COMPLETE statutory text is required. BOTH clauses must appear:\n"
    "  Clause 1: 'According to the Surgeon General, women should not drink\n"
    "    alcoholic beverages during pregnancy because of the risk of birth defects.'\n"
    "  Clause 2: 'Consumption of alcoholic beverages impairs your ability to drive\n"
    "    a car or operate machinery, and may cause health problems.'\n"
    "\n"
    "PASS: Both clauses fully and completely readable in the label image.\n"
    "FAIL: Either clause absent, truncated, rotated unreadably, or obscured.\n"
    "  'Contains Sulfites' alone is ALWAYS FAIL.\n"
    "  If you cannot confirm both complete clauses are present, mark FAIL.\n"
    "\n"
    "CRITICAL: 'GOVERNMENT WARNING: Contains Sulfites' is a sulfite declaration,\n"
    "  NOT the health warning -- always FAIL.\n"
    "CRITICAL: If the health warning text is rotated or only partially visible\n"
    "  and you cannot read both full clauses -- always FAIL.\n"
    "\n"
    "NEVER mark health_warning REVIEW -- it is always PASS or FAIL.\n"
    "\n"
    "DECISION RULES\n"
    "\n"
    "APPROVE -- all mandatory fields present and fully compliant with 27 CFR\n"
    "REVIEW  -- a field is genuinely ambiguous (partially visible, conflicting data);\n"
    "           do NOT use REVIEW to soften a clear compliance failure\n"
    "DENY    -- a mandatory field is absent, incomplete, or uses an invalid\n"
    "           designation; OR health_warning is FAIL; OR class_type is FAIL\n"
    "\n"
    "FIELD STATUS RULES\n"
    "\n"
    "PASS   -- field is present and meets TTB Standard of Identity\n"
    "          (minor capitalization differences OK; paraphrasing is NOT OK)\n"
    "REVIEW -- field is genuinely ambiguous\n"
    "FAIL   -- field is absent, uses an invalid designation, or\n"
    "          (for health_warning) either statutory clause is missing\n"
    "\n"
    "health_warning has only two states: PASS or FAIL -- never REVIEW.\n"
)


def build_prompt_vision(
    n_labels,
    has_form,
    submission_id,
    ocr_supplement="",
):
    prompt = "You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance specialist.\n\n"
    prompt += _TTB_RULES
    if has_form:
        prompt += (
            "\nYou are shown " + str(n_labels + 1) + " images. The FIRST image is the TTB "
            "application form -- use it as your reference. The remaining "
            + str(n_labels) + " image(s) are the label(s)."
        )
    else:
        prompt += (
            "\nYou are shown " + str(n_labels) + " label image(s). "
            "No application form was provided -- assess against TTB requirements only."
        )
    if ocr_supplement.strip():
        prompt += (
            "\n\n--- SUPPLEMENTARY OCR TEXT (use to confirm your visual read) ---\n"
            + ocr_supplement
            + "\n--- END OCR TEXT ---\n\n"
            "Note: OCR is provided as a cross-check only. Your primary source is the image.\n"
            "If OCR and visual read conflict, trust your visual interpretation of the image.\n"
        )
    prompt += "\nSubmission ID: " + submission_id + "\n"
    prompt += _FIELD_SCHEMA
    return prompt


def build_prompt_ocr(
    ocr_text,
    n_labels,
    has_form,
    submission_id,
):
    prompt = "You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance specialist.\n\n"
    prompt += _TTB_RULES
    prompt += (
        "\nThe following text was extracted from the submission images by OCR "
        "(four rotation passes were run to capture sideways text like the government warning).\n"
        "Use this as your PRIMARY source. The images are also provided as visual backup.\n\n"
        "--- OCR EXTRACTED TEXT ---\n"
        + ocr_text
        + "\n--- END OCR TEXT ---\n\n"
        "OCR GUIDANCE: OCR output may be imperfect (fragmented lines, misread characters).\n"
        "Use your best judgment to reconstruct what the label likely says -- but only mark\n"
        "PASS if the evidence actually supports it. Do NOT assume a field is present\n"
        "because it would be expected on this type of label. Evaluate what is actually\n"
        "in the text.\n"
    )
    if has_form:
        prompt += (
            "\nThe first image/OCR block is the TTB application form -- use it as reference "
            "values. The remaining " + str(n_labels) + " image(s)/block(s) are the label(s)."
        )
    else:
        prompt += "\nNo application form was provided. Assess against TTB requirements only."
    prompt += "\nSubmission ID: " + submission_id + "\n"
    prompt += _FIELD_SCHEMA
    return prompt
