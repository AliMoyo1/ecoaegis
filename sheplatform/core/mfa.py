"""MFA service (audit fix: pyotp/qrcode were installed but never used).

Flow:
  enroll(db, user_id)          -> generates TOTP secret + 10 backup codes (hashed),
                                  returns provisioning_uri for QR display
  confirm_enroll(db, user_id, code) -> validates a code, then enables MFA
  verify(db, user_id, code)    -> checks TOTP or a backup code
  verify_session(db, user_id, raw_token) -> marks the session mfa_verified
"""
from __future__ import annotations

import hashlib
import secrets

import pyotp
import qrcode
import qrcode.image.svg
from sheplatform.database import get_db

ISSUER = "EcoAegis"
BACKUP_CODE_COUNT = 10


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(user_email: str, secret: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=user_email, issuer_name=ISSUER)


def qr_svg(uri: str) -> str:
    """Return an inline SVG data URI for the enrollment QR code."""
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    import io
    buf = io.BytesIO()
    img.save(buf)
    import base64
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


def enroll(db, user_id: int, user_email: str) -> dict:
    """Start MFA enrollment: create secret + backup codes, do NOT enable yet."""
    secret = generate_secret()
    db.execute("UPDATE users SET mfa_secret = %s, mfa_enabled = FALSE WHERE id = %s",
               (secret, user_id))
    db.execute("DELETE FROM mfa_backup_codes WHERE user_id = %s", (user_id,))
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        code = secrets.token_hex(4).upper()  # 8 chars, e.g. A1B2C3D4
        db.execute("INSERT INTO mfa_backup_codes (user_id, code_hash) VALUES (%s, %s)",
                   (user_id, _hash_code(code)))
        codes.append(code)
    db.commit()
    return {
        "secret": secret,
        "uri": provisioning_uri(user_email, secret),
        "backup_codes": codes,
    }


def confirm_enroll(db, user_id: int, code: str) -> dict:
    """Validate the TOTP code, then enable MFA for the user."""
    row = db.execute("SELECT mfa_secret FROM users WHERE id = %s", (user_id,)).fetchone()
    if not row or not row["mfa_secret"]:
        return {"ok": False, "message": "no pending enrollment"}
    totp = pyotp.TOTP(row["mfa_secret"])
    if not totp.verify(code.strip(), valid_window=1):
        return {"ok": False, "message": "invalid code"}
    db.execute("UPDATE users SET mfa_enabled = TRUE WHERE id = %s", (user_id,))
    db.commit()
    return {"ok": True, "message": "MFA enabled"}


def verify(db, user_id: int, code: str) -> dict:
    """Check a TOTP code or one-time backup code."""
    row = db.execute("SELECT mfa_secret, mfa_enabled FROM users WHERE id = %s",
                     (user_id,)).fetchone()
    if not row or not row["mfa_enabled"]:
        return {"ok": False, "message": "MFA not enabled"}

    totp = pyotp.TOTP(row["mfa_secret"])
    if totp.verify(code.strip(), valid_window=1):
        return {"ok": True}

    # backup code: single-use, hashed compare
    hashed = _hash_code(code.strip().upper())
    bc = db.execute(
        "SELECT id FROM mfa_backup_codes WHERE user_id = %s AND code_hash = %s AND used = FALSE",
        (user_id, hashed)).fetchone()
    if bc:
        db.execute("UPDATE mfa_backup_codes SET used = TRUE WHERE id = %s", (bc["id"],))
        db.commit()
        return {"ok": True, "used_backup": True}
    return {"ok": False, "message": "invalid code"}


def verify_session(db, user_id: int, raw_token: str) -> dict:
    """Mark the session as MFA-verified (completes the login challenge)."""
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    cur = db.execute(
        "UPDATE sessions SET mfa_verified = TRUE "
        "WHERE token_hash = %s AND user_id = %s", (token_hash, user_id))
    db.commit()
    return {"ok": cur.rowcount > 0}


def user_mfa_status(db, user_id: int) -> dict:
    row = db.execute("SELECT mfa_enabled, mfa_secret FROM users WHERE id = %s",
                     (user_id,)).fetchone()
    return {"enabled": bool(row and row["mfa_enabled"]),
            "has_secret": bool(row and row["mfa_secret"])}
