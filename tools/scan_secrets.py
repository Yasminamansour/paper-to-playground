"""Pre-commit secret scan. Exits 1 if a staged file looks like it holds an OpenRouter key."""
import re
import subprocess
import sys

KEY_RE = re.compile(r"sk-or-v1-[A-Za-z0-9_\-]+")
ASSIGN_RE = re.compile(r"OPENROUTER_API_KEY\s*[=:]\s*[\"']?([^\s\"'#]+)")
PLACEHOLDERS = ("$", "<", "(", "...", "your", "os.", "None", "env")
# Files that quote fake keys on purpose (docs with test commands). Keep this list short.
ALLOW_PATHS = {"docs/ROADMAP.md"}
PRAGMA = "secret-scan: allow"


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def staged_files():
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [f for f in out.decode("utf-8", "replace").split("\0") if f]


def scan_text(text):
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if PRAGMA in line:
            continue
        if KEY_RE.search(line):
            hits.append((n, "sk-or-v1- key"))
            continue
        m = ASSIGN_RE.search(line)
        if m and not m.group(1).startswith(PLACEHOLDERS):
            hits.append((n, "OPENROUTER_API_KEY with a value"))
    return hits


def main():
    found = False
    for path in staged_files():
        if path in ALLOW_PATHS:
            continue
        blob = git("show", f":{path}")  # staged content, not working tree
        if b"\0" in blob[:8000]:
            continue  # skip binary
        for n, why in scan_text(blob.decode("utf-8", "replace")):
            print(f"SECRET? {path}:{n}: {why}", file=sys.stderr)
            found = True
    if found:
        print("Commit blocked. Remove the secret (and revoke the key if it was real).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
