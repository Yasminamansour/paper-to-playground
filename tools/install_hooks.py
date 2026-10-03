"""Install the secret scan as .git/hooks/pre-commit (Linux, macOS, Git for Windows)."""
import stat
import subprocess
from pathlib import Path

HOOK = """#!/bin/sh
# Blocks commits that contain an OpenRouter key. Installed by tools/install_hooks.py.
root="$(git rev-parse --show-toplevel)"
for py in "$root/.venv/Scripts/python.exe" "$root/.venv/bin/python" python python3 py; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c "" >/dev/null 2>&1; then
    exec "$py" "$root/tools/scan_secrets.py"
  fi
done
echo "pre-commit: no working Python found, commit blocked" >&2
exit 1
"""


def main():
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    hooks = Path(subprocess.check_output(["git", "rev-parse", "--git-path", "hooks"], text=True, cwd=root).strip())
    hooks = hooks if hooks.is_absolute() else root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(HOOK, newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed {hook}")


if __name__ == "__main__":
    main()
