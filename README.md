# TTB Automate — AI-Powered Label Compliance

A containerized intelligence layer designed to streamline the Certificate of Label Approval (COLA) screening process. By leveraging on-premises multimodal LLMs, it provides real time automated screening for alcohol beverage labels against 27 CFR requirements before they enter the human review queue. 

# Objective
The system intercepts COLA applications at the point of submission to perform automated 'preflight' verification. By extracting imagery and text and cross referencing them with the Code of Federal Regulations (CFR), the tool categorizes applications into three actionabl paths. This ensures that human staff only interact withc ases requiring nuanced judgement, effectively eliminating the administrative burden of clear pass/fail applications.

While this repository suggests an on premise solution, the alternative for an API cloud provider to host the tool is also feasible. A prototype can be found at [https://constitutes-blowing-previous-proposed.trycloudflare.com], although for security reasons outlined in the SECURITY.md, production environments should host services locally.

As well, future considerations, such as integrating machine learning and peripheral tools, are outlined in the documentation below for additional benefits and services.

To begin, follow the below steps. (a GPU with minimum specs of 6gb VRAM is recommended)
```bash
# 1. Clone the repository
git clone https://github.com/jchimino/ttb-automate.git
cd ttb-automate

# 2. Start everything — CPU mode (works on any machine, no GPU required)
docker compose up --build

# GPU acceleration is automatic if the NVIDIA Container Toolkit is installed.
#     docker-compose.yml includes the deploy block — no second file needed.

# 3. Once running, open your browser and navigate to:
#    http://localhost:8004        ← main web UI
#    http://localhost:15678        ← n8n workflow editor (login: admin / ttbexpress)
#
# Note: First boot pulls ~4.4 GB (qwen2.5:7b for CPU). GPU users also pull llava:7b (~8.8 GB total).
# Subsequent starts reuse the cached volume.
```

---

## What It Does

A label image goes in. A structured compliance decision comes out — field by field according to CFR citations. This tool is pre-emptive and helps facilite the screening process.

- **APPROVE** — all mandatory fields present and compliant. This is the decision; no human review needed, and expected to decrease the overall submissions required by 66%
- **REVIEW** — borderline findings. Routed to staff pre-annotated with AI notes. The reviewer makes the call with simple approve/deny buttons. AI highlights fields for review.
- **DENY** — clear failure. Returned to the applicant with specific citations and the ability to resubmit.

The goal: staff only touch cases that genuinely require human judgment. There were a high number of false negatives when encroaching sub 5 second processes. The extra time is allotted to the AI model to be more accurate than fast. This proposed process executes at the time users submit labels, before labels are entered into the queue, decreasing the number of actual submissions that need review. 

---

## Architecture

```
Browser ──▶ ttb-app (FastAPI :8004)
               │
               ▼
            assess (:8000) ──▶ ollama (:11434)
               │                  ├── llava:7b     (vision / GPU)
               │                  └── qwen2.5:7b   (text / CPU fallback)
               ├──▶ ocr (:8001)    (Tesseract, 4 rotations)
               └──▶ postgres (:5432) (audit log)

n8n (:15678) ◀── webhook ── decision routing
```

Six services on one internal Docker network. Only ports 8004 and 15678 are exposed to the host. Everything else — database, models, OCR — is unreachable from outside.

### Two Assessment Strategies

| Strategy | Path | Model | When |
|----------|------|-------|------|
| **Vision** | Image → LLM directly | llava:7b | GPU available (3–8s per label) |
| **Reconcile** | Image → Tesseract OCR → text LLM | qwen2.5:7b | CPU-only fallback (20–60s per label) |

The vision model reads labels the way a human reviewer would — interpreting layout, inferring partially obscured text, handling imperfect photos. The reconcile path runs OCR at all four rotations (0°/90°/180°/270°) so nothing — including sideways health warnings — is missed.

### Model-Agnostic Design

Swapping `llava:7b` for `llava:13b`, LLaVA-NeXT, or any future multimodal model requires one environment variable change and a restart. The prompt layer, image preprocessing, and response parsing are decoupled from the model. This allows adoption of newer models as they clear review — without re-architecting the pipeline.

---

## Compliance Checks (27 CFR)

| Check | Regulation |
|-------|------------|
| Brand Name | §5.34, §4.33, §7.63 |
| Class & Type (Standard of Identity) | §5.35, §4.34, §7.64 |
| Alcohol Content (ABV ± tolerance) | §5.37, §4.36, §7.71 |
| Net Contents (metric) | §5.38, §4.37, §7.73 |
| Government Warning (ABLA/Surgeon General) | 27 CFR §16 |
| Name & Address (bottler/importer) | §5.36, §4.35, §7.65 |
| Country of Origin (imports) | §5.36(d), §4.35(d) |
| Sulfite Declaration (wine >10ppm) | §4.32(e) |

ABV tolerances: Spirits/Wine ±0.3%, Malt Beverages ±0.15%. Validated deterministically, independent of the LLM.

### Scoring

| Deduction | Points |
|-----------|--------|
| Gov Warning FAIL | −25 |
| Mandatory field FAIL | −20 each |
| Non-mandatory FAIL | −10 each |
| Any WARNING | −5 each |
| ABV out of tolerance | −15 |

Score ≥80 → APPROVE. 50–79 → REVIEW (staff queue). <50 → DENY. Critical failures force DENY regardless of score.

---

## Stack

| Layer | Technology | Why |
|-------|------------|-----|
| Web app | Python 3.11 + FastAPI + Jinja2 | Async I/O for slow LLM calls; server-rendered HTML — no JS build pipeline |
| AI inference | Ollama (local Docker) | No outbound data; model-agnostic; offline-capable |
| Vision model | llava:7b | Reads labels directly; handles imperfect photos |
| Text model | qwen2.5:7b | Reconciles messy OCR against CFR requirements |
| OCR | Tesseract 5 | Local; 4-rotation extraction for rotated text |
| Database | PostgreSQL 16 | Multi-writer concurrency from 3 containers; SQLite would lock |
| Orchestration | Docker Compose | Single-command deployment; appropriate for prototype scope |
| Workflow | n8n | Visual routing; 400+ integrations without code changes |

---

## Setup

### Requirements

| | Minimum (CPU) | Recommended (GPU) |
|--|---|---|
| RAM | 12 GB | 32 GB |
| Disk | 20 GB free | 40 GB free |
| GPU | — | NVIDIA 8 GB+ VRAM |
| Docker | Docker Desktop 4.x or Engine + Compose | + NVIDIA Container Toolkit |

### Demo Accounts

| Role | Email | Password |
|------|-------|----------|
| Admin | admin@ttb.gov | Password1 |
| Staff | sam@treasury.gov | Password1 |
| Industry | industrytest@gmail.com | Password1 |

### Commands

```bash
docker compose up --build              # start everything
docker compose ps                      # check health
docker compose logs -f assess          # watch AI decisions
docker compose down                    # stop (keeps data)
docker compose down -v                 # full reset

# Database access
docker exec -it ttb-postgres psql -U ttb -d ttb
```

---

## Design Decisions

**Local LLM over cloud API.** Label images contain proprietary formulations, brand strategy, and applicant identities. Sending them to a third-party inference provider creates regulatory risk and an outbound dependency. Local execution means every input and output is logged, inspectable, and reproducible. The Anthropic API exists as an optional dev fallback — it would be disabled in any real deployment.

**Two models, not one.** llava:7b is a vision model — the right tool for reading images. qwen2.5:7b is a text model — the right tool for reasoning over messy OCR output. Using one model for both paths would degrade accuracy on whichever task it wasn't optimized for.

**Docker Compose over Kubernetes.** This needs to run on a single machine — an interviewer's laptop or a dedicated server. Compose provides the same service isolation, health checks, and networking at a fraction of the operational complexity. Kubernetes is the right tool when horizontal scaling demands it.

**PostgreSQL over SQLite.** Three containers write concurrently (assess, ttb-app, n8n). SQLite uses file-level write locking; under concurrent writes it produces `database is locked` errors. PostgreSQL handles this natively. The justification is concurrency, not scale.

**n8n over custom code.** A Python webhook handler is 20 lines. The value of n8n is what it enables without a developer: email alerts, Slack routing, CRM writes — all configurable through a visual UI by non-engineers.

**Server-rendered HTML over React.** No real-time data requirements. No JS build pipeline. Faster to build, easier to debug, simpler to deploy. If real-time updates were needed, HTMX or a WebSocket layer would be added incrementally.

---

## Forward-Looking Architecture

These capabilities are designed into the system's structure — some active, some ready to activate.

### Ensemble Assessment with Confidence Scoring

The dual-strategy architecture (vision + reconcile) is the foundation for ensemble assessment. When both strategies run independently and disagree, the case escalates to REVIEW automatically. The next step: calibrated confidence scores per field, enabling **field-level** automation — high-confidence fields auto-approved, low-confidence fields routed to human review individually, not the entire application.

### Human-in-the-Loop Feedback Pipeline

Staff approve/reject decisions already generate labeled training signal. Each review produces a `(label_image, structured_fields, human_decision)` tuple stored in PostgreSQL. The `REVIEW` status functions as an active learning trigger — the model flags uncertain cases for human adjudication, which can continuously improve the decision boundary. The `human_decision` column in the assessments table is the foundation for mining systematic disagreements and adjusting thresholds or fine-tuning.

### RAG over the Code of Federal Regulations

The current system hard-codes trimmed TTB rules in the prompt. The architecture supports migration to retrieval-augmented generation: chunk the full 27 CFR Parts 4, 5, and 7, embed them with pgvector (in the existing Postgres instance), and retrieve relevant sections at inference time based on label content. When regulations change, the vector store updates — no prompt rewriting required.

### Edge Deployment

The entire stack runs locally with zero cloud dependency. This means it can deploy on ruggedized hardware for field inspectors — photograph a label at a distillery, get preliminary compliance feedback offline, sync results when connectivity resumes.

### NIST AI RMF and EO 14110 Alignment

The three-tier decision output (APPROVE/REVIEW/DENY) aligns with NIST AI Risk Management Framework MAP and MANAGE functions. The system never makes a final regulatory determination autonomously on uncertain cases — REVIEW ensures human oversight. Audit logging captures every model input, output, and human override. This is consistent with Executive Order 14110 requirements for safe, secure, and trustworthy AI in federal systems.

---

## Project Structure

```
ttb-automate/
├── docker-compose.yml                # 6-service orchestration
├── README.md
├── SECURITY.md
│
├── python_app/                       # FastAPI web app (:8004)
│   ├── main.py                       # Entry point
│   ├── config.py                     # Environment bindings
│   ├── local_llm_client.py           # Assess adapter, CFR rules, scoring
│   ├── routers/
│   │   ├── pages.py                  # HTML routes
│   │   └── api/                      # REST endpoints
│   ├── templates/                    # Jinja2 (base, landing, verify, industry/, staff/)
│   └── static/                       # CSS, JS
│
├── assessment-service/app/           # LLM orchestrator (:8000, internal)
│   ├── main.py                       # POST /assess, strategy selection
│   ├── prompt.py                     # CFR prompt builder
│   ├── models.py                     # Parse + JSON repair + fallback
│   └── init.sql                      # PostgreSQL schema
│
├── ocr-service/                      # Tesseract OCR (:8001, internal)
├── ollama/                           # Model server + pull script
└── n8n/workflows/                    # Auto-imported decision routing
```

---

## API Reference

### POST /api/verify-label

Label compliance verification. Accepts `multipart/form-data`.

| Field | Type | Required |
|-------|------|----------|
| `label_image` | file (JPEG/PNG/WebP, max 10 MB) | yes |
| `back_label_image` | file | no |
| `submission_id` | string | no (auto-generated) |

Returns: `commodity_type`, `overall_status`, `compliance_score`, `findings[]` with per-field status, CFR references, and detected values.

### POST /assess (internal)

Called by ttb-app. Returns `decision`, `brand_name`, `reasoning`, `fields[]`, `strategy`, `active_model`.

### GET /health

Returns `{"status": "ok"}` on both ttb-app (:8004) and assess (:8000).

---

## Security

See [SECURITY.md](./SECURITY.md) for network isolation, RBAC, file upload security, malware quarantine, and production deployment guidance.

---

## License

Prototype developed for demonstration purposes. All rights reserved.
