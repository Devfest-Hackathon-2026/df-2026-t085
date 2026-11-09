"""UNSAID の Gemini 呼び出し（analyze / translate）。Cloud Run からもそのまま import して使う想定。"""
import difflib
import json
import os
import random
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from google import genai
from google.genai import types

# Gemini（Vertex AI）を呼ぶプロジェクトと場所。Firebase・Firestore・Speech-to-Text の接続先とは別（main.FIREBASE_PROJECT、stt.py）。
# 評価だけを別のプロジェクトで流すときは環境変数で替える（コードの変更は不要。既定はチームのプロジェクト）:
#   UNSAID_VERTEX_PROJECT=<プロジェクト ID> UNSAID_VERTEX_LOCATION=global python eval/compare.py --live
# global は Gemini 3.8 Flash の公式の提供先（GA）。処理する場所は保証されないので、実データでは us / eu などを検討する
PROJECT = os.environ.get("UNSAID_VERTEX_PROJECT", "unsaid-devfest")
LOCATION = os.environ.get("UNSAID_VERTEX_LOCATION", "global")
MODEL = os.environ.get("UNSAID_MODEL", "gemini-3.8-flash")
# translate だけ軽いモデルに替えて速度を比べられるようにしておく（例: Flash-Lite 系）
TRANSLATE_MODEL = os.environ.get("TRANSLATE_MODEL", MODEL)
# USSA の役割ごとのモデル（Cloud Run の環境変数で替えられる）。既定はすべて同じモデル。
# 検証（VERIFY_MODEL）を翻訳と別のモデルにすると、同じ誤りを共有しにくくなる（それでも一致は正しさの証明ではない）。
# 重要な意味の検証を軽いモデルにするのは、実測で精度が落ちないと分かるまでしない（DESIGN_DECISIONS.md）
SOURCE_MODEL = os.environ.get("SOURCE_MODEL", MODEL)
VERIFY_MODEL = os.environ.get("VERIFY_MODEL", MODEL)
AUDIT_MODEL = os.environ.get("AUDIT_MODEL", MODEL)
# 1回の待ち時間の上限と、超えたときの再試行回数。
# 実測では普段 2〜7 秒、まれに 40〜70 秒かかる外れ値があったため、待たずに打ち直す
TIMEOUT_SEC = 12
RETRIES = 1
# 429（RESOURCE_EXHAUSTED）は Gemini の共有キャパシティの一時的な混雑。すぐ打ち直すと同じく弾かれるので、
# 少しずつ間隔を空けて（1→2→4秒＋揺らぎ）最大 RETRIES_429 回まで再試行する。5xx も同じ扱い
RETRIES_429 = 4
BACKOFF_BASE_SEC = 1.0
# 混雑（429 / 5xx）が続いたときに切り替える予備のモデル。空なら切り替えない。
# デモ当日は他のチームも Gemini を叩くので、Cloud Run の環境変数で入れておく: --update-env-vars FALLBACK_MODEL=<モデル名>
# 入れてあるときは、元のモデルで 2 回弾かれた時点で予備に切り替える（待ち時間を伸ばさないため）
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "")
FALLBACK_AFTER = 2
# 遅い回の対策（hedged request）。HEDGE_SEC 秒たっても返らなければ、同じ依頼をもう1本投げて早い方を使う。
# 実測（eval/results.md）では中央値 約4秒に対し、まれに 16〜25 秒かかる回があった。遅い回は混雑による待ちや再試行なので、
# 投げ直した方が先に返ることが多い。追加の費用は遅い回の分だけ。0 にすると使わない（Cloud Run の環境変数 HEDGE_SEC）
HEDGE_SEC = float(os.environ.get("HEDGE_SEC", "5"))
# 患者の待ち時間対策（docs/ai-pipeline.md「待ち時間を縮める」）
#   split : 確認質問（clarify）と facts を別々の呼び出しにして並列に投げる。患者が待つのは出力の短い clarify だけ
#   single: 従来どおり1回の呼び出しで facts と確認質問をまとめて作る（品質が落ちたらこちらに戻す）
# Cloud Run の環境変数で切り替えられる: --update-env-vars ANALYZE_MODE=single
ANALYZE_MODE = os.environ.get("ANALYZE_MODE", "split")
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

# 医師が送った確認質問に患者が付けられる固定の答え（モデルに作らせない）。医師画面には doctor の文言で出す
ANSWER_FIXED = {
    "ja": {"unsure": "分からない", "none": "どれでもない"},
    "en": {"unsure": "I don't know", "none": "None of these"},
    "zh-CN": {"unsure": "我不知道", "none": "都不是"},
    "vi": {"unsure": "Tôi không biết", "none": "Không cái nào đúng"},
}
ANSWER_FIXED_DOCTOR = {"unsure": "（患者: 分からない）", "none": "（患者: どれにも当てはまらない）"}

# 理解の確認（teach-back）の依頼文。毎回同じ文にしたいのでモデルに作らせない。
# 「あなたが理解できたか」ではなく「私の説明が分かりやすかったか」と聞く（teach-back の基本。患者を試さない）
TEACHBACK_PROMPT = {
    "ja": "説明が伝わったか確かめたいので、理解した内容をご自分の言葉で教えてください。",
    "en": "To make sure I explained it clearly, please tell me in your own words what you understood.",
    "zh-CN": "为了确认我说明得清楚，请用你自己的话说一说你理解了什么。",
    "vi": "Để chắc chắn tôi đã giải thích rõ, bạn hãy nói lại bằng lời của mình những gì bạn đã hiểu.",
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


def _status(e):
    return getattr(e, "code", None) or getattr(e, "status_code", None)


# hedged request 用。main.py が clarify / facts / 翻訳を投げる _pool とは分ける
# （_pool のスレッドの中からさらに _pool に投げると、混んだときにお互いを待って止まるため）
_hedge_pool = ThreadPoolExecutor(max_workers=32)


def _call(system_file, schema_file, payload, model=MODEL):
    """Gemini を1回呼ぶ。HEDGE_SEC を過ぎても返らなければ、同じ依頼をもう1本投げて先に成功した方を返す。"""
    if HEDGE_SEC <= 0:
        return _call_once(system_file, schema_file, payload, model)
    start = time.time()
    args = (system_file, schema_file, payload, model)
    first = _hedge_pool.submit(_call_once, *args)
    done, _ = wait([first], timeout=HEDGE_SEC)
    if done:
        return first.result()
    print(f"  [hedge] {HEDGE_SEC:.0f}秒たっても返らないので、同じ依頼をもう1本投げる")
    pending, err = {first, _hedge_pool.submit(_call_once, *args)}, None
    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for f in done:
            if f.exception() is None:
                out, _ = f.result()
                out["_meta"] = {**out.get("_meta", {}), "hedged": True, "winner": "first" if f is first else "second"}
                return out, time.time() - start
            err = f.exception()
    raise err


def _call_once(system_file, schema_file, payload, model=MODEL):
    start = time.time()
    timeouts = busy = 0
    while True:
        attempt = timeouts + busy + 1
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
                "attempt": attempt,
                "thoughts": getattr(u, "thoughts_token_count", None),
                "out_tokens": getattr(u, "candidates_token_count", None),
            }
            return out, time.time() - start
        except Exception as e:
            code = _status(e)
            if code == 429 or (isinstance(code, int) and code >= 500):
                busy += 1
                if FALLBACK_MODEL and model != FALLBACK_MODEL and busy >= FALLBACK_AFTER:
                    print(f"  [fallback] {model} が混雑（{code}）→ {FALLBACK_MODEL} に切り替え")
                    model, busy = FALLBACK_MODEL, 0
                    continue
                if busy > RETRIES_429:
                    raise
                wait = BACKOFF_BASE_SEC * 2 ** (busy - 1) + random.uniform(0, 0.5)
            elif isinstance(code, int):
                raise  # 400 や 403 は打ち直しても直らない（スキーマや権限の問題）
            else:
                timeouts += 1  # タイムアウト・通信エラー・JSON 崩れ
                if timeouts > RETRIES:
                    raise
                wait = 0
            print(f"  [retry] attempt {attempt} failed after {time.time() - start:.1f}s "
                  f"({code or type(e).__name__}), {wait:.1f}s 待って再試行")
            time.sleep(wait)


def _payload(utterance, patient_lang, doctor_lang):
    return {"patient_lang": _lang(patient_lang), "doctor_lang": _lang(doctor_lang), "utterance": utterance}


def _fix_clarifications(out, utterance, patient_lang):
    # 念のためコード側でも最大1問を保証する
    out["clarifications"] = out.get("clarifications", [])[:1]
    out["needs_clarification"] = bool(out["clarifications"])
    for c in out["clarifications"]:
        snapped = _snap_target(c["target"], utterance)
        if snapped != c["target"]:
            c["target_raw"] = c["target"]  # モデルが言い換えた元の値（テストで警告表示）
            c["target"] = snapped
        c["fixed_options"] = FIXED_OPTIONS.get(patient_lang, FIXED_OPTIONS["en"])
    return out


def clarify(utterance, patient_lang, doctor_lang="ja"):
    """確認質問だけ（最大1つ、患者の言語）。出力が短いので速い。患者が待つのはこの呼び出し。"""
    out, sec = _call("prompts/clarify_system.txt", "schemas/clarify.json",
                     _payload(utterance, patient_lang, doctor_lang))
    return _fix_clarifications(out, utterance, patient_lang), sec


def facts(utterance, patient_lang, doctor_lang="ja"):
    """facts だけ（医師画面の補助表示）。患者は待たないので、確認質問の後に届いてよい。"""
    return _call("prompts/facts_system.txt", "schemas/facts.json",
                 _payload(utterance, patient_lang, doctor_lang))


def analyze_single(utterance, patient_lang, doctor_lang="ja"):
    """従来の1回呼び出し（facts と確認質問をまとめて作る）。ANALYZE_MODE=single のとき使う。"""
    out, sec = _call("prompts/analyze_system.txt", "schemas/analyze.json",
                     _payload(utterance, patient_lang, doctor_lang))
    return _fix_clarifications(out, utterance, patient_lang), sec


_pool = ThreadPoolExecutor(max_workers=16)


def submit(fn, *args):
    """Gemini 呼び出しを裏で始める（main.py が clarify / facts / 先回り翻訳を並列に投げる）。"""
    return _pool.submit(fn, *args)


def analyze(utterance, patient_lang, doctor_lang="ja"):
    """患者の発言 → facts と確認質問（最大1つ、患者の言語）。test.py 用に両方そろえて返す。

    split のときの sec は「確認質問が出るまで」（＝患者の待ち時間）。facts の時間は _meta に入れる。
    """
    if ANALYZE_MODE == "single":
        return analyze_single(utterance, patient_lang, doctor_lang)
    f_facts = submit(facts, utterance, patient_lang, doctor_lang)
    out, sec = clarify(utterance, patient_lang, doctor_lang)
    f_out, f_sec = f_facts.result()
    out["facts"] = f_out["facts"]
    out["_meta"] = {"clarify": out["_meta"], "facts": {**f_out["_meta"], "sec": round(f_sec, 1)}}
    return out, sec


def _snap_target(target, utterance):
    """target が原文にそのまま無いとき（言い換えられたとき）、原文の中で一番長く一致する部分に寄せる。

    UI は target で原文をハイライトするので、原文に存在する文字列であることを保証したい。
    """
    if target in utterance:
        return target
    i = utterance.lower().find(target.lower())
    if i >= 0:
        return utterance[i:i + len(target)]
    m = difflib.SequenceMatcher(None, utterance.lower(), target.lower()).find_longest_match()
    snapped = utterance[m.a:m.a + m.size].strip(" ,.。、")
    return snapped if len(snapped) >= 2 else target


def translate(original, source_lang, target_lang, direction, clarification=None, context=None,
              turns=None, confirmed_intent=None, fix=None):
    """確認後の翻訳。direction は "patient_to_doctor" か "doctor_to_patient"。

    clarification は {"target", "question", "answer_type": option|free_text|unsure, "answer"} か None。
    context は医師の確認質問への答えのとき {"question_as_shown", "question"}（短い答えを訳せるように）か None。
    """
    payload = {
        "direction": direction,
        "source_lang": _lang(source_lang),
        "target_lang": _lang(target_lang),
        "original": original,
        "clarification": clarification,
    }
    if context:
        payload["context"] = context
    for k, v in (("turns", turns), ("confirmed_intent", confirmed_intent), ("fix", fix)):
        if v:
            payload[k] = v
    return _call("prompts/translate_system.txt", "schemas/translate.json", model=TRANSLATE_MODEL, payload=payload)


def source_agent(utterance, speaker, speaker_lang, listener_lang, turns=None):
    """Source Agent: 発話意図（文脈で決まるか・本人に聞くべきか）と、原文の意味の枠（Semantic Bridge）。検証は ussa.build_intent。"""
    return _call("prompts/source_system.txt", "schemas/source.json", model=SOURCE_MODEL, payload={
        "speaker": speaker, "speaker_lang": _lang(speaker_lang), "listener_lang": _lang(listener_lang),
        "utterance": utterance, "context": turns or [],
    })


def extract_frame(text, lang, speaker):
    """Verification Engine の B: 訳文だけから、独立に意味の枠を取る（原文は渡さない）。"""
    return _call("prompts/frame_system.txt", "schemas/frame_extract.json", model=VERIFY_MODEL,
                 payload={"lang": _lang(lang), "speaker": speaker, "text": text})


def compare_meaning(source, source_lang, translation, target_lang, speaker, confirmed_intent=None, doctor_lang="ja"):
    """Verification Engine の C: 原文と訳文を直接照合する。翻訳した側の判断（意図の推測など）は渡さない。"""
    return _call("prompts/verify_system.txt", "schemas/verify_compare.json", model=VERIFY_MODEL, payload={
        "source_lang": _lang(source_lang), "target_lang": _lang(target_lang), "doctor_lang": _lang(doctor_lang),
        "speaker": speaker, "source": source, "translation": translation, "confirmed_intent": confirmed_intent,
    })


def audit(latest, memory, open_issues, closed_issues, doctor_lang="ja"):
    """Conversation Auditor: 最新の発言と、記憶（重要な発言の要点）と、未解決事項だけを渡す（会話全体は渡さない）。"""
    return _call("prompts/audit_system.txt", "schemas/audit.json", model=AUDIT_MODEL, payload={
        "doctor_lang": _lang(doctor_lang), "latest": latest, "memory": memory,
        "open_issues": open_issues, "closed_issues": closed_issues,
    })


def meaning(utterance, patient_lang, doctor_lang="ja", context=None, already_asked=None, pending_question=None):
    """Meaning Engine: 患者の発言 → 診療に関わる情報（原文の根拠つき）・確認候補・確認質問への答えの解釈を1回で。

    患者は待たない（翻訳と並列に投げ、医師が見るのは患者が送信した後）。出力の検証は clinical.build_meaning。
    """
    return _call("prompts/meaning_system.txt", "schemas/meaning.json", {
        "patient_lang": _lang(patient_lang),
        "doctor_lang": _lang(doctor_lang),
        "utterance": utterance,
        "context": context or [],
        "already_asked": already_asked or [],
        "pending_question": pending_question,
    })


def compare_understanding(explanation, explanation_as_shown, restatement, patient_lang, doctor_lang="ja",
                          explanation_frame=None):
    """理解の確認（teach-back）: 医師の説明と、患者が自分の言葉で言い直した内容を比べる。検証は clinical.build_teachback。
    explanation_frame は説明の意味の枠（ヒント。説明の文が常に正しい）。"""
    return _call("prompts/teachback_system.txt", "schemas/teachback.json", {
        "doctor_lang": _lang(doctor_lang),
        "patient_lang": _lang(patient_lang),
        "explanation": explanation,
        "explanation_as_shown": explanation_as_shown,
        "restatement": restatement,
        "explanation_frame": explanation_frame,
    })


def schema(name):
    """サーバー側の検証に使う JSON スキーマ（Gemini に渡すものと同じファイル）。"""
    return _load(f"schemas/{name}.json")


def warm_up():
    """サーバー起動時に1回呼んで、最初の呼び出しが遅い問題をデモ前に済ませておく。"""
    try:
        f = submit(meaning, "I have a headache.", "en")
        translate("I have a headache.", "en", "ja", "patient_to_doctor")
        f.result()
    except Exception:
        pass
