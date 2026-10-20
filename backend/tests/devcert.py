"""Self-signed TLS certificate for local tests and the single-machine gate.

Pure Python (the `cryptography` package), so no openssl binary is needed.
Valid for `localhost` and `127.0.0.1` only. Never use it beyond one machine.

    cd backend
    python -m tests.devcert ..\\certs        # writes cert.pem and key.pem
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def write_self_signed(directory: Path, days: int = 1) -> tuple[Path, Path]:
    """Write cert.pem and key.pem into `directory`; return their paths."""

    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    public = key.public_key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(public), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a local self-signed certificate")
    parser.add_argument("directory", type=Path, help="Folder for cert.pem and key.pem")
    parser.add_argument("--days", type=int, default=30, help="Validity in days (default 30)")
    args = parser.parse_args(argv)
    cert, key = write_self_signed(args.directory, days=args.days)
    print(f"wrote {cert.resolve()}")
    print(f"wrote {key.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
