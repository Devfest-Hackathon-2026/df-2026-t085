"""tests/ の共通の準備: Firestore と Gemini を偽物（devtools/）に差し替えた API を、医師・患者・第三者として呼ぶ。

ネットにもクレジットにも触れない（モックのテスト）。本物の Gemini を使う評価は eval/compare.py --live。
"""
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore", category=SyntaxWarning)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import unsaid_ai  # noqa: E402
from devtools import fake_ai, fakestore  # noqa: E402

fake_ai.install(unsaid_ai)


class Api:
    """1つの問診（医師 doc・患者 pat）。as_(uid) で呼ぶ人を替える。"""

    def __init__(self, db, client, sid="s1", lang="zh-CN"):
        self.db, self.client, self.sid, self.lang = db, client, sid, lang
        db.collection("sessions").document(sid).set({
            "patientLang": lang, "doctorLang": "ja", "members": ["doc", "pat"],
            "createdAt": datetime.now(timezone.utc),
        })
        self.uid = "pat"

    def as_(self, uid):
        self.uid = uid
        main.app.dependency_overrides[main.current_uid] = lambda: uid
        return self

    def post(self, path, **body):
        main.app.dependency_overrides[main.current_uid] = lambda: self.uid
        return self.client.post(path, json={"sessionId": self.sid, **body})

    def msgs(self):
        return self.db.collection("sessions").document(self.sid).collection("messages")

    def get(self, mid):
        return self.msgs().document(mid).get().to_dict()

    # 画面（web/unsaid.js）と同じ書き方で発言を作る（firestore.rules が許す項目だけ）
    def say(self, frm, text, **extra):
        ref = self.msgs().document()
        ref.set({"from": frm, "original": text, "status": "analyzing", "createdAt": fakestore.server_now(), **extra})
        return ref.id

    def patient_says(self, text, send=True, **extra):
        mid = self.as_("pat").say("patient", text, **extra)
        r = self.post("/analyze", messageId=mid)
        assert r.status_code == 200, r.text
        if send and self.get(mid)["status"] == "confirmed":
            assert self.post("/send", messageId=mid).status_code == 200
        return mid

    def doctor_says(self, text):
        mid = self.as_("doc").say("doctor", text)
        assert self.post("/translate", messageId=mid).status_code == 200
        return mid


@pytest.fixture
def api():
    db = fakestore.FakeDB()
    fakestore.install(main, db)
    main.CLARIFY_MODE = "doctor"
    client = TestClient(main.app)
    a = Api(db, client)
    yield a
    main.app.dependency_overrides.clear()
