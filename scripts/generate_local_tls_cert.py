# scripts/generate_local_tls_cert.py
# Generates a LOCAL-VALIDATION-ONLY self-signed TLS certificate + key for
# the Nginx edge boundary.
#
# This certificate is NOT trusted by any real browser or client without
# manually installing it, and it is NOT suitable for production use under
# any circumstances. No real public certificate is requested by this
# script or anywhere else in this phase.
#
# Usage:
#   uv run python scripts/generate_local_tls_cert.py
#   uv run python scripts/generate_local_tls_cert.py --hostname rfp.local --force
#
# Requires the `openssl` CLI to be available on PATH. Writes only under the
# gitignored secrets/ directory. Never accepts secret material as a
# command-line argument (there is none to accept here -- the private key is
# generated locally and never leaves the filesystem).

import argparse
import os
import subprocess
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SECRETS_DIR = "secrets"
CERT_FILENAME = "tls_cert.pem"
KEY_FILENAME = "tls_key.pem"


def _secrets_dir_path() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, SECRETS_DIR)


def _require_openssl() -> None:
    try:
        subprocess.run(
            ["openssl", "version"], capture_output=True, check=True, text=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            "ERROR: the 'openssl' CLI is required and was not found on PATH."
        ) from exc


def generate(hostname: str, force: bool) -> int:
    secrets_dir = _secrets_dir_path()
    os.makedirs(secrets_dir, exist_ok=True)
    try:
        os.chmod(secrets_dir, 0o700)
    except OSError:
        pass

    cert_path = os.path.join(secrets_dir, CERT_FILENAME)
    key_path = os.path.join(secrets_dir, KEY_FILENAME)

    if (os.path.exists(cert_path) or os.path.exists(key_path)) and not force:
        print(
            f"SKIP: {CERT_FILENAME} and/or {KEY_FILENAME} already exist. "
            "Use --force to overwrite."
        )
        return 1

    _require_openssl()

    # SANs cover localhost plus the operator-supplied local test hostname
    # (e.g. the NGINX_SERVER_NAME used for local Compose validation).
    san_hosts = {"localhost", hostname}
    san_entries = ",".join(f"DNS:{h}" for h in sorted(san_hosts))
    san_entries += ",IP:127.0.0.1"

    subject = "/CN=" + hostname

    result = subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            key_path,
            "-out",
            cert_path,
            "-days",
            "365",
            "-subj",
            subject,
            "-addext",
            f"subjectAltName={san_entries}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("ERROR: openssl failed to generate the certificate.")
        print(result.stderr.strip())
        return 1

    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass

    san_list = ", ".join(sorted(san_hosts))
    print(f"Wrote: {CERT_FILENAME}, {KEY_FILENAME} (SANs: {san_list})")
    print(
        "\nThis is a LOCAL VALIDATION certificate only -- self-signed, not "
        "trusted by any real client, and not suitable for production. No "
        "public certificate was requested."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a local-validation-only self-signed TLS certificate "
            "for the Nginx edge boundary. Never use the output in production."
        )
    )
    parser.add_argument(
        "--hostname",
        default="localhost",
        help="Local test hostname to include as a SAN (default: localhost).",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing cert/key files."
    )
    args = parser.parse_args()
    return generate(args.hostname, args.force)


if __name__ == "__main__":
    sys.exit(main())
