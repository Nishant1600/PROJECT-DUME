"""Mail account registry for JARVIS (README roadmap: "a multi-account inbox").

You declare your mail accounts once in config.yaml (label, address, what each is for), e.g.

    mail:
      accounts:
        - {label: personal, address: you@gmail.com,     purpose: default / personal}
        - {label: college,  address: you@college.edu,   purpose: university mail}

With that, JARVIS can map "reply from my college account" to the right address, filter and
draft against the correct identity, and tell you plainly when an account you named isn't the
one your Gmail connector is actually signed in to (the connector authorizes one account at a
time — connect another via /mcp to reach it). This module is just the resolver + a short
summary for the brain's context; the brain does the actual reading through the connector.
"""
from __future__ import annotations

import re


def accounts(cfg: dict) -> list[dict]:
    raw = ((cfg or {}).get("mail") or {}).get("accounts") or []
    out = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        label = str(a.get("label") or "").strip()
        address = str(a.get("address") or "").strip()
        if not address:
            continue
        out.append({"label": label or address.split("@")[0],
                    "address": address,
                    "purpose": str(a.get("purpose") or "").strip()})
    return out


def resolve(cfg: dict, phrase: str) -> dict | None:
    """Map a spoken reference ("my college account", "gmail", an address) to an account."""
    accs = accounts(cfg)
    if not accs:
        return None
    p = (phrase or "").lower()
    # explicit address match
    for a in accs:
        if a["address"].lower() in p:
            return a
    # label or purpose word match
    for a in accs:
        words = re.findall(r"[a-z0-9]+", (a["label"] + " " + a["purpose"]).lower())
        if any(w in p for w in words if len(w) >= 3):
            return a
    # "default" / "primary" / "main" -> the one marked default, else the first
    if re.search(r"\b(default|primary|main|usual)\b", p):
        for a in accs:
            if "default" in a["purpose"].lower():
                return a
        return accs[0]
    return None


def default_account(cfg: dict) -> dict | None:
    accs = accounts(cfg)
    if not accs:
        return None
    for a in accs:
        if "default" in a["purpose"].lower():
            return a
    return accs[0]


def context_summary(cfg: dict) -> str:
    """A short block for the brain's context so it knows which accounts exist and their labels."""
    accs = accounts(cfg)
    if not accs:
        return ""
    lines = ["# My mail accounts (map spoken references like \"my college account\" to these)"]
    for a in accs:
        purpose = f" — {a['purpose']}" if a["purpose"] else ""
        lines.append(f"- {a['label']}: {a['address']}{purpose}")
    lines.append("Your Gmail connector is signed in to one account at a time; if I ask about an "
                 "account that isn't the connected one, say so rather than guessing.")
    return "\n".join(lines)
