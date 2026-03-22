# TTB Automate — Security Architecture

Defense-in-depth across six layers: network, execution, application, database, file, and audit. No single control is relied upon exclusively.

Referenced from [README.md](./README.md).

---

## Local AI Execution

The single most important security decision: **all inference runs locally**.

Label images never leave the server. Models are downloaded once at first boot and cached permanently in a Docker volume. At steady state, there is zero outbound data transfer.

This eliminates:
- **Data exposure** — proprietary formulations, brand strategy, and applicant identities stay on-premises
- **Regulatory risk** — no third-party data processor to evaluate against federal handling requirements
- **Outbound dependency** — no cloud API rate limits, outages, or pricing changes
- **Audit gaps** — every input and output is logged locally and reproducible

The Anthropic Claude API exists as an optional dev/benchmarking configuration. It should be disabled in any deployment handling real applicant data.

---

## Network Isolation

Six services on an internal Docker bridge network (`ttb-network`). Two ports exposed to the host:

| Service | Port | Purpose |
|---------|------|---------|
| ttb-app | 8004 | Web UI and API |
| n8n | 5678 | Workflow editor (operations use only) |

PostgreSQL, Ollama, OCR, and the assessment service are **internal only** — unreachable from outside the container network.

### Production Topology

```
Internet ──▶ [Reverse Proxy / TLS] ──▶ ttb-app (:8004)
                                          ├── assess (:8000)
                                          ├── ocr (:8001)
                                          └── ollama (:11434)
                                               │
              [Ops subnet only] ◀── n8n (:5678) │
              [Separate VLAN]   ◀── postgres (:5432)
```

- TLS terminates at the reverse proxy
- Database reachable only from the application server
- n8n never internet-facing
- No label images or results leave the server network

---

## Authentication & Access Control

### Demo Mode (Implemented)

Server-side `demo_role` cookie. Selecting a demo account sets the role; FastAPI reads it on every request. No external auth required.

### Production Path

Login.gov or ID.me for verified identity. JWTs validated server-side; expired or invalid tokens return 401 before any application logic runs.

### Roles

| Role | Capabilities |
|------|-------------|
| Industry | Submit labels, view own applications |
| Staff | Review queue, approve/reject/return, AI annotations |
| Admin | All staff capabilities + audit log + quarantine review |

### Enforcement

**Application layer (implemented):** Every route calls `require_auth(request, allowed_roles)`. Restricted pages redirect before rendering. Staff-only fields (risk scores, reviewer notes) are never emitted in industry HTML — absent from the DOM, not CSS-hidden.

**Database layer (production design):** PostgreSQL row-level security as a second, independent enforcement layer. Even if application logic is bypassed, RLS policies prevent cross-user data access. Per-role database credentials replace the shared prototype credential.

---

## File Upload Security

```
Upload ──▶ Frontend validation (10 MB, JPEG/PNG/WebP, MIME match)
       ──▶ ClamAV scan ── threat ──▶ Quarantine (admin-only bucket)
       ──▶ Compression (2048×2048, 85% JPEG)
       ──▶ Storage ({user_id}/{app_id}/) + assessment
```

- Extension-to-MIME mismatch → rejected (prevents renamed-file attacks)
- Rate limit: 20 req/min per authenticated user
- If ClamAV unavailable: upload proceeds with logged warning (prototype); production should block and queue for retry

### Quarantine

Malware-flagged files are isolated in a private bucket. Admin reviews via `/admin/quarantine`. Files are never auto-deleted — preserved for forensic analysis. Status flow: `pending` → `reviewed` → `deleted` (explicit admin action only).

---

## Database Security

PostgreSQL is internal to the container network. Only the `assess` service connects.

### Audit Log

Every assessment writes: submission ID, timestamp, strategy, model, decision, field-level findings (JSON), full LLM output (untruncated), and any subsequent human override. The `human_decision`, `reviewed_by`, and `reviewed_at` columns capture every staff action. No application code issues DELETE — the log is effectively immutable.

### Production Additions

- Row-level security on all tables
- Per-role credentials (no shared `ttb`/`ttb`)
- `applications_industry` secure view with `security_invoker = true`

---

## AI Safety & Governance

### Decision Boundaries

The system's three-tier output is a deliberate safety design:

- **APPROVE** — high-confidence compliance. Automated decision.
- **REVIEW** — uncertainty detected. Human reviewer makes the final call.
- **DENY** — clear failure. Returned with citations; applicant can resubmit.

The model never makes a final regulatory determination on uncertain cases. This aligns with:

- **NIST AI Risk Management Framework** (MAP/MANAGE functions) — systematic identification of AI risks with human oversight controls
- **Executive Order 14110** — requirements for safe, secure, and trustworthy AI in federal systems
- **OMB M-24-10** — governance, innovation, and risk management for agency use of AI

### Audit & Reproducibility

Every inference is logged with its full input context and raw model output. Decisions are reproducible: the same image, model version, and prompt will produce the same assessment. Human overrides are captured separately, preserving both the AI recommendation and the final human decision for accountability and training signal.

### Drift Detection (Design Intent)

The `human_decision` column enables monitoring for two failure modes:

1. **Regulatory drift** — CFR rules change; the model's embedded guidance becomes stale
2. **Classification drift** — the model systematically flags patterns as REVIEW that humans consistently approve

When the override rate for any decision class crosses a threshold, it triggers prompt/scoring review. This keeps the system accurate as regulations evolve rather than degrading silently.

---

## Production Checklist

### Credentials & Secrets
- [ ] PostgreSQL password changed from `ttb`/`ttb`
- [ ] n8n password changed from `admin`/`ttbexpress`
- [ ] `ANTHROPIC_API_KEY` left blank
- [ ] All secrets via environment variables, never in source control

### Network
- [ ] TLS at reverse proxy; HTTP → HTTPS redirect
- [ ] Port 5432 not exposed to host or public network
- [ ] Port 5678 restricted to operations subnet
- [ ] Docker images pinned to specific digests

### Access Control
- [ ] Demo cookie auth replaced with Login.gov / ID.me
- [ ] RLS policies applied to all tables
- [ ] Per-role database credentials active
- [ ] Staff-only fields absent from industry HTML (verify with `curl`)

### File Handling
- [ ] ClamAV reachable and returning clean responses
- [ ] Quarantine bucket has no public access policy
- [ ] Rate limiting active on upload endpoint

### Operations
- [ ] `ollama-data` and `postgres-data` on encrypted storage
- [ ] Docker logs forwarded to SIEM (Splunk, Elastic, CloudWatch)
- [ ] PostgreSQL backup schedule configured
- [ ] Drift detection monitoring active on override rates

---

## Reporting Security Issues

Do not disclose publicly. Contact the development team with reproduction steps. Allow reasonable time for remediation before disclosure.
