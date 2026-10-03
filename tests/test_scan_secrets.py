import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from scan_secrets import scan_text  # noqa: E402

# Built by concatenation so this file never holds a literal key-shaped string.
PREFIX = "sk-or-" + "v1-"
VAR = "OPENROUTER_" + "API_KEY"


def test_flags_real_key():
    assert scan_text(f"x = '{PREFIX}abc123'")


def test_flags_env_assignment():
    assert scan_text(f"{VAR}=abc123")


def test_allows_empty_and_placeholders():
    assert not scan_text(f"{VAR}=")
    assert not scan_text(f'key = os.environ["{VAR}"]')
    assert not scan_text(f"export {VAR}=...")
    assert not scan_text(f"{VAR}=<your key>")


def test_pragma_skips_line():
    assert not scan_text(f"{PREFIX}abc  # secret-scan: allow")
