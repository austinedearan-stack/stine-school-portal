"""Repository hygiene checks that run with every test run (spec §23, §44)."""

import importlib.util
import sys

from tests.conftest import ROOT


def test_secret_scan_is_clean():
    spec = importlib.util.spec_from_file_location("secret_scan", ROOT / "scripts" / "secret_scan.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["secret_scan"] = module
    spec.loader.exec_module(module)
    assert module.main([]) == 0


def test_secret_scan_detects_planted_secrets(tmp_path):
    spec = importlib.util.spec_from_file_location("secret_scan2", ROOT / "scripts" / "secret_scan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    planted = tmp_path / "leak.py"
    fake_key = "AKIA" + "ABCDEFGHIJKLMNOP"
    planted.write_text(f'DB_PASSWORD = "Zx9#qL2!vB7$"\nKEY = "{fake_key}"\n')
    assert module.main([str(planted)]) == 1


def test_secret_scan_ignores_shell_required_variable_checks(tmp_path):
    spec = importlib.util.spec_from_file_location("secret_scan3", ROOT / "scripts" / "secret_scan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    script = tmp_path / "init.sh"
    script.write_text(': "${DB_PASSWORD:?DB_PASSWORD required}"\n')
    assert module.main([str(script)]) == 0
    fake_password = "Zx9qL2v" + "B7wTq4m"
    script.write_text("DB_PASSWORD=" + f'"{fake_password}"\n')
    assert module.main([str(script)]) == 1


def test_secret_scan_ignores_enum_labels(tmp_path):
    spec = importlib.util.spec_from_file_location("secret_scan4", ROOT / "scripts" / "secret_scan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "choices.py"
    source.write_text('    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED", "Password reset requested"\n')
    assert module.main([str(source)]) == 0


# Migration-only DDL helpers (triggers, sequences, privileges) are the one sanctioned use of raw SQL.
RAW_SQL_ALLOWED = {"apps/core/db_guards.py"}


def test_no_csrf_exempt_or_raw_sql_in_apps():
    offenders = []
    for path in (ROOT / "apps").rglob("*.py"):
        if path.relative_to(ROOT).as_posix() in RAW_SQL_ALLOWED:
            continue
        text = path.read_text()
        for needle in ("csrf_exempt", ".raw(", ".extra(", "cursor.execute(", "mark_safe("):
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert offenders == []


def test_no_safe_filter_in_templates():
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "templates").rglob("*.html") if "|safe" in p.read_text()]
    assert offenders == []


def test_templates_have_no_inline_script_or_style():
    """The CSP forbids inline script and style; keep templates compatible (no handlers, no style attributes)."""
    import re

    pattern = re.compile(r"<script(?![^>]*\bsrc=)|\son[a-z]+\s*=|\sstyle\s*=|<style", re.IGNORECASE)
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "templates").rglob("*.html") if pattern.search(p.read_text())]
    assert offenders == []


def test_row_locks_with_joins_lock_only_the_intended_row():
    """``select_for_update()`` + ``select_related()`` locks every joined row on PostgreSQL (e.g. the semester),
    serialising unrelated requests. Such locks must use ``of=("self",)``."""
    import re

    offenders = []
    for path in (ROOT / "apps").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in UNMOUNTED_LEGACY:
            continue
        for match in re.finditer(r"select_for_update\(([^)]*)\)\s*\.select_related\(", path.read_text()):
            if "of=" not in match.group(1):
                offenders.append(rel)
    assert offenders == []


# Inherited modules that are not mounted yet and are replaced in their feature phase (empty since Phase 7).
UNMOUNTED_LEGACY: set[str] = set()
