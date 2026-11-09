"""tests/ の共通の準備: Firestore と Gemini を偽物（devtools/）に差し替えた API を、医師・患者・第三者として呼ぶ。

ネットにもクレジットにも触れない（モックのテスト）。本物の Gemini を使う評価は eval/compare.py --live。

FIRESTORE_EMULATOR_HOST があれば、偽の Firestore の代わりに Firestore エミュレーターを使う（本物のクライアントと
本物のトランザクション。Gemini は偽物のまま）。テストごとに別の session id を使う:

  firebase emulators:exec --only firestore --project demo-unsaid ".venv/bin/python -m pytest tests/"
"""
import os
import sys
import uuid
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
EMULATOR = os.environ.get("FIRESTORE_EMULATOR_HOST")
REAL_TX, REAL_TRANSACT = main._tx, main._transact  # fakestore.install が差し替える前の、本物のトランザクション


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

    def gone(self):
        """診察終了で session と発言がすべて消えたか。"""
        if hasattr(self.db, "store"):
            return not any(k.startswith(f"sessions/{self.sid}") for k in self.db.store)
        s = self.db.collection("sessions").document(self.sid)
        return not s.get().exists and not list(s.collection("messages").list_documents())

    def session(self):
        return self.db.collection("sessions").document(self.sid).get().to_dict()

    def issues(self):
        return (self.session().get("audit") or {}).get("issues", [])

    def patient_says(self, text, send=True, **extra):
        mid = self.as_("pat").say("patient", text, **extra)
        r = self.post("/analyze", messageId=mid)
        assert r.status_code == 200, r.text
        if send and self.get(mid)["status"] == "confirmed":
            assert self.post("/send", messageId=mid).status_code == 200
        return mid

    def doctor_says(self, text, **extra):
        mid = self.as_("doc").say("doctor", text, **extra)
        r = self.post("/translate", messageId=mid)
        assert r.status_code == 200, r.text
        return mid


@pytest.fixture
def api():
    if EMULATOR:
        from google.cloud import firestore as gfs
        db = gfs.Client(project=os.environ.get("GCLOUD_PROJECT", "demo-unsaid"))
        main._db, main._tx, main._transact = db, REAL_TX, REAL_TRANSACT
        sid = f"s-{uuid.uuid4().hex[:12]}"
    else:
        db = fakestore.FakeDB()
        fakestore.install(main, db)
        sid = "s1"
    main.CLARIFY_MODE = "doctor"
    client = TestClient(main.app)
    a = Api(db, client, sid=sid)
    yield a
    main.app.dependency_overrides.clear()
