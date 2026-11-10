"""デプロイの構成: Cloud Run のイメージに、API が import する自前のモジュールがすべて入っているか。

以前の Dockerfile は ussa.py をコピーしておらず、イメージは起動時に ImportError で落ちる（ローカルのテストでは気づけない）。
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def local_imports(module, seen=None):
    seen = set() if seen is None else seen
    if module in seen:
        return seen
    seen.add(module)
    tree = ast.parse((ROOT / f"{module}.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else [])
        for n in names:
            top = n.split(".")[0]
            if (ROOT / f"{top}.py").exists():
                local_imports(top, seen)
    return seen


def copied_files():
    out = set()
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*COPY\s+(.+)\s+\S+\s*$", line)
        if m:
            out.update(m.group(1).split())
    return out


def test_image_contains_every_local_module_main_imports():
    need = {f"{m}.py" for m in local_imports("main")}
    missing = need - copied_files()
    assert not missing, f"Dockerfile にコピーされていない: {sorted(missing)}"


def test_image_contains_prompts_and_schemas():
    files = copied_files()
    assert "prompts/" in files and "schemas/" in files


def test_health_reports_build_commit(monkeypatch):
    import main
    monkeypatch.setattr(main, "BUILD_COMMIT", "abc1234")
    assert main.healthz() == {"ok": True, "commit": "abc1234"}
