"""本物の Vertex AI が、schemas/ のすべての JSON Schema を受け付けるかを確かめる（1 スキーマにつき 1 回、短い依頼）。

偽の AI ではスキーマの複雑さの上限は分からない（2026-11-10、meaning.json が 400 INVALID_ARGUMENT で断られていた）。
スキーマを変えたら、本物の Gemini を使う評価の前にこれを流す。

  UNSAID_VERTEX_PROJECT=<プロジェクト ID> GOOGLE_APPLICATION_CREDENTIALS=<ADC> python eval/live_schema_check.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google.genai import types  # noqa: E402

import unsaid_ai  # noqa: E402


def main_():
    client = unsaid_ai._client_()
    bad = 0
    for f in sorted((ROOT / "schemas").glob("*.json")):
        schema = json.loads(f.read_text(encoding="utf-8"))
        try:
            client.models.generate_content(
                model=unsaid_ai.MODEL, contents="Return a minimal valid JSON object. Input: I have a fever.",
                config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=schema,
                                                   thinking_config=types.ThinkingConfig(thinking_level="low")))
            print(f"OK  {f.name}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"NG  {f.name}: {type(e).__name__} {str(e)[:120]}")
    print(f"\n{unsaid_ai.MODEL} @ {unsaid_ai.LOCATION}: {bad} 件が受け付けられなかった")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main_()
