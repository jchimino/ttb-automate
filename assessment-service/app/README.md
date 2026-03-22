# TTB Automate — AI-Powered Alcohol Label Compliance System

An intelligent, fully containerized proof-of-concept that automates Certificate of Label Approval (COLA) compliance screening for alcohol beverage labels — enforcing conformity with 27 CFR regulations using on-premises AI, with zero reliance on external cloud APIs or outbound data transfers.

## Table of Contents

- [Overview](#overview)
- [Design Goals](#design-goals)
- [Technology Stack](#technology-stack)
- [System Architecture](#system-architecture)
- [Assessment Service Flow](#assessment-service-flow)
- [Features](#features)
- [Compliance Checks (27 CFR)](#compliance-checks-27-cfr)
- [Setup Instructions](#setup-instructions)
- [PostgreSQL Access](#postgresql-access)
- [Deployment Context](#deployment-context)
- [Technical Approach](#technical-approach)
- [Engineering & Design Decisions](#engineering--design-decisions)
- [Trade-offs & Future Direction](#trade-offs--future-direction)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Security](./SECURITY.md)

---

## Overview

TTB Automate is a prototype built to demonstrate how the TTB's label approval process could be modernized using local AI — without sending label images or regulatory data to a third-party cloud service. The system reads label images, classifies the beverage type, and verifies each required field against the applicable 27 CFR regulation.

For compliant labels, the AI determination is the decision — APPROVE is issued without human involvement. For borderline cases, the AI annotates the application and routes it to staff; the reviewer makes the final call. For clear failures, the application is returned to the applicant with specific CFR citations. The goal is not to replace human reviewers but to ensure staff only touch the cases that genuinely require their judgment.

### Key Features

- **Local AI, no outbound data** — label images are processed entirely on the host machine; nothing is transmitted to Google, Anthropic, or any cloud API. This also addresses the instructions limitation that API calls to certain domains may be limited.
- **Automated decision routing** — n8n workflow dispatches APPROVE / REVIEW / DENY outcomes automatically.
- **Vision model reads labels directly** — llava:7b sees and interprets label images as a human reviewer would, including handling imperfect photos. This feature addresses user's request to handle imperfect labels.
- **OCR fallback for CPU-only environments** — Tesseract reads all four label rotations when no GPU is present. In production, this capability will be unnecessary, reserved for redundancy if self hosted server hardware that includes a GPU fails.
- **Two-step BAM routing** — commodity classification (Spirits / Wine / Malt) followed by CFR-specific field verification
- **Calculated compliance scoring** — point deductions per field failure; no hardcoded scores
- **Batch processing** — submit up to 50 labels at once with consolidated results and PDF export
- **Role-based portals** — separate industry and staff interfaces; demo accounts included
- **Full audit trail** — every decision written to PostgreSQL; queryable at any time
- **Malware quarantine** — uploaded files scanned before processing; threats isolated and flagged for admin review

For security architecture and network isolation details, see [SECURITY.md](./SECURITY.md).

---

## Design Goals

These principles guided every architectural decision in this prototype.

**Simplicity.** The submission and review interface is intentionally minimal. Industry members upload a label, get an instant result with field-by-field findings and CFR citations, and can re-submit if corrections are needed. Staff see only the applications that require a human decision. Pre-built demo accounts let evaluators step into any role in seconds — no onboarding required.

**Speed as a filter, not a bottleneck.** TTB currently reviews every COLA application manually. TTB Automate handles the clear cases so staff don't have to. A fully compliant label is approved by the AI — that is the decision, not a pre-check before another review. A borderline label lands in the staff queue already annotated with field-by-field findings and the specific CFR citations that triggered each flag. A clear failure is returned to the applicant immediately with citations and the ability to correct and resubmit. Staff time is reserved for cases that require human judgment.

**Limited outbound traffic.** All AI inference runs inside Docker on the local machine. No label images or applicant data are transmitted to any external service. The only outbound traffic at steady state is the optional virus scan API call — which degrades gracefully if unavailable — and model downloads that happen once at first boot and are then cached permanently.

**Imperfect-image tolerance.** Labels are photographed in real-world conditions — poor lighting, slight rotation, glare, low resolution. The system is built to handle this. See [Handling Imperfect Label Images](#handling-imperfect-label-images).

---

## Technology Stack

### Backend

| Technology | Purpose |
|------------|---------|
| Python 3.11 + FastAPI | Web application and REST API |
| PostgreSQL 16 | Audit log and application database |
| httpx | Async HTTP client for inter-service calls |
| Jinja2 | Server-rendered HTML templates |

### AI / ML

| Technology | Purpose |
|------------|---------|
| Ollama | Local LLM inference server — runs entirely inside Docker |
| llava:7b | Multimodal vision model — reads label images directly; GPU preferred |
| qwen2.5:14b | Text model — reconcile/CPU path; parses messy OCR output into structured fields |
| Tesseract 5 | OCR engine; all 4 rotations (0°/90°/180°/270°) combined for accuracy |

### Infrastructure

| Technology | Purpose |
|------------|---------|
| Docker Compose | Orchestrates all 6 services as a single deployable unit |
| n8n | Visual workflow automation — APPROVE / REVIEW / DENY routing; 400+ integrations |
| NVIDIA Container Toolkit | Optional — passes GPU through to Ollama for vision path |

---

## System Architecture

```
                         ┌────────────────────────────────────────────────┐
                         │              TTB Automate Stack                │
                         │                                                │
  Browser ──────────────▶│  ttb-app (FastAPI :8004)                       │
                         │    ├── Industry portal  /industry/dashboard    │
                         │    ├── Staff portal     /staff/dashboard       │
                         │    ├── Label verifier   /verify                │
                         │    └── API              /api/verify-label      │
                         │                   │                            │
                         │                   ▼                            │
                         │  assess (:8000)                                │
                         │    │                                           │
                         │    ├── ollama (:11434)                         │
                         │    │     ├── llava:7b      (vision path)       │
                         │    │     └── qwen2.5:14b   (reconcile path)    │
                         │    │                                           │
                         │    ├── ocr (:8001)         (Tesseract OCR)     │
                         │    │                                           │
                         │    └── postgres (:5432)    (audit log)         │
                         │                                                │
  n8n (:5678) ◀──────────│  JSON webhook response                         │
                         └────────────────────────────────────────────────┘
```

All six services communicate over an internal Docker bridge network (`ttb-network`). Only `ttb-app` (port 8004) and `n8n` (port 5678) are exposed to the host. The database, assessment service, OCR service, and Ollama are all internal — unreachable from outside the container network.

### Service Dependency Order

```
postgres (healthy)
    └── ttb-app  (starts immediately — UI live while models load)
    └── ollama   (healthy)
            └── ollama-pull  (pulls llava:7b + qwen2.5:14b, exits 0)
                    └── assess  (healthy)
                    └── ocr     (healthy)
                            └── n8n  (starts after assess is healthy)
```

The web UI is available as soon as PostgreSQL is healthy — typically within 30 seconds of `docker compose up`. The `/assess` endpoint returns HTTP 502 until model download completes. On first boot this takes 10–20 minutes depending on network speed; subsequent starts load from the cached `ollama-data` volume in seconds.

### Decision Routing (n8n)

After the `assess` service returns a decision, `ttb-app` fires a webhook to n8n, which routes the outcome and sets application status automatically.

```
┌───────────────────────────────────────────────────────────────────────┐
│                           n8n Workflow                                 │
│                                                                        │
│  Webhook ──────────▶  POST /assess  ──────▶  Decision?               │
│  POST                 Local LLM              APPROVE check             │
│  /ttb-submission      service                      │                   │
│                                               true ├──▶ Handle APPROVE │
│                                                    │    ttb_status:    │
│                                                    │    approved       │
│                                               false│                   │
│                                                    ▼                   │
│                                            Review or Deny?             │
│                                            REVIEW check                │
│                                               true ├──▶ Handle REVIEW  │
│                                                    │    ttb_status:    │
│                                                    │    pending        │
│                                               false│                   │
│                                                    ▼                   │
│                                            Handle DENY                 │
│                                            ttb_status: rejected        │
│                                                    │                   │
│  Respond ◀──────────────────────────────────────────                  │
│  JSON → caller                                                         │
└───────────────────────────────────────────────────────────────────────┘
```

| Decision | `ttb_status` | What happens |
|----------|-------------|--------------|
| APPROVE | `approved` | Label meets all 27 CFR requirements — no staff action needed |
| REVIEW | `pending` | One or more warnings flagged — routed to staff queue with AI notes |
| DENY | `rejected` | Critical compliance failure — returned to applicant with CFR citations |

The workflow (`n8n/workflows/ttb-assessment.json`) is imported automatically at container startup. Extending it — adding email alerts, Slack routing, CRM writes — requires no code changes, only n8n UI configuration. n8n supports 400+ integrations.

---

## Assessment Service Flow

The `assess` service is the core LLM orchestrator. It selects a processing strategy at startup — vision (GPU) or reconcile (CPU) — and applies it to every submission.

```
┌────────────────────────────────────────────────────────────────────────┐
│                      assess service (main.py)                          │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  On startup: inspect OLLAMA_MODEL → select strategy                    │
│                                                                        │
│  ┌──────────────────┐  strategy  ┌──────────────────────────────────┐  │
│  │    Startup       │ ─────────▶ │  vision    (GPU / llava:7b)      │  │
│  │  detect model    │            ├──────────────────────────────────┤  │
│  │    or GPU        │ ─────────▶ │  reconcile (CPU / OCR path)      │  │
│  └──────────────────┘            └─────────────────┬────────────────┘  │
│                                                    │                   │
│  ┌──────────────────┐                             │ dispatch           │
│  │   POST /assess   │ ──────────────────────────────▶┤                 │
│  │  label_images    │                    ┌─────────┴──────────┐        │
│  └──────────────────┘                    │                    │        │
│                                  call_ollama           call_ocr        │
│                                  (llava:7b)         (Tesseract)        │
│                                                           │            │
│                                                    call_ollama         │
│                                                    (qwen2.5:14b)       │
│                                                           │            │
│  ┌──────────────────┐             JSON ──────────────────┘             │
│  │    prompt.py     │ ──────────▶ → n8n webhook                        │
│  │  TTB rules + CFR │                                                  │
│  └──────────────────┘                                                  │
│                                                                        │
│  ┌──────────────────┐   ┌──────────────────────────────────────┐       │
│  │   models.py      │──▶│  log_decision → PostgreSQL audit log  │       │
│  │ parse + fallback │   └──────────────────────────────────────┘       │
│  └──────────────────┘                                                  │
│                                                                        │
│  GET /health → { "status": "ok" }                                      │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

**Vision path (GPU / llava:7b):** The raw label image is sent directly to the vision model. No OCR is performed — the model reads text, interprets layout, identifies the beverage class, and evaluates each mandatory field in a single pass, the same way a trained human reviewer would scan a label.

**Reconcile path (CPU / qwen2.5:14b):** Tesseract extracts text from all four label rotations (0°/90°/180°/270°), combining unique lines so that no rotated content — such as a health warning printed sideways — is lost. The combined text is then fed to qwen2.5:14b, which interprets the extracted content and maps it against CFR requirements. This path is slower (20–60 seconds on CPU) but runs on any hardware.

---

## Features

### Simplified Interface

The prototype intentionally minimizes friction. There is no complex onboarding — evaluators use pre-built demo accounts to step into any role immediately. The industry-facing interface walks applicants through a submission in a few steps: upload a label, see results, re-submit if needed. The staff interface shows only what a reviewer needs: the label, the AI findings, and action buttons. Nothing more.

| Role | Access |
|------|--------|
| Industry | Submit labels, track status, communicate with staff |
| Staff | Review queue with AI annotations, approve/reject/return |
| Admin | All of the above + audit log + quarantine management |

Demo credentials are in [Setup Instructions](#demo-accounts).

### Core Verification Modes

**Single Label** — upload one image for instant compliance analysis.

**Split Label** — upload front and back images separately for products with two-panel label designs.

**Batch Processing** — submit up to 50 label images in a single session. Intended for manufacturers filing multiple SKUs, importers with multi-product portfolios, or staff bulk-reverifying an existing application set. Results consolidate into a table view exportable as PDF or CSV.

| Path | Per-image time | 50-image batch |
|------|---------------|----------------|
| Vision (GPU) | 3–8 seconds | ~5–7 minutes |
| Reconcile (CPU) | 20–60 seconds | ~30–50 minutes |

Sequential processing is deliberate for this prototype — it avoids overwhelming a single Ollama instance. A production deployment with multiple GPU-backed replicas could parallelize submissions proportionally.

**COLA Comparison** — compare a proposed label against a previously approved COLA to determine whether the changes require a new submission or qualify as an allowable revision.

### Staff Portal

Staff receive REVIEW cases pre-annotated by the AI. Each case shows the label image, a field-by-field compliance table with CFR citations, a risk score, and a plain-language summary of findings. The reviewer reads the AI's notes, makes a judgment call, and approves, rejects, or returns the application — with a free-text note if needed. All actions are logged with timestamp, user ID, and rationale.

### Imperfect Image Handling

Real-world label photos are rarely perfect — bottles photographed at angles, under store lighting, with glare, or at low resolution. The system addresses this at multiple levels.

**llava:7b (GPU path):** llava interprets label images contextually, the same way a human would. It can infer partially obscured text, recognize a blurry "Surgeon General" warning as present even if not sharp, and reason about spatial layout. When a field appears to exist but is unclear, it flags WARNING rather than FAIL — avoiding false rejections from imperfect photography.

**Multi-rotation OCR (CPU / reconcile path):** Tesseract runs at 0°, 90°, 180°, and 270°, combining unique lines from all four passes. Early versions selected a single "best" rotation, which caused the sideways-printed health warning to be discarded. All-rotation processing ensures nothing is lost regardless of label orientation.

**Lenient field evaluation:** If a mandatory field appears present but is uncertain due to blur, low contrast, or embossing, the model is instructed to PASS rather than FAIL. A FAIL is reserved for fields that are genuinely absent or demonstrably incorrect.

**Image preprocessing:** Before OCR, images are converted to grayscale, contrast-enhanced (2×), and sharpened — improving Tesseract accuracy on low-contrast and embossed labels.

---

## Compliance Checks (27 CFR)

| Check | Description | Regulation |
|-------|-------------|------------|
| Brand Name | Must be present and not misleading | §5.34, §4.33, §7.63 |
| Class & Type | Must match Standard of Identity exactly | §5.35, §4.34, §7.64 |
| Alcohol Content | ABV declared with CFR-specific tolerance | §5.37, §4.36, §7.71 |
| Net Contents | Metric volume; common conversions handled | §5.38, §4.37, §7.73 |
| Government Warning | ABLA text including "Surgeon General" and "Birth Defects" | 27 CFR §16 |
| Name & Address | Bottler/importer with city and state | §5.36, §4.35, §7.65 |
| Country of Origin | Required for imported products | §5.36(d), §4.35(d) |
| Sulfites | "Contains Sulfites" required for wine >10ppm | §4.32(e) |

### ABV Tolerances by Commodity

| Commodity | CFR Part | Tolerance | Valid ABV Range |
|-----------|----------|-----------|-----------------|
| Spirits | Part 5 | ±0.3% | 20–95% |
| Wine | Part 4 | ±0.3% | 7–24% |
| Malt Beverage | Part 7 | ±0.15% | 0.5–15% |

Malt beverages have a stricter tolerance (±0.15%) than spirits and wine (±0.3%) per 27 CFR 7.71.

### Compliance Scoring

Scores are computed from individual field results. There are no hardcoded outcomes.

| Deduction event | Points |
|----------------|--------|
| Government Warning FAIL | −25 (ABLA — highest priority) |
| Any other mandatory field FAIL | −20 each |
| Non-mandatory field FAIL | −10 each |
| Any field WARNING | −5 each |
| ABV outside tolerance | −15 |

| Score | Status | Action |
|-------|--------|--------|
| ≥ 80 | PASS | Auto-approved |
| 50–79 | WARNING | Routed to staff review queue |
| < 50 | FAIL | Returned to applicant |

A non-empty `critical_failures` list forces FAIL regardless of numeric score.

---

## Setup Instructions

### Prerequisites

**Minimum (CPU-only, reconcile path):**

| Requirement | Minimum |
|-------------|---------|
| OS | macOS 12+, Ubuntu 22.04+, or Windows 11 with WSL2 |
| RAM | 12 GB available |
| Disk | 20 GB free (models + images) |
| Docker | Docker Desktop 4.x or Docker Engine + Compose plugin |
| Network | Broadband (one-time ~11 GB model download) |

**Recommended (GPU, vision path — faster and more accurate):**

| Requirement | Recommended |
|-------------|-------------|
| RAM | 32 GB |
| GPU | NVIDIA with 8 GB+ VRAM (RTX 3060 or better) |
| Disk | 40 GB free |
| Driver | NVIDIA Container Toolkit installed |

On CPU-only hardware, expect 20–60 seconds per label via the OCR reconcile path. With a GPU, vision path response times drop to 3–8 seconds per label.

### Quick Start

```bash
# 1. Clone the repository
git clone <repository-url>
cd ttb-automate

# 2. Copy environment file
#    Demo mode works without Supabase — leave keys blank to use local demo auth
cp .env.example .env

# 3. First boot — builds images and downloads models (~11 GB, one-time)
docker compose up --build

# 4. Open the web UI
open http://localhost:8004        # industry / staff / admin portals
open http://localhost:5678        # n8n workflow editor (admin / ttbexpress)
```

Model downloads happen inside the `ollama-pull` container and are cached in the `ollama-data` Docker volume. Subsequent `docker compose up` commands start in seconds — no re-download.

### Demo Accounts

| Role | Email | Password |
|------|-------|----------|
| **Admin** | admin@ttb.gov | Password1 |
| **Staff** | sam@treasury.gov | Password1 |
| **Industry** | industrytest@gmail.com | Password1 |

Role assignment is automatic based on email domain: `@ttb.gov` / `@treasury.gov` → Staff/Admin; all other domains → Industry.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LOCAL_LLM_URL` | Assessment service | `http://assess:8000` (auto-set) |
| `OLLAMA_HOST` | Ollama API | `http://ollama:11434` (auto-set) |
| `OLLAMA_MODEL` | Active vision model | `llava:7b` (auto-set) |
| `VITE_SUPABASE_URL` | Optional cloud auth | blank → demo mode |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Optional cloud auth | blank → demo mode |
| `ANTHROPIC_API_KEY` | Optional Claude fallback | blank → local only |

### Useful Commands

```bash
docker compose ps                              # check all service health
docker compose logs -f                         # tail all logs
docker compose logs -f ttb-ollama-pull         # watch model download progress
docker compose logs -f ttb-assess              # watch assessment decisions

curl http://localhost:8004/health              # web app health
docker exec ttb-assess curl -s http://localhost:8000/health
docker exec ttb-ollama curl -s http://localhost:11434/api/version

docker compose up --build ttb-app             # rebuild after code change
docker compose down                            # stop, keep volumes
docker compose down -v                         # full reset, re-downloads models
```

---

## PostgreSQL Access

Every assessment decision is written to PostgreSQL by the `assess` service and is queryable at any time. The database is not exposed to the host by default — all access goes through `docker exec`.

```bash
# Open an interactive psql shell
docker exec -it ttb-postgres psql -U ttb -d ttb

# One-shot queries (no shell)
docker exec ttb-postgres psql -U ttb -d ttb -c "\dt"                          # list tables
docker exec ttb-postgres psql -U ttb -d ttb -c "SELECT * FROM submissions ORDER BY created_at DESC LIMIT 10;"
docker exec ttb-postgres psql -U ttb -d ttb -c "
  SELECT relname AS table, n_live_tup AS rows
  FROM pg_stat_user_tables
  ORDER BY n_live_tup DESC;"
```

**Connection details:**

| Setting | Value |
|---------|-------|
| Container | `ttb-postgres` |
| Database | `ttb` |
| User | `ttb` |
| Password | `ttb` |
| Port | `5432` (internal only) |

To connect with a GUI tool (TablePlus, DBeaver, pgAdmin), temporarily expose the port by adding `ports: ["5432:5432"]` under the `postgres` service in `docker-compose.yml` and restart the stack.

---

## Deployment Context

This prototype is designed to run anywhere Docker is available — including an interviewer's laptop. The following distinctions apply:

**Demo / laptop use:** Run `docker compose up --build` and access via `localhost`. All services run on the same machine. This is the expected mode for evaluation purposes and works fully without any external dependencies.

**Production / dedicated server:** For an operational deployment the recommended topology is:

- TTB Automate runs on a dedicated application server (bare metal or VM) not connected to the general intranet
- PostgreSQL runs on a separate database server on a network segment isolated from the application and from general staff workstations
- Ollama (if GPU-backed) runs on the same application server or on a dedicated GPU inference node reachable only from the application server
- The only inbound port exposed to end users is 8004 (the web UI) through a reverse proxy (nginx, Caddy) with TLS termination
- n8n (5678) is accessible only from the application server's loopback or an internal operations subnet
- No label images or assessment results leave the server network

This topology limits the blast radius of any compromise: the database is unreachable from the network the web app lives on except through the application's own service account. Full network isolation details are in [SECURITY.md](./SECURITY.md).

### Persistence Across Reboots

All containers are configured with `restart: unless-stopped` — Docker restarts them on crash or daemon restart, but not on system boot unless Docker itself starts automatically.

**macOS / Windows:** Enable "Start at Login" in Docker Desktop preferences. All `unless-stopped` containers resume on reboot with no further configuration.

**Linux:** Docker Engine does not start on boot by default. Enable it once:

```bash
sudo systemctl enable docker
```

After that, all `unless-stopped` containers come back automatically after a reboot. No additional systemd unit is needed for the application.

---

## Technical Approach

### Why Local LLM

Every label image submitted to TTB Automate stays on the host. Nothing is transmitted to Google, Anthropic, OpenAI, or any third-party inference provider. This matters for two reasons: regulatory sensitivity (label designs, formulations, and applicant identities are business-confidential) and cost predictability (no per-call API charges at scale).

The Anthropic Claude API is available as an optional fallback — set `ANTHROPIC_API_KEY` in `.env` — for development or comparison purposes. In a production deployment it would be disabled.

### GPU Strategy Selection

At startup the `assess` service inspects the configured `OLLAMA_MODEL` name. If it contains a known vision model identifier (`llava`, `bakllava`, `moondream`, etc.) it routes all submissions through `run_vision`. Otherwise it falls back to `run_reconcile`. This is intentionally simple: swapping the model and restarting the container changes the processing strategy with no code change. It also avoids unreliable runtime GPU probes that were the source of earlier bugs.

### Why qwen2.5:14b for Text

qwen2.5:14b is used in the reconcile (CPU-only) path to interpret OCR-extracted text. The question of "why 14 billion parameters for a text task" is valid: a smaller model like qwen2.5:7b or qwen2.5:3b could handle straightforward, clean label text. The 14b was chosen for this prototype because OCR output from real-world label photos is often messy — fragmented lines, misread numbers (8 vs B, 0 vs O), hyphenated words split across rotations. The larger model handles that ambiguity and edge-case regulatory language more reliably. In a resource-constrained environment, qwen2.5:7b is a reasonable swap with a modest accuracy tradeoff; change `TEXT_MODEL: qwen2.5:7b` in `docker-compose.yml` under the `assess` service.

### ABV Validation

`local_llm_client.py` extracts the declared ABV via regex from the `alcohol_content` field and cross-checks it against the CFR tolerance for the detected commodity type. A spirits label declaring 40.0% ABV is valid; one declaring 40.8% would trigger a WARNING since that exceeds the ±0.3% tolerance. This check runs independently of the LLM, so it is deterministic and fast.

---

## Assumptions

**Image quality:** Labels should be photographed with reasonable clarity. The system handles common imperfections (see [Handling Imperfect Label Images](#handling-imperfect-label-images)) but cannot reconstruct text from a completely blurred or obstructed label.

**Regulatory scope:** The prototype covers domestic products under 27 CFR Parts 4 (Wine), 5 (Spirits), and 7 (Malt Beverages). Imported product supplemental requirements and specialty categories (organic, kosher) are partially supported.

**Metric net contents:** The system expects metric measurements. Common conversions are handled: 75cl → 750ml, 1.75L → 1750ml. Imperial-only labeling is flagged as a WARNING.

**Authentication:** Demo mode uses a server-side `demo_role` cookie. A production deployment would integrate with Login.gov or ID.me for verified identity.

---

## Engineering & Design Decisions

The assignment guidelines are explicit: "You are free to use any programming languages, frameworks, or libraries you prefer. We want to see what kind of engineering, design, and integration decisions you make." This section documents those decisions and the reasoning behind each one.

### Python + FastAPI over Node.js, Go, or Django

Python was the right language for this problem because the two heaviest components — AI inference and OCR — have their best-maintained libraries in Python (`httpx` for async Ollama calls, `pytesseract`, `Pillow`). FastAPI was chosen over Django because this prototype has no need for Django's ORM, admin panel, or batteries-included features. FastAPI gives us async request handling (critical for a service that waits on slow LLM responses), automatic OpenAPI documentation, and Pydantic data validation with very little boilerplate. Go would have been faster at runtime but significantly slower to build, especially for a time-constrained prototype where Python's ecosystem has a clear edge in AI tooling.

### Docker Compose over Kubernetes

Kubernetes is the right tool for a multi-node, horizontally-scaled production deployment. It is not the right tool for a six-service prototype that needs to run on an interviewer's laptop or a single server. Docker Compose gives us the same service isolation, dependency ordering, health checks, and named networking that Kubernetes provides — at a fraction of the operational complexity. The `docker compose up --build` path is one command. A Kubernetes manifest stack would require `kubectl`, a container registry, and either a local cluster (minikube, kind) or a cloud provider. Compose is the honest choice here: appropriate scope, zero infrastructure overhead, easily replaced when scale demands it.

### Microservices over a Monolith

The system is split into four purpose-built services: the web app, the LLM assessment orchestrator, the OCR engine, and Ollama itself. This is not premature abstraction — each service has a fundamentally different runtime profile. Ollama is a long-running GPU-bound daemon. The OCR service is CPU-intensive and stateless. The assessment service is I/O-bound (waiting on model responses). The web app handles HTTP routing and session management. Combining these would mean a single process with wildly mixed resource requirements and no ability to restart a failing component independently. The microservice split also means we can swap the OCR engine, swap the LLM, or scale the assessment service horizontally without touching the web app.

### Local LLM (Ollama) over Cloud AI

This is documented in [SECURITY.md](./SECURITY.md) from a privacy and data-handling perspective. From an engineering perspective, it also made sense because: (1) the response schema is fully under our control — we are not dependent on a cloud provider's API versioning, (2) Ollama's REST API is simple and consistent, and (3) the offline capability matters — a federal application that cannot function without a live internet connection to a commercial AI provider is a liability. The Anthropic Claude API exists as an optional fallback for development comparison; it is not the primary path.

### llava:7b for Vision + qwen2.5:14b for Text (Two Models, Not One)

A single large multimodal model would be the simplest solution. We use two models for a deliberate reason: llava:7b is a vision model optimized for reading images — it is the correct tool for interpreting label photos directly. qwen2.5:14b is a text model optimized for reasoning over structured text — it is the correct tool for reconciling messy OCR output against regulatory requirements. Using llava:7b for both paths would produce poor results on the CPU/OCR path because qwen would be sent image bytes it was not trained to interpret. The two-model design means each model is used for what it was built for. The size of qwen2.5:14b is justified by the ambiguity of real OCR output; a 3b model handles clean text but degrades on fragmented lines, misread numbers, and rotated characters. A 7b swap is documented for resource-constrained environments.

### n8n for Workflow Automation over a Custom Webhook Handler

We could have written a Python function that reads the assessment result and updates a status field. That would be twenty lines of code. The reason n8n was chosen instead is not because those twenty lines were too hard to write — it is because the value of the automation layer is not in what it does today, but in what it enables without a developer. A custom Python handler means every new downstream action (send an email, post to Slack, create a ticket, write to a CRM) requires a code change, a test, and a deployment. n8n means a non-engineer can add those actions through a visual UI. For a government bureau evaluating whether to operationalize this, that extensibility is the difference between a one-time tool and a platform.

### PostgreSQL over SQLite or a NoSQL Store

At TTB's scale of roughly 150,000 label submissions per year (~411 per day), the raw data volume is modest — under 2 GB of structured records even with five years of history. SQLite would handle that volume without breaking a sweat, and for a truly single-process application it would be the simpler choice.

The reason PostgreSQL was chosen is not volume — it is the **concurrent multi-process write pattern** in this architecture. Three containers write to the database independently: the `assess` service writes audit records as decisions arrive, `ttb-app` writes application status updates when staff act on reviews, and n8n writes status fields when workflow nodes fire. SQLite uses file-level write locking; under concurrent writes from multiple Docker containers sharing a volume it produces `database is locked` errors under any real load. PostgreSQL is a client-server database built precisely for this isolation model.

If the architecture were refactored so that all writes were channeled through a single service (the assess service acting as the sole DB writer, with ttb-app and n8n reading via REST), SQLite would be a legitimate and lighter choice. In the current multi-writer topology, PostgreSQL is correct — but the justification is concurrency, not scale. That distinction matters: overselling PostgreSQL as a scale decision when the workload is small would be an engineering misrepresentation.

A NoSQL store (MongoDB, DynamoDB) would add query complexity for no benefit. The data model is straightforwardly relational: submissions have fields, fields have statuses, statuses reference CFR sections. There is no document hierarchy or variable schema that would motivate a document store.

### Server-Rendered HTML over a React SPA

A React frontend was the original approach (this project evolved from a TypeScript/React codebase). It was replaced with server-rendered Jinja2 templates for the Python rebuild. The reason is straightforward: a React SPA requires a separate build pipeline, a JavaScript bundler, a node_modules directory, and adds complexity to the Docker image for every page that could be rendered cleanly on the server. For a role-based, form-driven application with no real-time data requirements, server-rendered HTML is faster to build, easier to debug, and simpler to deploy. If a richer UI were needed in a production version — real-time queue updates, interactive data visualizations — React or HTMX would be the right addition at that point.

### Tesseract over AWS Textract or Google Vision OCR

Both AWS Textract and Google Vision OCR would likely produce more accurate text extraction than Tesseract on difficult images. They were excluded for the same reason cloud AI was excluded: outbound data transmission. Label images sent to Textract go to AWS infrastructure. For a prototype emphasizing local execution and data containment, Tesseract is the correct choice. The multi-rotation improvement (running all four orientations and combining unique lines) largely closes the accuracy gap for well-photographed labels.

---

## Trade-offs & Future Direction

The assignment calls for a working core over ambitious but incomplete features. The items below were consciously deferred — not overlooked — in favor of a fully functional, clean prototype. Each has a documented reactivation path.

### What works end-to-end

- Single and split label verification via `/verify`
- Full COLA submission, review, and status lifecycle
- AI assessment with field-by-field CFR findings and calculated compliance score
- n8n APPROVE / REVIEW / DENY routing
- PostgreSQL audit log for every decision
- Role-based portals for industry, staff, and admin
- Malware scanning and quarantine pipeline
- Batch upload with consolidated results
- Demo accounts for all three roles — no setup required

### What was deferred and how to reactivate

| Feature | Why deferred | Reactivation path |
|---------|-------------|-------------------|
| Parallel batch processing | Requires a task queue (Celery + Redis) and multiple Ollama replicas | Add Celery + Redis to `docker-compose.yml`; swap sequential loop for `asyncio.gather` |
| Real-time queue updates | SSE/WebSocket incompatible with server-rendered HTML without a page refresh | Add HTMX polling or a WebSocket endpoint to FastAPI |
| Login.gov / ID.me | Requires a registered application with a federal identity provider | Replace demo cookie with OAuth2/PKCE; session management already exists |
| Appeals workflow UI | n8n stub and `appealing` status exist; the UI button was not built | Activate the existing n8n stub; add "Request Human Review" to the DENY result page |
| TTBONLINE integration | No API access available; requires bilateral data agreement | REST bridge or batch CSV/XML; assessment output schema is already structured for this |
| Mobile-responsive layout | Lower priority than functional correctness | Tailwind breakpoints loaded via CDN; grid layouts need `sm:` prefix variants |
| First-boot model download (~11 GB) | Ollama pulls models at runtime rather than baking them into the image | Embed via `ollama pull` during `docker build` to eliminate first-boot delay |

### Future enhancements

The n8n workflow and modular service architecture are designed to grow without application code changes for most additions.

**No code changes required (n8n only):**
- Email applicants their decision and CFR citations via n8n SMTP node
- Push REVIEW cases to Slack or Teams with a direct link to the staff review page
- "Request Human Review" for DENY cases — triggers a staff-assigned ticket

**Minor code changes:**
- Larger vision model (llava:13b or llava:34b) for higher accuracy as GPU hardware permits
- Signature/stamp detection for imported products requiring country-of-origin seals
- Parallel batch processing with Redis task queue

**Integration work:**
- REST API bridge to TTBONLINE for status synchronization
- Power BI / Grafana connector from PostgreSQL for compliance trend dashboards
- Login.gov / ID.me for verified applicant identity

**Model evolution and feedback loop:**

The `human_decision` column in the `assessments` table captures every auditor override — the foundation for a learning loop that doesn't exist yet. Two failure modes make this worth building: regulatory drift (27 CFR rules change; a model trained on prior guidance will start producing incorrect findings) and classification drift (if the model consistently flags a pattern as REVIEW that humans consistently approve, it is wasting reviewer time and should learn to APPROVE that pattern directly).

The reactivation path has three stages. First, mine `human_decision` to identify systematic disagreements between AI decisions and human outcomes — any REVIEW that becomes APPROVE more than 80% of the time is a candidate for threshold adjustment. Second, use that override corpus to fine-tune the local model via Ollama's model import pipeline or to update the CFR rules dict and scoring weights in `local_llm_client.py` without full retraining. Third, add drift detection: alert when the override rate for any decision class crosses a defined threshold, triggering a review of whether the model's prompt or scoring logic needs updating. This keeps the system accurate as regulations evolve rather than degrading silently over time.

---

## Project Structure

```
ttb-automate/
├── docker-compose.yml             # 6-service orchestration
├── README.md
├── SECURITY.md
│
├── python_app/                    # FastAPI web application (:8004)
│   ├── main.py                    # FastAPI entry point
│   ├── config.py                  # Environment variable bindings
│   ├── prompts.py                 # CLASSIFIER_PROMPT, BAM verifier prompt
│   ├── local_llm_client.py        # Assess adapter, CFR rules, calculated scoring
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── routers/
│   │   ├── pages.py               # HTML page routes
│   │   └── api/
│   │       ├── verify_label.py    # POST /api/verify-label
│   │       ├── applications.py    # COLA application CRUD
│   │       ├── history.py         # Verification history
│   │       └── scan_file.py       # Virus scan endpoint
│   ├── templates/                 # Jinja2 HTML templates
│   │   ├── base.html
│   │   ├── landing.html
│   │   ├── verify.html
│   │   ├── industry/
│   │   └── staff/
│   └── static/
│       ├── css/custom.css
│       └── js/                    # Vanilla JS (auth, verify, dashboards)
│
├── assessment-service/app/        # LLM orchestrator (:8000, internal only)
│   ├── main.py                    # POST /assess, GET /health
│   ├── prompt.py                  # BAM verification prompt
│   ├── models.py                  # Pydantic models + parse/fallback logic
│   ├── init.sql                   # PostgreSQL schema
│   ├── Dockerfile
│   └── requirements.txt
│
├── ocr-service/                   # Tesseract OCR (:8001, internal only)
│   ├── main.py                    # POST /ocr — all-rotation extraction
│   └── Dockerfile
│
├── ollama/                        # Custom Ollama image + model pull
│   ├── Dockerfile                 # Extends ollama/ollama:latest with curl
│   └── pull-models.sh             # Pulls llava:7b + qwen2.5:14b via REST API
│
└── n8n/
    └── workflows/
        └── ttb-assessment.json    # Auto-imported APPROVE/REVIEW/DENY routing workflow
```

---

## API Reference

### POST /api/verify-label

Main label compliance verification endpoint.

**Request:** `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `label_image` | file | Primary label image (JPEG/PNG/WebP, max 10 MB) |
| `back_label_image` | file | Optional back label |
| `product_details` | string | Optional JSON with expected field values |
| `submission_id` | string | Optional — auto-generated if absent |

**Response:**

```json
{
  "commodity_type": "Spirits",
  "overall_status": "PASS",
  "compliance_score": 85,
  "critical_failures": [],
  "warnings": ["Net Contents: metric format not confirmed"],
  "findings": [
    {
      "field": "brand_name",
      "status": "PASS",
      "label_value": "Mountain Oak Bourbon",
      "expected_value": "Must be present and prominent",
      "reason": "Brand name clearly visible on front label",
      "cfr_reference": "27 CFR 5.34"
    }
  ],
  "abv_validation": {
    "detected_abv": 45.0,
    "class_detected": "Bourbon Whisky",
    "min_required": 20.0,
    "max_allowed": 95.0,
    "tolerance": 0.3,
    "status": "PASS"
  }
}
```

### POST /assess (Assessment Service — internal)

Called by `ttb-app` and n8n. Not exposed to the host network.

**Request:** `multipart/form-data` with `label_images` (file) + `submission_id` (string)

**Response:**

```json
{
  "submission_id": "TTB-20260318-143022-A4F2B1",
  "decision": "APPROVE",
  "brand_name": "Mountain Oak",
  "reasoning": "All mandatory fields present and compliant.",
  "fields": [
    {
      "name": "brand_name",
      "status": "PASS",
      "found_on_label": "Mountain Oak",
      "reference_value": "Required",
      "note": ""
    }
  ],
  "strategy": "vision",
  "active_model": "llava:7b"
}
```

### GET /health

Available on `ttb-app` (:8004) and `assess` (:8000).

```json
{ "status": "ok" }
```

---

## License

This project is a prototype developed for demonstration purposes. All rights reserved.

---

## Acknowledgments

- TTB for regulatory guidance and Beverage Alcohol Manual (BAM) documentation
- Ollama for making local LLM inference deployable in Docker
- n8n for open-source visual workflow automation
