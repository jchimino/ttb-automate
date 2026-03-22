FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python -m venv $VIRTUAL_ENV
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pytesseract Pillow python-multipart

COPY main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
