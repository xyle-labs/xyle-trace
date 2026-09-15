"""Reject private material in the index, working tree, and all reachable history.

Diagnostics contain rule names and object IDs, never matching content or filenames.
Credential detection is handled separately by Gitleaks in hooks and CI.
"""

import argparse
import fnmatch
import re
import subprocess
from pathlib import Path, PurePosixPath

PRIVATE_DIRS = {
    "projects", "misc", ".lineage", ".superpowers", ".aws", ".ssh", ".azure", ".kube",
    ".codex", ".agents", ".cursor", ".continue", ".config", ".gnupg", ".vscode", ".idea",
    "sessions", "transcripts", "conversations", "chats", "logs", "backups", ".venv",
    "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build",
}
PRIVATE_NAMES = {
    ".env", ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".bash_history",
    ".zsh_history", ".python_history", "credentials", "credentials.json", "auth.json",
    "secrets.json", "secrets.yaml", "secrets.yml", "settings.local.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".ds_store",
}
PRIVATE_GLOBS = (
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.db", "*.db-*",
    "*.sqlite", "*.sqlite3", "*.sqlite-*", "*.sqlite3-*", "*.tfstate", "*.tfstate.*",
    "*.tfvars", "*.tfvars.json", "*.jsonl", "*.ndjson", "*.log", "*.log.*",
    "*.zip", "*.gz", "*.tar", "*.tgz", "*.7z", "*.bz2", "*.xz", "*.bundle",
    "*.pdf", "*.xlsx", "*.xls", "*.docx", "*.doc", "*.bak", "*.swp", "*.pyc",
    ".aider*", "*chat-session*", "*chat_history*", "*chat-history*", "*transcript*",
    "*conversation*", "session-*.json", "session-*.md", "rollout-*",
)
CLAUDE = re.compile(r"^co-authored-by:\s*.*(?:\bclaude\b|@anthropic\.com)", re.IGNORECASE | re.MULTILINE)
EMAIL = re.compile(r"[\w.+%-]+(?:\[bot\])?@(?:[\w-]+\.)+[a-zA-Z]{2,}")
PUBLIC_EMAILS = {
    "jesse@xyle.de", "no-reply@xyle.de",
    "jesse@j-apps.com",
    "noreply@github.com", "noreply@anthropic.com", "support@github.com",
    "49699333+dependabot[bot]@users.noreply.github.com",
}
# Exact project, owner-approved Jesse-jApps, and automation identities.
PUBLIC_IDENTITIES = {
    "Xyle Labs <jesse@xyle.de>", "Xyle Labs <no-reply@xyle.de>",
    "Jesse <no-reply@xyle.de>",
    "Jesse <jesse@j-apps.com>",
    "GitHub <noreply@github.com>",
    "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
}
CONTENT_RULES = {
    "personal home path": re.compile(r"(?i)(?:/(?:Users|home)/[^/\s]+|[a-z]:\\+Users\\+[^\\\s]+)"),
    "chat/session link": re.compile(
        r"https?://(?:claude\.ai/(?:share|chat|code)|chatgpt\.com/(?:c|share|codex)|"
        r"chat\.openai\.com/(?:c|share)|cursor\.com/(?:s|agents)|"
        r"aistudio\.google\.com/(?:prompts|app/prompts)|gemini\.google\.com/share)/", re.IGNORECASE
    ),
    "conversation record": re.compile(
        r'''["']role["']\s*:\s*["'](?:user|assistant|system)["']|'''
        r"<\|(?:im_start|im_end)\|>|<(?:user|assistant)>|"
        r'''["'](?:session_id|conversation_id)["']\s*:''', re.IGNORECASE
    ),
}
MAX_BYTES = 1024 * 1024


def private_path(value):
    path = PurePosixPath(value.lower())
    return (
        bool(PRIVATE_DIRS.intersection(path.parts))
        or (".claude" in path.parts and value != ".claude/settings.json")
        or path.name in PRIVATE_NAMES
        or (path.name.startswith(".env.") and path.name != ".env.example")
        or any(fnmatch.fnmatchcase(path.name, pattern) for pattern in PRIVATE_GLOBS)
    )


def content_findings(data):
    if len(data) > MAX_BYTES:
        return {"file exceeds text review limit"}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"non-text content"}
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        return {"non-text content"}
    findings = {name for name, pattern in CONTENT_RULES.items() if pattern.search(text)}
    for match in EMAIL.finditer(text):
        email = match.group().lower()
        domain = email.rsplit("@", 1)[1]
        if (email not in PUBLIC_EMAILS
                and domain not in {"example.com", "example.org", "example.net", "example.test"}):
            findings.add("non-public email address")
    return findings


def git(*args):
    return subprocess.check_output(["git", "--no-replace-objects", *args],
                                   stderr=subprocess.PIPE)


def blob(oid):
    if int(git("cat-file", "-s", oid)) > MAX_BYTES:
        return b"x" * (MAX_BYTES + 1)  # Size finding without loading an oversized blob.
    return git("cat-file", "blob", oid)


def report(findings, location):
    for finding in sorted(findings):
        print(f"Repository policy: {finding} ({location})")
    return bool(findings)


def inspect_file(path, data, mode, location):
    findings = content_findings(path.encode("utf-8")) | content_findings(data)
    if private_path(path):
        findings.add("private/generated path")
    if mode not in {"100644", "100755"}:
        findings.add("symlink, submodule, or unresolved index entry")
    return report(findings, location)


def inspect_identity(identity, location):
    # git var adds a timestamp and timezone after the identity.
    identity = identity.split(">", 1)[0] + ">"
    return report({"non-public commit identity"} if identity not in PUBLIC_IDENTITIES else set(),
                  location)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--history", action="store_true")
    mode.add_argument("--commit-msg", type=Path)
    args = parser.parse_args()
    if args.commit_msg:
        data = args.commit_msg.read_bytes()
        findings = content_findings(data)
        if CLAUDE.search(data.decode("utf-8", errors="replace")):
            findings.add("Claude co-author attribution")
        return int(report(findings, "commit message"))

    failed = False
    if args.history:
        if git("rev-parse", "--is-shallow-repository").strip() == b"true":
            return int(report({"full history required; fetch without a depth limit"}, "repository"))
        seen = set()
        for commit in git("rev-list", "--all").decode().splitlines():
            metadata = git("show", "-s", "--format=%an <%ae>%n%cn <%ce>%n%B", commit)
            identities = metadata.decode("utf-8", errors="replace").splitlines()[:2]
            for identity in identities:
                failed |= inspect_identity(identity, commit[:12])
            findings = content_findings(metadata)
            if CLAUDE.search(metadata.decode("utf-8", errors="replace")):
                findings.add("Claude co-author attribution")
            failed |= report(findings, commit[:12])
            for entry in git("ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
                if not entry or entry in seen:
                    continue
                seen.add(entry)
                header, path = entry.split(b"\t", 1)
                mode, kind, oid = header.decode().split()
                data = blob(oid) if kind == "blob" else b""
                failed |= inspect_file(path.decode("utf-8", errors="replace"), data,
                                       mode, oid[:12])
        # Tools can create refs pointing directly to trees or blobs. rev-list's
        # commit traversal does not inspect these snapshots.
        for ref in git("for-each-ref", "--format=%(objectname) %(objecttype)").splitlines():
            oid, kind = ref.decode().split()
            if kind == "blob":
                failed |= report(content_findings(blob(oid)), oid[:12])
            elif kind == "tree":
                for entry in git("ls-tree", "-rz", oid).split(b"\0"):
                    if not entry or entry in seen:
                        continue
                    seen.add(entry)
                    header, path = entry.split(b"\t", 1)
                    mode, kind, child = header.decode().split()
                    failed |= inspect_file(path.decode("utf-8", errors="replace"),
                                           blob(child) if kind == "blob" else b"",
                                           mode, child[:12])
        # Annotated tags and notes may carry private text outside commit messages.
        for entry in git("for-each-ref", "--format=%(refname)%00%(taggername) "
                         "%(taggeremail)%00%(contents)").split(b"\0"):
            failed |= report(content_findings(entry), "reference metadata")
    elif args.staged:
        for kind in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
            failed |= inspect_identity(git("var", kind).decode().strip(), kind)
        for entry in git("ls-files", "--stage", "-z").split(b"\0"):
            if not entry:
                continue
            header, path = entry.split(b"\t", 1)
            mode, oid, stage = header.decode().split()
            data = blob(oid) if mode != "160000" else b""
            failed |= inspect_file(path.decode("utf-8", errors="replace"), data,
                                   mode if stage == "0" else "unmerged", oid[:12])
    else:
        paths = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        for index, name in enumerate(sorted(set(paths.split(b"\0")))):
            if not name:
                continue
            path = Path(name.decode("utf-8"))
            if path.is_symlink():
                failed |= report({"symlink"}, f"working file {index}")
            elif path.exists():
                with path.open("rb") as stream:
                    failed |= inspect_file(path.as_posix(), stream.read(MAX_BYTES + 1), "100644",
                                           f"working file {index}")
    return int(failed)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError):
        raise SystemExit("Repository policy scan failed; refusing to proceed.") from None
