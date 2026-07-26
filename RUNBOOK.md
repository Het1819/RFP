# RFP Architect MVP - Staging & Pilot Operations Runbook

This runbook documents the procedures for deployment rehearsals, rollback drills, backup/restore operations, and incident response for the RFP Architect MVP.

---

## 1. Staging Deployment Rehearsal

Using the Compose-based production stack (`docker-compose.prod.yml`),
secrets come from mounted files under `secrets/`, not a `.env` file. To
execute a rehearsal:
1. Generate local validation secrets:
   ```bash
   uv run python scripts/generate_local_prod_secrets.py
   uv run python scripts/generate_local_prod_secrets.py --import-anthropic-key
   ```
2. Set the non-secret model name (optional; defaults to `claude-sonnet-4-6`):
   ```bash
   export LLM_MODEL=claude-sonnet-4-6
   ```
3. Build the production Docker image:
   ```bash
   docker build -t rfp-architect-mvp:pilot .
   ```
4. Start the application stack (app, db, redis, worker):
   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```
5. Check service health:
   ```bash
   docker compose -f docker-compose.prod.yml ps
   ```
6. Run the local smoke tests:
   ```bash
   powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1 -BaseUrl "http://127.0.0.1:8000"
   ```

If you are instead deploying to a genuinely separate staging host (not
this repo's Compose stack), `.env.staging.example` remains the template
for that path — copy it to `.env` and replace every placeholder with a
real secret; do not set `LLM_PROVIDER=fake` there either.

---

## 2. Rollback Drill Procedure

If a deployment fails smoke testing or experiences critical failures post-deployment, follow this drill.

### Step 2.1: Identify the Current Deployed Tag / Commit
Inspect the running app version metadata or execute git checks:
```bash
git describe --tags --always
```

### Step 2.2: Determine Rollback Target Tag
Select a stable step tag from the following list of verified points:
- **`pilot-hardening-step12`**: Current stable (CI/CD gates, supply-chain checks, local test runner).
- **`pilot-hardening-step11`**: Production observability, Prometheus metrics, structured logs, KPI dashboard.
- **`pilot-hardening-step10`**: Redis queue-backed worker processing.
- **`pilot-hardening-step9`**: Human review workflow and export approval gating.

### Step 2.3: Revert Code & Rebuild Container
Check out the stable tag and rebuild the pilot image:
```bash
# Check out stable tag
git checkout pilot-hardening-step12

# Rebuild container image
docker build -t rfp-architect-mvp:pilot .

# Redeploy container stack
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d
```

### Step 2.4: Database Migration Rollback Policy
If the failed version introduced new migrations, rollback the database schema:
1. **Identify the target migration:** Check the migration files under `alembic/versions/` to determine the last stable migration revision.
2. **Execute downgrade:**
   ```bash
   docker compose -f docker-compose.prod.yml exec app alembic downgrade <stable-revision-hash>
   ```
   *Caution: Downgrades can result in dropping tables or columns. If the column contains new customer pilot data, do not perform an automatic downgrade without executing a database backup first.*

### Step 2.5: When NOT to Roll Back Automatically
Do not perform automatic schema rollbacks if:
- The database contains new critical records that cannot be recovered after dropping columns/tables.
- The failure is isolated to a third-party API outage (e.g. Anthropic API downtime). Do **not** toggle `LLM_PROVIDER=fake` to "restore service" — the fake provider is rejected at startup outside development/local/test and must never run against real pilot data. During an Anthropic outage, degrade by pausing document-processing jobs (leave them `QUEUED` for retry) rather than swapping providers.

### Step 2.6: Preserve Uploaded Files and Audit Logs
* **Uploaded Files:** Uploaded/source documents live under `LOCAL_STORAGE_PATH` (`/data/storage` in the production Compose config), backed by the `app_storage` named volume. Ensure you do not run `docker compose down -v` (which deletes volumes) -- recreating the `app` container alone (`docker compose up -d --force-recreate app`) does not touch the volume and documents survive.
* **Quarantined Files:** New uploads land first in `QUARANTINE_STORAGE_PATH`, backed by its own named volume — do not confuse this with `LOCAL_STORAGE_PATH`. Every upload is written under an application-generated storage identifier (never the original filename) and is independently classified as a PDF/DOCX candidate; a mismatch is rejected before any parsing, retrieval, or LLM action occurs. A file that passes candidate-type detection is queued for and run through the malware-scan/content-policy stage (see "Malware Scan Operations" below). A document that reaches `CLEAN_PENDING_PROMOTION` has passed that stage — it is still **not** safe to open, download, parse, or send to the LLM; promotion to actually-usable `CLEAN` storage is a later phase (A5d). An operator must never describe a `QUARANTINED`, `VALIDATING`, `SCANNING`, `SCAN_FAILED`, or `CLEAN_PENDING_PROMOTION` document as "scanned and safe," "verified clean," or "malware-free."
* **Quarantine Storage Growth:** In this phase, files written to `QUARANTINE_STORAGE_PATH` — including rejected-type and quarantined uploads — are never automatically deleted; unbounded retention is by design until a later phase implements a retention/cleanup policy. Operators should monitor the `quarantine_storage` volume's disk usage as part of routine operations and escalate before it approaches capacity, since no automatic cleanup mechanism exists to fall back on.
* **Audit Logs:** Ensure the log files under container stdout or host paths are preserved. Do not clear host log locations during container recreation.

### Step 2.7: Verify Rollback
Run the smoke test script to confirm restoration of normal operation:
```bash
powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1
```

---

## 3. Backup and Restore Drill Procedure

The backup/restore procedure must be tested regularly. Under NIST incident response guidelines, recovery must be testable and verified.

### Step 3.1: PostgreSQL Backup Command
Run the backup script (which uses `pg_dump`) to dump the database state:
```bash
# Backup via Docker
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U rfp_user -d rfp_architect -F c -b -v -f /var/lib/postgresql/data/backups/db_backup_$(date +%F).dump
```
Or locally on the host:
```bash
pg_dump -h localhost -U rfp_user -d rfp_architect -F c -b -v -f storage/backups/db_backup.dump
```

### Step 3.2: Restore to a Separate Staging Database First
**Never restore directly over active production data during a drill.** Always verify the backup by restoring it to a separate validation database:
1. Create a temporary validation database:
   ```bash
   docker compose -f docker-compose.prod.yml exec postgres psql -U rfp_user -d postgres -c "CREATE DATABASE rfp_architect_validation;"
   ```
2. Restore the backup dump into the validation database:
   ```bash
   docker compose -f docker-compose.prod.yml exec postgres pg_restore -U rfp_user -d rfp_architect_validation -v "/var/lib/postgresql/data/backups/db_backup_xxxx.dump"
   ```

### Step 3.3: Verify Schema and Content
Connect to the validation database and run a test query to verify record presence:
```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U rfp_user -d rfp_architect_validation -c "SELECT COUNT(*) FROM organization;"
```

### Step 3.4: Verify Uploaded Files / Object Storage
Ensure uploaded documents under `LOCAL_STORAGE_PATH` (`/data/storage/documents`
in the production Compose config, backed by the `app_storage` volume) match
the database metadata records:
```bash
docker compose -f docker-compose.prod.yml exec app ls -la /data/storage/documents/
```
Verify that files referenced in the `document` database table exist in the folder.

### Step 3.5: Verify Redis Queue Behavior
Note that Redis persistence (via Append-Only File / AOF) is enabled to protect active background jobs, but it is **not a substitute for PostgreSQL database backups**. 
Verify queue health:
* Check Redis keys: `redis-cli ping` or `redis-cli KEYS "*"`
* Confirm the `worker` service automatically reconnects to Redis and resumes tasks on reboot.

---

## 4. TLS Certificate Operating Procedure

**No public certificate has been issued or renewed by this project.**
Local validation uses a self-signed certificate
(`scripts/generate_local_tls_cert.py`) that no real browser trusts. This
section is a provider-neutral procedure for when a real deployment is
explicitly authorized (Phase A4 does not perform any step below).

### 4.1 Prerequisites
- A registered, DNS-resolvable hostname pointing at the deployment host's
  public IP (matches `NGINX_SERVER_NAME` / `ALLOWED_HOSTS` /
  `PUBLIC_BASE_URL` exactly).
- Port 80 (and 443) reachable from the public internet for HTTP-01
  challenge validation, if using an ACME HTTP-01 flow.
- An explicit decision on the ACME client/provider (e.g. Certbot, a
  managed load balancer's built-in TLS, or a purchased certificate) --
  not selected or integrated in this phase.

### 4.2 Initial issuance (explicitly authorized activity only)
Initial certificate issuance is a deployment activity requiring explicit
operator authorization -- it is not run automatically by any script in
this repository. Whichever ACME client or CA process is chosen, the
result must be exactly two files:
- a full certificate chain (leaf + intermediates), and
- the matching private key,

placed where `docker-compose.prod.yml`'s `tls_cert`/`tls_key` secrets
expect them (`./secrets/tls_cert.pem`, `./secrets/tls_key.pem` for local
Compose; substitute your real secret-management path for a genuine
deployment). Nginx refuses to start if either file is missing, empty,
malformed, or if the certificate and key do not match.

### 4.3 Renewal frequency
Controlled entirely by the selected ACME client/provider (e.g. Certbot's
default ~60-day renewal cadence for ~90-day Let's Encrypt certificates, or
your CA's documented renewal window). Not automated by this repository.

### 4.4 Configuration validation before reload
Never reload Nginx with unvalidated config or an unverified certificate
pair:
```bash
docker compose -f docker-compose.prod.yml exec nginx nginx -t
```

### 4.5 Atomic certificate replacement
Write the new cert/key pair to their target paths as a single atomic
operation (e.g. write to a temp path on the same filesystem, then
`mv`/rename over the existing files) so Nginx never observes a
half-written certificate.

### 4.6 Graceful Nginx reload
```bash
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
```
This reloads configuration and picks up the new certificate without
dropping in-flight connections. Do not `docker compose restart nginx`
for a routine rotation -- that is a harder restart than necessary.

### 4.7 Certificate-expiry monitoring
Monitor certificate expiry independently of the renewal mechanism itself
(e.g. a scheduled check hitting the TLS endpoint and alerting when
remaining validity drops below a threshold such as 14 days). Not
implemented in this repository.

### 4.8 Renewal dry run
Whichever ACME client is selected, use its dry-run/staging mode (e.g.
Certbot's `--dry-run` against Let's Encrypt's staging environment) before
trusting it against the production certificate. Record the dry-run result
as evidence before relying on automated renewal.

### 4.9 Rollback to the last valid certificate
Keep the previous cert/key pair until the new one is confirmed working
(steps 4.4-4.6 succeed and a real TLS handshake against the new
certificate succeeds). If reload fails or the new certificate is
otherwise bad, restore the previous pair and reload again -- do not leave
Nginx running with a config that failed `nginx -t`.

### 4.10 Emergency handling for an expired or mismatched certificate
1. Confirm the failure with `nginx -t` and a direct TLS handshake test
   (`openssl s_client -connect <host>:443`).
2. Restore the last known-good cert/key pair (4.9) and reload
   immediately -- do not attempt to debug the root cause with the
   deployment in a broken TLS state.
3. Once service is restored, investigate why renewal did not happen in
   time (missed cron/scheduler run, DNS validation failure, rate limiting
   from the CA, etc.) before the next renewal window.
4. If no valid certificate is available at all, taking the edge offline
   (stopping the `nginx` service) is preferable to serving a broken/
   expired-certificate TLS handshake to real users.

---

## 5. Malware Scan Operations (Phase A5c)

Every uploaded document now passes through ClamAV malware scanning and
PDF/DOCX content-policy inspection before it can ever be parsed. This
section covers day-to-day operator response for that stage. See
`DEPLOYMENT.md` section "9b. Malware Scanning & Content-Policy Inspection"
for the full state list, check lists, and resource-limit reference.

### 5.1 Documents stuck in `SCAN_FAILED`
`SCAN_FAILED` is expected transiently — a document is automatically
re-armed to `SCANNING` for another attempt as long as
`scan_attempt_count < SCAN_MAX_ATTEMPTS` (default 3). A document is only
a real operator concern once it has exhausted its attempts.

1. **Confirm whether it is exhausted.** Query the document's
   `scan_attempt_count` and look for the corresponding `AuditEvent` rows
   (action `document_ingestion_transition`, `to: SCAN_FAILED`). The final
   exhausting attempt's `details` JSON includes `scan_exhausted: true`,
   and the worker log carries a matching
   `run_scan: document <id> exhausted scan attempts (N/N), reason=...`
   warning line. If `scan_attempt_count < SCAN_MAX_ATTEMPTS`, no action is
   needed — a retry is already scheduled (or, in `QUEUE_ENABLED=False`
   dev/test mode, already ran inline).
2. **Read the reason code** on the audit event / `document.scan_status`
   to decide what's actually wrong:
   - `SCANNER_UNAVAILABLE` / `SCANNER_TIMEOUT` / `SCANNER_PROTOCOL_ERROR`
     — `clamd` connectivity problem. Check `clamd` health first (5.2).
   - `SIGNATURE_DATABASE_STALE` — `clamd`'s reported signature age exceeds
     `CLAMAV_MAX_SIGNATURE_AGE_HOURS` (default 48h). Check freshclam is
     actually updating inside the `clamd` container:
     ```bash
     docker compose -f docker-compose.prod.yml logs clamd | grep -i freshclam
     ```
   - `SCAN_SIZE_LIMIT_EXCEEDED` — the file exceeded
     `CLAMAV_STREAM_MAX_BYTES`. This should be rare in practice, since it
     is bounded below `MAX_UPLOAD_SIZE` at startup; if seen, it indicates
     the upload limits need reconciling, not a per-document fix.
   - `PDF_INSPECTION_FAILED` — the isolated PDF inspector subprocess
     could not reach a verdict (timeout, resource-limit kill, or an
     unparseable/malformed structure `pypdf` itself rejected). This is a
     genuine "could not determine," distinct from a confirmed policy
     rejection.
   - `QUARANTINE_INTEGRITY_MISMATCH` — the file's on-disk digest no longer
     matches what was recorded at quarantine time (missing file, disk
     issue, or tampering). Treat as a priority incident, not a routine
     retry candidate — investigate the quarantine volume before manually
     retrying.
   - `SCAN_SYSTEM_ERROR` — an unexpected internal exception escaped one of
     the scan/inspection modules (which are each documented to catch
     everything internally). Check worker logs around the matching
     timestamp for the exception type and escalate as a code defect.
3. **Never manually flip an exhausted `SCAN_FAILED` document to
   `CLEAN`/`CLEAN_PENDING_PROMOTION` to "unblock" a user.** Every
   transition must go through `ingestion_state.transition()`'s validated
   state machine and produce a real scan/inspection result — there is no
   supported operator override that marks a document safe without it
   actually passing scanning.
4. If the underlying cause is fixed (e.g. `clamd` restored, signatures
   refreshed) and the document is still sitting exhausted in
   `SCAN_FAILED`, re-queuing requires a fresh scan attempt through the
   normal application path (re-upload, or a supported retry action if one
   is exposed in the UI/job-retry route) — not a direct database edit.

### 5.2 Checking `clamd` health
```bash
# Container-level health (Docker's own healthcheck, using clamd's bundled script)
docker compose -f docker-compose.prod.yml ps clamd

# Direct PING/PONG handshake and version/signature info from inside the worker
docker compose -f docker-compose.prod.yml exec worker python -c "
from app.services import clamav_client
print('connectivity:', clamav_client.check_connectivity())
print('version info:', clamav_client.get_version_info())
"

# clamd container logs (startup, freshclam activity, errors)
docker compose -f docker-compose.prod.yml logs clamd
```
`clamd` has no published host port and receives no secrets — it is only
reachable from `worker` over the private `backend` network. There is
nothing to check from the host directly; always go through `worker` or
`docker compose exec clamd`.

### 5.3 What `/readyz` reports for scanning
`/readyz` (reachable only from inside the private network, never through
the public Nginx edge — see DEPLOYMENT.md "Internal endpoints") includes a
`clamd` connectivity check (`app.core.readiness.check_clamav_connectivity`)
alongside its existing PostgreSQL/Redis/quarantine-storage checks:
- It performs a bounded PING/PONG handshake only — it never scans a file
  and never reports signature age (that is enforced per-scan-attempt
  inside `run_scan`, not at the readiness layer).
- A `503` from `/readyz` with detail `"Scanner not ready"` means `clamd`
  is unreachable or timed out the PING within
  `CLAMAV_CONNECT_TIMEOUT_SECONDS` — check clamd health (5.2) before
  investigating anything else.
- The detail message deliberately omits host/port and any internal error
  detail, matching every other readiness check's no-internal-detail
  convention — do not expect more diagnostic detail from `/readyz` itself;
  use 5.2's direct checks for that.

---

## 6. Pilot Support & Triage Escalation
For managing participant bug reports, usability blockers, or AI quality issues reported via the `/feedback` workspace route:
- Refer to [PILOT_TRIAGE_WORKFLOW.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_TRIAGE_WORKFLOW.md) for detailed severity definitions (Blocker, High, Medium, Low), SLA targets, and assignment owners.
- If a security incident is identified (e.g. cross-tenant leakage or auth bypass), immediately execute Step 2 (Rollback Drill) to secure and isolate the stack.

