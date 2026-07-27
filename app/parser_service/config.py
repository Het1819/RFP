"""Configuration and resource limits for the isolated parser service."""

PROTOCOL_VERSION = "1.0"
PARSER_NAME = "rfp-isolated-parser"
PARSER_VERSION = "1.0.0"

# Resource and output limits
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_UNITS = 500
MAX_CHARS_PER_UNIT = 100_000
MAX_TOTAL_CHARS = 2_000_000
MAX_PARSE_TIME_SECONDS = 15.0
CHUNK_TARGET_CHARS = 4_000

# MIME Types
PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
