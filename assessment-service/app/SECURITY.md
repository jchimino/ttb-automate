# TTB Automate — Security Architecture

This document describes what is implemented in the prototype and what is specified for production, covering local AI execution, network isolation, file scanning and quarantine, and role-based access control.

Referenced from [README.md](./README.md).

## Table of Contents

- [Design Philosophy](#design-philosophy)
- [Local Execution — Why No Cloud AI](#local-execution--why-no-cloud-ai)
- [Network Isolation](#network-isolation)
- [Authentication and Authorization](#authentication-and-authorization)
- [Role-Based Access Control](#role-based-access-control)
- [File Upload Security](#file-upload-security)
- [Malware Scanning and Quarantine](#malware-scanning-and-quarantine)
- [Database Security](#database-security)
- [Deployment Security (Production)](#deployment-security-production)
- [Security Checklist](#security-checklist)

---

## Design Philosophy

TTB Automate applies defense-in-depth: no single control is relied upon exclusively. The layers are:

1. **Network layer** — only two ports exposed to the host; all internal services unreachable from outside the container network
2. **Execution layer** — AI inference runs locally; label images never leave the server
3. **Application layer** — role-based UI rendering; staff fields hidden from industry users
4. **Database layer** — prototype: single audit table, immutable by convention; production design: row-level security policies per table, per-role credentials
5. **File layer** — virus scanning before storage; infected files isolated in a quarantine bucket with admin-only access
6. **Audit layer** — every assessment decision and staff action logged to PostgreSQL with timestamp and user ID

---

## Local Execution — Why No Cloud AI

The single most important security decision in this system is where AI inference happens.

Most AI-powered document review tools send uploaded files to an external API — Google, Anthropic, OpenAI. This creates several problems for a government application:

- **Data sensitivity:** Label images may reveal proprietary formulations, brand strategy, or production details. Applicants have a reasonable expectation that their submissions are not routed through commercial AI providers.
- **Regulatory risk:** Federal systems processing business-confidential information are subject to data handling requirements that cloud AI providers may not satisfy.
- **No outbound dependency:** A cloud API going down, rate-limiting, or changing its pricing takes down the system with it.
- **Auditability:** When inference runs locally, every input and output can be logged, inspected, and reproduced. When it runs in a third-party cloud, it cannot.

TTB Automate resolves this by running `llava:7b` and `qwen2.5:14b` inside Docker on the same machine that hosts the web application. The models are downloaded once at first boot and cached permanently in the `ollama-data` Docker volume. At steady state, **no label images or assessment results leave the server.**

The Anthropic Claude API is available as an optional configuration (`ANTHROPIC_API_KEY` in `.env`) for development or benchmarking purposes only. It should be left blank in any deployment handling real applicant data.

---

## Network Isolation

### Container Network

All six services run on an internal Docker bridge network (`ttb-network`). Container-to-container communication uses service names as hostnames (`assess`, `ollama`, `ocr`, `postgres`). These names are not resolvable from outside the container network.

The only ports mapped to the host are:

| Service | Host Port | Purpose |
|---------|-----------|---------|
| `ttb-app` | 8004 | Web UI and public API |
| `n8n` | 5678 | Workflow automation editor (internal operations use) |

Everything else — PostgreSQL, Ollama, OCR, and the assessment service — is only reachable from within the container network. A process running on the host machine cannot connect to the database directly; it must go through `docker exec` or through the application.

### Production Network Topology (Recommended)

For a deployed environment the recommended topology further isolates components:

```
Internet / User Browsers
          │
          ▼
  [Reverse Proxy — nginx/Caddy with TLS]
          │  port 443 only
          ▼
  [Application Server — Docker host]
    ├── ttb-app   (internal :8004)
    ├── assess    (internal :8000)
    ├── ocr       (internal :8001)
    └── ollama    (internal :11434)
          │                          │
          │  DB connection only      │  operations subnet only
          ▼                          ▼
  [Database — separate VLAN]   [n8n — not internet-facing]
    └── postgres (:5432)          └── n8n (:5678)
```

Key principles:
- TLS terminates at the reverse proxy; the application never handles raw TLS
- Port 5432 is reachable only from the application server IP — not from staff workstations or the public internet
- n8n (port 5678) is restricted to an operations subnet; it is never internet-facing
- All user traffic reaches the application server exclusively through the reverse proxy

---

## Authentication and Authorization

### Demo Mode

In the prototype, authentication is handled by a server-side `demo_role` cookie. Selecting a demo account on the landing page sets this cookie, which the FastAPI application reads on every request to determine the user's role and enforce access control. No external auth service is required, which makes evaluation straightforward.

### Production Authentication

A production deployment would replace the demo cookie with verified identity through Login.gov or ID.me. JWTs issued after authentication would be validated server-side on every request; expired or invalid tokens return HTTP 401 before any application logic runs.

### Role Assignment

Three roles are supported:

| Role | Who | Capabilities |
|------|-----|--------------|
| `industry` | Distilleries, wineries, breweries, importers | Submit labels, view own applications, communicate with staff |
| `staff` | TTB compliance reviewers | Review queue, approve/reject/return, add notes, view all applications |
| `admin` | System administrators | All staff capabilities + audit log + quarantine review + user management |

Role assignment in the demo is determined solely by the `demo_role` cookie set when a user selects an account on the landing page. In production, roles would be assigned by an administrator at onboarding and stored in a dedicated `user_roles` table — kept separate from the profiles table to prevent privilege escalation through profile manipulation.

---

## Role-Based Access Control

The prototype enforces access control at the application layer. The production design adds a second, independent database layer so that any bypass of application logic still cannot expose protected data.

### Prototype: Application-Layer Enforcement (Implemented)

Every page route calls `require_auth(request, allowed_roles)` in `pages.py`, which reads the `demo_role` cookie and redirects to `/auth` if the role is absent or not permitted. Industry routes are blocked from staff; staff routes are blocked from industry; the quarantine page is admin-only. Because this check runs before the template renders, a user cannot reach a restricted page by constructing its URL — the server redirects before any content is produced.

**Template-level field masking:** Jinja2 templates branch on the role passed in the template context. Staff-only fields (AI risk scores, reviewer notes, internal findings) are never emitted in the HTML response to industry users. The fields are absent from the DOM entirely — not hidden with CSS — so client-side manipulation cannot reveal them.

### Production: Database-Layer Enforcement (Design Intent)

A production deployment adds PostgreSQL row-level security as a second, independent enforcement layer. Even if a bug in the application layer were exploited, database policies would prevent cross-user data access:

| Table | Policy |
|-------|--------|
| `profiles` | Users can only SELECT/UPDATE their own row |
| `applications` | Industry users see only their own applications; staff and admin see all |
| `verification_history` | Users can only SELECT their own records |
| `user_roles` | Users can read their own role; staff/admin can read all |
| `quarantined_files` | SELECT and UPDATE for admins only; INSERT by service account only |

**Secure view for field masking at the database layer:** The `applications_industry` view exposes only the fields an industry user is permitted to see, hiding `ai_risk_score`, `ai_verification_result`, `reviewer_notes`, `reviewer_id`, and `reviewed_at`. The view is defined with `security_invoker = true` so it executes under the calling user's permissions and automatically inherits the same row-level restrictions as the underlying table — it cannot be used to read more than the underlying policies allow.

**Prototype trade-off:** The prototype's single audit table (`assessments`) does not yet have RLS enabled; all write access flows through the `assess` service container using a single shared credential. This is appropriate for a controlled demo environment. A production deployment would partition credentials by role and enable per-table RLS before any real applicant data is processed.

---

## File Upload Security

Every label image passes through a four-stage pipeline before it reaches the assessment service.

```
User uploads file
        │
        ▼
[Stage 1] Frontend validation
   · Max 10 MB
   · JPEG, PNG, WebP, GIF only
   · Extension must match MIME type
        │
        ▼
[Stage 2] Virus / malware scan ── Malware detected ──▶ [QUARANTINE]
   · ClamAV-backed API                                    · File isolated in
   · Graceful degradation if unavailable                    private bucket
        │ clean                                           · Record written to
        ▼                                                   quarantined_files
[Stage 3] Client-side compression                        · Upload blocked;
   · Resize to max 2048×2048                               admin notified
   · 85% JPEG quality
        │
        ▼
[Stage 4] Storage + assessment
   · Private bucket: {user_id}/{application_id}/
   · Forwarded to assess service
```

**Extension-to-MIME matching:** A file named `label.jpg` with MIME type `application/pdf` is rejected. This prevents renamed-file attacks where a potentially malicious file is given an innocuous-looking extension.

**Compression rationale:** 2048×2048 at 85% JPEG preserves label text legibility for human review and AI analysis while reducing storage by 50–80% and eliminating multi-hundred-megabyte raw camera uploads from clogging the pipeline.

**Rate limiting:** The scan endpoint enforces a sliding-window rate limit of 20 requests per minute per authenticated user. Submissions exceeding this return HTTP 429 with a `Retry-After` header.

---

## Malware Scanning and Quarantine

Any file upload surface is a potential vector for malware delivery — including image files, which can carry exploit payloads targeting image parsers. Scanning happens before storage, not after.

The `/admin/quarantine` route and role guard are implemented. ClamAV integration and the `quarantined_files` table are production steps. The pipeline diagram in the File Upload Security section above covers the full flow; on a clean scan processing continues, on a threat detection the file is isolated and the upload is blocked before any application record is created.

### Quarantine Database Schema (Production Design)

The prototype routes the `/admin/quarantine` page and enforces admin-only access, but the backing `quarantined_files` table is a production schema — it is not present in the current `init.sql`. In a production deployment this table would be added alongside the RLS policies described above:

```sql
CREATE TABLE public.quarantined_files (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  original_filename TEXT NOT NULL,
  file_size         INTEGER NOT NULL,
  mime_type         TEXT,
  threat_name       TEXT NOT NULL,       -- e.g. "Trojan.Generic"
  storage_path      TEXT NOT NULL,       -- path in quarantine bucket
  uploader_user_id  UUID NOT NULL,
  uploader_ip       TEXT,
  scan_details      JSONB,               -- full scanner response
  status            TEXT DEFAULT 'pending',  -- pending | reviewed | deleted
  reviewed_by       UUID,
  review_notes      TEXT,
  reviewed_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ DEFAULT now()
);
```

### Admin Review Workflow

Once a file is quarantined, an admin reviews it through the `/admin/quarantine` page:

| Status | Meaning | Admin action |
|--------|---------|--------------|
| `pending` | Awaiting review | Review threat details; add notes |
| `reviewed` | Admin has examined the file | Kept for forensic reference |
| `deleted` | Admin has permanently removed the file | File removed from quarantine bucket |

Quarantined files are never automatically deleted. An admin must explicitly mark them for deletion, preserving the audit trail and enabling forensic analysis if a pattern of malicious uploads emerges.

### Graceful Degradation

If the ClamAV service is unavailable, the system logs a warning and allows the upload to proceed — availability over strict blocking. For production, this policy should be inverted: block uploads when scanning is unavailable and queue for retry rather than accepting unscanned files.

### Storage Buckets

| Bucket | Access | Contents |
|--------|--------|---------|
| `label-images` | Owner (read/write) + Staff (read) | Clean label uploads |
| `quarantine` | Admin + service account only | Malware-flagged files |

The quarantine bucket has no public access policy. No signed URLs are generated for general users; admins access quarantined files exclusively through the admin UI.

---

## Database Security

### Connection

PostgreSQL is not exposed to the host network. Only the `assess` service connects to it, using `DATABASE_URL` from the Docker environment. The database port and credential are internal to the container network and unreachable from outside.

### Current Schema

The prototype uses a single `assessments` table in PostgreSQL. All write access flows through the `assess` service container under a single shared credential (`ttb`/`ttb` in the demo). There are no per-table RLS policies in the current `init.sql`; access isolation is handled entirely at the application layer (see Role-Based Access Control above).

```sql
CREATE TABLE assessments (
    id              SERIAL PRIMARY KEY,
    submission_id   TEXT        NOT NULL,
    decision        TEXT        NOT NULL,   -- APPROVE | REVIEW | DENY
    brand_name      TEXT,
    model           TEXT,
    strategy        TEXT,                   -- vision | reconcile
    fields_json     TEXT,
    reasoning       TEXT,
    raw_response    TEXT,                   -- complete LLM output, never truncated
    assessed_at     TIMESTAMP   DEFAULT NOW(),
    human_decision  TEXT,                   -- auditor override: APPROVE | DENY
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMP
);
```

### Row-Level Security (Production Design)

A production deployment would enable RLS on all tables and partition credentials by role so that each container can only perform the operations it requires. The policies described in the Role-Based Access Control section above would be applied before any real applicant data enters the system.

### Audit Log

Every assessment decision written by the `assess` service includes: submission ID, timestamp, strategy used (vision or reconcile), active model, decision (APPROVE/REVIEW/DENY), field-level findings as JSON, and the full reasoning string from the model. The `raw_response` column stores the complete LLM output without truncation for forensic use.

The log is effectively immutable: no application-layer code issues DELETE against `assessments`, and the shared credential has no destructive privilege beyond INSERT and the three auditor-override columns (`human_decision`, `reviewed_by`, `reviewed_at`).

---

## Deployment Security (Production)

| Control | Implementation |
|---------|---------------|
| TLS | Terminate at nginx/Caddy reverse proxy; redirect HTTP → HTTPS |
| Secrets | Environment variables only; never committed to source control |
| Docker images | Pin to specific digests in production; rebuild regularly for CVE patches |
| Database password | Change default `ttb`/`ttb` credentials before any non-demo deployment |
| n8n | Place behind VPN or restrict to operations subnet only; change default `admin`/`ttbexpress` credentials |
| Model storage | `ollama-data` volume should be on encrypted storage in production |
| Log retention | Forward Docker logs to a SIEM (Splunk, Elastic, CloudWatch) for long-term retention |
| Backups | PostgreSQL volume (`postgres-data`) should be backed up on a schedule; `pg_dump` can be run via `docker exec` |

### Changing Default Credentials

Before any non-demo deployment, replace `POSTGRES_PASSWORD` and `N8N_BASIC_AUTH_PASSWORD` in `docker-compose.yml` with strong random values, and update `DATABASE_URL` in the `assess` service environment to match.

---

## Security Checklist

### Before any non-demo deployment

- [ ] Default PostgreSQL credentials changed (`ttb`/`ttb` → strong random)
- [ ] Default n8n credentials changed (`admin`/`ttbexpress` → strong random)
- [ ] `ANTHROPIC_API_KEY` left blank (no cloud AI in production)
- [ ] TLS configured at reverse proxy
- [ ] n8n port (5678) not exposed to public internet
- [ ] Database port (5432) not exposed to host or public network
- [ ] `ollama-data` and `postgres-data` volumes on encrypted storage
- [ ] Docker log forwarding configured for audit retention
- [ ] ClamAV scan service reachable from application server

### File uploads

- [ ] Frontend MIME type + extension validation in place
- [ ] ClamAV scanning endpoint reachable and returning clean responses
- [ ] Quarantine bucket has no public access policy
- [ ] Rate limiting active on scan endpoint (20 req/min per user)
- [ ] Admin quarantine review page tested and accessible

### Access control

- [ ] Demo cookie auth replaced with Login.gov / ID.me in production
- [ ] Staff-only fields not present in industry-facing HTML responses (verify with `curl` + each role cookie)
- [ ] Admin quarantine page returns redirect for non-admin roles (verify with industry and staff cookies)
- [ ] Production: RLS policies applied to all tables before real applicant data enters the system
- [ ] Production: Per-role database credentials replace shared `ttb` credential
- [ ] Production: `applications_industry` secure view with `security_invoker = true` verified against each role

---

## Reporting Security Issues

If you discover a vulnerability:

1. Do not disclose it publicly
2. Contact the development team directly with reproduction steps
3. Allow reasonable time for remediation before disclosure
