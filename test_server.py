"""API の流れのテスト（Gemini と Firestore は偽物に差し替えるので、ネットもクレジットも使わない）。

使い方: python test_server.py
"""
import sys

from fastapi.testclient import TestClient
from firebase_admin import firestore

import main
import stt
import unsaid_ai

sys.stdout.reconfigure(encoding="utf-8")


class FakeDoc:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def collection(self, name):
        return FakeCol(self.store, self.path + "/" + name)

    def get(self):
        return FakeSnap(self.store.get(self.path))

    def update(self, data):
        doc = self.store[self.path]
        for k, v in data.items():
            if v is firestore.DELETE_FIELD:
                doc.pop(k, None)
            else:
                doc[k] = "<ts>" if v is firestore.SERVER_TIMESTAMP else v


class FakeCol:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def document(self, id):
        return FakeDoc(self.store, self.path + "/" + id)


class FakeSnap:
    def __init__(self, data):
        self.exists, self._data = data is not None, data

    def to_dict(self):
        return dict(self._data)


class FakeDB:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        return FakeCol(self.store, name)


def fake_analyze(text, patient_lang, doctor_lang="ja"):
    clars = []
    if "for a while" in text:
        clars = [{"target": "for a while", "question": "How long?", "ui_type": "choice",
                  "options": ["Today", "1-3 days"], "fixed_options": unsaid_ai.FIXED_OPTIONS["en"]}]
    return {"facts": [{"text": text, "doctor_gloss": "頭痛", "kind": "symptom",
                       "negated": False, "uncertain": False}],
            "needs_clarification": bool(clars), "clarifications": clars}, 0.1


def fake_clarify(text, patient_lang, doctor_lang="ja"):
    out, sec = fake_analyze(text, patient_lang, doctor_lang)
    out.pop("facts")
    return out, sec


def fake_facts(text, patient_lang, doctor_lang="ja"):
    return {"facts": fake_analyze(text, patient_lang, doctor_lang)[0]["facts"]}, 0.1


calls = []


def fake_translate(original, src, dst, direction, clarification=None):
    calls.append((direction, clarification))
    return {"translation": f"[{dst}] {original}", "back_translation": original, "note": ""}, 0.1


S = "sessions/s1"
store = {
    S: {"patientLang": "en", "doctorLang": "ja", "members": ["patient", "doctor"]},
    S + "/messages/m1": {"from": "patient", "original": "I've had a headache for a while.", "status": "analyzing"},
    S + "/messages/m2": {"from": "patient", "original": "I don't have a fever.", "status": "analyzing"},
    S + "/messages/m3": {"from": "doctor", "original": "1日3回飲んでください。", "status": "analyzing"},
}
main._db = FakeDB(store)


def fake_transact(m_ref, check):  # 本物はトランザクション。ここでは読んで、check が通れば書くだけ
    msg = m_ref.get().to_dict()
    m_ref.update(check(msg))
    return msg


main._transact = fake_transact
unsaid_ai.analyze_single, unsaid_ai.translate, unsaid_ai.warm_up = fake_analyze, fake_translate, lambda: None
unsaid_ai.clarify, unsaid_ai.facts = fake_clarify, fake_facts
main.app.dependency_overrides[main.current_uid] = lambda: "patient"
client = TestClient(main.app)
fails = 0


def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += not cond


def post(path, mid, sid="s1"):
    return client.post(path, json={"sessionId": sid, "messageId": mid})


m = lambda i: store[S + "/messages/" + i]

r = post("/analyze", "m1")
check("確認質問あり → clarifying", r.status_code == 200 and m("m1")["status"] == "clarifying")
check("確認質問ありなら先回りの翻訳は書かない", m("m1")["translation"] is None)
check("facts も後から書き足される", m("m1")["facts"][0]["doctor_gloss"] == "頭痛")
check("clarification.answer は null で待つ", m("m1")["clarification"]["answer"] is None)
check("答える前の /translate は 409", post("/translate", "m1").status_code == 409)
m("m1")["clarification"]["answer"] = {"type": "option", "text": "1-3 days"}  # 患者の端末が書く
r = post("/translate", "m1")
check("回答後 → confirmed + translation", m("m1")["status"] == "confirmed" and m("m1")["translation"]["lang"] == "ja")
check("回答が translate に渡る", calls[-1][1]["answer_type"] == "option" and calls[-1][1]["answer"] == "1-3 days")
check("2回目の /analyze は 409", post("/analyze", "m1").status_code == 409)

r = post("/analyze", "m2")
check("確認質問なし → そのまま confirmed", m("m2")["status"] == "confirmed" and m("m2")["clarification"] is None)
check("確認質問なし → 先回りの翻訳が入る", m("m2")["translation"]["lang"] == "ja" and m("m2")["facts"])

r = post("/translate", "m3")
check("医師の発言 → sent", m("m3")["status"] == "sent" and m("m3")["translation"]["lang"] == "en")
check("医師の発言は doctor_to_patient", calls[-1][0] == "doctor_to_patient")
check("医師の発言に /analyze は 400", post("/analyze", "m3").status_code == 400)

store[S + "/messages/m4"] = {"from": "patient", "original": "I feel dizzy.", "status": "analyzing"}
boom = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
unsaid_ai.clarify, unsaid_ai.translate = boom, boom
check("Gemini 失敗 → 502", post("/analyze", "m4").status_code == 502)
check("失敗したら status=error", m("m4")["status"] == "error")
check("失敗したら処理中の印は外す", "lockedUntil" not in m("m4"))
unsaid_ai.translate = fake_translate
unsaid_ai.clarify, unsaid_ai.facts = fake_clarify, boom
check("error からやり直せる", post("/analyze", "m4").status_code == 200 and m("m4")["status"] == "confirmed")
check("facts だけ失敗しても発言は止めない（facts=[]）", m("m4")["facts"] == [])
unsaid_ai.facts = fake_facts
check("終わったら処理中の印は外す", "lockedUntil" not in m("m4"))

# 確認質問の呼び出しだけ失敗 → 確認なしの翻訳で進める（発言を止めない）
store[S + "/messages/m6"] = {"from": "patient", "original": "I've had a headache for a while.", "status": "analyzing"}
unsaid_ai.clarify = boom
r = post("/analyze", "m6")
check("確認質問が失敗しても confirmed まで進む", r.status_code == 200 and m("m6")["status"] == "confirmed")
check("医師には確認できなかったと注意書きを出す", main.NO_CHECK_NOTE in m("m6")["translation"]["note"])
unsaid_ai.clarify = fake_clarify

# 処理中の印（二重に届いた「もう一度」で Gemini を2回呼ばない）
from datetime import datetime, timedelta, timezone
now = datetime.now(timezone.utc)
store[S + "/messages/m7"] = {"from": "patient", "original": "I feel dizzy.", "status": "analyzing",
                             "lockedUntil": now + timedelta(seconds=30)}
n = len(calls)
check("処理中の発言にもう一度届いたら 409", post("/analyze", "m7").status_code == 409)
check("処理中なら Gemini を呼ばない", len(calls) == n and m("m7")["status"] == "analyzing")
m("m7")["lockedUntil"] = now - timedelta(seconds=1)
check("印の期限が切れていればやり直せる", post("/analyze", "m7").status_code == 200 and m("m7")["status"] == "confirmed")
store[S + "/messages/m8"] = {"from": "doctor", "original": "お大事に。", "status": "analyzing",
                             "lockedUntil": now + timedelta(seconds=30)}
check("医師の発言も処理中なら 409", post("/translate", "m8").status_code == 409 and m("m8")["status"] == "analyzing")

store[S + "/messages/m5"] = {"from": "patient", "original": "I've had a headache for a while.", "status": "analyzing"}
unsaid_ai.ANALYZE_MODE = "single"
check("ANALYZE_MODE=single でも同じ結果", post("/analyze", "m5").status_code == 200
      and m("m5")["status"] == "clarifying" and m("m5")["facts"])
unsaid_ai.ANALYZE_MODE = "split"

main.app.dependency_overrides[main.current_uid] = lambda: "stranger"
check("メンバー以外は 403", post("/analyze", "m2").status_code == 403)
check("無い session は 404", post("/analyze", "m2", sid="nope").status_code == 404)

# --- 音声入力（/transcribe）---
main.app.dependency_overrides[main.current_uid] = lambda: "patient"
heard = []
stt.transcribe = lambda audio, lang: (heard.append(lang) or "I've had a headache for a while.", 0.1)
tx = lambda speaker, body=b"RIFF....", sid="s1": client.post(
    f"/transcribe?sessionId={sid}&speaker={speaker}", content=body, headers={"Content-Type": "audio/wav"})
r = tx("patient")
check("患者の音声 → 文字が返る", r.status_code == 200 and r.json()["text"].startswith("I've had"))
check("患者の音声は patientLang で聞く", heard[-1] == "en")
tx("doctor")
check("医師の音声は doctorLang で聞く", heard[-1] == "ja")
check("文字にしただけでは Firestore に書かない", len(store) == 9)
check("空の音声は 400", tx("patient", b"").status_code == 400)
check("大きすぎる音声は 413", tx("patient", b"x" * (main.MAX_AUDIO_BYTES + 1)).status_code == 413)
check("speaker が不正なら 422", tx("nurse").status_code == 422)
stt.transcribe = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
check("Speech-to-Text 失敗 → 502", tx("patient").status_code == 502)
main.app.dependency_overrides[main.current_uid] = lambda: "stranger"
check("メンバー以外は音声も 403", tx("patient").status_code == 403)

# 医師が QR で作った直後（患者がまだ参加していない = patientLang が null）
store["sessions/s2"] = {"patientLang": None, "doctorLang": "ja", "members": ["doctor"]}
store["sessions/s2/messages/d1"] = {"from": "doctor", "original": "こんにちは。", "status": "analyzing"}
main.app.dependency_overrides[main.current_uid] = lambda: "doctor"
r = client.post("/translate", json={"sessionId": "s2", "messageId": "d1"})
check("患者が参加する前（patientLang null）は 409", r.status_code == 409)
check("患者が参加する前は何も書かない", store["sessions/s2/messages/d1"]["status"] == "analyzing")

# --- 混雑（429）が続いたら予備のモデルに切り替える ---
class Busy(Exception):
    code = 429


class FakeModels:
    def __init__(self):
        self.models = []

    def generate_content(self, model, **kw):
        self.models.append(model)
        if model != "fallback-model":
            raise Busy()
        return type("Res", (), {"text": '{"ok": true}', "usage_metadata": None})()


fm = FakeModels()
unsaid_ai._client_ = lambda: type("C", (), {"models": fm})()
unsaid_ai.BACKOFF_BASE_SEC = 0
unsaid_ai.FALLBACK_MODEL = "fallback-model"
out, _ = unsaid_ai._call("prompts/translate_system.txt", "schemas/translate.json", {})
check("429 が2回続いたら予備のモデルに切り替える", out["ok"] and fm.models == [unsaid_ai.MODEL] * 2 + ["fallback-model"])
unsaid_ai.FALLBACK_MODEL = ""
fm.models = []
try:
    unsaid_ai._call("prompts/translate_system.txt", "schemas/translate.json", {})
    check("予備が無ければ再試行を使い切って失敗", False)
except Busy:
    check("予備が無ければ再試行を使い切って失敗", len(fm.models) == unsaid_ai.RETRIES_429 + 1)

print(f"\n{fails} 件 FAIL" if fails else "\nすべて PASS")
sys.exit(1 if fails else 0)
