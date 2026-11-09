"""Secure form-fill vault (#62): your profile details, encrypted at rest.

An opt-in store of the ordinary details JARVIS needs to help fill forms *with* you —
name, email, phone, postal address, and the like. On Windows it's encrypted with DPAPI
(CryptProtectData), so the data can only be read back by your own logged-in Windows
account; no passphrase to remember, and the file is useless if copied elsewhere.

It will NOT store secrets. Passwords, PINs, OTPs, card numbers, CVVs, SSNs, API keys
and tokens are refused by design — those stay with you and your password manager, in
line with JARVIS's safety rules.

    python -m jarvis.vault set phone "+91 90000 00000"
    python -m jarvis.vault get phone
    python -m jarvis.vault list            # keys only, never values
    python -m jarvis.vault remove phone
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys

from .config import RUNTIME_DIR

VAULT_FILE = RUNTIME_DIR / "vault.dat"


class VaultError(Exception):
    pass


# Things we will never keep — enforced in code, not just by policy.
_FORBIDDEN_KEY = re.compile(
    r"(pass\s*word|passwd|pwd|\bpin\b|\botp\b|\bcvv\b|\bcvc\b|security\s*code|"
    r"card\s*(?:no|number)|credit\s*card|debit\s*card|\bssn\b|social\s*security|"
    r"\bsecret\b|api[\s_-]*key|\btoken\b|private\s*key|passcode|routing\s*(?:no|number)|"
    r"account\s*(?:no|number)|aadhaar|aadhar|pan\s*(?:no|number)|cvn)", re.I)
# A value that looks like a bare card number (13-19 digits, maybe spaced) is refused too.
_CARD_VALUE = re.compile(r"^\s*(?:\d[ -]?){13,19}\s*$")


def _forbidden_reason(key: str, value: str) -> str | None:
    if _FORBIDDEN_KEY.search(key or ""):
        return f"'{key}' looks like a credential or sensitive identifier"
    if _CARD_VALUE.match(value or ""):
        return "that value looks like a card number"
    return None


# --------------------------------------------------------------------------- encryption

def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def to_blob(b: bytes) -> BLOB:
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    blob_in = to_blob(data)
    blob_out = BLOB()
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _have_dpapi() -> bool:
    try:
        import ctypes
        return hasattr(ctypes, "windll")
    except Exception:
        return False


def encryption_mode() -> str:
    return "dpapi" if _have_dpapi() else "plain"


# --------------------------------------------------------------------------- store

def _load() -> dict:
    try:
        wrapper = json.loads(VAULT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw = base64.b64decode(wrapper.get("data", ""))
    if wrapper.get("enc") == "dpapi":
        try:
            raw = _dpapi(raw, encrypt=False)
        except Exception:
            return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _save(fields: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(fields).encode("utf-8")
    mode = encryption_mode()
    if mode == "dpapi":
        try:
            raw = _dpapi(raw, encrypt=True)
        except Exception:
            mode = "plain"
    wrapper = {"enc": mode, "data": base64.b64encode(raw).decode("ascii")}
    VAULT_FILE.write_text(json.dumps(wrapper, indent=2), encoding="utf-8")


def set_field(key: str, value: str) -> None:
    key = (key or "").strip().lower()
    if not key:
        raise VaultError("No field name given.")
    reason = _forbidden_reason(key, value)
    if reason:
        raise VaultError(f"I won't store that — {reason}. Keep secrets in your password manager.")
    fields = _load()
    fields[key] = value
    _save(fields)


def get_field(key: str) -> str | None:
    return _load().get((key or "").strip().lower())


def get_all() -> dict:
    """All stored fields (values included) — for actually filling a form."""
    return _load()


def keys() -> list[str]:
    return sorted(_load().keys())


def remove(key: str) -> bool:
    fields = _load()
    if (key or "").strip().lower() in fields:
        del fields[(key or "").strip().lower()]
        _save(fields)
        return True
    return False


def clear() -> None:
    VAULT_FILE.unlink(missing_ok=True)


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.vault", description="Encrypted form-fill vault (no secrets)")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("set"); s.add_argument("key"); s.add_argument("value", nargs="+")
    g = sub.add_parser("get"); g.add_argument("key")
    sub.add_parser("list")
    r = sub.add_parser("remove"); r.add_argument("key")
    sub.add_parser("clear")

    args = ap.parse_args(argv)
    if args.cmd == "set":
        try:
            set_field(args.key, " ".join(args.value))
        except VaultError as e:
            print(str(e)); return 1
        print(f"Saved {args.key} ({encryption_mode()}-encrypted).")
        return 0
    if args.cmd == "get":
        v = get_field(args.key)
        print(v if v is not None else f"No field {args.key!r}.")
        return 0 if v is not None else 1
    if args.cmd == "remove":
        print("Removed." if remove(args.key) else f"No field {args.key!r}.")
        return 0
    if args.cmd == "clear":
        clear(); print("Vault cleared.")
        return 0
    ks = keys()
    print("Vault is empty." if not ks else "Stored fields: " + ", ".join(ks)
          + f"\n(encryption: {encryption_mode()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
