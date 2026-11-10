"""clinical.py（原文との突き合わせ・Meaning Engine の検証・状態遷移・理解の確認の判定）の単体テスト。"""
import json
from pathlib import Path

import pytest

import clinical as C
from devtools import fake_ai

ROOT = Path(__file__).resolve().parent.parent
MEANING_SCHEMA = json.loads((ROOT / "schemas/meaning.json").read_text(encoding="utf-8"))
TEACHBACK_SCHEMA = json.loads((ROOT / "schemas/teachback.json").read_text(encoding="utf-8"))


def raw_meaning(text, **kw):
    out, _ = fake_ai.meaning(text, "zh-CN", **kw)
    out.pop("_meta")
    return out


def build(text=fake_ai.A_TEXT, known=(), lang="zh-CN", raw=None):
    return C.build_meaning(raw or raw_meaning(text), utterance_id="m1", source_text=text, source_lang=lang,
                           known_quotes=known, schema=MEANING_SCHEMA)


# ── 原文との突き合わせ ──
@pytest.mark.parametrize("quote,source,expect", [
    ("头晕", "今天突然觉得头晕。", ("头晕", "exact")),
    ("Headache", "I have a headache.", ("headache", "normalized")),          # 大文字小文字
    ("38.5", "熱は３８．５度", ("３８．５", "normalized")),                     # 全角・半角
    ("for  a while", "I've had it for a while.", ("for a while", "normalized")),  # 空白
    ("「头晕」", "觉得头晕", ("头晕", "normalized")),                          # 前後の括弧・句読点
])
def test_ground_accepts_real_spans(quote, source, expect):
    s, e, ev = C.ground(quote, source)
    assert (source[s:e], ev) == expect


@pytest.mark.parametrize("quote", ["dizziness", "我有贫血", "头疼", "", "   "])
def test_ground_rejects_paraphrase_and_invention(quote):
    # 言い換え（dizzy → dizziness）・原文に無い文・近い別の語（头晕 → 头疼）は採用しない
    assert C.ground(quote, "I feel dizzy. 今天觉得头晕。") is None


# ── Meaning Engine の出力の検証 ──
def test_build_meaning_keeps_grounded_facts_and_rejects_invented():
    m = build()
    quotes = [f["source_quote"] for f in m["facts"]]
    assert "头晕" in quotes and "我有点害怕" in quotes
    assert all(fake_ai.A_TEXT[f["span_start"]:f["span_end"]] == f["source_quote"] for f in m["facts"])
    # 原文に無い「我有贫血」（貧血の既往）は採用されず、捨てた記録だけ残る
    assert not any(f["category"] == "history" for f in m["facts"])
    assert {"type": "fact", "reason": "quote_not_in_source", "category": "history",
            "normalized_meaning": "貧血の既往"} in m["rejected"]


def test_build_meaning_initial_status_and_links():
    m = build()
    dizzy = next(f for f in m["facts"] if f["source_quote"] == "头晕")
    others = [f for f in m["facts"] if f is not dizzy]
    assert dizzy["status"] == C.UNCONFIRMED and dizzy["clarification_status"] == "open"
    assert all(f["status"] == C.AI_EXTRACTED for f in others)
    c = m["candidates"][0]
    assert c["source_quote"] == "头晕" and c["fact_ids"] == [dizzy["fact_id"]] and c["status"] == C.C_PROPOSED
    assert len(c["options_patient"]) == len(c["options_doctor"]) == 3
    # 属性はどの症状の話かにつながる
    onset = next(f for f in m["facts"] if f["category"] == "onset")
    assert onset["about_fact_id"] == dizzy["fact_id"]
    assert all(h["by"] == "ai" for f in m["facts"] for h in f["history"])


def test_build_meaning_skips_already_asked():
    m = build(known=["头晕"])
    assert m["candidates"] == []
    assert any(r["reason"] == "already_asked" for r in m["rejected"])


def _amb(**over):
    a = {"source_quote": "头晕", "meaning_doctor": "めまい", "reason": "r", "unresolved": "u", "priority": "low",
         "clinically_significant": True, "question_patient": "哪种?", "question_doctor": "どれ？",
         "options_patient": ["a", "b"], "options_doctor": ["A", "B"]}
    a.update(over)
    return a


@pytest.mark.parametrize("over,reason", [
    ({"clinically_significant": False}, "not_significant"),
    ({"source_quote": "头疼"}, "quote_not_in_source"),
    ({"question_patient": "めまいはどんな感じ?"}, "question_language"),  # 患者に日本語を出さない
    ({"question_patient": ""}, "no_question"),
])
def test_candidate_filters(over, reason):
    raw = {"facts": [], "ambiguities": [_amb(**over)], "resolution": fake_ai.NO_RESOLUTION}
    m = build(text="我头晕。", raw=raw)
    assert m["candidates"] == [] and m["rejected"][0]["reason"] == reason


def test_candidate_options_must_align_and_priority_sorts():
    raw = {"facts": [], "resolution": fake_ai.NO_RESOLUTION, "ambiguities": [
        _amb(source_quote="有时候", priority="low", options_doctor=["A"]),  # 選択肢がそろわない → 自由回答に
        _amb(source_quote="头晕", priority="high"),
    ]}
    m = build(text="我有时候头晕。", raw=raw)
    assert [c["source_quote"] for c in m["candidates"]] == ["头晕", "有时候"]
    assert m["candidates"][1]["options_patient"] == [] and m["candidates"][0]["rank"] == 1


def test_schema_violation_raises_without_leaking_text():
    bad = {"facts": [{"category": "symptom", "normalized_meaning": "患者の秘密の文", "source_quote": "x",
                      "polarity": "maybe", "about": ""}], "ambiguities": [], "resolution": fake_ai.NO_RESOLUTION}
    with pytest.raises(C.InvalidAIOutput) as e:
        build(text="x", raw=bad)
    assert "秘密" not in str(e.value) and "facts/0/polarity" == str(e.value)


def test_resolution_requires_grounding():
    raw = {"facts": [], "ambiguities": [],
           "resolution": {"applies": True, "resolves": "yes", "meaning_doctor": "回転性", "source_quote": "天旋地转"}}
    m = build(text="就是那种感觉。", raw=raw)  # 答えのどこにも「天旋地转」は無い
    assert m["resolution"]["resolves"] == "no" and m["resolution"]["meaning_doctor"] == ""


# ── 状態遷移 ──
DOCTOR_ONLY = {C.DOCTOR_CONFIRMED, C.NEEDS_CORRECTION}


def test_ai_and_patient_events_never_reach_doctor_states():
    for (frm, ev), to in C.TRANSITIONS.items():
        if C.EVENT_ACTOR[ev] in ("ai", "patient") and to in DOCTOR_ONLY:
            assert frm == to, f"{ev} が {frm} → {to} にしている"


def test_illegal_transitions_raise():
    f = {"status": C.AI_EXTRACTED, "history": []}
    with pytest.raises(C.TransitionError):
        C.apply_fact_event(f, "candidate_dismissed")
    C.apply_fact_event(f, "doctor_confirm")
    with pytest.raises(C.TransitionError):
        C.apply_fact_event(f, "doctor_confirm")  # 2回目
    assert f["status"] == C.DOCTOR_CONFIRMED and len(f["history"]) == 1


def _asked():
    m = build()
    C.mark_question_sent(m, "c1", "q1")
    return m, next(f for f in m["facts"] if f["source_quote"] == "头晕")


def test_answer_option_resolves_and_is_idempotent():
    m, dizzy = _asked()
    assert m["candidates"][0]["status"] == C.C_ASKED and dizzy["clarification_status"] == "asked"
    for _ in range(2):
        C.apply_answer(m, "c1", message_id="a1", answer_type="option", resolves="yes", meaning_doctor="回転性")
    c = m["candidates"][0]
    assert c["status"] == C.C_ANSWERED and len(c["answers"]) == 1
    assert dizzy["status"] == C.PATIENT_ANSWERED and dizzy["resolved_meaning"] == "回転性"
    assert dizzy["status"] != C.DOCTOR_CONFIRMED  # 患者が答えても医師確認済みにはならない


@pytest.mark.parametrize("atype,resolves", [("unsure", "no"), ("none", "no"), ("free_text", "no"), ("option", "yes")])
def test_unsure_none_or_nonanswer_keep_unconfirmed(atype, resolves):
    m, dizzy = _asked()
    meaning = "" if atype != "option" else ""  # option でも意味が空なら確定しない
    C.apply_answer(m, "c1", message_id="a1", answer_type=atype, resolves=resolves, meaning_doctor=meaning)
    assert dizzy["status"] == C.UNCONFIRMED and dizzy["resolved_meaning"] == ""
    assert m["candidates"][0]["status"] == C.C_UNRESOLVED and dizzy["clarification_status"] == "unresolved"


def test_revised_answer_keeps_history():
    m, dizzy = _asked()
    C.apply_answer(m, "c1", message_id="a1", answer_type="none", resolves="no", meaning_doctor="")
    C.apply_answer(m, "c1", message_id="a2", answer_type="free_text", resolves="yes", meaning_doctor="回転性のめまい")
    c = m["candidates"][0]
    assert [a["message_id"] for a in c["answers"]] == ["a1", "a2"] and c["status"] == C.C_ANSWERED
    assert dizzy["resolved_meaning"] == "回転性のめまい"
    assert [h["event"] for h in dizzy["history"]] == ["extracted", "ambiguity_found", "question_sent",
                                                       "patient_unsure", "patient_answered"]


def test_doctor_actions_and_reset():
    m, dizzy = _asked()
    C.apply_answer(m, "c1", message_id="a1", answer_type="option", resolves="yes", meaning_doctor="回転性")
    C.doctor_fact_action(m, dizzy["fact_id"], "confirm")
    assert dizzy["status"] == C.DOCTOR_CONFIRMED
    C.doctor_fact_action(m, dizzy["fact_id"], "reset")
    assert dizzy["status"] == C.PATIENT_ANSWERED  # 患者の回答の状態に戻る
    C.doctor_fact_action(m, dizzy["fact_id"], "flag")
    assert dizzy["status"] == C.NEEDS_CORRECTION
    with pytest.raises(C.TransitionError):
        C.doctor_fact_action(m, dizzy["fact_id"], "explode")


def test_dismiss_keeps_fact_unconfirmed_and_late_answer_is_only_recorded():
    m = build()
    dizzy = next(f for f in m["facts"] if f["source_quote"] == "头晕")
    C.doctor_candidate_action(m, "c1", "hold")
    assert m["candidates"][0]["status"] == C.C_HELD
    C.doctor_candidate_action(m, "c1", "dismiss")
    assert dizzy["status"] == C.UNCONFIRMED and dizzy["clarification_status"] == "skipped"
    with pytest.raises(C.TransitionError):
        C.doctor_candidate_action(m, "c1", "hold")
    C.doctor_candidate_action(m, "c1", "restore")
    assert m["candidates"][0]["status"] == C.C_PROPOSED and dizzy["clarification_status"] == "open"


def test_custom_candidate_links_to_fact():
    m = build()
    f = next(x for x in m["facts"] if x["category"] == "course")
    c = C.custom_candidate(m, utterance_id="m1", source_text=fake_ai.A_TEXT, fact_id=f["fact_id"],
                           question_doctor="今も続いていますか？", question_patient="现在还在吗？")
    assert c["origin"] == "doctor" and c["source_quote"] == f["source_quote"] and c["fact_ids"] == [f["fact_id"]]
    with pytest.raises(KeyError):
        C.custom_candidate(m, utterance_id="m1", source_text="", fact_id="f99", question_doctor="", question_patient="")


# ── 理解の確認 ──
def _tb(restatement):
    out, _ = fake_ai.compare_understanding("", "", restatement, "zh-CN")
    out.pop("_meta")
    return C.build_teachback(out, restatement=restatement, schema=TEACHBACK_SCHEMA)


def test_teachback_mismatch_detected():
    r = _tb(fake_ai.B_WRONG)
    assert r["overall"] == "inconsistent" and r["points"][0]["restatement_quote"] == "明天再来就可以了"
    assert r["suggested_reexplanation"] and r["decided_by"] == "ai"


def test_teachback_consistent_has_no_warning():
    for text in (fake_ai.B_RIGHT, fake_ai.C_RIGHT):
        r = _tb(text)
        assert r["overall"] == "consistent" and r["suggested_reexplanation"] == ""


def test_teachback_overall_is_computed_not_trusted():
    p = lambda v, q="": {"doctor_point": "x", "patient_understanding": "", "verdict": v, "explanation": "",
                         "restatement_quote": q}
    mk = lambda *pts: C.build_teachback({"points": list(pts), "suggested_reexplanation": "s"}, restatement="明天来")
    assert mk(p("match", "明天"), p("missing"))["overall"] == "partial"
    assert mk(p("match", "明天"), p("unclear"))["overall"] == "unclear"
    # 患者の言葉のどこが食い違うか示せない mismatch は「不一致」と言い切らない
    r = mk(p("mismatch", "後天"))
    assert r["overall"] == "unclear" and r["rejected"] == 1
    assert mk()["overall"] == "unclear"


def test_teachback_transitions():
    tb = {"status": C.T_ASKED, "history": []}
    C.apply_teachback_event(tb, "answer_sent", by="patient")
    C.apply_teachback_event(tb, "assessed", by="ai")
    assert tb["status"] == C.T_ASSESSED  # AI の判定は「確認済み」ではない
    C.apply_teachback_event(tb, "doctor_reexplain", by="doctor")
    C.apply_teachback_event(tb, "doctor_confirm", by="doctor")
    assert tb["status"] == C.T_CONFIRMED
    for (_frm, ev), to in C.TEACHBACK_TRANSITIONS.items():
        if to == C.T_CONFIRMED:
            assert ev == "doctor_confirm"


def test_meaning_facts_capped_in_code_not_schema():
    """件数の上限（20）はコードで守る。スキーマの maxItems は本物の Vertex AI が「複雑すぎる」と断るので付けない。"""
    import json

    import clinical
    from pathlib import Path
    schema = json.loads((Path(__file__).resolve().parent.parent / "schemas/meaning.json").read_text())
    assert "maxItems" not in schema["properties"]["facts"]
    text = "头" * 30
    raw = {"facts": [{"category": "symptom", "normalized_meaning": f"症状{i}", "source_quote": "头", "polarity": "present",
                      "about": ""} for i in range(25)], "ambiguities": [],
           "resolution": {"applies": False, "resolves": "no", "meaning_doctor": "", "source_quote": ""}}
    m = clinical.build_meaning(raw, utterance_id="u", source_text=text, source_lang="zh-CN", schema=schema)
    assert len(m["facts"]) == clinical.MAX_FACTS
    assert {"type": "fact", "reason": "too_many", "dropped": 5} in m["rejected"]
