"""
cfr_loader.py
=============
Loads 27 CFR regulatory text into pgvector for retrieval-augmented assessment.

Runs once at assess-service startup (called from main.py detect_strategy).
If cfr_chunks already has rows, loading is skipped — idempotent.

Embedding model: nomic-embed-text via Ollama /api/embeddings (384 dimensions).
If Ollama is not yet ready, loader retries with backoff and gives up gracefully
so the assess service still starts — RAG is additive, never blocking.

To refresh the CFR corpus (e.g. after a regulation update):
  docker exec ttb-assess python cfr_loader.py --force
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Optional

import httpx
import psycopg2

OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://ollama:11434")
DATABASE_URL = os.getenv("DATABASE_URL")
EMBED_MODEL  = "nomic-embed-text"   # 384-dim, fast, runs on CPU
EMBED_DIM    = 384

# ── CFR corpus ────────────────────────────────────────────────────────────────
# Each entry is one chunk: a discrete, self-contained regulatory rule.
# Sourced from eCFR 27 CFR Parts 4, 5, 7, and 16.
# Format: (cfr_part, section, commodity, topic, text)
#
# Commodity values: "Wine" | "Spirits" | "Malt" | None (applies to all)
# Topic values match the field names used in the prompt schema.
#
# Keeping chunks at ~150-250 words each ensures focused retrieval.
# ─────────────────────────────────────────────────────────────────────────────

CFR_CHUNKS: list[tuple[str, str, Optional[str], str, str]] = [

    # ── 27 CFR Part 16 — Government Health Warning (all commodities) ──────────
    ("16", "Part 16", None, "health_warning",
     "GOVERNMENT WARNING (27 CFR Part 16 — Alcoholic Beverage Labeling Act): "
     "All containers of alcoholic beverages containing 0.5% or more alcohol by "
     "volume must display the following mandatory health warning statement in "
     "conspicuous and legible type: "
     "'GOVERNMENT WARNING: (1) According to the Surgeon General, women should "
     "not drink alcoholic beverages during pregnancy because of the risk of "
     "birth defects. (2) Consumption of alcoholic beverages impairs your ability "
     "to drive a car or operate machinery, and may cause health problems.' "
     "Both clauses are mandatory. The word 'GOVERNMENT WARNING' must appear as "
     "the heading. Omission of either clause, or substitution of the heading, "
     "constitutes a labeling violation. The warning may appear on the back label "
     "and may be in smaller type, but must be legible. 27 CFR 16.21, 16.22."),

    ("16", "16.32", None, "health_warning",
     "Health Warning Placement and Type Size (27 CFR 16.32): The mandatory "
     "health warning statement must appear on a separate label or be set apart "
     "from other information. The word 'GOVERNMENT WARNING' must appear in "
     "capital letters. The statement must appear in Roman type (upright, not "
     "italic) on a contrasting background. The minimum type size is 1mm (about "
     "3 points) for containers of 8 fl oz or less, and 2mm for larger containers. "
     "A sulfite declaration ('Contains Sulfites') is a SEPARATE requirement under "
     "27 CFR 4.32(e) and does NOT satisfy or substitute for the Part 16 health "
     "warning. A label showing only 'GOVERNMENT WARNING: Contains Sulfites' is "
     "non-compliant — both the Surgeon General clause and the impairment clause "
     "are still required."),

    # ── 27 CFR Part 4 — Wine ──────────────────────────────────────────────────
    ("4", "4.21", "Wine", "class_type",
     "Wine Class and Type Designations (27 CFR 4.21): The class and type "
     "designation must appear on the brand label. Valid class designations for "
     "still grape wine include: Table Wine (also called Light Wine), Dessert Wine, "
     "and Aperitif Wine. The term 'Red Wine' is explicitly listed as an allowable "
     "type designation under 27 CFR 4.21(a)(1) — it refers to a still grape wine "
     "with a red color derived from the grape skins. 'Red Wine', 'White Wine', "
     "'Rosé Wine', and 'Pink Wine' are valid type designations. A label showing "
     "'Red Wine' satisfies the class/type requirement without needing to add "
     "'Table Wine' — however, 'Table Wine' may be added for clarity. Wine bearing "
     "a varietal designation (e.g. Cabernet Sauvignon, Merlot, Chardonnay) must "
     "contain at least 75% of that variety and satisfies the class/type requirement "
     "by virtue of the varietal name."),

    ("4", "4.21(e)", "Wine", "class_type",
     "Appellation of Origin and Vintage Date Requirements (27 CFR 4.21(e), 4.25, "
     "4.26, 4.27): If a vintage date is shown on a wine label, an appellation of "
     "origin is mandatory. The appellation must be a truthful, specific geographic "
     "designation. 'Valle Central, Chile' is an example of an acceptable appellation "
     "of origin for an imported wine. When both a vintage date and an appellation "
     "are shown, at least 95% of the wine must be derived from grapes grown in "
     "the labeled appellation and at least 95% must be from the labeled vintage "
     "year. Foreign appellations must comply with the laws of the producing country. "
     "Showing 'Valle Central, Chile' with '2021 Vintage' is compliant if the wine "
     "meets the 95% origin and vintage requirements."),

    ("4", "4.36", "Wine", "alcohol_content",
     "Alcohol Content Labeling for Wine (27 CFR 4.36): Table wine (7-14% ABV) "
     "may state the specific alcohol content as a percentage or may use the "
     "designation 'Table Wine' or 'Light Wine' in lieu of a specific percentage. "
     "Dessert wine (more than 14% ABV) must state the specific alcohol content. "
     "The tolerance for wines stating a specific percentage is plus or minus 0.3% "
     "of the stated amount. For wines labeled 'Table Wine' or 'Light Wine', the "
     "actual content must be between 7% and 14%. A wine labeled 13.5% by volume "
     "is compliant if the actual content is between 13.2% and 13.8%."),

    ("4", "4.35", "Wine", "bottler_info",
     "Name and Address Requirements for Wine (27 CFR 4.35): The label must show "
     "the name and address of the bottler, producer, packer, or importer. The "
     "address must include the city and state (for domestic wine) or city and "
     "country (for imported wine). Acceptable phrases preceding the name include: "
     "'Produced and Bottled by', 'Bottled by', 'Vinted and Bottled by', "
     "'Cellared and Bottled by', 'Packed by', 'Imported by', or 'Distributed by'. "
     "For imported wine, the importer's name and U.S. address, or the foreign "
     "producer's name and foreign address, must appear. 'Produced by Bella Vista "
     "Estates Valle Central, Chile' satisfies this requirement for an imported wine."),

    ("4", "4.37", "Wine", "net_contents",
     "Net Contents for Wine (27 CFR 4.37): The net contents must be stated in "
     "metric measure. Standard metric sizes for wine are: 100 mL, 187 mL, 375 mL, "
     "500 mL, 750 mL, 1 L, 1.5 L, 3 L. The net contents may be stated on a "
     "separate label or may be embossed on the bottle — embossed net contents "
     "satisfy the labeling requirement. '750 mL', '750ml', and '750 MILLILITERS' "
     "are all equivalent and acceptable."),

    ("4", "4.32(e)", "Wine", "sulfites",
     "Sulfite Declaration for Wine (27 CFR 4.32(e)): Wine containing 10 parts "
     "per million (ppm) or more of sulfur dioxide must display the statement "
     "'Contains Sulfites' or 'Contains (a) Sulfiting Agent(s)'. This is a "
     "SEPARATE requirement from the government health warning under 27 CFR Part 16. "
     "The presence of a sulfite declaration does not satisfy the Part 16 health "
     "warning requirement, and vice versa. Most commercially produced wines "
     "contain sulfites and must include this statement."),

    ("4", "4.33", "Wine", "brand_name",
     "Brand Name Requirements for Wine (27 CFR 4.33): The brand name must be "
     "shown on the brand label (typically the front label). The brand name must "
     "not be misleading as to the age, origin, identity, or other characteristics "
     "of the wine. A brand name that is a geographic name may require an appellation "
     "of origin. The brand name is distinct from the producer or bottler name and "
     "address, which is required separately under 27 CFR 4.35."),

    # ── 27 CFR Part 5 — Distilled Spirits ────────────────────────────────────
    ("5", "5.35", "Spirits", "class_type",
     "Distilled Spirits Class and Type Designations (27 CFR 5.35): The class and "
     "type designation must appear on the brand label. Valid classes include: "
     "Whisky (Bourbon Whisky, Straight Bourbon Whisky, Tennessee Whisky, Rye "
     "Whisky, Scotch Whisky, Irish Whiskey, Blended Whisky), Vodka, Gin, Rum, "
     "Brandy (Cognac, Armagnac, Pisco), Tequila, Mezcal, Liqueur (also Cordial), "
     "Neutral Spirits, Grain Spirits. The class designation must match the "
     "Standard of Identity exactly. Generic terms like 'Spirit' or 'Distilled "
     "Spirit' without a recognized class name do not satisfy this requirement."),

    ("5", "5.37", "Spirits", "alcohol_content",
     "Alcohol Content for Distilled Spirits (27 CFR 5.37): The alcohol content "
     "must be stated as percent alcohol by volume (e.g., '40% ALC. BY VOL.' or "
     "'ALC. 40% BY VOL.'). Proof may be shown optionally; if shown, proof must "
     "equal twice the percentage of alcohol by volume. The tolerance is plus or "
     "minus 0.3% of the stated percentage. Spirits must contain at least 20% "
     "alcohol by volume."),

    ("5", "5.36", "Spirits", "bottler_info",
     "Name and Address for Distilled Spirits (27 CFR 5.36): The label must show "
     "the name and address of the distiller, bottler, or importer. Acceptable "
     "phrases include 'Distilled by', 'Bottled by', 'Blended and Bottled by', "
     "'Imported by', 'Produced by'. The address must include city and state for "
     "domestic products, or city and country for imports."),

    ("5", "5.38", "Spirits", "net_contents",
     "Net Contents for Distilled Spirits (27 CFR 5.38): Net contents must be "
     "stated in metric measure. Standard metric sizes: 50 mL, 100 mL, 200 mL, "
     "375 mL, 500 mL, 750 mL, 1 L, 1.75 L. Net contents may be blown, etched, "
     "or embossed on the container and need not appear on the label if stated "
     "on the container itself."),

    # ── 27 CFR Part 7 — Malt Beverages ───────────────────────────────────────
    ("7", "7.64", "Malt", "class_type",
     "Malt Beverage Class Designations (27 CFR 7.64): The class designation "
     "must accurately describe the product. Valid designations include: Beer, "
     "Ale, Porter, Stout, Lager, Pilsner, Pilsener, Bock Beer, Malt Liquor, "
     "Malt Beverage, Malt Cooler. A designation of the specific beer style is "
     "acceptable (e.g., India Pale Ale, Hefeweizen). The class must not be "
     "misleading as to the nature of the product."),

    ("7", "7.71", "Malt", "alcohol_content",
     "Alcohol Content for Malt Beverages (27 CFR 7.71): Alcohol content is "
     "optional on malt beverage labels at the federal level (state law may "
     "require it). If stated, the tolerance is plus or minus 0.15% of the "
     "stated percentage — stricter than the 0.3% tolerance for wine and spirits. "
     "Alcohol content may be stated as percent alcohol by volume or as percent "
     "alcohol by weight. Malt beverages must contain at least 0.5% alcohol by "
     "volume to be subject to TTB regulation."),
]


# ── Embedding via Ollama ──────────────────────────────────────────────────────

async def _embed(text: str, client: httpx.AsyncClient) -> list[float] | None:
    """Get embedding vector from Ollama nomic-embed-text."""
    try:
        r = await client.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=30.0,
        )
        if r.is_success:
            return r.json().get("embedding")
    except Exception as e:
        print(f"[cfr_loader] Embedding error: {e}")
    return None


def _pull_embed_model_sync() -> bool:
    """Pull nomic-embed-text synchronously by streaming the response.

    The Ollama /api/pull endpoint streams NDJSON lines while downloading.
    We must read and consume ALL lines until we see {"status":"success"}
    (or the connection closes).  Simply opening the request without reading
    leaves the download stalled.

    Returns True if the final status line is 'success', False otherwise.
    """
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/pull",
            data=json.dumps({"name": EMBED_MODEL, "stream": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            last_status = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    status = obj.get("status", "")
                    if status:
                        last_status = status
                except json.JSONDecodeError:
                    pass
            if last_status == "success":
                print(f"[cfr_loader] {EMBED_MODEL} pull complete.")
                return True
            else:
                print(f"[cfr_loader] {EMBED_MODEL} pull ended with status: {last_status!r}")
                return False
    except Exception as e:
        print(f"[cfr_loader] Could not pull {EMBED_MODEL}: {e}")
        return False


# ── Database helpers ──────────────────────────────────────────────────────────

def _get_conn():
    return psycopg2.connect(DATABASE_URL)


def _is_loaded() -> bool:
    """Return True if cfr_chunks already has data (skip reload)."""
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM cfr_chunks")
        count = cur.fetchone()[0]
        cur.close(); conn.close()
        return count > 0
    except Exception:
        return False


def _insert_chunk(
    conn,
    cfr_part: str,
    section: str,
    commodity: Optional[str],
    topic: str,
    text: str,
    embedding: list[float],
    source: str = "eCFR",
):
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO cfr_chunks
             (cfr_part, section, commodity, topic, chunk_text, embedding, source)
           VALUES (%s, %s, %s, %s, %s, %s::vector, %s)""",
        (cfr_part, section, commodity, topic, text,
         "[" + ",".join(str(x) for x in embedding) + "]",
         source),
    )
    conn.commit()
    cur.close()


# ── Main loader ───────────────────────────────────────────────────────────────

async def load_cfr_chunks(force: bool = False) -> bool:
    """
    Embed and store all CFR_CHUNKS in pgvector.

    Parameters
    ----------
    force : if True, truncate existing rows and reload.

    Returns True on success, False if embedding service unavailable.
    """
    import asyncio

    if not force and _is_loaded():
        print(f"[cfr_loader] cfr_chunks already populated — skipping (use --force to reload)")
        return True

    print(f"[cfr_loader] Pulling embedding model {EMBED_MODEL} ...")
    _pull_embed_model_sync()

    async with httpx.AsyncClient() as client:
        # Verify embed model is responsive (12 attempts × 15 s = 3 min max)
        for attempt in range(12):
            emb = await _embed("test", client)
            if emb and len(emb) == EMBED_DIM:
                print(f"[cfr_loader] {EMBED_MODEL} ready (dim={len(emb)})")
                break
            print(f"[cfr_loader] Waiting for {EMBED_MODEL} ... attempt {attempt + 1}/12")
            await asyncio.sleep(15)
        else:
            print(f"[cfr_loader] {EMBED_MODEL} not available — skipping RAG load (assess will run without RAG)")
            return False

        if force:
            try:
                conn = _get_conn()
                conn.cursor().execute("TRUNCATE cfr_chunks RESTART IDENTITY")
                conn.commit(); conn.close()
                print("[cfr_loader] Existing chunks cleared.")
            except Exception as e:
                print(f"[cfr_loader] Truncate failed: {e}")

        conn = _get_conn()
        loaded = 0
        for cfr_part, section, commodity, topic, text in CFR_CHUNKS:
            emb = await _embed(text, client)
            if emb is None:
                print(f"[cfr_loader] WARNING: Could not embed {section} — skipping")
                continue
            _insert_chunk(conn, cfr_part, section, commodity, topic, text, emb)
            loaded += 1
            print(f"[cfr_loader] Loaded {section} ({topic})")

        conn.close()
        print(f"[cfr_loader] Done — {loaded}/{len(CFR_CHUNKS)} chunks loaded into pgvector.")
        return True


# ── Retrieval ─────────────────────────────────────────────────────────────────

async def retrieve_relevant_chunks(
    query: str,
    commodity: Optional[str] = None,
    top_k: int = 4,
) -> list[dict]:
    """
    Retrieve the top-k CFR chunks most relevant to the query.

    Used by main.py before each assessment to inject grounded regulatory
    context into the prompt.

    Parameters
    ----------
    query     : free-text description of what's being assessed
                e.g. "Red Wine class type designation wine label"
    commodity : "Wine" | "Spirits" | "Malt" | None
                If provided, boosts chunks for that commodity.
    top_k     : number of chunks to return (default 4)

    Returns a list of dicts with keys: section, topic, commodity, chunk_text
    """
    import asyncio

    async with httpx.AsyncClient() as client:
        query_emb = await _embed(query, client)

    if not query_emb:
        return []  # embedding service unavailable — degrade gracefully

    emb_str = "[" + ",".join(str(x) for x in query_emb) + "]"

    try:
        conn = _get_conn()
        cur = conn.cursor()

        # If commodity is known, retrieve commodity-specific + universal chunks
        if commodity:
            cur.execute(
                """SELECT section, topic, commodity, chunk_text,
                          1 - (embedding <=> %s::vector) AS similarity
                   FROM cfr_chunks
                   WHERE commodity = %s OR commodity IS NULL
                   ORDER BY embedding <=> %s::vector
                   LIMIT %s""",
                (emb_str, commodity, emb_str, top_k),
            )
        else:
            cur.execute(
                """SELECT section, topic, commodity, chunk_text,
                          1 - (embedding <=> %s::vector) AS similarity
                   FROM cfr_chunks
                   ORDER BY embedding <=> %s::vector
                   LIMIT %s""",
                (emb_str, emb_str, top_k),
            )

        rows = cur.fetchall()
        cur.close(); conn.close()

        return [
            {
                "section":    row[0],
                "topic":      row[1],
                "commodity":  row[2],
                "chunk_text": row[3],
                "similarity": float(row[4]),
            }
            for row in rows
        ]
    except Exception as e:
        print(f"[cfr_loader] Retrieval error: {e}")
        return []


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    parser = argparse.ArgumentParser(description="Load CFR chunks into pgvector")
    parser.add_argument("--force", action="store_true", help="Truncate and reload all chunks")
    args = parser.parse_args()
    asyncio.run(load_cfr_chunks(force=args.force))
