# RFP Architect MVP - Staging & Pilot Operations Runbook

This runbook documents the procedures for deployment rehearsals, rollback drills, backup/restore operations, and incident response for the RFP Architect MVP.

---

## 1. Staging Deployment Rehearsal

To execute a staging deployment:
1. Copy the staging environment configuration:
   ```bash
   cp .env.staging.example .env
   ```
2. Edit `.env` to configure your keys (e.g., `SESSION_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`).
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
   powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1 -BaseUrl "http://localhost:8000"
   ```

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
- The failure is isolated to a third-party API outage (e.g. Anthropic/OpenAI API downtime). Instead, toggle `LLM_PROVIDER=fake` in `.env` to restore service in degraded mode.

### Step 2.6: Preserve Uploaded Files and Audit Logs
* **Uploaded Files:** The `storage/uploads` directory is mapped as a persistent volume in Docker Compose. Ensure you do not run `docker compose down -v` (which deletes volumes).
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
Ensure uploaded documents in `storage/uploads` match the database metadata records.
Run a custom test script or perform file list check:
```bash
ls -la storage/uploads/
```
Verify that files referenced in `document` database table exist in the folder.

### Step 3.5: Verify Redis Queue Behavior
Note that Redis persistence (via Append-Only File / AOF) is enabled to protect active background jobs, but it is **not a substitute for PostgreSQL database backups**. 
Verify queue health:
* Check Redis keys: `redis-cli ping` or `redis-cli KEYS "*"`
* Confirm the `worker` service automatically reconnects to Redis and resumes tasks on reboot.

---

## 4. Pilot Support & Triage Escalation
For managing participant bug reports, usability blockers, or AI quality issues reported via the `/feedback` workspace route:
- Refer to [PILOT_TRIAGE_WORKFLOW.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_TRIAGE_WORKFLOW.md) for detailed severity definitions (Blocker, High, Medium, Low), SLA targets, and assignment owners.
- If a security incident is identified (e.g. cross-tenant leakage or auth bypass), immediately execute Step 2 (Rollback Drill) to secure and isolate the stack.

