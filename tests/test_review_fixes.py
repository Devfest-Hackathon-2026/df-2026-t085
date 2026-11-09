"""レビューで見つかった不具合の回帰テスト（2026-11-09、HEAD 30523c3 時点の指摘）。

  1. 医師が編集・自作した確認質問が Safety Gate を通らずに届く
  3. 確認質問への自由回答の解析をやり直しても、元の確認候補に反映されない
（2. 患者画面で未回答の質問が消える件は web/patient-view.test.mjs）
"""
import main
import unsaid_ai
import ussa as U
from devtools import fake_ai as F


def cand(api, mid, i=0):
    return api.get(mid)["meaning"]["candidates"][i]


def dizzy(api, mid):
    return next(f for f in api.get(mid)["meaning"]["facts"] if f["source_quote"] == "头晕")


def count_verify(fn):
    calls = []
    real = main._verify

    def spy(*a, **k):
        calls.append(a[2])  # 照合した訳
        return real(*a, **k)
    main._verify = spy
    try:
        return fn(), calls
    finally:
        main._verify = real


# ── 1. 編集・自作した確認質問にも Safety Gate ──
def test_edited_question_with_lost_number_is_not_sent(api):
    mid = api.patient_says(F.A_TEXT)
    r, calls = count_verify(lambda: api.as_("doc").post(
        "/clarify/send", messageId=mid, candidateId="c1", questionDoctor=F.G_TEXT))
    assert r.status_code == 200, r.text
    qid = r.json()["messageId"]
    q = api.get(qid)
    assert calls, "編集した質問の訳を照合していない"
    assert q["status"] == "needs_review" and not q.get("translation")  # 「8時間」が落ちた訳は届かない
    assert q["gate"]["state"] == U.NEEDS_REVIEW
    assert cand(api, mid)["status"] == "proposed"  # まだ送っていないので「質問送信済み」にしない
    # 届いていない質問には答えられない
    aid = api.as_("pat").say("patient", "好", replyTo=qid, answer={"type": "free_text"})
    assert api.post("/analyze", messageId=aid).status_code == 400
    # 医師が「未検証として送る」を選ぶと届き、その時点で候補が「質問送信済み」になる
    assert api.as_("doc").post("/review", messageId=qid, action="send_unverified").status_code == 200
    q = api.get(qid)
    assert q["status"] == "sent" and q["gate"]["override"] == "speaker_ack"
    c = cand(api, mid)
    assert c["status"] == "asked" and c["question_message_id"] == qid
    assert dizzy(api, mid)["clarification_status"] == "asked"


def test_edited_question_verified_is_sent_and_linked(api):
    mid = api.patient_says(F.A_TEXT)
    (r, calls) = count_verify(lambda: api.as_("doc").post(
        "/clarify/send", messageId=mid, candidateId="c1", questionDoctor="周りが回る感じでしたか？"))
    qid = r.json()["messageId"]
    q = api.get(qid)
    assert calls and q["status"] == "sent" and q["gate"]["verification"]["status"] == U.VERIFIED
    assert q["clarify"]["edited"] and q["clarify"]["options"] == []
    assert cand(api, mid)["status"] == "asked" and cand(api, mid)["question_message_id"] == qid
    assert r.json()["status"] == "sent"


def test_edited_question_rephrase_keeps_candidate_open(api):
    mid = api.patient_says(F.A_TEXT)
    qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1",
                              questionDoctor=F.G_TEXT).json()["messageId"]
    assert api.post("/review", messageId=qid, action="rephrase").json()["status"] == "cancelled"
    assert cand(api, mid)["status"] == "proposed"
    # 言い直して送り直せる（取り消した質問は「送信中」とみなさない）
    r = api.post("/clarify/send", messageId=mid, candidateId="c1", questionDoctor="周りが回る感じでしたか？")
    assert r.status_code == 200 and api.get(r.json()["messageId"])["status"] == "sent"


def test_pending_edited_question_blocks_second_send(api):
    mid = api.patient_says(F.A_TEXT)
    api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1", questionDoctor=F.G_TEXT)
    assert api.post("/clarify/send", messageId=mid, candidateId="c1").status_code == 409


def test_custom_question_candidate_created_only_when_sent(api):
    mid = api.patient_says(F.A_TEXT)
    course = next(f for f in api.get(mid)["meaning"]["facts"] if f["category"] == "course")
    r = api.as_("doc").post("/clarify/send", messageId=mid, factId=course["fact_id"], questionDoctor=F.G_TEXT)
    qid = r.json()["messageId"]
    assert api.get(qid)["status"] == "needs_review"
    assert [c["origin"] for c in api.get(mid)["meaning"]["candidates"]] == ["ai"]  # まだ足さない
    api.post("/review", messageId=qid, action="send_unverified")
    c = cand(api, mid, -1)
    assert c["origin"] == "doctor" and c["status"] == "asked" and c["question_message_id"] == qid
    assert c["question_patient"] == api.get(qid)["translation"]["text"]


def test_issue_question_marked_only_when_sent(api):
    api.patient_says(F.H_NO_ALLERGY)
    api.patient_says(F.H_PENICILLIN)
    iid = api.issues()[0]["issue_id"]
    qid = api.as_("doc").post("/clarify/send", questionDoctor=F.G_TEXT, issueId=iid).json()["messageId"]
    assert api.issues()[0]["status"] == U.OPEN
    api.post("/review", messageId=qid, action="send_unverified")
    assert api.issues()[0]["status"] == U.CLAR_REQ


def test_candidate_pair_path_is_pretranslated_and_unchanged(api):
    mid = api.patient_says(F.A_TEXT)
    (r, calls) = count_verify(lambda: api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1"))
    q = api.get(r.json()["messageId"])
    assert not calls and q["status"] == "sent"  # 確認候補の対訳はそのまま（従来どおり）
    assert q["gate"]["verification"]["status"] == U.PRETRANSLATED  # 照合していないことを記録する
    assert cand(api, mid)["status"] == "asked"


# ── 3. 自由回答の解析のやり直しを、元の確認候補に反映する ──
def _answer_with_meaning_failure(api):
    mid = api.patient_says(F.A_TEXT)
    qid = api.as_("doc").post("/clarify/send", messageId=mid, candidateId="c1").json()["messageId"]
    real = unsaid_ai.meaning

    def flaky(utterance, *a, **k):
        if utterance == F.A_ANSWER:
            raise TimeoutError("slow")
        return real(utterance, *a, **k)
    unsaid_ai.meaning = flaky
    try:
        aid = api.patient_says(F.A_ANSWER, replyTo=qid, answer={"type": "free_text"})
    finally:
        unsaid_ai.meaning = real
    assert api.get(aid)["status"] == "sent" and api.get(aid)["meaning"]["status"] == "error"
    assert cand(api, mid)["status"] == "asked" and cand(api, mid)["answers"] == []
    backdate(api, aid)  # 答えが届いてから、医師が見て判断・やり直しを押すまでの時間
    return mid, qid, aid


def backdate(api, mid, sec=30):
    """発言 mid が sec 秒前に届いたことにする（元の確認候補に記録済みの送信時刻も合わせる）。"""
    from datetime import datetime, timedelta
    m = api.get(mid)
    api.msgs().document(mid).update({"sentAt": m["sentAt"] - timedelta(seconds=sec)})
    for d in api.msgs().stream():
        meaning = d.to_dict().get("meaning") or {}
        hit = [a for c in meaning.get("candidates", []) for a in c.get("answers", []) if a["message_id"] == mid]
        for a in hit:
            a["sent_at"] = (datetime.fromisoformat(a["sent_at"]) - timedelta(seconds=sec)).isoformat()
        if hit:
            api.msgs().document(d.id).update({"meaning": meaning})


def test_meaning_retry_restores_question_context_and_applies_answer(api):
    mid, qid, aid = _answer_with_meaning_failure(api)
    seen = {}
    real = unsaid_ai.meaning

    def spy(*a, **k):
        seen["pending"] = a[5]
        return real(*a, **k)
    unsaid_ai.meaning = spy
    try:
        assert api.as_("doc").post("/meaning/retry", messageId=aid).status_code == 200
    finally:
        unsaid_ai.meaning = real
    assert seen["pending"] and seen["pending"]["source_quote"] == "头晕"
    assert api.get(aid)["meaning"]["resolution"]["resolves"] == "yes"
    c = cand(api, mid)
    assert c["status"] == "answered" and [x["message_id"] for x in c["answers"]] == [aid]
    assert dizzy(api, mid)["status"] == "patient_answered"
    assert api.get(qid)["clarify"]["last_answer_id"] == aid
    # もう一度やり直しても重複しない
    assert api.post("/meaning/retry", messageId=aid).status_code == 409
    main._apply_reply(api.db.collection("sessions").document(api.sid), aid)
    assert len(cand(api, mid)["answers"]) == 1


def test_late_retry_does_not_overwrite_doctor_confirmed(api):
    mid, qid, aid = _answer_with_meaning_failure(api)
    fid = dizzy(api, mid)["fact_id"]
    api.as_("doc").post("/fact/update", messageId=mid, factId=fid, action="confirm")
    assert api.post("/meaning/retry", messageId=aid).status_code == 200
    f = dizzy(api, mid)
    assert f["status"] == "doctor_confirmed" and f["resolved_meaning"] == ""  # 医師の確認を上書きしない
    assert any(h["event"] == "patient_answered_late" for h in f["history"])  # 答えが届いたことは履歴に残す
    assert cand(api, mid)["status"] == "answered" and len(cand(api, mid)["answers"]) == 1


# ── 3'. 再試行の AI 処理の途中で医師が確認・要修正にしても、遅れて終わった再試行で上書きしない ──
def _retry_paused(api, during):
    """再試行の Meaning Engine を止めている間に during(mid, qid, aid) を実行し、それから再開する（完了の順序を固定）。"""
    import threading
    mid, qid, aid = _answer_with_meaning_failure(api)
    started, resume = threading.Event(), threading.Event()
    real = unsaid_ai.meaning

    def paused(*a, **k):
        started.set()
        assert resume.wait(10), "再開されない"
        return real(*a, **k)
    unsaid_ai.meaning = paused
    box = {}
    client = api.client

    def retry():
        main.app.dependency_overrides[main.current_uid] = lambda: "doc"
        box["r"] = client.post("/meaning/retry", json={"sessionId": api.sid, "messageId": aid})
    t = threading.Thread(target=retry)
    try:
        t.start()
        assert started.wait(10), "再試行の AI 処理が始まらない"  # 1) AI 処理を一時停止中
        assert api.get(aid)["meaning"]["status"] == "retrying"
        unsaid_ai.meaning = real  # during の中の解析は止めない（再試行の1本だけ止めている）
        out = during(mid, qid, aid)  # 2) 途中で別の操作
        resume.set()  # 3) AI 処理を再開
        t.join(10)
        assert not t.is_alive()
    finally:
        resume.set()
        unsaid_ai.meaning = real
    return box["r"], mid, qid, aid, out


def _retry_paused_while_doctor_acts(api, action):
    def doctor(mid, qid, aid):
        r = api.as_("doc").post("/fact/update", messageId=mid, factId=dizzy(api, mid)["fact_id"], action=action,
                                note="医師の判断")
        assert r.status_code == 200, r.text  # 医師が確認／要修正
        return dizzy(api, mid)
    r, mid, qid, aid, before = _retry_paused(api, doctor)
    assert r.status_code == 200, r.text
    return mid, qid, aid, before


def _assert_doctor_kept(api, mid, qid, aid, before, status):
    f = dizzy(api, mid)
    assert f["status"] == status  # 4) 医師の状態を維持
    assert f["resolved_meaning"] == before["resolved_meaning"]
    assert f["history"][:len(before["history"])] == before["history"]  # 医師の判断の履歴を消さない
    assert f["history"][len(before["history"]) - 1]["by"] == "doctor"
    late = [h for h in f["history"] if h["event"] == "patient_answered_late"]
    assert [h["ref"] for h in late] == [aid]  # 答えが届いたことは1回だけ履歴に残す
    assert not any(h["event"] == "patient_answered" for h in f["history"][len(before["history"]):])
    c = cand(api, mid)
    assert c["status"] == "answered" and [x["message_id"] for x in c["answers"]] == [aid]  # 回答履歴は重複しない
    assert api.get(aid)["meaning"]["status"] == "ready"
    assert api.get(qid)["clarify"]["last_answer_id"] == aid
    # もう一度結びつけても（/send と解析の完了の両方から呼ばれる）重複も上書きもしない
    main._apply_reply(api.db.collection("sessions").document(api.sid), aid)
    main._apply_reply(api.db.collection("sessions").document(api.sid), aid)
    assert dizzy(api, mid) == f and len(cand(api, mid)["answers"]) == 1


def test_doctor_confirm_during_paused_retry_is_kept(api):
    mid, qid, aid, before = _retry_paused_while_doctor_acts(api, "confirm")
    assert before["status"] == "doctor_confirmed"
    _assert_doctor_kept(api, mid, qid, aid, before, "doctor_confirmed")


def test_doctor_flag_during_paused_retry_is_kept(api):
    mid, qid, aid, before = _retry_paused_while_doctor_acts(api, "flag")
    assert before["status"] == "needs_correction"
    _assert_doctor_kept(api, mid, qid, aid, before, "needs_correction")


def test_late_retry_does_not_override_newer_corrected_answer(api):
    """再試行の途中で患者が答えを直した（新しい答えが先に届いた）: 遅れて終わった古い答えで巻き戻さない。"""
    newer = "不是转的，是眼前发黑。"

    def patient_corrects(mid, qid, aid):
        a2 = api.patient_says(newer, replyTo=qid, answer={"type": "free_text"})
        assert dizzy(api, mid)["resolved_meaning"] == f"[ja] {newer}"
        return a2
    r, mid, qid, aid, a2 = _retry_paused(api, patient_corrects)
    assert r.status_code == 200, r.text
    assert dizzy(api, mid)["resolved_meaning"] == f"[ja] {newer}"  # 新しい答えのまま
    c = cand(api, mid)
    assert [x["message_id"] for x in c["answers"]] == [aid, a2]  # 送った順（最後が今の答え）。重複しない
    assert c["answers"][0]["superseded_by"] == a2
    assert api.get(aid)["meaning"]["status"] == "ready"  # 古い答え自体の解析結果は残す


def test_retry_does_not_write_back_over_replaced_meaning(api):
    """再試行の途中で、その答えの meaning がほかの処理に置き換えられた: 古い結果を書き戻さず、元の候補にも当てない。"""
    def replaced(mid, qid, aid):
        api.msgs().document(aid).update({"meaning": {**api.get(aid)["meaning"], "status": "ready", "retry_run": "x",
                                                     "facts": [], "candidates": []}})
    r, mid, qid, aid, _ = _retry_paused(api, replaced)
    assert r.status_code == 409
    assert api.get(aid)["meaning"]["retry_run"] == "x"
    assert cand(api, mid)["answers"] == [] and dizzy(api, mid)["status"] != "patient_answered"
