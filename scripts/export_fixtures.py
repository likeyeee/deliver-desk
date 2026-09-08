"""Export local synthetic test pages; never requests the real website."""

import ast
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
tree = ast.parse((root / "tests/test_browser.py").read_text(encoding="utf-8"))
fixtures = {
    node.targets[0].id: ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign)
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id.endswith("_HTML")
}
print(json.dumps(fixtures, ensure_ascii=False))
