"""本当に同時に走らせる（threading.Barrier で揃えてから呼ぶ）。順序を決め打ちしないので、どちらが先でも成り立つことを確かめる。

偽の Firestore はロックで直列になるので、本物のトランザクションでの確認は Firestore エミュレーターで流す
（tests/conftest.py の FIRESTORE_EMULATOR_HOST）。
"""
import threading
from functools import partial

import pytest

import main
import unsaid_ai
from devtools import fake_ai as F
from tests.test_review_fixes import _answer_with_meaning_failure, backdate, cand, dizzy

ROUNDS = 5


def rounds(api):
    """回ごとに新しい問診（同じ文を 10 秒以内に続けて送ると、二重送信として取り消されるため）。"""
    from tests.conftest import Api
    for i in range(ROUNDS):
        yield Api(api.db, api.client, sid=f"{api.sid}-{i}")


def together(*fns):
    """fns を別々のスレッドで、同時に始める。結果（または例外）を順に返す。"""
    gate = threading.Barrier(len(fns))
    out = [None] * len(fns)

    def run(i, fn):
        gate.wait()
        try:
            out[i] = fn()
        except Exception as e:  # noqa: BLE001
            out[i] = e
    ts = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    return out


def as_doctor(api, path, **body):
    """スレッドから医師として呼ぶ（dependency_overrides は共有なので、同時に呼ぶのは同じ人だけにする）。"""
    main.app.dependency_overrides[main.current_uid] = lambda: "doc"
    return api.client.post(path, json={"sessionId": api.sid, **body})


def test_two_retries_at_once_only_one_runs(api0):
    for api in rounds(api0):
        mid, qid, aid = _answer_with_meaning_failure(api)
        r = together(partial(as_doctor, api, "/meaning/retry", messageId=aid),
                     partial(as_doctor, api, "/meaning/retry", messageId=aid))
        codes = sorted(x.status_code for x in r)
        assert codes == [200, 409], codes  # 同時でも後からでも、2つ目は 409（やり直し中・もう必要ない）
        c = cand(api, mid)
        assert [x["message_id"] for x in c["answers"]] == [aid]  # 答えは1回だけ


def test_doctor_decision_and_new_answer_at_once_lose_nothing(api0):
    """医師の「確認済み」と、患者の新しい答えの反映が同時: どちらの記録も失わず、結果はどちらかの順と同じ。"""
    for api in rounds(api0):
        mid = api.patient_says(F.A_TEXT)
        qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").json()["messageId"]
        a1 = api.patient_says(F.A_ANSWER, replyTo=qid, answer={"type": "free_text"})
        backdate(api, a1)
        real = main._apply_reply
        main._apply_reply = lambda *a, **k: None  # 新しい答えを、届いたが結びつける前の状態にしておく
        try:
            a2 = api.patient_says("不是转的，是眼前发黑。", replyTo=qid, answer={"type": "free_text"})
        finally:
            main._apply_reply = real
        fid = dizzy(api, mid)["fact_id"]
        s_ref = api.db.collection("sessions").document(api.sid)
        r = together(partial(as_doctor, api, "/fact/update", messageId=mid, factId=fid, action="confirm"),
                     partial(main._apply_reply, s_ref, a2))
        assert r[0].status_code == 200, r[0].text
        assert not isinstance(r[1], Exception), r[1]

        f = dizzy(api, mid)
        events = [(h["event"], h.get("ref")) for h in f["history"]]
        assert ("doctor_confirm", None) in events  # 医師の判断が消えていない
        assert ("patient_answered", a2) in events  # 新しい答えも消えていない
        assert [x["message_id"] for x in cand(api, mid)["answers"]] == [a1, a2]
        confirm_at = events.index(("doctor_confirm", None))
        answer_at = events.index(("patient_answered", a2))
        if confirm_at < answer_at:  # 確認 → 答え: 再確認が必要
            assert f["status"] == "patient_answered" and f["rechecks"][-1]["required"]
            assert f["rechecks"][-1]["new_answer_id"] == a2
        else:  # 答え → 確認: 医師は新しい答えを見て確認した
            assert f["status"] == "doctor_confirmed" and not any(x["required"] for x in f.get("rechecks", []))


def test_double_send_at_once_sends_and_links_once(api0):
    for api in rounds(api0):
        mid = api.patient_says(F.A_TEXT)
        qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").json()["messageId"]
        aid = api.as_("pat").say("patient", "周围的东西在转（天旋地转）", replyTo=qid, answer={"type": "option", "index": 0})
        # 意図・照合まで終えてから、同じ答えの /send と、解析の完了からの結びつけを同時に
        real = main._apply_reply
        main._apply_reply = lambda *a, **k: None
        try:
            assert api.post("/analyze", messageId=aid).status_code == 200
        finally:
            main._apply_reply = real
        assert api.get(aid)["status"] == "sent"
        s_ref = api.db.collection("sessions").document(api.sid)
        r = together(partial(main._apply_reply, s_ref, aid), partial(main._apply_reply, s_ref, aid),
                     partial(main._apply_reply, s_ref, aid))
        assert not any(isinstance(x, Exception) for x in r), r
        c = cand(api, mid)
        assert [x["message_id"] for x in c["answers"]] == [aid]
        assert [h["event"] for h in dizzy(api, mid)["history"]].count("patient_answered") == 1


def test_meaning_engine_is_called_once_per_retry(api):
    """同時に2つやり直しても、Gemini（Meaning Engine）を2回呼ばない。"""
    mid, qid, aid = _answer_with_meaning_failure(api)
    calls = []
    real = unsaid_ai.meaning

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    unsaid_ai.meaning = counting
    try:
        together(partial(as_doctor, api, "/meaning/retry", messageId=aid),
                 partial(as_doctor, api, "/meaning/retry", messageId=aid))
    finally:
        unsaid_ai.meaning = real
    assert len(calls) == 1


@pytest.fixture
def api0(api):
    return api
