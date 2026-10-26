# core/security.py
import base64
import hashlib
import hmac
import io
import os
from urllib.parse import parse_qs, unquote, urlparse

import pyotp
import qrcode

PBKDF2_ITERATIONS = 260_000

ALGO_MAP = {
    "SHA1": hashlib.sha1,
    "SHA256": hashlib.sha256,
    "SHA512": hashlib.sha512,
}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        _algo, iterations, salt_hex, digest_hex = stored_hash.split("$")
    except (AttributeError, ValueError):
        return False

    salt = bytes.fromhex(salt_hex)
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(iterations)
    )
    return hmac.compare_digest(candidate.hex(), digest_hex)


def make_totp(secret: str, digits: int = 6, period: int = 30, algorithm: str = "SHA1"):
    digest = ALGO_MAP.get((algorithm or "SHA1").upper(), hashlib.sha1)
    return pyotp.TOTP(secret, digits=int(digits), interval=int(period), digest=digest)


def is_valid_secret(secret: str) -> bool:
    if not secret:
        return False
    try:
        pyotp.TOTP(secret).now()
        return True
    except Exception:
        return False


def generate_qr_base64(uri: str) -> str:
    image = qrcode.make(uri)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def parse_otpauth_uri(uri: str) -> dict | None:
    """Parse a standard otpauth://totp/... URI into a TOTP entry dict."""
    try:
        parsed = urlparse((uri or "").strip())
    except Exception:
        return None

    if parsed.scheme.lower() != "otpauth" or parsed.netloc.lower() != "totp":
        return None

    params = parse_qs(parsed.query)
    secret = (params.get("secret") or [None])[0]
    if not secret:
        return None

    issuer = (params.get("issuer") or [None])[0] or ""
    label = unquote(parsed.path.lstrip("/"))
    account = label

    if ":" in label:
        left, right = label.split(":", 1)
        issuer = issuer or left.strip()
        account = right.strip()

    try:
        digits = int((params.get("digits") or ["6"])[0])
        period = int((params.get("period") or ["30"])[0])
    except ValueError:
        digits, period = 6, 30

    algorithm = ((params.get("algorithm") or ["SHA1"])[0] or "SHA1").upper()

    if not is_valid_secret(secret):
        return None

    return {
        "secret": secret,
        "issuer": issuer.strip(),
        "account": account.strip(),
        "digits": digits,
        "period": period,
        "algorithm": algorithm,
    }