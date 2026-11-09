"""USSA を API で通すテスト: 意図の確認（双方向）、検証と訳し直し、Safety Gate が送らない場合、取り消し・古い結果・二重送信、
未検証の共有、Conversation Auditor、teach-back との連携、権限。Firestore と Gemini は偽物（devtools/）。"""
from datetime import datetime, timedelta, timezone

import pytest

import main
import unsaid_ai
import ussa as U
from devtools import fake_ai as F


def gate(api, mid):
    return api.get(mid).get("gate") or {}


def delivered(api, mid):
    m = api.get(mid)
    return m["status"] == "sent" and bool(m.get("translation"))


def later(sec):
    return datetime.now(timezone.utc) + timedelta(seconds=sec)


# ── 発話意図: 文脈で決まるなら聞かない ──
@pytest.mark.parametrize("question,expect", [
    (F.D_PAIN, "いいえ、痛みはありません。"),        # 症状の否定
    (F.D_HELP, "大丈夫です、手伝いは要りません。"),   # 手伝いを断る
])
def test_intent_resolved_by_context_no_extra_question(api, question, expect):
    api.doctor_says(question)
    mid = api.patient_says(F.D_FINE)
    m = api.get(mid)
    assert delivered(api, mid) and m["translation"]["text"] == expect
    assert m["gate"]["intent"]["state"] == U.RESOLVED and m["gate"]["intent"]["decided_by"] == "ai"


# ── 発話意図: 決まらなければ発言者に聞く（患者 → 医師）──
def test_patient_intent_clarification_then_confirmed_translation(api):
    api.doctor_says(F.D_ASK)
    mid = api.patient_says(F.D_FINE)
    m = api.get(mid)
    assert m["status"] == "awaiting_intent" and not m.get("translation")  # 確定した訳は送らない
    g = m["gate"]
    assert g["state"] == U.AWAITING_INTENT and g["intent"]["state"] == U.CLARIFICATION_REQUIRED
    assert [i["meaning_speaker"] for i in g["intent"]["interpretations"]] == [o[0] for o in F.D_OPTIONS]
    assert api.as_("doc").post("/intent", messageId=mid, choice="i1").status_code == 403  # 相手は選べない
    r = api.as_("pat").post("/intent", messageId=mid, choice="i1")
    assert r.status_code == 200 and r.json()["status"] == "sent"
    m = api.get(mid)
    assert m["translation"]["text"] == F.D_OPTIONS[0][1]  # 本人が選んだ意味で訳す
    assert m["gate"]["intent"]["state"] == U.USER_CONFIRMED and m["gate"]["intent"]["decided_by"] == "speaker"
    assert m["gate"]["verification"]["status"] == U.VERIFIED
    assert api.post("/intent", messageId=mid, choice="i2").status_code == 409  # 送った後に変えられない


def test_intent_none_cancels_and_free_text(api):
    api.doctor_says(F.D_ASK)
    mid = api.patient_says(F.D_FINE)
    r = api.post("/intent", messageId=mid, choice="none")
    assert r.json() == {"status": "cancelled", "original": F.D_FINE} and gate(api, mid)["state"] == U.CANCELLED
    mid2 = api.patient_says(F.D_FINE, createdAt=later(30))
    assert api.post("/intent", messageId=mid2, choice="free_text", text="").status_code == 400
    assert api.post("/intent", messageId=mid2, choice="i9").status_code == 400
    r = api.post("/intent", messageId=mid2, choice="free_text", text="意思是可以继续")
    assert r.status_code == 200 and gate(api, mid2)["intent"]["confirmed"]["kind"] == "free_text"


# ── 発話意図: 医師 → 患者でも同じ ──
def test_doctor_intent_clarification(api):
    mid = api.as_("doc").say("doctor", F.D_DR_FINE)
    assert api.post("/translate", messageId=mid).json()["status"] == "awaiting_intent"
    assert not delivered(api, mid)
    assert api.as_("pat").post("/intent", messageId=mid, choice="i2").status_code == 403
    api.as_("doc").post("/intent", messageId=mid, choice="i2")
    m = api.get(mid)
    assert delivered(api, mid) and m["translation"]["text"] == F.D_DR_OPTIONS[1][1]


# ── Verification Engine: 否定が落ちた訳 → 見つけて訳し直す ──
def test_mismatch_is_retranslated_and_verified(api):
    mid = api.doctor_says(F.F_TEXT)
    m = api.get(mid)
    assert delivered(api, mid) and m["translation"]["text"] == F.F_GOOD
    att = m["gate"]["attempts"]
    assert [a["status"] for a in att] == [U.MISMATCH, U.VERIFIED] and att[0]["translation"] == F.F_BAD
    assert {"polarity_flip"} <= {i["type"] for i in att[0]["issues"]}
    layers = {i["layer"] for i in att[0]["issues"]}
    assert {"frame", "llm"} <= layers  # 枠の比較（A/B）と直接の照合（C）の両方が見つけた


# ── Verification Engine: 訳し直しても数が落ちる → 自動では送らず、発言者が判断 ──
def test_unresolved_mismatch_goes_to_speaker_review(api):
    mid = api.doctor_says(F.G_TEXT)
    m = api.get(mid)
    assert m["status"] == "needs_review" and not m.get("translation")
    g = m["gate"]
    assert g["state"] == U.NEEDS_REVIEW and len(g["attempts"]) == main.TRANSLATION_RETRIES + 1
    assert any(i["type"] == "quantity_change" and i["layer"] == "rule" for i in g["verification"]["issues"])
    assert api.as_("pat").post("/review", messageId=mid, action="send_unverified").status_code == 403
    r = api.as_("doc").post("/review", messageId=mid, action="send_unverified")
    assert r.status_code == 200
    m = api.get(mid)
    assert delivered(api, mid) and m["gate"]["override"] == "speaker_ack"
    assert m["gate"]["verification"]["status"] == U.MISMATCH  # 「未検証として送った」ことが記録に残る
    assert api.post("/review", messageId=mid, action="send_unverified").status_code == 409


def test_review_rephrase_cancels(api):
    mid = api.doctor_says(F.G_TEXT)
    r = api.as_("doc").post("/review", messageId=mid, action="rephrase")
    assert r.json()["status"] == "cancelled" and not delivered(api, mid)
    assert api.post("/review", messageId=mid, action="boom").status_code == 409


@pytest.mark.parametrize("broken", ["extract_frame", "compare_meaning"])
def test_verifier_failure_never_sends(api, broken):
    real = getattr(unsaid_ai, broken)
    setattr(unsaid_ai, broken, lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    try:
        mid = api.doctor_says(F.C_EXPLAIN)
    finally:
        setattr(unsaid_ai, broken, real)
    g = gate(api, mid)
    assert not delivered(api, mid) and g["state"] == U.NEEDS_REVIEW
    assert g["verification"]["status"] == U.VERIFICATION_FAILED


def test_invalid_schema_from_verifier_never_sends(api):
    real = unsaid_ai.compare_meaning
    unsaid_ai.compare_meaning = lambda *a, **k: ({"equivalent": True}, 0.1)  # issues が無い = スキーマ違反
    try:
        mid = api.doctor_says(F.C_EXPLAIN)
    finally:
        unsaid_ai.compare_meaning = real
    assert not delivered(api, mid) and gate(api, mid)["verification"]["status"] == U.VERIFICATION_FAILED


def test_source_agent_failure_fails_closed_and_keeps_original(api):
    real = unsaid_ai.source_agent
    unsaid_ai.source_agent = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow"))
    mid = api.as_("pat").say("patient", F.A_TEXT)
    try:
        assert api.post("/analyze", messageId=mid).status_code == 502
    finally:
        unsaid_ai.source_agent = real
    m = api.get(mid)
    assert m["status"] == "error" and m["gate"]["state"] == U.FAILED and m["original"] == F.A_TEXT
    assert not m.get("translation")
    assert api.post("/analyze", messageId=mid).status_code == 200 and delivered(api, mid)  # もう一度


# ── 取り消し・古い結果・二重送信・問診の終了 ──
def _hook_verify(fn):
    real = main._verify

    def wrapped(*a, **k):
        out = real(*a, **k)
        fn()
        return out
    main._verify = wrapped
    return real


def test_cancel_during_verification_is_not_sent(api):
    mid = api.as_("doc").say("doctor", F.C_EXPLAIN)
    real = _hook_verify(lambda: api.as_("doc").post("/cancel", messageId=mid))
    try:
        r = api.post("/translate", messageId=mid)
    finally:
        main._verify = real
    assert r.json()["status"] == "stopped" and gate(api, mid)["state"] == U.CANCELLED and not delivered(api, mid)


def test_stale_run_result_is_not_sent(api):
    mid = api.as_("doc").say("doctor", F.C_EXPLAIN)

    def new_run():  # 解析中に別の解析が始まった（run_id が変わった）
        g = gate(api, mid)
        g["run_id"] = "newer"
        api.msgs().document(mid).update({"gate": g})
    real = _hook_verify(new_run)
    try:
        r = api.post("/translate", messageId=mid)
    finally:
        main._verify = real
    assert r.json()["status"] == "stopped" and not delivered(api, mid)


def test_session_end_during_analysis_sends_nothing(api):
    mid = api.as_("doc").say("doctor", F.C_EXPLAIN)
    real = _hook_verify(lambda: api.as_("doc").post("/end"))
    try:
        r = api.post("/translate", messageId=mid)
    finally:
        main._verify = real
    assert r.json()["status"] == "stopped" and not any(k.startswith("sessions/s1") for k in api.db.store)


def test_double_send_is_cancelled(api):
    first = api.as_("pat").say("patient", F.A_TEXT)
    second = api.say("patient", F.A_TEXT)
    assert api.post("/analyze", messageId=first).status_code == 200
    assert api.post("/analyze", messageId=second).status_code == 409
    assert gate(api, second)["state"] == U.CANCELLED and delivered(api, first) and not delivered(api, second)
    assert api.post("/analyze", messageId=first).status_code == 409  # 同じ発言の二重押し


def test_only_the_author_triggers_the_gate(api):
    mid = api.as_("pat").say("patient", F.A_TEXT)
    assert api.as_("doc").post("/analyze", messageId=mid).status_code == 403
    dmid = api.as_("doc").say("doctor", F.C_EXPLAIN)
    assert api.as_("pat").post("/translate", messageId=dmid).status_code == 403
    assert api.as_("pat").post("/cancel", messageId=dmid).status_code == 403


# ── 緊急時: 未検証の原文を医師に共有 ──
def test_doctor_can_reveal_unverified_original(api):
    api.doctor_says(F.D_ASK)
    mid = api.patient_says(F.D_FINE)  # 意図の確認待ち（患者が答えていない）
    assert api.as_("pat").post("/reveal", messageId=mid).status_code == 403
    r = api.as_("doc").post("/reveal", messageId=mid)
    assert r.status_code == 200 and r.json()["original"] == F.D_FINE and r.json()["verified"] is False
    assert r.json()["state"] == U.AWAITING_INTENT and r.json()["lang"] == "zh-CN"
    assert gate(api, mid)["revealed"][0]["by"] == "doctor"  # 見たことが記録に残る
    assert not delivered(api, mid) and "meaning" not in api.get(mid)  # 問診メモには入らない
    done = api.patient_says("Yes, I have a little fever too.")
    assert api.as_("doc").post("/reveal", messageId=done).status_code == 400


# ── Conversation Auditor ──
def test_auditor_dose_misunderstanding_one_issue(api):
    d = api.doctor_says(F.E_DOSE)
    p = api.patient_says(F.E_WRONG)
    issues = [i for i in api.issues() if i["status"] in U.ACTIVE_ISSUE]
    assert len(issues) == 1  # ルールの照合と LLM が同じ食い違いを見つけても1件
    i = issues[0]
    assert i["type"] == "instruction_misunderstanding" and i["source"] == "rule" and set(i["related_utterances"]) == {d, p}
    assert "説明 1錠 / 理解 2錠" in i["description"] and i["evidence"][0]["quote"] == F.E_WRONG


def test_auditor_no_warning_for_correct_understanding(api):
    api.doctor_says(F.E_DOSE)
    api.patient_says(F.E_RIGHT)
    assert api.issues() == []


def test_auditor_contradiction_and_insufficient_explanation(api):
    a = api.patient_says(F.H_NO_ALLERGY)
    b = api.patient_says(F.H_PENICILLIN)
    c = api.doctor_says(F.H_VAGUE)
    by_type = {i["type"]: i for i in api.issues()}
    assert set(by_type["contradiction"]["related_utterances"]) == {a, b}
    assert {e["quote"] for e in by_type["contradiction"]["evidence"]} == {"アレルギーはありません", "ペニシリンにアレルギーがあります"}
    assert by_type["insufficient_explanation"]["related_utterances"] == [c]


def test_issue_actions_and_no_repeat_after_resolve(api):
    api.doctor_says(F.E_DOSE)
    api.patient_says(F.E_WRONG)
    iid = api.issues()[0]["issue_id"]
    assert api.as_("pat").post("/issue/update", issueId=iid, action="resolve").status_code == 403
    doc = api.as_("doc")
    assert doc.post("/issue/update", issueId=iid, action="hold").status_code == 200 and api.issues()[0]["held"]
    assert doc.post("/issue/update", issueId=iid, action="unhold").status_code == 200
    assert doc.post("/issue/update", issueId=iid, action="resolve", note="再説明した").status_code == 200
    assert api.issues()[0]["status"] == U.RESOLVED_I and api.issues()[0]["resolution"] == "再説明した"
    assert doc.post("/issue/update", issueId=iid, action="resolve").status_code == 409
    assert doc.post("/issue/update", issueId="u99", action="hold").status_code == 404
    # 同じ発言についてもう一度監査が走っても、解決済みは繰り返さない
    s_ref = api.db.collection("sessions").document(api.sid)
    p = api.issues()[0]["related_utterances"][-1]
    main._audit(s_ref, p, api.get(p)["gate"]["intent"], None)
    assert len(api.issues()) == 1


def test_clarify_question_for_issue_tracks_answer(api):
    api.patient_says(F.H_NO_ALLERGY)
    api.patient_says(F.H_PENICILLIN)
    iid = api.issues()[0]["issue_id"]
    r = api.as_("doc").post("/clarify/send", questionDoctor="ペニシリンのアレルギーについて教えてください。", issueId=iid)
    assert r.status_code == 200 and api.issues()[0]["status"] == U.CLAR_REQ
    api.patient_says("Yes, I have a little fever too.", replyTo=r.json()["messageId"], answer={"type": "free_text"})
    assert api.issues()[0]["status"] == U.ANSWERED  # 答えた。解決にするのは医師


# ── teach-back と Auditor の連携 ──
def test_teachback_mismatch_becomes_issue_and_confirm_resolves_chain(api):
    exp = api.doctor_says(F.B_EXPLAIN)
    tb = api.as_("doc").post("/teachback/start", messageId=exp).json()["messageId"]
    api.patient_says(F.B_WRONG, replyTo=tb, answer={"type": "free_text"})
    issues = [i for i in api.issues() if i["status"] in U.ACTIVE_ISSUE]
    assert len(issues) == 1 and issues[0]["type"] == "understanding_mismatch" and issues[0]["source"] == "teachback"
    api.as_("doc").post("/teachback/resolve", messageId=tb, action="reexplain")
    assert api.issues()[0]["status"] == U.CLAR_REQ
    exp2 = api.doctor_says(F.B_REEXPLAIN)
    tb2 = api.as_("doc").post("/teachback/start", messageId=exp2, previousId=tb).json()["messageId"]
    api.patient_says(F.B_RIGHT, replyTo=tb2, answer={"type": "free_text"})
    assert api.get(tb2)["teachback"]["result"]["overall"] == "consistent"
    assert api.issues()[0]["status"] == U.CLAR_REQ  # AI が一致と判定しただけでは解決にしない
    api.as_("doc").post("/teachback/resolve", messageId=tb2, action="confirm")
    assert api.issues()[0]["status"] == U.RESOLVED_I


def test_teachback_uses_explanation_frame(api):
    seen = {}
    real = unsaid_ai.compare_understanding

    def spy(*a, **k):
        seen["frame"] = a[-1]
        return real(*a, **k)
    unsaid_ai.compare_understanding = spy
    try:
        exp = api.doctor_says(F.C_EXPLAIN)
        tb = api.as_("doc").post("/teachback/start", messageId=exp).json()["messageId"]
        api.patient_says(F.C_RIGHT, replyTo=tb, answer={"type": "free_text"})
    finally:
        unsaid_ai.compare_understanding = real
    assert seen["frame"]["frequency"][0]["count"] == 3


# ── Clinical Memory ──
def test_memory_records_gate_and_does_not_overwrite_doctor_confirmed(api):
    mid = api.patient_says(F.A_TEXT)
    m = api.get(mid)
    fid = next(f for f in m["meaning"]["facts"] if f["source_quote"] == "头晕")["fact_id"]
    api.as_("doc").post("/fact/update", messageId=mid, factId=fid, action="confirm")
    assert api.as_("doc").post("/meaning/retry", messageId=mid).status_code == 409  # 古い AI 処理で上書きしない
    assert api.get(mid)["gate"]["verification"]["status"] == U.VERIFIED
    t = api.get(mid)["gate"]["timings"]
    assert {"source_ms", "translate_ms", "verify_ms_1", "total_ms"} <= set(t)


def test_option_answers_are_pretranslated_not_verified(api):
    mid = api.patient_says(F.A_TEXT)
    qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").json()["messageId"]
    q = api.get(qid)
    aid = api.patient_says(q["clarify"]["options"][1], replyTo=qid, answer={"type": "option", "index": 1})
    g = gate(api, aid)
    assert delivered(api, aid) and g["verification"]["status"] == U.PRETRANSLATED  # 正直に記録する
    assert g["intent"]["state"] == U.USER_CONFIRMED
