"""UNSAID の Gemini 呼び出し（analyze / translate）。Cloud Run からもそのまま import して使う想定。"""
import json
import time
from pathlib import Path

from google import genai
from google.genai import types

PROJECT = "unsaid-devfest"
LOCATION = "global"
MODEL = "gemini-3.8-flash"
# translate だけ軽いモデルに替えて速度を比べられるようにしておく（例: Flash-Lite 系）
TRANSLATE_MODEL = MODEL
# 1回の待ち時間の上限と、超えたときの再試行回数。
# 実測では普段 2〜7 秒、まれに 40〜70 秒かかる外れ値があったため、待たずに打ち直す
TIMEOUT_SEC = 12
RETRIES = 1
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


def _client_():
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=PROJECT,
            location=LOCATION,
            http_options=types.HttpOptions(timeout=TIMEOUT_SEC * 1000),  # ミリ秒
        )
    return _client


def _call(system_file, schema_file, payload, model=MODEL):
    start = time.time()
    last_err = None
    for attempt in range(RETRIES + 1):
        try:
            res = _client_().models.generate_content(
                model=model,
                # 入力は JSON で渡す。システムプロンプトが言語に依存せず常に同じなので、暗黙キャッシュも効きやすい
                contents=json.dumps(payload, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    system_instruction=_load(system_file),
                    response_mime_type="application/json",
                    response_json_schema=_load(schema_file),
                    thinking_config=types.ThinkingConfig(thinking_level="low"),
                ),
            )
            out = json.loads(res.text)
            u = res.usage_metadata
            # 遅い回の原因切り分け用：思考トークンが多いのか、待たされているだけなのか
            out["_meta"] = {
                "attempt": attempt + 1,
                "thoughts": getattr(u, "thoughts_token_count", None),
                "out_tokens": getattr(u, "candidates_token_count", None),
            }
            return out, time.time() - start
        except Exception as e:  # タイムアウト・429・JSON 崩れ（まれ）をまとめて再試行
            last_err = e
    raise last_err


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
        model=TRANSLATE_MODEL,
        payload={
            "direction": direction,
            "source_lang": _lang(source_lang),
            "target_lang": _lang(target_lang),
            "original": original,
            "clarification": clarification,
        },
    )


def warm_up():
    """サーバー起動時に1回呼んで、最初の呼び出しが遅い問題をデモ前に済ませておく。"""
    try:
        analyze("I have a headache.", "en")
    except Exception:
        pass
