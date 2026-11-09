"""端末が Firestore に直接書いて Safety Gate を飛ばせないこと（devtools/rules.py = firestore.rules の書き写し）。

本物のルールエンジンでの確認ではない（Firebase エミュレーターでの確認は SECURITY.md の残課題）。
ここでは「書き写したルールが、画面が使う書き込みを通し、迂回の書き込みを全部断る」ことを確かめる。
"""
from datetime import datetime, timezone

import pytest

from devtools import rules

S = {"sessions/s1": {"members": ["doc", "pat"], "patientLang": "zh-CN", "doctorLang": "ja",
                     "createdAt": datetime.now(timezone.utc)}}
session_of = lambda sid: S.get(f"sessions/{sid}")  # noqa: E731
MSG = "sessions/s1/messages"


def create(data, uid):
    return rules.check_create(MSG, data, uid, session_of)


def update(old, data, uid):
    return rules.check_update(f"{MSG}/m1", old, data, uid, session_of)


BASE = {"original": "没事", "status": "analyzing", "createdAt": "__TS__"}


def test_normal_writes_are_allowed():
    assert create({**BASE, "from": "patient"}, "pat")
    assert create({**BASE, "from": "doctor"}, "doc")
    assert create({**BASE, "from": "patient", "replyTo": "q1", "answer": {"type": "option", "index": 0}}, "pat")
    assert update({"from": "patient", "status": "sent"}, {"status": "received", "receivedAt": "__TS__"}, "doc")


@pytest.mark.parametrize("data,uid", [
    ({**BASE, "from": "patient", "status": "sent"}, "pat"),                                   # 最初から送信済み
    ({**BASE, "from": "patient", "translation": {"text": "x"}}, "pat"),                        # 訳を自分で書く
    ({**BASE, "from": "patient", "gate": {"state": "READY_TO_SEND"}}, "pat"),                 # Gate の状態を書く
    ({**BASE, "from": "patient", "kind": "clarify"}, "pat"),                                   # 確認質問になりすます
    ({**BASE, "from": "doctor"}, "pat"),                                                       # 医師になりすます
    ({**BASE, "from": "patient"}, "doc"),                                                      # 患者になりすます
    ({**BASE, "from": "doctor", "replyTo": "q1"}, "doc"),                                      # 医師が答えを作る
    ({**BASE, "from": "patient", "createdAt": "2030-01-01"}, "pat"),                           # 時刻を偽る
    ({**BASE, "from": "patient"}, "stranger"),                                                 # 第三者
    ({**BASE, "from": "patient", "replyTo": "q1", "answer": {"type": "option", "index": 9}}, "pat"),
])
def test_bypass_creates_are_refused(data, uid):
    assert not create(data, uid)


@pytest.mark.parametrize("old,data,uid", [
    ({"from": "patient", "status": "confirmed"}, {"status": "sent", "sentAt": "__TS__"}, "pat"),       # 自分で送信済みに
    ({"from": "patient", "status": "awaiting_intent"}, {"status": "sent"}, "pat"),                     # 意図の確認を飛ばす
    ({"from": "patient", "status": "needs_review"}, {"status": "sent"}, "pat"),                        # 検証の確認を飛ばす
    ({"from": "doctor", "status": "analyzing"}, {"status": "sent"}, "doc"),                            # 医師も同じ
    ({"from": "patient", "status": "analyzing"}, {"gate": {"state": "READY_TO_SEND"}}, "pat"),        # Gate を書き換える
    ({"from": "patient", "status": "sent"}, {"translation": {"text": "改ざん"}}, "pat"),               # 送った訳を書き換える
    ({"from": "patient", "status": "sent"}, {"original": "改ざん"}, "pat"),                             # 原文を書き換える
    ({"from": "patient", "status": "analyzing"}, {"status": "received"}, "doc"),                       # 届く前に既読
    ({"from": "patient", "status": "sent"}, {"meaning": {}}, "doc"),                                   # 問診メモを書き換える
])
def test_bypass_updates_are_refused(old, data, uid):
    assert not update(old, data, uid)


def test_session_audit_cannot_be_written_by_clients():
    old = {"members": ["doc", "pat"], "patientLang": "zh-CN", "createdAt": datetime.now(timezone.utc)}
    assert not rules.check_update("sessions/s1", old, {"audit": {"issues": []}}, "doc", session_of)
