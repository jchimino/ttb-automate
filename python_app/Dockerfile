FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv $VIRTUAL_ENV

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8004

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8004 --workers ${WORKERS:-2}"]
