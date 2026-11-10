"""意味の解析のやり直しの lease と、医師の判断の後に届いた答えの「再確認が必要」（2026-11-10）。

  1. サーバーが止まって「やり直し中」のまま残っても、期限（lease）が切れたらやり直せる。
     期限内は二重に走らせない。引き継がれた古い処理は書き戻さない。
  2. 医師が確認済み・要修正にした後に新しい答えが届いたら、医師の判断の履歴を消さずに「再確認が必要」にし、
     医師が判断したときの答え・新しい答え・医師の判断を結びつけて残す。
"""
import threading
from datetime import datetime, timedelta, timezone

import pytest

import clinical
import main
import unsaid_ai
from devtools import fake_ai as F
from tests.test_review_fixes import _answer_with_meaning_failure, backdate, cand, dizzy

PAST = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def stuck(api, aid, **meaning):
    """やり直しの途中でサーバーが止まった状態を作る（meaning.status = retrying のまま）。"""
    api.msgs().document(aid).update({"meaning.status": "retrying", **{f"meaning.{k}": v for k, v in meaning.items()}})


# ── 1. lease ──
def test_stuck_retry_with_expired_lease_can_be_retried(api):
    mid, qid, aid = _answer_with_meaning_failure(api)
    stuck(api, aid, retry_run="dead-server", retry_until=PAST)
    r = api.as_("doc").post("/meaning/retry", messageId=aid)
    assert r.status_code == 200, r.text
    assert api.get(aid)["meaning"]["status"] == "ready"
    c = cand(api, mid)
    assert c["status"] == "answered" and [x["message_id"] for x in c["answers"]] == [aid]


def test_stuck_retry_from_old_version_without_lease_can_be_retried(api):
    mid, qid, aid = _answer_with_meaning_failure(api)
    stuck(api, aid)  # 期限の無い古いデータ
    assert api.as_("doc").post("/meaning/retry", messageId=aid).status_code == 200
    assert api.get(aid)["meaning"]["status"] == "ready"


def test_live_lease_blocks_second_retry(api):
    mid, qid, aid = _answer_with_meaning_failure(api)
    future = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    stuck(api, aid, retry_run="running", retry_until=future)
    r = api.as_("doc").post("/meaning/retry", messageId=aid)
    assert r.status_code == 409 and "整理し直しています" in r.json()["detail"]
    assert api.get(aid)["meaning"]["retry_run"] == "running" and cand(api, mid)["answers"] == []


def test_expired_owner_cannot_write_back_after_takeover(api):
    """A が止まっている間に期限が切れ、B が引き継いで終える → 遅れて再開した A は書き戻さず、答えも重複しない。"""
    mid, qid, aid = _answer_with_meaning_failure(api)
    started, resume = threading.Event(), threading.Event()
    real = unsaid_ai.meaning
    calls = []

    def first_paused(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert resume.wait(10)
            out, sec = real(*a, **k)
            out = {**out, "facts": [], "ambiguities": []}  # A の結果は B と区別できるように変える
            return out, sec
        return real(*a, **k)
    unsaid_ai.meaning = first_paused
    box = {}

    def run_a():
        main.app.dependency_overrides[main.current_uid] = lambda: "doc"
        box["a"] = api.client.post("/meaning/retry", json={"sessionId": api.sid, "messageId": aid})
    t = threading.Thread(target=run_a)
    try:
        t.start()
        assert started.wait(10)
        a_run = api.get(aid)["meaning"]["retry_run"]
        api.msgs().document(aid).update({"meaning.retry_until": PAST})  # A の期限が切れた
        b = api.as_("doc").post("/meaning/retry", messageId=aid)  # B が引き継ぐ
        assert b.status_code == 200, b.text
        b_meaning = api.get(aid)["meaning"]
        assert b_meaning["status"] == "ready" and b_meaning["facts"]
        resume.set()
        t.join(10)
    finally:
        resume.set()
        unsaid_ai.meaning = real
    assert box["a"].status_code == 409  # 古い所有者は書き戻さない
    assert api.get(aid)["meaning"] == b_meaning
    assert a_run and len(cand(api, mid)["answers"]) == 1


def test_failure_outside_ai_releases_lease(api, monkeypatch):
    mid, qid, aid = _answer_with_meaning_failure(api)

    def boom(*a, **k):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(main, "_all_messages", boom)
    with pytest.raises(RuntimeError):
        api.as_("doc").post("/meaning/retry", messageId=aid)
    assert api.get(aid)["meaning"]["status"] == "error"  # 期限を待たずにやり直せる
    monkeypatch.undo()
    assert api.as_("doc").post("/meaning/retry", messageId=aid).status_code == 200


# ── 2. 医師の判断の後に届いた答え → 再確認が必要 ──
def answered(api):
    """確認質問に答えてもらい（答えは 30 秒前に届いたことにする）、元の発言・質問・答えの id を返す。"""
    mid = api.patient_says(F.A_TEXT)
    qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").json()["messageId"]
    a1 = api.patient_says(F.A_ANSWER, replyTo=qid, answer={"type": "free_text"})
    backdate(api, a1)
    assert dizzy(api, mid)["status"] == "patient_answered"
    return mid, qid, a1


@pytest.mark.parametrize("action,status", [("confirm", "doctor_confirmed"), ("flag", "needs_correction")])
def test_new_answer_after_doctor_decision_requires_recheck(api, action, status):
    mid, qid, a1 = answered(api)
    fid = dizzy(api, mid)["fact_id"]
    first = dizzy(api, mid)["resolved_meaning"]
    api.as_("doc").post("/fact/update", messageId=mid, factId=fid, action=action, note="医師のメモ")
    decided = dizzy(api, mid)
    assert decided["status"] == status
    a2 = api.patient_says("不是转的，是眼前发黑。", replyTo=qid, answer={"type": "free_text"})

    f = dizzy(api, mid)
    assert f["status"] == "patient_answered"  # 医師確認済みのままにしない
    assert clinical.recheck_required(f)
    assert f["history"][:len(decided["history"])] == decided["history"]  # 医師の判断の履歴は消さない
    assert [h["event"] for h in f["history"][len(decided["history"]):]] == ["patient_answered", "recheck_required"]
    r = f["rechecks"][-1]
    assert r["doctor_status"] == status and r["doctor_event"] == f"doctor_{action}"
    assert r["doctor_note"] == "医師のメモ" and r["doctor_at"] == decided["history"][-1]["at"]
    assert r["doctor_answer_id"] == a1 and r["previous_answer_id"] == a1  # 医師が見て判断した答え
    assert r["doctor_resolved_meaning"] == first
    assert r["new_answer_id"] == a2 and r["new_meaning"] == f["resolved_meaning"] != first
    assert [x["message_id"] for x in cand(api, mid)["answers"]] == [a1, a2]

    # 医師がもう一度判断すると「再確認が必要」は外れる。記録は残る
    assert api.as_("doc").post("/fact/update", messageId=mid, factId=fid, action="confirm").status_code == 200
    f = dizzy(api, mid)
    assert f["status"] == "doctor_confirmed" and not clinical.recheck_required(f)
    assert f["rechecks"][-1]["resolved_by"] == "confirm" and f["rechecks"][-1]["new_answer_id"] == a2
    events = [h["event"] for h in f["history"]]
    assert events.count(f"doctor_{action}") >= 1 and events[-1] == "doctor_confirm"


def test_same_answer_applied_twice_does_not_add_second_recheck(api):
    mid, qid, a1 = answered(api)
    fid = dizzy(api, mid)["fact_id"]
    api.as_("doc").post("/fact/update", messageId=mid, factId=fid, action="confirm")
    a2 = api.patient_says("不是转的，是眼前发黑。", replyTo=qid, answer={"type": "free_text"})
    s_ref = api.db.collection("sessions").document(api.sid)
    main._apply_reply(s_ref, a2)  # /send と解析の完了の両方から呼ばれる
    main._apply_reply(s_ref, a2)
    f = dizzy(api, mid)
    assert len(f["rechecks"]) == 1 and [h["event"] for h in f["history"]].count("recheck_required") == 1
    assert len(cand(api, mid)["answers"]) == 2


def test_unsure_answer_after_doctor_confirm_also_requires_recheck(api):
    mid, qid, a1 = answered(api)
    fid = dizzy(api, mid)["fact_id"]
    api.as_("doc").post("/fact/update", messageId=mid, factId=fid, action="confirm")
    api.patient_says("我不知道", replyTo=qid, answer={"type": "free_text"})
    f = dizzy(api, mid)
    assert f["status"] == "unconfirmed" and clinical.recheck_required(f)
    assert f["rechecks"][-1]["new_meaning"] == ""


def _fact_and_cand(decision_at):
    f = {"fact_id": "f1", "status": clinical.DOCTOR_CONFIRMED, "resolved_meaning": "x", "clarification_status": "answered",
         "history": [{"at": decision_at, "by": "doctor", "event": "doctor_confirm", "from": "patient_answered",
                      "to": "doctor_confirmed"}]}
    c = {"candidate_id": "c1", "status": clinical.C_ANSWERED, "fact_ids": ["f1"], "answers": [], "history": []}
    return {"facts": [f], "candidates": [c]}


@pytest.mark.parametrize("gap,kept", [(5, True), (1.5, True), (0.5, False), (-3, False)])
def test_answer_time_vs_doctor_decision_errs_to_recheck(gap, kept):
    """答えを送った時刻が医師の判断の gap 秒前。時計のずれの幅（1 秒）に入るときは「見ていない」として再確認へ。"""
    t = datetime(2026, 11, 10, 3, 0, 0, tzinfo=timezone.utc)
    meaning = _fact_and_cand(t.isoformat())
    clinical.apply_answer(meaning, "c1", message_id="a", answer_type="free_text", resolves="yes", meaning_doctor="y",
                          sent_at=(t - timedelta(seconds=gap)).isoformat())
    f = meaning["facts"][0]
    assert (f["status"] == clinical.DOCTOR_CONFIRMED and not clinical.recheck_required(f)) is kept
    assert clinical.recheck_required(f) is (not kept)


def test_answer_without_time_requires_recheck():
    meaning = _fact_and_cand(datetime.now(timezone.utc).isoformat())
    clinical.apply_answer(meaning, "c1", message_id="a", answer_type="free_text", resolves="yes", meaning_doctor="y")
    assert clinical.recheck_required(meaning["facts"][0])
