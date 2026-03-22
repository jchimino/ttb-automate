"""Minimal OCR microservice — POST /extract → { pages: [{index, text}] }

Runs Tesseract at all four rotations and COMBINES the results.
Picking only the "best" rotation caused the health warning (rotated 90°) to
win, making the main label text (Red Wine, ABV, etc.) disappear from output.
Combining all rotations gives the LLM the full picture.
"""
import io
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter
from fastapi import FastAPI, File, UploadFile

app = FastAPI()


def _ocr_all_rotations(img: Image.Image) -> str:
    """Run Tesseract at 0°/90°/180°/270° and return combined unique lines."""
    gray = img.convert("L")
    gray = ImageEnhance.Contrast(gray).enhance(2.0)
    gray = gray.filter(ImageFilter.SHARPEN)

    seen: set[str] = set()
    parts: list[str] = []

    for angle in (0, 90, 180, 270):
        rotated = gray.rotate(angle, expand=True)
        text = pytesseract.image_to_string(rotated, config="--psm 3").strip()
        if not text:
            continue
        # Deduplicate lines already captured by another rotation
        new_lines = [
            line for line in text.splitlines()
            if line.strip() and line.strip().lower() not in seen
        ]
        for line in new_lines:
            seen.add(line.strip().lower())
        if new_lines:
            parts.append(f"[rotation {angle}°]\n" + "\n".join(new_lines))

    return "\n\n".join(parts)


@app.post("/extract")
async def extract(images: list[UploadFile] = File(...)):
    pages = []
    for i, f in enumerate(images):
        img = Image.open(io.BytesIO(await f.read()))
        text = _ocr_all_rotations(img)
        pages.append({"index": i, "text": text})
    return {"pages": pages}


@app.get("/health")
async def health():
    return {"status": "ok"}
