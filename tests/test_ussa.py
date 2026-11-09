"""ussa.py（Semantic Bridge・Verification Engine のルールと枠の比較・Safety Gate・Source Agent の検証・Conversation Auditor）の単体テスト。"""
import json
from pathlib import Path

import pytest

import clinical
import ussa as U
from devtools.fake_ai import _frame as full_frame

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = {n: json.loads((ROOT / f"schemas/{n}.json").read_text(encoding="utf-8"))
          for n in ("source", "frame_extract", "verify_compare", "audit")}


def frame(**kw):
    return U.build_frame(kw, kw.pop("_text", ""))


# ── ルール（D）: 数・時刻・日付・否定 ──
@pytest.mark.parametrize("src,sl,tr,tl", [
    ("この薬は1日2回、朝と夜に1錠ずつ飲んでください。", "ja", "这个药一天两次，早上和晚上各吃一片。", "zh-CN"),
    ("熱は38度5分です。", "ja", "The fever is 38.5 degrees.", "en"),
    ("8時半に来てください。", "ja", "Please come at 8:30.", "en"),
    ("11月10日に検査します。", "ja", "The test is on November 10.", "en"),
    ("来週の月曜日にもう一度来てください。", "ja", "Please come back next Monday.", "en"),
    ("来週の月曜日にもう一度来てください。", "ja", "Thứ Hai tuần sau hãy quay lại.", "vi"),
    ("今日は帰らずに待ってください。", "ja", "今天请先不要回家，在这里等。", "zh-CN"),
    ("必ず飲んでください。", "ja", "Be sure to take it.", "en"),
    ("Tôi uống hai viên mỗi ngày.", "vi", "毎日2錠飲んでいます。", "ja"),
    ("我休息了十分钟。", "zh-CN", "10分休みました。", "ja"),
])
def test_rules_accept_correct_translations(src, sl, tr, tl):
    assert [i for i in U.rule_check(src, sl, tr, tl) if i["strength"] == "strong"] == []


@pytest.mark.parametrize("src,sl,tr,tl,kind", [
    ("1日2回飲んでください。", "ja", "Take it three times a day.", "en", "quantity_change"),
    ("今日は帰らずに待ってください。", "ja", "明天再来。", "zh-CN", "time_change"),
    ("来週の月曜日に来てください。", "ja", "Hãy quay lại vào thứ Hai tuần này.", "vi", "time_change"),
    ("検査の前は8時間何も食べないでください。", "ja", "检查前请不要吃东西。", "zh-CN", "quantity_change"),
    ("熱は38度5分です。", "ja", "The fever is 39.5 degrees.", "en", "quantity_change"),
    ("8時半に来てください。", "ja", "Please come at 9:30.", "en", "quantity_change"),
])
def test_rules_catch_number_and_date_changes(src, sl, tr, tl, kind):
    strong = [i["type"] for i in U.rule_check(src, sl, tr, tl) if i["strength"] == "strong"]
    assert kind in strong


def test_rules_negation_is_only_a_weak_signal():
    # 「没事」と「I'm fine」は否定の字面が違うだけで同じ意味。ルールだけでは不一致にしない
    issues = U.rule_check("没事。", "zh-CN", "I'm fine.", "en")
    assert [i["strength"] for i in issues] == ["weak"]
    assert U.combine(issues, [], {"equivalent": True, "issues": []})["status"] == U.VERIFIED


# ── 枠の比較（A と B）──
def test_frame_compare_catches_prohibition_flip_and_counts():
    a = frame(speech_act="instruction", polarity="negative", modality="must_not", action="take medicine",
              conditions=["on an empty stomach"])
    b = frame(speech_act="instruction", polarity="affirmative", modality="must", action="take medicine",
              conditions=["on an empty stomach"])
    types = {i["type"] for i in U.compare_frames(a, b) if i["strength"] == "strong"}
    assert "polarity_flip" in types
    a2 = frame(quantities=[{"value": 1, "unit": "tablet"}], frequency=[{"count": 2, "per": "day"}])
    b2 = frame(quantities=[{"value": 2, "unit": "tablet"}], frequency=[{"count": 1, "per": "day"}])
    t2 = {i["type"] for i in U.compare_frames(a2, b2)}
    assert {"quantity_change", "frequency_change"} <= t2


def test_frame_compare_consent_refusal_condition_and_presence():
    issues = U.compare_frames(frame(speech_act="consent"), frame(speech_act="refuse"))
    assert issues[0]["type"] == "consent_refusal_confusion" and issues[0]["strength"] == "strong"
    issues = U.compare_frames(frame(conditions=["until results are out"]), frame())
    assert issues[0]["type"] == "condition_missing"
    issues = U.compare_frames(frame(medical_entities=[{"concept": "pain", "presence": "absent"}]),
                              frame(medical_entities=[{"concept": "pain", "presence": "present"}]))
    assert issues[0]["type"] == "symptom_presence_flip"
    assert U.compare_frames(frame(times=[{"kind": "part_of_day", "value": "evening"}]),
                            frame(times=[{"kind": "part_of_day", "value": "night"}])) == []  # 夜 = evening / night


def test_frame_keeps_unknowns_and_grounds_evidence():
    f = U.build_frame({"speech_act": "nonsense", "quantities": [{"value": "x", "unit": "tablet"}]}, "1錠飲む",
                      [{"element": "quantity", "quote": "1錠"}, {"element": "quantity", "quote": "2錠"}])
    assert f["speech_act"] == "other" and f["polarity"] == "none" and f["quantities"] == []
    assert f["evidence"] == [{"element": "quantity", "quote": "1錠"}] and f["evidence_dropped"] == 1


# ── 結果のまとめ方（LLM の「同じ意味」で強い不一致は消えない）──
def test_combine_strong_rule_beats_llm_equivalent():
    rules = U.rule_check("1日2回", "ja", "three times a day", "en")
    v = U.combine(rules, [], {"equivalent": True, "issues": []})
    assert v["status"] == U.MISMATCH


def test_combine_failed_and_ambiguous():
    assert U.combine([], [], None, ["frame:Timeout"])["status"] == U.VERIFICATION_FAILED
    medium = [{"type": "actor_change", "strength": "medium", "layer": "frame", "detail": ""}]
    assert U.combine([], medium, {"equivalent": False, "issues": []})["status"] == U.AMBIGUOUS
    assert U.combine([], medium, {"equivalent": True, "issues": []})["status"] == U.VERIFIED


def test_llm_issue_needs_grounding_to_be_strong():
    raw = {"equivalent": False, "issues": [
        {"type": "polarity_flip", "severity": "critical", "source_quote": "飲まないで", "translation_quote": "吃",
         "explanation_doctor": "x", "explanation_speaker": "x"},
        {"type": "addition", "severity": "critical", "source_quote": "存在しない", "translation_quote": "也不存在",
         "explanation_doctor": "y", "explanation_speaker": "y"}]}
    c = U.build_llm_check(raw, source="飲まないでください", translation="请吃药", schema=SCHEMA["verify_compare"])
    assert [i["strength"] for i in c["issues"]] == ["strong", "medium"]


# ── Source Agent の結果 ──
def _src(state, interps, question="哪个意思？"):
    return {"intent_state": state, "speech_act": "answer", "summary_listener": "", "question_speaker": question,
            "reason_speaker": "", "interpretations": interps, "frame": full_frame("answer"), "evidence": []}


def test_intent_requires_valid_options_in_the_right_languages():
    ok = [{"meaning_speaker": "可以，继续", "meaning_listener": "続けてください", "speech_act": "consent"},
          {"meaning_speaker": "我没事", "meaning_listener": "体調は大丈夫です", "speech_act": "statement"}]
    i = U.build_intent(_src("clarification_required", ok), source_text="没事", speaker_lang="zh-CN",
                       listener_lang="ja", schema=SCHEMA["source"])
    assert i["state"] == U.CLARIFICATION_REQUIRED and [x["id"] for x in i["interpretations"]] == ["i1", "i2"]
    bad = [{**ok[0], "meaning_speaker": "続けて"}, ok[1]]  # 患者向けの文に日本語 → 選択肢として使わない
    i = U.build_intent(_src("clarification_required", bad), source_text="没事", speaker_lang="zh-CN", listener_lang="ja")
    assert i["state"] == U.UNRESOLVED
    i = U.build_intent(_src("resolved", ok), source_text="没事", speaker_lang="zh-CN", listener_lang="ja")
    assert i["state"] == U.RESOLVED and i["interpretations"] == [] and i["decided_by"] == "ai"


def test_confirm_intent_is_distinct_from_resolved():
    ok = [{"meaning_speaker": "可以，继续", "meaning_listener": "続けてください", "speech_act": "consent"},
          {"meaning_speaker": "我没事", "meaning_listener": "体調は大丈夫です", "speech_act": "statement"}]
    i = U.build_intent(_src("clarification_required", ok), source_text="没事", speaker_lang="zh-CN", listener_lang="ja")
    c = U.confirm_intent(i, "i1")
    assert c["state"] == U.USER_CONFIRMED and c["decided_by"] == "speaker" and c["confirmed"]["speech_act"] == "consent"
    f = U.confirm_intent(i, "free_text", "意思是可以")
    assert f["confirmed"]["kind"] == "free_text"
    for bad in [("i9", ""), ("free_text", "  ")]:
        with pytest.raises(ValueError):
            U.confirm_intent(i, *bad)
    with pytest.raises(ValueError):
        U.confirm_intent(c, "i1")  # 確認済みをもう一度は変えない


# ── Safety Gate ──
def test_gate_transitions_have_no_auto_send_shortcuts():
    # SENT に入れるのは READY_TO_SEND からだけ。READY_TO_SEND には検証（VERIFYING）か発言者の確認（NEEDS_REVIEW）を通ってしか来ない
    into_sent = [s for s, nxt in U.GATE_TRANSITIONS.items() if U.SENT in nxt]
    into_ready = [s for s, nxt in U.GATE_TRANSITIONS.items() if U.READY in nxt]
    assert into_sent == [U.READY] and set(into_ready) == {U.VERIFYING, U.NEEDS_REVIEW}
    assert U.GATE_TRANSITIONS[U.SENT] == set() and U.GATE_TRANSITIONS[U.CANCELLED] == set()
    g = {"state": U.SOURCE_CONFIRMED}
    with pytest.raises(U.GateError):
        U.gate_move(g, U.SENT)


def _ready_msg(**over):
    g = {"state": U.READY, "source_confirmed_at": "t", "source_hash": U.source_hash("hi"), "run_id": "r1",
         "intent": {"state": U.RESOLVED}, "verification": {"status": U.VERIFIED}, "draft": {"translation": "你好"}}
    g.update(over.pop("gate", {}))
    return {"from": "patient", "original": "hi", "gate": g, **over}


@pytest.mark.parametrize("over,reason", [
    ({}, None),
    ({"gate": {"intent": {"state": U.CLARIFICATION_REQUIRED}}}, "intent"),
    ({"gate": {"intent": {"state": U.UNRESOLVED}}}, "intent"),
    ({"gate": {"verification": {"status": U.MISMATCH}}}, "verification"),
    ({"gate": {"verification": {"status": U.VERIFICATION_FAILED}}}, "verification"),
    ({"gate": {"run_id": "old"}}, "stale_run"),
    ({"original": "changed"}, "source_fixed"),
    ({"gate": {"state": U.CANCELLED}}, f"state:{U.CANCELLED}"),
    ({"gate": {"source_confirmed_at": None}}, "explicit_send"),
    ({"from": "doctor"}, "sender"),
    ({"gate": {"draft": {}}}, "translation"),
])
def test_send_checks(over, reason):
    fails = U.send_checks(_ready_msg(**over), run_id="r1", author_role="patient")
    assert fails == ([] if reason is None else [reason])


def test_speaker_override_only_lifts_verification():
    m = _ready_msg(gate={"verification": {"status": U.MISMATCH}, "override": "speaker_ack"})
    assert U.send_checks(m, run_id="r1", author_role="patient") == []
    m = _ready_msg(gate={"intent": {"state": U.CLARIFICATION_REQUIRED}, "override": "speaker_ack"})
    assert U.send_checks(m, run_id="r1", author_role="patient") == ["intent"]


# ── Conversation Auditor ──
UTTER = {"d1": {"original": "この薬は1日2回、朝と夜に1錠ずつ飲んでください。", "translation": "这个药一天两次"},
         "p1": {"original": "早上吃两片就可以了，对吗？", "translation": "朝に2錠飲めばいいということですね？"}}


def test_cross_check_dose_example():
    instr = frame(speech_act="instruction", quantities=[{"value": 1, "unit": "tablet"}], frequency=[{"count": 2, "per": "day"}],
                  times=[{"kind": "part_of_day", "value": "morning"}, {"kind": "part_of_day", "value": "evening"}],
                  action="take medicine")
    wrong = frame(speech_act="confirm_understanding", quantities=[{"value": 2, "unit": "tablet"}],
                  times=[{"kind": "part_of_day", "value": "morning"}], action="take medicine")
    right = frame(speech_act="confirm_understanding", quantities=[{"value": 1, "unit": "tablet"}],
                  frequency=[{"count": 2, "per": "day"}],
                  times=[{"kind": "part_of_day", "value": "morning"}, {"kind": "part_of_day", "value": "evening"}])
    diffs = U.cross_check(instr, wrong)
    assert "量: 説明 1錠 / 理解 2錠" in diffs and any("時間帯" in d for d in diffs)
    assert U.cross_check(instr, right) == []  # 正しく理解していれば何も出さない


def test_cross_check_negation_about_another_action_is_not_a_flip():
    # 説明「帰らずに待って」と理解「家に帰らず病院で待つ」: 否定は「帰る」に向いていて、「待つ」は両方とも肯定
    instr = frame(speech_act="instruction", modality="must", action="wait", negated=["go home"])
    right = frame(speech_act="confirm_understanding", polarity="negative", action="wait", negated=["go home"])
    assert U.cross_check(instr, right) == []
    # 説明「空腹時に飲まないで」と理解「空腹で飲む」: 同じ行為の禁止と肯定 → 逆
    no = frame(speech_act="instruction", polarity="negative", modality="must_not", action="take medicine")
    yes = frame(speech_act="confirm_understanding", polarity="affirmative", action="take medicine")
    assert U.cross_check(no, yes) == ["してよい／してはいけないが逆"]


def test_issue_requires_grounded_evidence_and_is_not_repeated():
    a = U.empty_audit()
    raw = {"new_issues": [{"type": "instruction_misunderstanding", "description_doctor": "朝に2錠と理解",
                           "related_utterance_ids": ["d1", "p1", "ghost"],
                           "evidence": [{"utterance_id": "p1", "quote": "朝に2錠"}], "severity": "high",
                           "suggested_question_doctor": ""},
                          {"type": "contradiction", "description_doctor": "作り話", "related_utterance_ids": ["d1"],
                           "evidence": [{"utterance_id": "d1", "quote": "存在しない引用"}], "severity": "low",
                           "suggested_question_doctor": ""}], "answered": []}
    a, added, answered, dropped = U.merge_audit(a, raw, latest_id="p1", utterances=UTTER, schema=SCHEMA["audit"])
    assert (added, dropped) == (1, 1)
    issue = a["issues"][0]
    assert issue["related_utterances"] == ["d1", "p1"] and issue["evidence"][0]["field"] == "translation"
    U.issue_event(issue, "resolve")
    a, added, _, dropped = U.merge_audit(a, raw, latest_id="p1", utterances=UTTER)
    assert added == 0  # 医師が解決済みにしたものを繰り返し出さない
    # 同じ仲間（理解の相違）は別の名前でも重ねない
    assert U.add_issue(a, itype="understanding_mismatch", description="x", related=["d1", "p1"],
                       evidence=[{"utterance_id": "p1", "field": "original", "quote": "早上"}], source="teachback") is None


def test_issue_transitions_doctor_only_resolve():
    for (_frm, ev), to in U.ISSUE_TRANSITIONS.items():
        if to in (U.RESOLVED_I, U.DISMISSED):
            assert U.ISSUE_EVENT_ACTOR[ev] == "doctor"
    i = {"status": U.OPEN, "history": []}
    U.issue_event(i, "question_sent")
    U.issue_event(i, "answered")
    assert i["status"] == U.ANSWERED
    with pytest.raises(clinical.TransitionError):
        U.issue_event({"status": U.DISMISSED, "history": []}, "answered")


def test_answered_requires_quote_in_latest():
    a = U.empty_audit()
    U.add_issue(a, itype="unanswered_question", description="痛みの場所が未回答", related=["d1"],
                evidence=[{"utterance_id": "d1", "field": "original", "quote": "この薬"}], source="ai")
    raw = {"new_issues": [], "answered": [{"issue_id": "u1", "quote": "作った引用", "note": ""}]}
    _, _, answered, dropped = U.merge_audit(a, raw, latest_id="p1", utterances=UTTER)
    assert (answered, dropped) == (0, 1) and a["issues"][0]["status"] == U.OPEN
    raw = {"new_issues": [], "answered": [{"issue_id": "u1", "quote": "早上吃两片", "note": ""}]}
    _, _, answered, _ = U.merge_audit(a, raw, latest_id="p1", utterances=UTTER)
    assert answered == 1 and a["issues"][0]["status"] == U.ANSWERED  # 解決済みにはしない（医師だけ）


def test_memory_keeps_instructions_and_ids():
    a = U.empty_audit()
    U.remember(a, {"utterance_id": "d1", "speaker": "doctor", "speech_act": "instruction", "text_doctor_lang": "x", "frame": {}})
    for n in range(30):
        U.remember(a, {"utterance_id": f"p{n}", "speaker": "patient", "speech_act": "statement", "text_doctor_lang": "y",
                       "frame": {}})
    ids = [m["utterance_id"] for m in a["memory"]]
    assert "d1" in ids and len(ids) == U.MEMORY_SIZE and ids[-1] == "p29"


# ── 本物の Gemini での確認で見つかった誤警報（2026-11-10）: 中国語の体温「三十八度五」を 38 と読んでいた ──
@pytest.mark.parametrize("zh", ["我发烧三十八度五。", "我发烧38度5。", "我发烧三十八点五度。", "我发烧38.5度。"])
def test_chinese_temperature_with_decimal_is_one_number(zh):
    assert U.numbers(zh) == {38.5}
    assert not [i for i in U.rule_check(zh, "zh-CN", "38.5度の熱があります。", "ja") if i["strength"] == "strong"]


def test_chinese_temperature_wrong_value_still_caught():
    assert [i for i in U.rule_check("我发烧三十八度五。", "zh-CN", "38度の熱があります。", "ja") if i["strength"] == "strong"]
    assert [i for i in U.rule_check("我发烧三十九度。", "zh-CN", "38.5度の熱があります。", "ja") if i["strength"] == "strong"]


def test_chinese_clock_with_dian_is_not_decimal():
    assert 8.5 not in U.numbers("明天早上八点半来。")
    assert 8.5 not in U.numbers("八点五十分吃药。")


def test_modality_rule_is_the_same_for_source_and_translation_frames():
    """原文の枠（A）と訳文の枠（B）で、複数の様相を持つ文の様相の決め方が同じ（禁止を優先）。違うと正しい訳を止める
    （本物の Gemini で、規則が無いと B が「may」を選び 5/15 で食い違った。eval/live_modality_check.py）。"""
    root = Path(__file__).resolve().parent.parent
    rule = [ln for ln in (root / "prompts/source_system.txt").read_text(encoding="utf-8").splitlines()
            if "most restrictive" in ln or "must_not > cannot" in ln]
    assert rule and all(ln in (root / "prompts/frame_system.txt").read_text(encoding="utf-8") for ln in rule)


# ── 本物の Gemini での比較評価で見つかった誤警報（2026-11-10）: 「帰らずに待って」を A は「待つ（必要）＋帰るを否定」、
#    B は「帰らないで（禁止）」と表した。同じ行為（go home）を両方が禁止・否定しているなら、表し方の違いとして強い証拠にしない ──
def test_same_prohibited_action_in_different_form_is_not_strong():
    a = full_frame("instruction", "affirmative", "must", "addressee", "wait", negated=["go home"])
    b = full_frame("instruction", "negative", "must_not", "addressee", "go home")
    strong = [i for i in U.compare_frames(a, b) if i["strength"] == "strong"]
    assert not [i for i in strong if i["type"] == "polarity_flip"]


def test_prohibition_lost_is_still_strong():
    src = full_frame("instruction", "negative", "must_not", "addressee", "go home")
    for b in (full_frame("instruction", "affirmative", "may", "addressee", "go home"),       # 帰ってよい
              full_frame("instruction", "affirmative", "must", "addressee", "wait")):        # 待って（帰るの禁止が消えた）
        assert [i for i in U.compare_frames(src, b) if i["type"] == "polarity_flip" and i["strength"] == "strong"], b
    # 「待って（帰るの否定つき）」→「待たないで」: 禁止が別の行為に移った
    a = full_frame("instruction", "affirmative", "must", "addressee", "wait", negated=["go home"])
    b = full_frame("instruction", "negative", "must_not", "addressee", "wait")
    assert [i for i in U.compare_frames(a, b) if i["type"] == "polarity_flip" and i["strength"] == "strong"]


def test_both_prohibit_different_actions_is_medium():
    """両方とも禁止だが、禁止する行為が違う（「帰らないで」→「待たないで」）。行為の名前は抽出のゆれが大きいので中くらい
    （LLM の直接照合 C が同じ意味と言わなければ AMBIGUOUS 以上）。"""
    a = full_frame("instruction", "negative", "must_not", "addressee", "go home")
    b = full_frame("instruction", "negative", "must_not", "addressee", "wait")
    assert [i for i in U.compare_frames(a, b) if i["type"] == "polarity_flip" and i["strength"] == "medium"]


# ── 本物の Gemini での比較評価で見つかった誤警報（2026-11-10）: 「もう一度来て」を A は量「1回」、B は量なしと取った。
#    1 は暗黙の「1つ・1回」と区別できない（ルール層も 1 と 0 は数えない）ので、片方にだけあるときは中くらい ──
def test_implicit_one_on_one_side_is_medium():
    a = full_frame("instruction", "affirmative", "must", "addressee", "come back",
                   quantities=[{"value": 1.0, "unit": "times", "of": "visit"}])
    b = full_frame("instruction", "affirmative", "must", "addressee", "come back")
    q = [i for i in U.compare_frames(a, b) if i["type"] in ("quantity_change", "addition")]
    assert q and all(i["strength"] == "medium" for i in q)
    assert all(i["strength"] == "medium" for i in U.compare_frames(b, a) if i["type"] in ("quantity_change", "addition"))


def test_one_vs_two_tablets_is_still_strong():
    a = full_frame("instruction", "affirmative", "must", "addressee", "take medicine",
                   quantities=[{"value": 1.0, "unit": "tablet", "of": "medicine"}])
    b = full_frame("instruction", "affirmative", "must", "addressee", "take medicine",
                   quantities=[{"value": 2.0, "unit": "tablet", "of": "medicine"}])
    assert [i for i in U.compare_frames(a, b) if i["type"] == "quantity_change" and i["strength"] == "strong"]
    c = full_frame("instruction", "affirmative", "must", "addressee", "take medicine")  # 2錠が消えた
    assert [i for i in U.compare_frames(b, c) if i["type"] == "quantity_change" and i["strength"] == "strong"]


# ── 本物の Gemini での繰り返しの照合で見つかった誤警報（2026-11-10）: 同じ期間を「量 3 day」「期間 P3D」「3日前（day-3）」と
#    別の形で取った。期間としてそろえてから比べる。値が違う・片方に無いときは強い証拠のまま ──
def _dur(**kw):
    return full_frame("statement", "affirmative", "none", "speaker", "cough", **kw)


Q3D = {"value": 3.0, "unit": "day", "of": "cough"}
P3D = {"kind": "duration", "value": "p3d"}
AGO3 = {"kind": "relative_day", "value": "day-3"}


@pytest.mark.parametrize("a,b", [
    (_dur(times=[P3D]), _dur(quantities=[Q3D])),
    (_dur(times=[P3D]), _dur(times=[AGO3])),
    (_dur(quantities=[Q3D]), _dur(times=[AGO3])),
    (_dur(times=[{"kind": "duration", "value": "pt8h"}]), _dur(quantities=[{"value": 8.0, "unit": "hour", "of": "fasting"}])),
])
def test_same_duration_in_different_form_is_not_strong(a, b):
    assert not [i for i in U.compare_frames(a, b) + U.compare_frames(b, a) if i["strength"] == "strong"]


@pytest.mark.parametrize("a,b", [
    (_dur(times=[P3D]), _dur(quantities=[{"value": 5.0, "unit": "day", "of": "cough"}])),   # 3日 → 5日
    (_dur(times=[AGO3]), _dur(times=[{"kind": "duration", "value": "p3w"}])),               # 3日 → 3週
    (_dur(times=[P3D]), _dur()),                                                              # 期間が消えた
    (_dur(times=[{"kind": "relative_day", "value": "day-1"}]), _dur()),                       # 昨日が消えた
])
def test_changed_or_missing_duration_is_still_strong(a, b):
    assert [i for i in U.compare_frames(a, b) if i["strength"] == "strong"]


def test_rejected_amount_rule_is_the_same_for_source_and_translation_frames():
    """否定・訂正された量（「2錠まとめてではなく」）は量に入れない、の規則が A と B で同じ。入れると、正しく言い直した患者に
    理解の相違を出す（本物の Gemini で 9/9 → 0/9。eval/live_negated_quantity_check.py）。"""
    root = Path(__file__).resolve().parent.parent
    rule = [ln for ln in (root / "prompts/source_system.txt").read_text(encoding="utf-8").splitlines()
            if "Only amounts the text" in ln or "morning and one at night" in ln]
    assert len(rule) == 2 and all(ln in (root / "prompts/frame_system.txt").read_text(encoding="utf-8") for ln in rule)


def test_now_is_not_today_rule_is_the_same_for_source_and_translation_frames():
    """「今も・现在」を日付「今日」にしない、の規則が A と B で同じ。日付にすると、正しい訳を「日時が違う」で止める
    （本物の Gemini で「今は痛みはありますか？」を 3/3 止めた。eval/live_now_check.py）。"""
    root = Path(__file__).resolve().parent.parent
    rule = [ln for ln in (root / "prompts/source_system.txt").read_text(encoding="utf-8").splitlines()
            if "is not a date" in ln or "when the text names the day" in ln]
    assert len(rule) == 2 and all(ln in (root / "prompts/frame_system.txt").read_text(encoding="utf-8") for ln in rule)
