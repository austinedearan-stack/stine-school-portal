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


def test_no_csrf_exempt_or_raw_sql_in_apps():
    offenders = []
    for path in (ROOT / "apps").rglob("*.py"):
        text = path.read_text()
        for needle in ("csrf_exempt", ".raw(", ".extra(", "cursor.execute(", "mark_safe("):
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert offenders == []


def test_no_safe_filter_in_templates():
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "templates").rglob("*.html") if "|safe" in p.read_text()]
    assert offenders == []
