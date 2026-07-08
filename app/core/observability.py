import contextvars
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.document import Document
from app.models.job import ProcessingJob
from app.models.project import ProposalProject
from app.models.requirement import Requirement

# Request Correlation ID Context Variable
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

SAFE_ID_REGEX = re.compile(r"^[a-zA-Z0-9\-_]{1,50}$")


class MetricsRegistry:
    # Thread-safe in-memory metrics store for process-level telemetry
    request_counts: ClassVar[dict[tuple[str, str, int], int]] = {}
    request_latencies: ClassVar[dict[tuple[str, str], list[int]]] = {}
    llm_calls: ClassVar[dict[tuple[str, str, str], int]] = {}
    llm_estimated_cost: ClassVar[float] = 0.0
    evidence_validation_failures: ClassVar[int] = 0


class JSONFormatter(logging.Formatter):
    """Structured JSON logging formatter to record application logs safely."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(UTC).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event": record.msg if isinstance(record.msg, str) else str(record.msg),
        }

        # Inject correlation ID if available in request context
        req_id = request_id_var.get()
        if req_id:
            log_data["request_id"] = req_id

        # Copy metadata attributes if set on the record
        for attr in (
            "org_id",
            "user_id",
            "project_id",
            "document_id",
            "job_id",
            "latency_ms",
            "status_code",
            "method",
            "route",
        ):
            if hasattr(record, attr):
                log_data[attr] = str(getattr(record, attr))

        if record.exc_info and record.exc_info[0]:
            log_data["exception_type"] = record.exc_info[0].__name__
            log_data["exception_message"] = str(record.exc_info[1])
            log_data["stack_trace"] = self.formatException(record.exc_info)

        # Sanitize sensitive data to prevent secret/PII leak
        sensitive_keys = {
            "prompt",
            "completion",
            "text",
            "secret",
            "key",
            "token",
            "password",
            "cookie",
            "auth_header",
            "email",
            "hashed_password",
            "document_content",
            "csrf_token",
            "session_secret_key",
            "anthropic_api_key",
        }

        for key in list(log_data.keys()):
            if any(s in key.lower() for s in sensitive_keys):
                if (
                    key in ("email", "user_id")
                    and isinstance(log_data[key], str)
                    and "@" in log_data[key]
                ):
                    parts = log_data[key].split("@")
                    log_data[key] = f"{parts[0][0]}***@{parts[1]}"
                else:
                    log_data[key] = "[REDACTED]"

        # Also sanitize any dynamic extras
        for key, val in record.__dict__.items():
            if (
                key
                not in (
                    "args",
                    "asctime",
                    "created",
                    "exc_info",
                    "exc_text",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "message",
                    "msg",
                    "name",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "stack_info",
                    "thread",
                    "threadName",
                )
                and key not in log_data
            ):
                if any(s in key.lower() for s in sensitive_keys):
                    if key == "email" and isinstance(val, str) and "@" in val:
                        parts = val.split("@")
                        log_data[key] = f"{parts[0][0]}***@{parts[1]}"
                    else:
                        log_data[key] = "[REDACTED]"
                else:
                    try:
                        json.dumps(val)
                        log_data[key] = val
                    except Exception:
                        log_data[key] = str(val)

        return json.dumps(log_data)


def setup_logging() -> None:
    """Configures structured JSON logging for the application and worker processes."""
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    # Set third-party logger levels to avoid noise
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("arq").setLevel(logging.INFO)


def generate_prometheus_metrics(db: Session) -> str:
    """Queries stores to output standard Prometheus text metrics."""
    metrics = []

    # Helper function to append metrics
    def add_metric(
        name: str,
        value: Any,
        label_dict: dict[str, str] | None = None,
        m_type: str = "counter",
        help_text: str = "",
    ) -> None:
        if help_text:
            metrics.append(f"# HELP {name} {help_text}")
            metrics.append(f"# TYPE {name} {m_type}")
        if label_dict:
            labels_str = ",".join(f'{k}="{v}"' for k, v in label_dict.items())
            metrics.append(f"{name}{{{labels_str}}} {value}")
        else:
            metrics.append(f"{name} {value}")

    # Active pilot projects count
    proj_count = db.scalar(select(func.count(ProposalProject.id))) or 0
    add_metric(
        "pilot_projects_active",
        proj_count,
        m_type="gauge",
        help_text="Total active projects in pilot",
    )

    # Document upload metrics
    doc_success = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.processing_status == "succeeded"
            )
        )
        or 0
    )
    doc_failure = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.processing_status == "failed"
            )
        )
        or 0
    )
    add_metric(
        "document_uploads_total",
        doc_success,
        {"status": "success"},
        "counter",
        "Total uploaded documents count",
    )
    add_metric("document_uploads_total", doc_failure, {"status": "failure"}, "counter")

    # Jobs counts
    jobs_summary = db.execute(
        select(
            ProcessingJob.status, ProcessingJob.job_type, func.count(ProcessingJob.id)
        ).group_by(ProcessingJob.status, ProcessingJob.job_type)
    ).all()
    for status, job_type, count in jobs_summary:
        add_metric(
            "processing_jobs_total",
            count,
            {"status": status, "job_type": job_type},
            "counter",
            "Total background jobs count",
        )

    # Job retries
    retries_count = db.scalar(select(func.sum(ProcessingJob.attempts))) or 0
    add_metric(
        "processing_job_retries_total",
        retries_count,
        m_type="counter",
        help_text="Total background job retry attempts",
    )

    # Needs-review and approval metrics
    needs_review = (
        db.scalar(
            select(func.count(Requirement.id)).where(
                Requirement.status == "NEEDS_REVIEW"
            )
        )
        or 0
    )
    approved = (
        db.scalar(
            select(func.count(Requirement.id)).where(Requirement.status == "APPROVED")
        )
        or 0
    )
    needs_evidence = (
        db.scalar(
            select(func.count(Requirement.id)).where(
                Requirement.status == "NEEDS_EVIDENCE"
            )
        )
        or 0
    )
    total_reqs = db.scalar(select(func.count(Requirement.id))) or 0

    add_metric(
        "requirements_total",
        needs_review,
        {"status": "NEEDS_REVIEW"},
        "gauge",
        "Requirements count by status",
    )
    add_metric("requirements_total", approved, {"status": "APPROVED"}, "gauge")
    add_metric(
        "requirements_total", needs_evidence, {"status": "NEEDS_EVIDENCE"}, "gauge"
    )

    approval_rate = approved / total_reqs if total_reqs > 0 else 0.0
    add_metric(
        "requirements_approval_rate",
        round(approval_rate, 4),
        m_type="gauge",
        help_text="Ratio of approved requirements to total",
    )

    # Evidence validation failures
    add_metric(
        "evidence_validation_failures_total",
        MetricsRegistry.evidence_validation_failures,
        m_type="counter",
        help_text="Total evidence candidate or citation validation failures",
    )

    # Export metrics
    exports_count = (
        db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.action.like("EXPORT_%"))
        )
        or 0
    )
    add_metric(
        "proposal_exports_total",
        exports_count,
        m_type="counter",
        help_text="Total proposals or compliance matrices exported",
    )

    # Request metrics (in-memory)
    for (route, method, status), count in MetricsRegistry.request_counts.items():
        add_metric(
            "http_requests_total",
            count,
            {"route": route, "method": method, "status": str(status)},
            "counter",
            "Total HTTP requests handled",
        )

    # Request latency averages
    for (route, method), latencies in MetricsRegistry.request_latencies.items():
        avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
        add_metric(
            "http_request_duration_ms_average",
            round(avg_lat, 2),
            {"route": route, "method": method},
            "gauge",
            "Average HTTP request latency in ms",
        )

    # LLM telemetry (in-memory)
    for (provider, model, operation), count in MetricsRegistry.llm_calls.items():
        add_metric(
            "llm_calls_total",
            count,
            {"provider": provider, "model": model, "operation": operation},
            "counter",
            "Total AI LLM provider calls",
        )

    add_metric(
        "llm_cost_estimated_usd",
        round(MetricsRegistry.llm_estimated_cost, 6),
        m_type="counter",
        help_text="Aggregated cost of all LLM calls in USD",
    )

    return "\n".join(metrics) + "\n"
