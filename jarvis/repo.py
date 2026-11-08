"""Repository & PR helpers for JARVIS (features #37, #38).

The brain reviews code and writes PR descriptions itself; this just gives it reliable,
read-only state via `git` and the authenticated `gh` CLI, so it isn't guessing:

    python -m jarvis.repo status [path]         # branch, dirty files, ahead/behind
    python -m jarvis.repo diff [path] [--staged] # the diff to review (capped)
    python -m jarvis.repo pr-status [path]       # open PRs + CI + review state (gh)
    python -m jarvis.repo deploy-info [path]     # detected host + deploy command (#38)

Everything here is read-only. Actually committing, pushing or deploying stays with the
brain (through its normal tools) and the user's confirmation — see the persona.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .config import resolve_path

MAX_DIFF = 12000


def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError:
        return 127, "git not found"


def _is_repo(cwd: Path) -> bool:
    code, out = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    return code == 0 and out.strip() == "true"


# --------------------------------------------------------------------------- status / diff (#37)

def status(path: str | None) -> dict:
    cwd = resolve_path(path or ".")
    if not _is_repo(cwd):
        return {"repo": False, "note": f"{cwd} is not a git repository."}
    def g(args):
        return _git(args, cwd)[1].strip()
    branch = g(["rev-parse", "--abbrev-ref", "HEAD"])
    dirty = [ln[3:] for ln in _git(["status", "--porcelain"], cwd)[1].splitlines() if ln.strip()]
    ahead = behind = 0
    upstream = g(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if "fatal" not in upstream and upstream:
        counts = g(["rev-list", "--left-right", "--count", f"{upstream}...HEAD"])
        parts = counts.split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])
    remote = g(["remote", "get-url", "origin"])
    return {"repo": True, "branch": branch, "upstream": upstream or None,
            "ahead": ahead, "behind": behind, "dirty_count": len(dirty),
            "dirty": dirty[:50], "remote": remote or None}


def diff(path: str | None, staged: bool) -> dict:
    cwd = resolve_path(path or ".")
    if not _is_repo(cwd):
        return {"repo": False, "note": f"{cwd} is not a git repository."}
    args = ["diff", "--stat"] + (["--staged"] if staged else [])
    stat = _git(args, cwd)[1].strip()
    full = _git(["diff"] + (["--staged"] if staged else []), cwd)[1]
    truncated = len(full) > MAX_DIFF
    return {"repo": True, "staged": staged, "stat": stat,
            "diff": full[:MAX_DIFF], "truncated": truncated}


# --------------------------------------------------------------------------- PRs (#37)

def pr_status(path: str | None) -> dict:
    cwd = resolve_path(path or ".")
    if not shutil.which("gh"):
        return {"ok": False, "note": "The GitHub CLI (gh) isn't installed, so I can't read PRs."}
    if not _is_repo(cwd):
        return {"ok": False, "note": f"{cwd} is not a git repository."}
    fields = "number,title,headRefName,url,isDraft,reviewDecision,statusCheckRollup,updatedAt"
    try:
        p = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--limit", "20", "--json", fields],
            cwd=str(cwd), capture_output=True, text=True)
    except FileNotFoundError:
        return {"ok": False, "note": "gh not found"}
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip()
        if "auth" in msg.lower() or "logged" in msg.lower():
            return {"ok": False, "note": "gh isn't signed in. Run `gh auth login` to let me read PRs."}
        return {"ok": False, "note": msg[:300] or "Couldn't list PRs."}
    try:
        prs = json.loads(p.stdout or "[]")
    except ValueError:
        prs = []
    out = []
    for pr in prs:
        rollup = pr.get("statusCheckRollup") or []
        states = [c.get("conclusion") or c.get("state") or "" for c in rollup]
        failed = sum(1 for s in states if s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"))
        pending = sum(1 for s in states if s in ("PENDING", "QUEUED", "IN_PROGRESS", ""))
        ci = "failing" if failed else "pending" if pending else "passing" if states else "no checks"
        out.append({
            "number": pr.get("number"), "title": pr.get("title"),
            "branch": pr.get("headRefName"), "draft": pr.get("isDraft"),
            "review": (pr.get("reviewDecision") or "none").lower(),
            "ci": ci, "url": pr.get("url"),
        })
    needs = [p for p in out if p["ci"] == "failing" or p["review"] == "changes_requested"]
    return {"ok": True, "count": len(out), "prs": out, "needs_attention": needs}


# --------------------------------------------------------------------------- deploy (#38)

def deploy_info(path: str | None) -> dict:
    root = resolve_path(path or ".")
    pkg = {}
    pj = root / "package.json"
    if pj.exists():
        try:
            pkg = json.loads(pj.read_text(encoding="utf-8"))
        except ValueError:
            pkg = {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}

    platform = command = None
    if (root / "vercel.json").exists() or (root / ".vercel").exists() or "next" in deps:
        platform, command = "vercel", "npx vercel --prod"
    elif (root / "netlify.toml").exists() or (root / ".netlify").exists():
        platform, command = "netlify", "npx netlify deploy --prod"
    elif any((root / ".github" / "workflows" / w).exists()
             for w in ("deploy.yml", "pages.yml", "gh-pages.yml")):
        platform, command = "github-pages", "git push origin main  # triggers the Pages workflow"
    elif (root / "index.html").exists():
        platform, command = "static", "(any static host - e.g. `npx vercel --prod`, or Netlify drop)"

    build = "npm run build" if (pkg.get("scripts") or {}).get("build") else None
    return {
        "platform": platform, "build": build, "deploy_command": command,
        "confirm": True,
        "note": "Deploying is outward-facing: show the build + deploy command and get the "
                "user's explicit yes before running it." if command else
                "No deploy target detected. Ask the user where they host.",
    }


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jarvis.repo", description="JARVIS repo & PR helpers")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "pr-status", "deploy-info"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?", default=".")
    dp = sub.add_parser("diff")
    dp.add_argument("path", nargs="?", default=".")
    dp.add_argument("--staged", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps(status(args.path), indent=2))
    elif args.cmd == "diff":
        r = diff(args.path, args.staged)
        if r.get("repo"):
            print(r["stat"] or "(no changes)")
            if r["diff"].strip():
                print("\n--- diff ---\n" + r["diff"] + ("\n... (truncated)" if r["truncated"] else ""))
        else:
            print(r["note"])
    elif args.cmd == "pr-status":
        print(json.dumps(pr_status(args.path), indent=2))
    elif args.cmd == "deploy-info":
        print(json.dumps(deploy_info(args.path), indent=2))
    else:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
