"""UNSAID の Gemini 呼び出し（analyze / translate）。Cloud Run からもそのまま import して使う想定。"""
import json
import time
from pathlib import Path

from google import genai
from google.genai import types

PROJECT = "unsaid-devfest"
LOCATION = "global"
MODEL = "gemini-3.8-flash"
BASE = Path(__file__).parent

# 言語を増やすときはここに1行足すだけ（プロンプトは変更不要）
LANGS = {
    "ja": "Japanese",
    "en": "English",
    "zh-CN": "Simplified Chinese",
    "vi": "Vietnamese",
}

# 「うまく説明できない」「自分で書く」はモデルに作らせず UI が固定で足す。
# id で判定できるので、どの言語でも「自分で書く」→テキスト入力 の分岐が確実に動く。
FIXED_OPTIONS = {
    "ja": {"unsure": "うまく説明できない", "free_text": "自分で書く"},
    "en": {"unsure": "I can't explain it well", "free_text": "I'll write it myself"},
    "zh-CN": {"unsure": "我说不清楚", "free_text": "我自己写"},
    "vi": {"unsure": "Tôi không giải thích được", "free_text": "Tôi sẽ tự viết"},
}

_client = None
_files = {}


def _load(rel):
    if rel not in _files:
        text = (BASE / rel).read_text(encoding="utf-8")
        _files[rel] = json.loads(text) if rel.endswith(".json") else text
    return _files[rel]


def _lang(code):
    return f"{code} ({LANGS.get(code, code)})"


def _call(system_file, schema_file, payload):
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    start = time.time()
    res = _client.models.generate_content(
        model=MODEL,
        # 入力は JSON で渡す。システムプロンプトが言語に依存せず常に同じなので、暗黙キャッシュも効きやすい
        contents=json.dumps(payload, ensure_ascii=False),
        config=types.GenerateContentConfig(
            system_instruction=_load(system_file),
            response_mime_type="application/json",
            response_json_schema=_load(schema_file),
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        ),
    )
    return json.loads(res.text), time.time() - start


def analyze(utterance, patient_lang, doctor_lang="ja"):
    """患者の発言 → facts と確認質問（最大1つ、患者の言語）。"""
    out, sec = _call(
        "prompts/analyze_system.txt",
        "schemas/analyze.json",
        {"patient_lang": _lang(patient_lang), "doctor_lang": _lang(doctor_lang), "utterance": utterance},
    )
    # 念のためコード側でも最大1問を保証する
    out["clarifications"] = out.get("clarifications", [])[:1]
    out["needs_clarification"] = bool(out["clarifications"])
    for c in out["clarifications"]:
        c["fixed_options"] = FIXED_OPTIONS.get(patient_lang, FIXED_OPTIONS["en"])
    return out, sec


def translate(original, source_lang, target_lang, direction, clarification=None):
    """確認後の翻訳。direction は "patient_to_doctor" か "doctor_to_patient"。

    clarification は {"target", "question", "answer_type": option|free_text|unsure, "answer"} か None。
    """
    return _call(
        "prompts/translate_system.txt",
        "schemas/translate.json",
        {
            "direction": direction,
            "source_lang": _lang(source_lang),
            "target_lang": _lang(target_lang),
            "original": original,
            "clarification": clarification,
        },
    )
