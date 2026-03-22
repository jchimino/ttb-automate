FROM ollama/ollama:latest
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY pull-models.sh /pull-models.sh
RUN chmod +x /pull-models.sh
