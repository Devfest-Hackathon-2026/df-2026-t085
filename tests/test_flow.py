"""API を通した流れのテスト（DEMO.md のシナリオ A・B・C、権限、順番の入れ替わり、失敗時の動き）。

Firestore と Gemini は偽物（devtools/）。画面と同じ書き方で発言を作り、API を呼ぶ。
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest

import clinical as C
import main
import unsaid_ai
from devtools import fake_ai


def cand_of(api, mid, i=0):
    return api.get(mid)["meaning"]["candidates"][i]


def fact_by_quote(api, mid, quote):
    return next(f for f in api.get(mid)["meaning"]["facts"] if f["source_quote"] == quote)


# ── シナリオ A: めまいの曖昧さ ──
def test_scenario_a_dizziness_end_to_end(api):
    mid = api.patient_says(fake_ai.A_TEXT)
    m = api.get(mid)
    # 1〜3) 認識・翻訳・構造化。患者の送信操作1回で、意図の解析・検証・Safety Gate を通って医師に届く
    assert m["status"] == "sent" and m["translation"]["text"] == fake_ai.A_JA
    assert m["gate"]["state"] == "SENT" and m["gate"]["verification"]["status"] == "VERIFIED"
    assert m["gate"]["intent"]["state"] == "RESOLVED" and m["gate"]["intent"]["decided_by"] == "ai"
    assert m.get("clarification") is None  # 医師の確認なしに患者へ質問は出さない
    assert m["meaning"]["status"] == "ready"
    # 4) 不安感（本人の言葉）を残す。5) めまいの曖昧さを確認候補にする
    psych = fact_by_quote(api, mid, "我有点害怕")
    assert psych["category"] == "psychological" and psych["status"] == C.AI_EXTRACTED
    assert cand_of(api, mid)["source_quote"] == "头晕" and cand_of(api, mid)["status"] == "proposed"
    assert m["facts"] and m["facts"][0]["doctor_gloss"]  # 旧い形（患者の「自分のメモ」用）も書く

    # 6〜7) 医師が確認候補をそのまま送る
    r = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1")
    assert r.status_code == 200, r.text
    qid = r.json()["messageId"]
    q = api.get(qid)
    assert q["from"] == "doctor" and q["kind"] == "clarify" and q["status"] == "sent"
    assert q["translation"]["text"] == cand_of(api, mid)["question_patient"]
    assert q["clarify"]["options"] == cand_of(api, mid)["options_patient"]
    assert q["clarify"]["fixed"] == unsaid_ai.ANSWER_FIXED["zh-CN"]
    assert cand_of(api, mid)["status"] == "asked" and cand_of(api, mid)["question_message_id"] == qid

    # 8〜10) 患者が選択肢で答える → Gemini を呼ばずに訳が付き、そのまま医師に届く → 問診メモ（fact）が更新
    opt = q["clarify"]["options"][0]
    aid = api.patient_says(opt, replyTo=qid, answer={"type": "option", "index": 0})
    a = api.get(aid)
    assert a["status"] == "sent" and a["translation"]["text"] == q["clarify"]["options_doctor"][0]
    dizzy = fact_by_quote(api, mid, "头晕")
    assert dizzy["status"] == C.PATIENT_ANSWERED and dizzy["resolved_meaning"] == "周囲がぐるぐる回る（回転性）"
    assert cand_of(api, mid)["status"] == "answered" and cand_of(api, mid)["answers"][0]["message_id"] == aid
    assert api.get(qid)["clarify"]["last_answer_id"] == aid
    # 11) 原文の根拠と、確認の履歴をたどれる
    assert fake_ai.A_TEXT[dizzy["span_start"]:dizzy["span_end"]] == "头晕"
    assert [h["event"] for h in dizzy["history"]] == ["extracted", "ambiguity_found", "question_sent", "patient_answered"]
    assert {h["by"] for h in dizzy["history"]} == {"ai", "doctor", "patient"}

    # 医師が確認済みにする（ここで初めて医師確認済み）
    assert api.as_("doc").post("/fact/update", messageId=mid, factId=dizzy["fact_id"], action="confirm").status_code == 200
    assert fact_by_quote(api, mid, "头晕")["status"] == C.DOCTOR_CONFIRMED


def test_scenario_a_free_text_answer_with_context(api):
    mid = api.patient_says(fake_ai.A_TEXT)
    qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").json()["messageId"]
    aid = api.patient_says(fake_ai.A_ANSWER, replyTo=qid, answer={"type": "free_text"})
    a = api.get(aid)
    # 自由回答も Safety Gate を通って届き、届いた後に元の情報に結びつく
    assert a["status"] == "sent" and a["meaning"]["resolution"]["resolves"] == "yes"
    dizzy = fact_by_quote(api, mid, "头晕")
    assert dizzy["status"] == C.PATIENT_ANSWERED and dizzy["resolved_meaning"] == "回転性のめまい（周囲がぐるぐる回る）"
    # 答えも新しい発言として記録され、自分の facts を持つ
    assert api.get(aid)["meaning"]["facts"][0]["category"] == "quality"


def test_apply_reply_is_idempotent(api):
    """答えの結びつけは何度呼んでも1回分（送信と解析の完了の両方から呼ばれても重複しない）。"""
    mid = api.patient_says(fake_ai.A_TEXT)
    qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").json()["messageId"]
    aid = api.patient_says(fake_ai.A_ANSWER, replyTo=qid, answer={"type": "free_text"})
    s_ref = api.db.collection("sessions").document(api.sid)
    for _ in range(3):
        main._apply_reply(s_ref, aid)
    assert len(cand_of(api, mid)["answers"]) == 1
    assert fact_by_quote(api, mid, "头晕")["status"] == C.PATIENT_ANSWERED


@pytest.mark.parametrize("atype", ["unsure", "none"])
def test_unsure_or_none_stays_unconfirmed(api, atype):
    mid = api.patient_says(fake_ai.A_TEXT)
    qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").json()["messageId"]
    text = unsaid_ai.ANSWER_FIXED["zh-CN"][atype]
    aid = api.patient_says(text, replyTo=qid, answer={"type": atype})
    assert api.get(aid)["status"] == "sent"
    assert api.get(aid)["translation"]["text"] == unsaid_ai.ANSWER_FIXED_DOCTOR[atype]
    assert fact_by_quote(api, mid, "头晕")["status"] == C.UNCONFIRMED
    assert cand_of(api, mid)["status"] == "unresolved"
    # 聞き直せる
    assert api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").status_code == 200


def test_option_answer_must_match_question(api):
    mid = api.patient_says(fake_ai.A_TEXT)
    qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").json()["messageId"]
    bad = api.as_("pat").say("patient", "自己编的", replyTo=qid, answer={"type": "option", "index": 0})
    assert api.post("/analyze", messageId=bad).status_code == 400
    bad2 = api.say("patient", "x", replyTo=qid, answer={"type": "option", "index": 9})
    assert api.post("/analyze", messageId=bad2).status_code == 400
    bad3 = api.say("patient", "x", replyTo=mid)  # 患者の発言には答えられない
    assert api.post("/analyze", messageId=bad3).status_code == 400


def test_edited_and_custom_questions_are_translated(api):
    mid = api.patient_says(fake_ai.A_TEXT)
    r = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1", questionDoctor="周りが回る感じでしたか？")
    q = api.get(r.json()["messageId"])
    assert q["clarify"]["edited"] and q["clarify"]["options"] == []  # 編集したら選択肢は付けない
    assert q["translation"]["text"].startswith("[zh-CN]")  # Gemini（の偽物）で訳した
    course = next(f for f in api.get(mid)["meaning"]["facts"] if f["category"] == "course")
    r = api.post("/clarify/send", messageId=mid, factId=course["fact_id"], questionDoctor="今も続いていますか？")
    assert r.status_code == 200 and r.json()["candidateId"] == "d1"
    assert cand_of(api, mid, -1)["origin"] == "doctor" and cand_of(api, mid, -1)["status"] == "asked"
    r = api.post("/clarify/send", questionDoctor="ほかに気になることはありますか？")  # どの情報にもひもづけない
    assert r.status_code == 200 and api.get(r.json()["messageId"])["clarify"]["source_message_id"] == ""
    assert api.post("/clarify/send", questionDoctor="x" * 301).status_code == 400
    assert api.post("/clarify/send").status_code == 400


def test_hold_dismiss_restore_and_bad_transitions(api):
    mid = api.patient_says(fake_ai.A_TEXT)
    doc = api.as_("doc")
    assert doc.post("/clarify/update", messageId=mid, candidateId="c1", action="hold").status_code == 200
    assert cand_of(api, mid)["status"] == "held"
    assert doc.post("/clarify/update", messageId=mid, candidateId="c1", action="dismiss").status_code == 200
    assert fact_by_quote(api, mid, "头晕")["clarification_status"] == "skipped"
    assert fact_by_quote(api, mid, "头晕")["status"] == C.UNCONFIRMED  # 無視しても確定しない
    assert doc.post("/clarify/send", messageId=mid, candidateId="c1").status_code == 409  # 無視したものは送れない
    assert doc.post("/clarify/update", messageId=mid, candidateId="c1", action="restore").status_code == 200
    assert doc.post("/clarify/update", messageId=mid, candidateId="c9", action="hold").status_code == 404
    assert doc.post("/clarify/update", messageId=mid, candidateId="c1", action="boom").status_code == 409
    fid = fact_by_quote(api, mid, "我有点害怕")["fact_id"]
    assert doc.post("/fact/update", messageId=mid, factId=fid, action="flag").status_code == 200
    assert doc.post("/fact/update", messageId=mid, factId=fid, action="flag").status_code == 409
    assert doc.post("/fact/update", messageId=mid, factId=fid, action="reset").status_code == 200
    assert fact_by_quote(api, mid, "我有点害怕")["status"] == C.AI_EXTRACTED
    assert doc.post("/fact/update", messageId=mid, factId=fid, action="nope").status_code == 400


def test_second_utterance_does_not_repeat_question(api):
    from datetime import datetime, timedelta, timezone
    api.patient_says(fake_ai.A_TEXT)
    # 少し時間をおいて同じことをもう一度言った（二重送信ではない）
    mid2 = api.patient_says(fake_ai.A_TEXT, createdAt=datetime.now(timezone.utc) + timedelta(seconds=30))
    assert api.get(mid2)["meaning"]["candidates"] == []


def test_context_is_passed_to_meaning_engine(api):
    seen = {}
    real = unsaid_ai.meaning

    def spy(*a):
        seen["args"] = a
        return real(*a)
    unsaid_ai.meaning = spy
    try:
        api.doctor_says("熱はありますか？")
        api.patient_says("Yes, I have a little fever too.")
    finally:
        unsaid_ai.meaning = real
    utterance, pl, dl, context, known, pending = seen["args"]
    assert context == [{"speaker": "doctor", "text": "熱はありますか？"}] and pending is None


# ── シナリオ B / C: 理解の確認 ──
def _teachback(api, explanation_id):
    r = api.as_("doc").post("/teachback/start", messageId=explanation_id)
    assert r.status_code == 200, r.text
    return r.json()["messageId"]


def test_scenario_b_mismatch_reexplain_recheck(api):
    exp = api.doctor_says(fake_ai.B_EXPLAIN)
    assert api.get(exp)["translation"]["text"] == fake_ai.B_EXPLAIN_ZH
    tb = _teachback(api, exp)
    t = api.get(tb)
    assert t["kind"] == "teachback" and t["translation"]["text"] == unsaid_ai.TEACHBACK_PROMPT["zh-CN"]
    assert t["teachback"]["status"] == "asked"
    aid = api.patient_says(fake_ai.B_WRONG, replyTo=tb, answer={"type": "free_text"})
    a = api.get(aid)
    assert a["translation"]["text"] == fake_ai.B_WRONG_JA
    assert a["teachback_result"]["overall"] == "inconsistent"
    t = api.get(tb)["teachback"]
    assert t["status"] == "assessed" and t["answer_message_id"] == aid and t["result"]["overall"] == "inconsistent"
    # 医師が「再説明が必要」→ 再説明 → 再確認
    assert api.as_("doc").post("/teachback/resolve", messageId=tb, action="reexplain").status_code == 200
    assert api.get(tb)["teachback"]["status"] == "needs_reexplain"
    exp2 = api.doctor_says(t["result"]["suggested_reexplanation"])
    tb2 = _teachback(api, exp2)
    api.patient_says(fake_ai.B_RIGHT, replyTo=tb2, answer={"type": "free_text"})
    t2 = api.get(tb2)["teachback"]
    assert t2["result"]["overall"] == "consistent" and t2["status"] == "assessed"  # AI の判定だけでは確定しない
    assert api.as_("doc").post("/teachback/resolve", messageId=tb2, action="confirm").status_code == 200
    assert api.get(tb2)["teachback"]["status"] == "doctor_confirmed"


def test_scenario_c_correct_understanding_no_warning(api):
    exp = api.doctor_says(fake_ai.C_EXPLAIN)
    tb = _teachback(api, exp)
    aid = api.patient_says(fake_ai.C_RIGHT, replyTo=tb, answer={"type": "free_text"})
    assert api.get(aid)["status"] == "sent"  # 正しい理解は確認を挟まずに届く
    r = api.get(aid)["teachback_result"]
    assert r["overall"] == "consistent" and r["suggested_reexplanation"] == ""
    assert all(p["verdict"] == "match" for p in r["points"])
    assert api.issues() == []  # 不要な警告・未解決事項を出さない


def test_teachback_rules(api):
    exp = api.doctor_says(fake_ai.C_EXPLAIN)
    tb = _teachback(api, exp)
    doc = api.as_("doc")
    assert doc.post("/teachback/start", messageId=tb).status_code == 400  # 依頼そのものには使えない
    pt_mid = api.patient_says("Yes, I have a little fever too.")
    assert api.as_("doc").post("/teachback/start", messageId=pt_mid).status_code == 400
    bad = api.as_("pat").say("patient", "一天三次", replyTo=tb, answer={"type": "option", "index": 0})
    assert api.post("/analyze", messageId=bad).status_code == 400
    assert api.as_("doc").post("/teachback/resolve", messageId=tb, action="maybe").status_code == 400
    assert api.post("/teachback/resolve", messageId=exp, action="confirm").status_code == 404


def test_teachback_compare_failure_keeps_answer(api):
    exp = api.doctor_says(fake_ai.B_EXPLAIN)
    tb = _teachback(api, exp)
    real = unsaid_ai.compare_understanding
    unsaid_ai.compare_understanding = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        aid = api.patient_says(fake_ai.B_WRONG, replyTo=tb, answer={"type": "free_text"})
    finally:
        unsaid_ai.compare_understanding = real
    a = api.get(aid)
    assert a["status"] == "sent" and a["original"] == fake_ai.B_WRONG and a["translation"]["text"] == fake_ai.B_WRONG_JA
    assert a["teachback_result"]["overall"] == "error"
    assert api.get(tb)["teachback"]["status"] == "answered"  # 判定できなかった。医師が会話を見て判断する


# ── 失敗しても原文は失われない ──
def test_meaning_failure_keeps_original_and_can_retry(api, caplog):
    real = unsaid_ai.meaning
    unsaid_ai.meaning = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        with caplog.at_level(logging.INFO, logger="unsaid"):
            mid = api.patient_says(fake_ai.A_TEXT)
    finally:
        unsaid_ai.meaning = real
    m = api.get(mid)
    assert m["status"] == "sent" and m["original"] == fake_ai.A_TEXT and m["translation"]["text"] == fake_ai.A_JA
    assert m["meaning"]["status"] == "error" and m["meaning"]["facts"] == []
    assert fake_ai.A_TEXT not in caplog.text and "头晕" not in caplog.text  # ログに患者の発言を出さない
    assert api.as_("doc").post("/meaning/retry", messageId=mid).status_code == 200
    assert api.get(mid)["meaning"]["status"] == "ready"
    assert api.post("/meaning/retry", messageId=mid).status_code == 409


def test_invalid_ai_output_is_not_adopted(api):
    real = unsaid_ai.meaning
    unsaid_ai.meaning = lambda *a: ({"facts": [{"category": "symptom"}], "ambiguities": []}, 0.1)
    try:
        mid = api.patient_says(fake_ai.A_TEXT)
    finally:
        unsaid_ai.meaning = real
    assert api.get(mid)["meaning"]["status"] == "error" and api.get(mid)["meaning"]["error"] == "InvalidAIOutput"


def test_translation_failure_marks_error(api):
    real = unsaid_ai.translate
    unsaid_ai.translate = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    mid = api.as_("pat").say("patient", fake_ai.A_TEXT)
    try:
        assert api.post("/analyze", messageId=mid).status_code == 502
    finally:
        unsaid_ai.translate = real
    assert api.get(mid)["status"] == "error" and api.get(mid)["original"] == fake_ai.A_TEXT
    assert api.post("/analyze", messageId=mid).status_code == 200  # 「もう一度」


# ── 権限・問診どうしの分離 ──
DOCTOR_ENDPOINTS = [
    ("/clarify/send", {"messageId": "m", "candidateId": "c1"}),
    ("/clarify/update", {"messageId": "m", "candidateId": "c1", "action": "hold"}),
    ("/fact/update", {"messageId": "m", "factId": "f1", "action": "confirm"}),
    ("/teachback/start", {"messageId": "m"}),
    ("/teachback/resolve", {"messageId": "m", "action": "confirm"}),
]


@pytest.mark.parametrize("path,body", DOCTOR_ENDPOINTS)
def test_doctor_only_endpoints(api, path, body):
    mid = api.patient_says(fake_ai.A_TEXT)
    body = {**body, "messageId": mid}
    assert api.as_("pat").post(path, **body).status_code == 403  # 患者は医師の操作をできない
    assert api.as_("stranger").post(path, **body).status_code == 403


def test_patient_only_send_and_session_isolation(api):
    mid = api.patient_says(fake_ai.A_TEXT, send=False)
    assert api.as_("doc").post("/send", messageId=mid).status_code == 403
    assert api.as_("stranger").post("/send", messageId=mid).status_code == 403
    # 別の問診（s2）の医師は、s1 の発言を自分の問診の id で指しても読めない
    from tests.conftest import Api
    other = Api(api.db, api.client, sid="s2")
    other.db.collection("sessions").document("s2").update({"members": ["doc2", "pat2"]})
    r = other.as_("doc2").post("/clarify/send", messageId=mid, candidateId="c1")
    assert r.status_code == 404  # s2 の中にその発言は無い
    assert other.as_("doc2").post("/fact/update", messageId=mid, factId="f1", action="confirm").status_code == 404
    assert api.as_("doc2").post("/clarify/send", messageId=mid, candidateId="c1").status_code == 403
    assert api.as_("pat").post("/send", messageId="../s2/messages/x").status_code == 400  # パスを渡せない
    assert api.as_("pat").post("/send", messageId=mid).status_code == 200
    assert api.post("/send", messageId=mid).status_code == 200  # 二重押しは何もしない
    assert api.get(mid)["status"] == "sent"


def test_send_requires_confirmed(api):
    mid = api.as_("pat").say("patient", "hello")
    assert api.post("/send", messageId=mid).status_code == 409


def test_session_expiry(api):
    api.db.collection("sessions").document(api.sid).update(
        {"createdAt": datetime.now(timezone.utc) - timedelta(hours=main.SESSION_MAX_HOURS + 1)})
    mid = api.as_("pat").say("patient", "hello")
    assert api.post("/analyze", messageId=mid).status_code == 410
    assert api.as_("doc").post("/teachback/start", messageId=mid).status_code == 410


def test_end_deletes_all_new_records(api):
    mid = api.patient_says(fake_ai.A_TEXT)
    qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").json()["messageId"]
    api.patient_says("周围的东西在转（天旋地转）", replyTo=qid, answer={"type": "option", "index": 0})
    exp = api.doctor_says(fake_ai.C_EXPLAIN)
    _teachback(api, exp)
    assert len(list(api.msgs().list_documents())) == 5
    r = api.as_("doc").post("/end")
    assert r.status_code == 200 and r.json()["deleted"] == 5
    assert api.gone()


def test_legacy_patient_mode_still_available(api):
    main.CLARIFY_MODE = "patient"
    try:
        mid = api.as_("pat").say("patient", fake_ai.E_TEXT)
        assert api.post("/analyze", messageId=mid).status_code == 200
        assert api.get(mid)["status"] == "confirmed" and "meaning" not in api.get(mid)
    finally:
        main.CLARIFY_MODE = "doctor"
