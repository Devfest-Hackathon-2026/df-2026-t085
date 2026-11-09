"""テストとローカル開発（devserver.py）用の、決まった答えを返す Gemini の代わり。本番では使わない。

DEMO.md の3つのシナリオ（めまいの曖昧さ・理解の不一致・正しい理解）の発言には、本物の Gemini が返しそうな
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
}


def _meta(out):
    out["_meta"] = {"fake": True}
    time.sleep(DELAY_SEC)
    return out, DELAY_SEC


def translate(original, source_lang, target_lang, direction, clarification=None, context=None):
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


def compare_understanding(explanation, explanation_as_shown, restatement, patient_lang, doctor_lang="ja"):
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


def install(module=unsaid_ai):
    """unsaid_ai の Gemini 呼び出しをこのファイルの関数に差し替える（clarify / facts は従来モード用に機械的な答え）。"""
    module.translate = translate
    module.meaning = meaning
    module.compare_understanding = compare_understanding
    module.clarify = lambda u, pl, dl="ja": _meta({"needs_clarification": False, "clarifications": []})
    module.facts = lambda u, pl, dl="ja": _meta({"facts": []})
    module.warm_up = lambda: None
