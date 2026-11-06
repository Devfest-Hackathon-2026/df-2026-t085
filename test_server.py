"""API の流れのテスト（Gemini と Firestore は偽物に差し替えるので、ネットもクレジットも使わない）。

使い方: python test_server.py
"""
import sys

from fastapi.testclient import TestClient
from firebase_admin import firestore

import main
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
unsaid_ai.analyze, unsaid_ai.translate, unsaid_ai.warm_up = fake_analyze, fake_translate, lambda: None
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
check("clarification.answer は null で待つ", m("m1")["clarification"]["answer"] is None)
check("答える前の /translate は 409", post("/translate", "m1").status_code == 409)
m("m1")["clarification"]["answer"] = {"type": "option", "text": "1-3 days"}  # 患者の端末が書く
r = post("/translate", "m1")
check("回答後 → confirmed + translation", m("m1")["status"] == "confirmed" and m("m1")["translation"]["lang"] == "ja")
check("回答が translate に渡る", calls[-1][1]["answer_type"] == "option" and calls[-1][1]["answer"] == "1-3 days")
check("2回目の /analyze は 409", post("/analyze", "m1").status_code == 409)

r = post("/analyze", "m2")
check("確認質問なし → そのまま confirmed", m("m2")["status"] == "confirmed" and m("m2")["clarification"] is None)

r = post("/translate", "m3")
check("医師の発言 → sent", m("m3")["status"] == "sent" and m("m3")["translation"]["lang"] == "en")
check("医師の発言は doctor_to_patient", calls[-1][0] == "doctor_to_patient")
check("医師の発言に /analyze は 400", post("/analyze", "m3").status_code == 400)

store[S + "/messages/m4"] = {"from": "patient", "original": "I feel dizzy.", "status": "analyzing"}
unsaid_ai.analyze = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
check("Gemini 失敗 → 502", post("/analyze", "m4").status_code == 502)
check("失敗したら status=error", m("m4")["status"] == "error")
unsaid_ai.analyze = fake_analyze
check("error からやり直せる", post("/analyze", "m4").status_code == 200 and m("m4")["status"] == "confirmed")

main.app.dependency_overrides[main.current_uid] = lambda: "stranger"
check("メンバー以外は 403", post("/analyze", "m2").status_code == 403)
check("無い session は 404", post("/analyze", "m2", sid="nope").status_code == 404)

print(f"\n{fails} 件 FAIL" if fails else "\nすべて PASS")
sys.exit(1 if fails else 0)
