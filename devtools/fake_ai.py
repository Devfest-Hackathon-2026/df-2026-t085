"""テストとローカル開発（devserver.py）用の、決まった答えを返す Gemini の代わり。本番では使わない。

DEMO.md のシナリオ（めまいの曖昧さ・理解の不一致・正しい理解・「没事」の意図・否定が落ちた訳の検出と訳し直し・
数が落ちた訳の発言者確認・服薬の理解の相違・アレルギーの矛盾・説明不足）の発言には、本物の Gemini が返しそうな
構造化出力を手で書いておく。それ以外の発言には機械的な答え（"[ja] 原文"）を返す。
ここでの結果はモデルの性能ではない（EVALUATION.md）。サーバーの検証・状態遷移・画面の流れを確かめるためのもの。
"""
import time

import unsaid_ai

DELAY_SEC = 0.0  # devserver では少し待たせて「翻訳中…」を見せる

# ── シナリオ A: めまいの曖昧さ（中国語）──
A_TEXT = "医生，我是来日本旅游的，昨天刚到。今天在车站突然觉得头晕，眼前发黑，差点晕倒。坐下来休息了一会儿好一些，但现在还是有点不舒服，我有点害怕。"
A_JA = "先生、私は日本に旅行で来ていて、昨日着いたばかりです。今日、駅で突然めまいがして、目の前が暗くなり、倒れそうになりました。座って少し休んだら少し良くなりましたが、今もまだ少し気分が悪く、ちょっと怖いです。"
A_ANSWER = "就是天旋地转的那种，周围的东西都在转。"
A_ANSWER_JA = "天井がぐるぐる回るような感じで、周りのものがみんな回っていました。"

# ── シナリオ B / C: 医師の説明と理解の確認 ──
B_EXPLAIN = "今日は検査の結果が出るまで、帰らずに待ってください。"
B_EXPLAIN_ZH = "今天请先不要回家，请在这里等到检查结果出来。"
B_WRONG = "明天再来就可以了，是吗？"
B_WRONG_JA = "明日また来ればいいということですね？"
B_REEXPLAIN = "今日は帰らないでください。検査の結果が出るまで、この病院で待ってください。"
B_REEXPLAIN_ZH = "今天请不要回家。请在这家医院等到检查结果出来。"
B_RIGHT = "今天不回家，在医院等检查结果出来。"
B_RIGHT_JA = "今日は家に帰らず、病院で検査の結果が出るまで待ちます。"
C_EXPLAIN = "この薬は1日3回、食後に飲んでください。"
C_EXPLAIN_ZH = "这个药一天吃三次，饭后吃。"
C_RIGHT = "一天三次，吃完饭以后吃。"
C_RIGHT_JA = "1日3回、ご飯を食べた後に飲みます。"

# ── 英語の例（"for a while"）──
E_TEXT = "I've had a headache for a while."

# ── D: 発話意図（「没事」）──
D_ASK = "このまま検査を続けてもいいですか？"
D_ASK_ZH = "我们可以继续检查吗？"
D_PAIN = "痛みはありますか？"
D_PAIN_ZH = "你有疼痛吗？"
D_HELP = "お手伝いしましょうか？"
D_HELP_ZH = "需要我帮你吗？"
D_FINE = "没事。"
D_DR_FINE = "大丈夫です。"
D_OPTIONS = [
    ("可以，请继续检查。", "はい、検査を続けてください（同意）。", "consent"),
    ("我身体没事，不用担心。", "体調は大丈夫です。心配いりません。", "statement"),
    ("不用了，我不想继续。", "いいえ、結構です。続けたくありません（拒否）。", "refuse"),
]
D_DR_OPTIONS = [
    ("問題ありません。心配いりません。", "没有问题，不用担心。", "statement"),
    ("結構です。必要ありません。", "不用了，不需要。", "decline"),
    ("それで構いません。了承します。", "可以，我同意。", "consent"),
]

# ── F: 否定が落ちた訳 → 検証で見つけて訳し直す ／ G: 数が落ちた訳 → 訳し直しても直らない → 発言者の確認 ──
F_TEXT = "この薬は空腹時に飲まないでください。"
F_BAD = "请空腹时吃这个药。"
F_GOOD = "请不要空腹吃这个药。"
G_TEXT = "検査の前は8時間何も食べないでください。"
G_BAD = "检查前请不要吃东西。"

# ── E: 服薬の理解の相違（Conversation Auditor）──
E_DOSE = "この薬は1日2回、朝と夜に1錠ずつ飲んでください。"
E_DOSE_ZH = "这个药一天吃两次，早上和晚上各吃一片。"
E_WRONG = "早上吃两片就可以了，对吗？"
E_WRONG_JA = "朝に2錠飲めばいいということですね？"
E_RIGHT = "一天两次，早上和晚上各吃一片，对吗？"
E_RIGHT_JA = "1日2回、朝と夜に1錠ずつですね？"
# アレルギーの矛盾・説明不足
H_NO_ALLERGY = "我没有过敏。"
H_NO_ALLERGY_JA = "アレルギーはありません。"
H_PENICILLIN = "我对青霉素过敏。"
H_PENICILLIN_JA = "ペニシリンにアレルギーがあります。"
H_VAGUE = "この薬を飲んでください。"
H_VAGUE_ZH = "请吃这个药。"

TRANSLATIONS = {
    A_TEXT: (A_JA, "医生，我是来日本旅游的，昨天刚到。今天在车站突然头晕，眼前发黑，差点晕倒。坐下休息了一会儿好了一些，但现在还有点不舒服，我有点害怕。"),
    A_ANSWER: (A_ANSWER_JA, "就是天旋地转的感觉，周围的东西都在转。"),
    B_EXPLAIN: (B_EXPLAIN_ZH, "今日はまだ家に帰らず、検査の結果が出るまでここで待ってください。"),
    B_WRONG: (B_WRONG_JA, "明天再来就可以，对吗？"),
    B_REEXPLAIN: (B_REEXPLAIN_ZH, "今日は家に帰らないでください。この病院で検査の結果が出るまで待ってください。"),
    B_RIGHT: (B_RIGHT_JA, "今天不回家，在医院等到检查结果出来。"),
    C_EXPLAIN: (C_EXPLAIN_ZH, "この薬は1日3回、食後に飲みます。"),
    C_RIGHT: (C_RIGHT_JA, "一天三次，饭后吃。"),
    E_TEXT: ("しばらく前から頭痛があります。", "I've had a headache for a while."),
    "Yes, I have a little fever too.": ("はい、少し熱もあります。", "Yes, I have a little fever too."),
    D_ASK: (D_ASK_ZH, "このまま検査を続けてもいいですか？"),
    D_PAIN: (D_PAIN_ZH, "痛みはありますか？"),
    D_HELP: (D_HELP_ZH, "お手伝いが必要ですか？"),
    G_TEXT: (G_BAD, "検査の前は何も食べないでください。"),
    E_DOSE: (E_DOSE_ZH, "この薬は1日2回、朝と夜に1錠ずつ飲みます。"),
    E_WRONG: (E_WRONG_JA, "早上吃两片就可以，对吗？"),
    E_RIGHT: (E_RIGHT_JA, "一天两次，早晚各一片，对吗？"),
    H_NO_ALLERGY: (H_NO_ALLERGY_JA, "我没有过敏。"),
    H_PENICILLIN: (H_PENICILLIN_JA, "我对青霉素过敏。"),
    H_VAGUE: (H_VAGUE_ZH, "この薬を飲んでください。"),
}


def _meta(out):
    out["_meta"] = {"fake": True}
    time.sleep(DELAY_SEC)
    return out, DELAY_SEC


def _last_doctor(turns):
    return next((t["original"] for t in reversed(turns or []) if t.get("speaker") == "doctor"), "")


def translate(original, source_lang, target_lang, direction, clarification=None, context=None,
              turns=None, confirmed_intent=None, fix=None):
    if original == F_TEXT:  # 1回目は否定を落とす。検証の指摘（fix）があれば正しく訳す
        text, back = (F_GOOD, "空腹時にこの薬を飲まないでください。") if fix else (F_BAD, "空腹時にこの薬を飲んでください。")
    elif original in (D_FINE, D_DR_FINE):
        last = _last_doctor(turns) if original == D_FINE else ""
        if confirmed_intent:
            text = confirmed_intent["meaning_listener"] or f"[{target_lang}] {confirmed_intent['meaning_speaker']}"
            back = confirmed_intent["meaning_speaker"]
        elif "痛" in last:
            text, back = "いいえ、痛みはありません。", "不疼。"
        elif "手伝" in last:
            text, back = "大丈夫です、手伝いは要りません。", "不用帮忙。"
        else:
            text, back = ("大丈夫です。", "没事。") if original == D_FINE else ("没事。", "大丈夫です。")
    else:
        text, back = TRANSLATIONS.get(original, (f"[{target_lang}] {original}", original))
    return _meta({"translation": text, "back_translation": back, "note": "",
                  "clarified_target": "", "clarified_answer": ""})


def _fact(category, meaning, quote, polarity="present", about=""):
    return {"category": category, "normalized_meaning": meaning, "source_quote": quote, "polarity": polarity,
            "about": about}


NO_RESOLUTION = {"applies": False, "resolves": "no", "meaning_doctor": "", "source_quote": ""}


def meaning(utterance, patient_lang, doctor_lang="ja", context=None, already_asked=None, pending_question=None):
    asked = set(already_asked or [])
    if utterance == A_TEXT:
        facts = [
            _fact("background", "旅行で来日、昨日到着", "我是来日本旅游的，昨天刚到"),
            _fact("symptom", "めまい", "头晕"),
            _fact("onset", "今日、駅で突然", "今天在车站突然", about="めまい"),
            _fact("associated_symptom", "眼前が暗くなった", "眼前发黑"),
            _fact("associated_symptom", "倒れそうになった（失神には至らず）", "差点晕倒"),
            _fact("course", "座って休むと少し軽快", "坐下来休息了一会儿好一些", about="めまい"),
            _fact("course", "現在も軽い不快感が続く", "现在还是有点不舒服", about="めまい"),
            _fact("psychological", "不安の訴え（本人の言葉）", "我有点害怕"),
            # 原文に無い根拠（AI の創作の例）。サーバーが捨てることを確かめる
            _fact("history", "貧血の既往", "我有贫血"),
        ]
        amb = [] if "头晕" in asked else [{
            "source_quote": "头晕",
            "meaning_doctor": "めまい",
            "reason": "「头晕」は回転性めまい・浮動感・気が遠くなる感じ（失神前）のどれも指しうる。どれかで確認すべきことが変わる。",
            "unresolved": "回転性か、浮動性か、失神前の感じか",
            "priority": "high",
            "clinically_significant": True,
            "question_patient": "您说的“头晕”，是哪一种感觉？",
            "question_doctor": "「头晕（めまい）」は、どのような感じでしたか？",
            "options_patient": ["周围的东西在转（天旋地转）", "头重脚轻，轻飘飘的", "快要晕过去，眼前发黑"],
            "options_doctor": ["周囲がぐるぐる回る（回転性）", "ふわふわする（浮動性）", "気が遠くなる（失神前）"],
        }]
        return _meta({"facts": facts, "ambiguities": amb, "resolution": NO_RESOLUTION})
    if utterance == A_ANSWER:
        res = NO_RESOLUTION
        if pending_question:
            res = {"applies": True, "resolves": "yes", "meaning_doctor": "回転性のめまい（周囲がぐるぐる回る）",
                   "source_quote": "天旋地转"}
        return _meta({"facts": [_fact("quality", "周囲が回る感じ", "周围的东西都在转", about="めまい")],
                      "ambiguities": [], "resolution": res})
    if utterance == E_TEXT:
        amb = [] if "for a while" in asked else [{
            "source_quote": "for a while", "meaning_doctor": "しばらく前から",
            "reason": "「for a while」は数日にも数か月にも取れる。急性か慢性かで確認すべきことが変わる。",
            "unresolved": "いつからか", "priority": "medium", "clinically_significant": True,
            "question_patient": "When did the headache start?",
            "question_doctor": "頭痛はいつからですか？",
            "options_patient": ["Today", "A few days ago", "About a week or more", "A month or more"],
            "options_doctor": ["今日", "数日前", "1週間ほど以上前", "1か月以上前"],
        }]
        return _meta({"facts": [_fact("symptom", "頭痛", "headache"),
                                _fact("onset", "しばらく前から", "for a while", about="頭痛")],
                      "ambiguities": amb, "resolution": NO_RESOLUTION})
    if pending_question:
        unsure = utterance.strip().lower() in ("i don't know", "我不知道", "不知道", "tôi không biết")
        res = {"applies": True, "resolves": "no" if unsure else "yes",
               "meaning_doctor": "" if unsure else f"[ja] {utterance}", "source_quote": "" if unsure else utterance}
        return _meta({"facts": [], "ambiguities": [], "resolution": res})
    q = utterance[:40]
    return _meta({"facts": [_fact("other", f"[ja] {q}", q)], "ambiguities": [], "resolution": NO_RESOLUTION})


def compare_understanding(explanation, explanation_as_shown, restatement, patient_lang, doctor_lang="ja",
                          explanation_frame=None):
    def point(dp, pu, verdict, why, quote):
        return {"doctor_point": dp, "patient_understanding": pu, "verdict": verdict, "explanation": why,
                "restatement_quote": quote}
    if restatement == B_WRONG:
        return _meta({"points": [
            point("今日、検査の結果が出るまで帰らずに待つ", "明日また来ればよい", "mismatch",
                  "説明は「今日、結果が出るまで帰らずに待つ」、患者は「明日また来る」と理解している", "明天再来就可以了"),
        ], "suggested_reexplanation": B_REEXPLAIN})
    if restatement == B_RIGHT:
        return _meta({"points": [
            point("今日は帰らない", "今日は家に帰らない", "match", "一致", "今天不回家"),
            point("検査の結果が出るまで待つ", "病院で検査結果が出るまで待つ", "match", "一致", "在医院等检查结果出来"),
        ], "suggested_reexplanation": ""})
    if restatement == C_RIGHT:
        return _meta({"points": [
            point("1日3回飲む", "1日3回", "match", "一致", "一天三次"),
            point("食後に飲む", "ご飯を食べた後", "match", "一致", "吃完饭以后吃"),
        ], "suggested_reexplanation": ""})
    return _meta({"points": [point(explanation[:40], "", "unclear", "自動では判断できません", "")],
                  "suggested_reexplanation": ""})


# ───────────── USSA: Source Agent・独立した枠の抽出・直接の照合・Conversation Auditor ─────────────
def _frame(speech_act="statement", polarity="none", modality="none", actor="unspecified", action="", times=(),
           quantities=(), frequency=(), conditions=(), entities=(), certainty="certain", negated=()):
    return {"speech_act": speech_act, "polarity": polarity, "modality": modality, "certainty": certainty,
            "actor": actor, "action": action, "object": "", "negated": list(negated), "times": list(times),
            "quantities": list(quantities), "frequency": list(frequency), "conditions": list(conditions),
            "medical_entities": list(entities), "unresolved_slots": []}


DAY0, DAY1 = {"kind": "relative_day", "value": "day+0"}, {"kind": "relative_day", "value": "day+1"}
MORNING, EVENING = {"kind": "part_of_day", "value": "morning"}, {"kind": "part_of_day", "value": "evening"}
WAIT = _frame("instruction", "affirmative", "must", "addressee", "wait", [DAY0], conditions=["until test results are out"],
              negated=["go home"])
DOSE = _frame("instruction", "affirmative", "must", "addressee", "take medicine", [MORNING, EVENING],
              [{"value": 1, "unit": "tablet", "of": "medicine"}], [{"count": 2, "per": "day", "of": "take medicine"}])
TID = _frame("instruction", "affirmative", "must", "addressee", "take medicine",
             frequency=[{"count": 3, "per": "day", "of": "take medicine"}], conditions=["after meals"])
NO_FASTING = _frame("instruction", "negative", "must_not", "addressee", "take medicine", conditions=["on an empty stomach"])
FASTING = _frame("instruction", "negative", "must_not", "addressee", "eat",
                 quantities=[{"value": 8, "unit": "hour", "of": "fasting"}], conditions=["before the test"])
# 文 → 意味の枠（原文と、その正しい訳で同じ枠。間違った訳には違う枠）
FRAMES = {
    A_TEXT: _frame(entities=[{"concept": "dizziness", "presence": "present"}], times=[DAY0]),
    A_JA: _frame(entities=[{"concept": "dizziness", "presence": "present"}], times=[DAY0]),
    B_EXPLAIN: WAIT, B_EXPLAIN_ZH: WAIT, B_REEXPLAIN: WAIT, B_REEXPLAIN_ZH: WAIT,
    B_WRONG: _frame("confirm_understanding", "affirmative", "may", "speaker", "come back", [DAY1]),
    B_WRONG_JA: _frame("confirm_understanding", "affirmative", "may", "speaker", "come back", [DAY1]),
    B_RIGHT: _frame("confirm_understanding", "negative", "none", "speaker", "wait", [DAY0], negated=["go home"]),
    B_RIGHT_JA: _frame("confirm_understanding", "negative", "none", "speaker", "wait", [DAY0], negated=["go home"]),
    C_EXPLAIN: TID, C_EXPLAIN_ZH: TID,
    C_RIGHT: _frame("confirm_understanding", "affirmative", action="take medicine",
                    frequency=[{"count": 3, "per": "day", "of": "take medicine"}], conditions=["after meals"]),
    F_TEXT: NO_FASTING, F_GOOD: NO_FASTING,
    F_BAD: _frame("instruction", "affirmative", "must", "addressee", "take medicine", conditions=["on an empty stomach"]),
    G_TEXT: FASTING,
    G_BAD: _frame("instruction", "negative", "must_not", "addressee", "eat", conditions=["before the test"]),
    E_DOSE: DOSE, E_DOSE_ZH: DOSE,
    E_WRONG: _frame("confirm_understanding", "affirmative", "may", "speaker", "take medicine", [MORNING],
                    [{"value": 2, "unit": "tablet", "of": "medicine"}]),
    E_RIGHT: _frame("confirm_understanding", "affirmative", "none", "speaker", "take medicine", [MORNING, EVENING],
                    [{"value": 1, "unit": "tablet", "of": "medicine"}], [{"count": 2, "per": "day", "of": "take medicine"}]),
    H_NO_ALLERGY: _frame(polarity="negative", entities=[{"concept": "allergy", "presence": "absent"}]),
    H_NO_ALLERGY_JA: _frame(polarity="negative", entities=[{"concept": "allergy", "presence": "absent"}]),
    H_PENICILLIN: _frame(polarity="affirmative", entities=[{"concept": "penicillin allergy", "presence": "present"}]),
    H_PENICILLIN_JA: _frame(polarity="affirmative", entities=[{"concept": "penicillin allergy", "presence": "present"}]),
    H_VAGUE: _frame("instruction", "affirmative", "must", "addressee", "take medicine"),
    H_VAGUE_ZH: _frame("instruction", "affirmative", "must", "addressee", "take medicine"),
}
FRAMES[E_WRONG_JA] = FRAMES[E_WRONG]
FRAMES[E_RIGHT_JA] = FRAMES[E_RIGHT]
FRAMES[C_RIGHT_JA] = FRAMES[C_RIGHT]


def _generic_frame(text):
    q = any(c in text for c in "?？吗") or text.rstrip().endswith("か")
    return _frame("question" if q else "statement")


def source_agent(utterance, speaker, speaker_lang, listener_lang, turns=None):
    last = _last_doctor(turns)
    base = {"speech_act": "statement", "summary_listener": "", "interpretations": [], "question_speaker": "",
            "reason_speaker": "", "frame": FRAMES.get(utterance) or _generic_frame(utterance), "evidence": []}
    if utterance == D_FINE and not ("痛" in last or "手伝" in last):
        return _meta({**base, "intent_state": "clarification_required", "speech_act": "answer",
                      "question_speaker": "您说的“没事”是什么意思？",
                      "reason_speaker": "“没事”可能是同意继续，也可能是说身体没事，或者是不用了。",
                      "interpretations": [{"meaning_speaker": a, "meaning_listener": b, "speech_act": c}
                                          for a, b, c in D_OPTIONS]})
    if utterance == D_DR_FINE and not turns:
        return _meta({**base, "intent_state": "clarification_required", "speech_act": "statement",
                      "question_speaker": "「大丈夫です」はどの意味ですか？",
                      "reason_speaker": "「大丈夫」は、問題ない・必要ない・了承する、のどれにも取れます。",
                      "interpretations": [{"meaning_speaker": a, "meaning_listener": b, "speech_act": c}
                                          for a, b, c in D_DR_OPTIONS]})
    if utterance == D_FINE:
        frame = _frame("decline", entities=[]) if "手伝" in last else _frame(
            "answer", "negative", entities=[{"concept": "pain", "presence": "absent"}])
        return _meta({**base, "intent_state": "resolved", "speech_act": frame["speech_act"], "frame": frame})
    sa = base["frame"]["speech_act"]
    ev = [{"element": "quantity", "quote": q} for q in ("1錠", "两片", "8時間") if q in utterance]
    return _meta({**base, "intent_state": "resolved", "speech_act": sa, "evidence": ev})


def extract_frame(text, lang, speaker):
    if text in ("いいえ、痛みはありません。",):
        return _meta({"frame": _frame("answer", "negative", entities=[{"concept": "pain", "presence": "absent"}]),
                      "evidence": []})
    if text == "大丈夫です、手伝いは要りません。":
        return _meta({"frame": _frame("decline"), "evidence": []})
    return _meta({"frame": FRAMES.get(text) or _generic_frame(text), "evidence": []})


def compare_meaning(source, source_lang, translation, target_lang, speaker, confirmed_intent=None, doctor_lang="ja"):
    if source == F_TEXT and translation == F_BAD:
        return _meta({"equivalent": False, "issues": [{
            "type": "polarity_flip", "severity": "critical", "source_quote": "飲まないでください", "translation_quote": "吃这个药",
            "explanation_doctor": "「飲まないで」が訳文では「飲んで」になっている",
            "explanation_speaker": "「飲まないで」が訳文では「飲んで」になっています"}]})
    if source == G_TEXT and translation == G_BAD:
        return _meta({"equivalent": False, "issues": [{
            "type": "omission", "severity": "critical", "source_quote": "8時間", "translation_quote": "",
            "explanation_doctor": "「8時間」が訳文に無い", "explanation_speaker": "「8時間」が訳文にありません"}]})
    return _meta({"equivalent": True, "issues": []})


def audit(latest, memory, open_issues, closed_issues, doctor_lang="ja"):
    new = []
    text = latest.get("text", "")
    if text == E_WRONG_JA:
        instr = next((m for m in reversed(memory) if m["speaker"] == "doctor" and "1日2回" in m["text_doctor_lang"]), None)
        if instr:
            new.append({"type": "instruction_misunderstanding", "severity": "high",
                        "description_doctor": "説明は「1日2回、朝と夜に1錠ずつ」、患者は「朝に2錠」と理解",
                        "related_utterance_ids": [instr["utterance_id"], latest["utterance_id"]],
                        "evidence": [{"utterance_id": latest["utterance_id"], "quote": "朝に2錠"}],
                        "suggested_question_doctor": "朝と夜に1錠ずつ、と伝わっていますか？"})
    if text == H_PENICILLIN_JA:
        before = next((m for m in memory if m["text_doctor_lang"] == H_NO_ALLERGY_JA), None)
        if before:
            new.append({"type": "contradiction", "severity": "high",
                        "description_doctor": "先に「アレルギーはありません」、今は「ペニシリンにアレルギーがあります」",
                        "related_utterance_ids": [before["utterance_id"], latest["utterance_id"]],
                        "evidence": [{"utterance_id": before["utterance_id"], "quote": "アレルギーはありません"},
                                     {"utterance_id": latest["utterance_id"], "quote": "ペニシリンにアレルギーがあります"}],
                        "suggested_question_doctor": "ペニシリンのアレルギーについて、もう一度教えてください。"})
    if text == H_VAGUE:
        new.append({"type": "insufficient_explanation", "severity": "medium",
                    "description_doctor": "飲む量とタイミングが説明に無い",
                    "related_utterance_ids": [latest["utterance_id"]],
                    "evidence": [{"utterance_id": latest["utterance_id"], "quote": "この薬を飲んでください"}],
                    "suggested_question_doctor": ""})
    return _meta({"new_issues": new, "answered": []})


def install(module=unsaid_ai):
    """unsaid_ai の Gemini 呼び出しをこのファイルの関数に差し替える（clarify / facts は従来モード用に機械的な答え）。"""
    module.translate = translate
    module.meaning = meaning
    module.compare_understanding = compare_understanding
    module.source_agent = source_agent
    module.extract_frame = extract_frame
    module.compare_meaning = compare_meaning
    module.audit = audit
    module.clarify = lambda u, pl, dl="ja": _meta({"needs_clarification": False, "clarifications": []})
    module.facts = lambda u, pl, dl="ja": _meta({"facts": []})
    module.warm_up = lambda: None
