"""UNSAID Semantic Safety Architecture（USSA）のうち、Gemini にも Firestore にも依存しない部分。

  Source Agent の検証    発話意図（RESOLVED / CLARIFICATION_REQUIRED / UNRESOLVED / USER_CONFIRMED）と、原文の意味の枠
  Semantic Bridge        言語に依存しない意味の枠（frame）の検証と、原文の枠と訳文の枠の比較
  Verification Engine    ルール（数値・日時・否定）・枠の比較・LLM の照合を合わせて VERIFIED / MISMATCH / AMBIGUOUS / VERIFICATION_FAILED
  Safety Gate            送信の状態遷移と、送信直前の最終チェック。**LLM ではなくこのコードが送るかどうかを決める**
  Conversation Auditor   未解決事項の状態遷移・証拠の検証・重複の除去・説明と理解の数量の照合・差分更新に使う記憶

方針（DESIGN_DECISIONS.md）:
  - LLM の自己申告の確信度や「安全です」は送信の根拠にしない。
  - 複数の LLM の一致を正しさの証明として扱わない（強い不一致は、LLM が「同じ意味」と言っても MISMATCH のまま）。
  - 原文・訳文・意味の枠・根拠・未確認事項をすべて残す。枠だけを正解として扱わない。
"""
import hashlib
import re
import unicodedata
from datetime import datetime, timezone

import clinical

# ───────────────────────── Semantic Bridge: 意味の枠 ─────────────────────────
SPEECH_ACTS = ["statement", "question", "answer", "instruction", "request", "offer", "accept", "decline", "consent",
               "refuse", "confirm_understanding", "greeting", "other"]
POLARITY = ["affirmative", "negative", "mixed", "none"]
MODALITY = ["none", "must", "must_not", "should", "should_not", "may", "can", "cannot", "want", "unknown"]
CERTAINTY = ["certain", "uncertain", "unknown"]
ACTOR = ["speaker", "addressee", "both", "third_party", "unspecified"]
TIME_KINDS = ["relative_day", "relative_week", "weekday", "clock", "duration", "date", "part_of_day", "other"]
UNITS = ["tablet", "capsule", "mg", "g", "ml", "drop", "pack", "times", "day", "week", "month", "hour", "minute",
         "celsius", "year", "other"]
FREQ_PER = ["day", "week", "month", "hour", "meal", "other"]
PRESENCE = ["present", "absent", "uncertain"]
EVIDENCE_ELEMENTS = ["negation", "quantity", "time", "frequency", "condition", "action", "entity", "modality", "other"]

# 訳の照合で必ず見る意味の変化（schemas/verify_compare.json の type と同じ）
VERIFY_TYPES = ["polarity_flip", "quantity_change", "unit_change", "time_change", "frequency_change",
                "condition_missing", "actor_change", "consent_refusal_confusion", "certainty_change",
                "symptom_presence_flip", "omission", "addition", "other"]

# 意図の状態
RESOLVED, CLARIFICATION_REQUIRED, UNRESOLVED, USER_CONFIRMED = (
    "RESOLVED", "CLARIFICATION_REQUIRED", "UNRESOLVED", "USER_CONFIRMED")
INTENT_OK = {RESOLVED, USER_CONFIRMED}  # 翻訳してよい（RESOLVED はシステムの解釈、USER_CONFIRMED は本人の確認。別物として記録する）

VERIFIED, MISMATCH, AMBIGUOUS, VERIFICATION_FAILED = "VERIFIED", "MISMATCH", "AMBIGUOUS", "VERIFICATION_FAILED"
# 医師が送った確認質問の選択肢で答えた（訳は確認質問と一緒に用意した対訳。訳の検証はしていない、と正直に記録する）
PRETRANSLATED = "PRETRANSLATED"

KANA = re.compile(r"[぀-ヿ]")
HAN = re.compile(r"[一-鿿]")
MAX_TEXT = 300


# 医師の画面に出す説明の言葉（内部のラベルや 8.0 のような表記を出さない）
UNIT_JA = {"tablet": "錠", "capsule": "カプセル", "mg": "mg", "g": "g", "ml": "mL", "drop": "滴", "pack": "包", "times": "回",
           "day": "日", "week": "週", "month": "か月", "hour": "時間", "minute": "分", "celsius": "℃", "year": "年", "other": ""}
PER_JA = {"day": "1日", "week": "1週", "month": "1か月", "hour": "1時間", "meal": "1食", "other": "決まった期間"}
PART_JA = {"am": "朝", "mid": "昼", "pm": "夕・夜"}
TIME_JA = {"day+0": "今日", "day+1": "明日", "day+2": "明後日", "day-1": "昨日", "day-2": "一昨日", "week+0": "今週",
           "week+1": "来週", "week-1": "先週", **{f"wd{i}": f"{d}曜" for i, d in enumerate("月火水木金土日", 1)},
           **{str(i): f"{d}曜" for i, d in enumerate("月火水木金土日", 1)}}
LABEL_JA = {"affirmative": "肯定", "negative": "否定", "mixed": "混在", "none": "なし", "must": "必要", "must_not": "禁止",
            "should": "推奨", "should_not": "非推奨", "may": "可", "can": "できる", "cannot": "できない", "want": "希望",
            "unknown": "不明", "consent": "同意", "refuse": "拒否", "accept": "受け入れ", "decline": "断る", "question": "質問",
            "statement": "陳述", "instruction": "指示", "answer": "回答", "request": "依頼", "offer": "申し出",
            "confirm_understanding": "理解の確認", "greeting": "挨拶", "other": "その他", "speaker": "話し手",
            "addressee": "聞き手", "both": "双方", "third_party": "第三者", "unspecified": "不明", "certain": "断定",
            "uncertain": "不確か"}


def _ja(x):
    return LABEL_JA.get(x, x)


def _amount(vals, unit):
    return "・".join(f"{_fmt(v)}{UNIT_JA.get(unit, '')}" for v in vals) or "なし"


def _when(tokens):
    return "・".join(TIME_JA.get(t, t) for t in tokens) or "なし"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def source_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _clip(s, n=MAX_TEXT):
    return (s or "").strip()[:n] if isinstance(s, str) else ""


def _enum(v, allowed, default):
    return v if v in allowed else default


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e6 else None


def build_frame(raw, text, evidence=()):
    """LLM が返した枠を検証して保存する形にする。値が分からない項目は分からないまま（none / unknown / 空）。
    evidence の引用は text（その枠の元の文）に実在するものだけ残す。"""
    raw = raw or {}
    times = [{"kind": _enum(t.get("kind"), TIME_KINDS, "other"), "value": _clip(t.get("value"), 40).lower()}
             for t in raw.get("times", []) if isinstance(t, dict) and _clip(t.get("value"), 40)]
    qty = []
    for q in raw.get("quantities", []):
        v = _num((q or {}).get("value"))
        if v is not None:
            qty.append({"value": v, "unit": _enum(q.get("unit"), UNITS, "other"), "of": _clip(q.get("of"), 60).lower()})
    freq = []
    for q in raw.get("frequency", []):
        c = _num((q or {}).get("count"))
        if c is not None:
            freq.append({"count": c, "per": _enum(q.get("per"), FREQ_PER, "other"), "of": _clip(q.get("of"), 60).lower()})
    ents = [{"concept": _clip(e.get("concept"), 60).lower(), "presence": _enum(e.get("presence"), PRESENCE, "uncertain")}
            for e in raw.get("medical_entities", []) if isinstance(e, dict) and _clip(e.get("concept"), 60)]
    ev, dropped = [], 0
    for e in evidence or []:
        g = clinical.ground((e or {}).get("quote", ""), text)
        if g:
            ev.append({"element": _enum(e.get("element"), EVIDENCE_ELEMENTS, "other"), "quote": text[g[0]:g[1]]})
        else:
            dropped += 1
    return {
        "speech_act": _enum(raw.get("speech_act"), SPEECH_ACTS, "other"),
        "polarity": _enum(raw.get("polarity"), POLARITY, "none"),
        "modality": _enum(raw.get("modality"), MODALITY, "unknown"),
        "certainty": _enum(raw.get("certainty"), CERTAINTY, "unknown"),
        "actor": _enum(raw.get("actor"), ACTOR, "unspecified"),
        "action": _clip(raw.get("action"), 80).lower(),
        "object": _clip(raw.get("object"), 80).lower(),
        "negated": [_clip(x, 60).lower() for x in raw.get("negated", []) if _clip(x, 60)][:8],
        "times": times[:8], "quantities": qty[:8], "frequency": freq[:4],
        "conditions": [_clip(x, 120).lower() for x in raw.get("conditions", []) if _clip(x, 120)][:4],
        "medical_entities": ents[:10],
        "unresolved_slots": [_clip(x, 60) for x in raw.get("unresolved_slots", []) if _clip(x, 60)][:6],
        "evidence": ev, "evidence_dropped": dropped,
    }


# ───────────────────────── Source Agent の結果 ─────────────────────────
def _in_lang(text, lang):
    """患者向けの文に日本語が混ざっていないか（ja 以外）／日本語の文が日本語か（ja）。"""
    if not text:
        return False
    if lang == "ja":
        return bool(KANA.search(text) or HAN.search(text))
    return not KANA.search(text)


def build_intent(raw, *, source_text, speaker_lang, listener_lang, schema=None):
    """Source Agent の出力（schemas/source.json）→ {state, speech_act, interpretations, question, reason, frame}。

    CLARIFICATION_REQUIRED は「医学的・意思表示として意味が分かれ、文脈でも決まらない」ときだけ。
    選択肢が作れていなければ UNRESOLVED（本人に自分の言葉で言ってもらう）。
    """
    if schema is not None:
        clinical.validate(raw, schema)
    state = {"resolved": RESOLVED, "clarification_required": CLARIFICATION_REQUIRED,
             "unresolved": UNRESOLVED}.get(raw.get("intent_state"), UNRESOLVED)
    interps = []
    for i in raw.get("interpretations", [])[:4]:
        ms, ml = _clip(i.get("meaning_speaker")), _clip(i.get("meaning_listener"))
        if ms and ml and _in_lang(ms, speaker_lang) and _in_lang(ml, listener_lang):
            interps.append({"id": f"i{len(interps) + 1}", "meaning_speaker": ms, "meaning_listener": ml,
                            "speech_act": _enum(i.get("speech_act"), SPEECH_ACTS, "other")})
    question = _clip(raw.get("question_speaker"))
    if state == CLARIFICATION_REQUIRED and (len(interps) < 2 or not _in_lang(question, speaker_lang)):
        state = UNRESOLVED  # 選択肢がそろわない: 本人の言葉で説明してもらう
    if state == RESOLVED:
        interps = []
    return {
        "state": state,
        "decided_by": "ai",  # RESOLVED はシステムの解釈。本人が選んだら USER_CONFIRMED / decided_by=speaker
        "speech_act": _enum(raw.get("speech_act"), SPEECH_ACTS, "other"),
        "summary_listener": _clip(raw.get("summary_listener")),
        "interpretations": interps,
        "question_speaker": question if state == CLARIFICATION_REQUIRED else "",
        "reason_speaker": _clip(raw.get("reason_speaker")),
        "frame": build_frame(raw.get("frame"), source_text, raw.get("evidence", [])),
        "confirmed": None,
    }


def confirm_intent(intent, choice, free_text=""):
    """発言者が意図を選んだ・説明した → USER_CONFIRMED。choice: 選択肢の id / "free_text" / "none"（言い直す）。"""
    if intent["state"] not in (CLARIFICATION_REQUIRED, UNRESOLVED):
        raise ValueError(f"意図の確認は {intent['state']} ではできません")
    if choice == "free_text":
        text = _clip(free_text, 500)
        if not text:
            raise ValueError("説明が空です")
        confirmed = {"kind": "free_text", "meaning_speaker": text, "meaning_listener": ""}
    else:
        it = next((i for i in intent["interpretations"] if i["id"] == choice), None)
        if it is None:
            raise ValueError("選んだ意味がありません")
        confirmed = {"kind": "option", **it}
    return {**intent, "state": USER_CONFIRMED, "decided_by": "speaker", "confirmed": confirmed, "confirmed_at": now_iso()}


# ───────────────────────── Verification Engine: ルールによる照合（D）─────────────────────────
_CJK = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_COUNTERS = "次回錠片粒日天週周个個月年時点點分秒度歳岁杯包袋瓶滴毫克升粒枚"
_EN_NUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
           "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
           "hundred": 100, "twice": 2, "thrice": 3, "half": 0.5}
_EN_MONTH = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                             "september", "october", "november", "december"])}
_VI_NUM = {"hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9, "mười": 10, "nửa": 0.5}
_VI_COUNTERS = r"(?:lần|viên|ngày|tuần|tháng|giờ|tiếng|phút|độ|gói|ly|cốc|giọt|ml|mg)"


def _cjk_value(s):
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (_CJK.get(a, 1) if a else 1) * 10 + (_CJK.get(b, 0) if b else 0)
    return _CJK.get(s)


def numbers(text):
    """文の中の、医学的に意味のある数（2 以上と小数）と時刻。1 と 0 は「a day」「毎日」のような暗黙の 1 と区別できないので数えない。"""
    t = unicodedata.normalize("NFKC", text or "")
    low = t.lower()
    out = set()
    # 時刻: 8:30 / 8時30分 / 8時半 / 8点半 / 8点30分
    for h, m in re.findall(r"(\d{1,2}):(\d{2})", t):
        out.add(f"{int(h)}:{m}")
    for h, m in re.findall(r"(\d{1,2})\s*[時点點]\s*(\d{1,2})\s*分", t):
        out.add(f"{int(h)}:{int(m):02d}")
    for h in re.findall(r"(\d{1,2})\s*[時点點]\s*半", t):
        out.add(f"{int(h)}:30")
    clock_spans = re.sub(r"\d{1,2}:\d{2}|\d{1,2}\s*[時点點]\s*(\d{1,2}\s*分|半)", " ", t)
    # 体温: 38度5分 → 38.5
    for a, b in re.findall(r"(\d{2})\s*度\s*(\d)\s*分", clock_spans):
        out.add(float(f"{a}.{b}"))
    clock_spans = re.sub(r"\d{2}\s*度\s*\d\s*分", " ", clock_spans)
    for n in re.findall(r"\d+(?:[.,]\d+)?", clock_spans):
        v = float(n.replace(",", "."))
        if v not in (0, 1):
            out.add(v)
    for s in re.findall(rf"([零〇一二两兩三四五六七八九十]+)(?=[{_COUNTERS}])", t):
        v = _cjk_value(s)
        if v is not None and v not in (0, 1):
            out.add(float(v))
    if re.search(rf"半(?=[{_COUNTERS}])", t):
        out.add(0.5)
    for w, v in _EN_NUM.items():
        if re.search(rf"\b{w}\b", low):
            out.add(float(v))
    for w, v in _EN_MONTH.items():
        if v > 1 and re.search(rf"\b{w}\b", low) and (w != "may" or re.search(r"\bmay\s+\d", low)):
            out.add(float(v))
    for w, v in _VI_NUM.items():
        # 「thứ hai」（月曜日）・「thứ ba」（火曜日）は数ではない
        if re.search(rf"(?<!thứ\s)(?<!\w){w}\s+{_VI_COUNTERS}", low):
            out.add(float(v))
    return {x for x in out if not isinstance(x, float) or x not in (0.0, 1.0)}


# 相対的な日・週・曜日（言語ごと）。長いものから順に照合する
_TIME_WORDS = {
    "day+2": ["明後日", "あさって", "后天", "day after tomorrow", "ngày kia", "ngày mốt"],
    "day-2": ["一昨日", "おととい", "前天", "day before yesterday", "hôm kia"],
    "day+1": ["明日", "あした", "あす", "明天", "tomorrow", "ngày mai", "sáng mai", "tối mai"],
    "day-1": ["昨日", "きのう", "昨天", "昨晚", "yesterday", "last night", "hôm qua", "tối qua"],
    "day+0": ["今日", "きょう", "本日", "今朝", "今晩", "今夜", "今天", "今早", "今晚", "today", "tonight", "this morning",
              "this evening", "hôm nay", "sáng nay", "tối nay"],
    "week+1": ["来週", "下周", "下星期", "下个星期", "下個星期", "next week", "tuần sau", "tuần tới"],
    "week-1": ["先週", "上周", "上星期", "上个星期", "last week", "tuần trước"],
    "week+0": ["今週", "这周", "这个星期", "本周", "this week", "tuần này"],
}
_WEEKDAYS = {
    1: ["月曜", "星期一", "周一", "礼拜一", "monday", "thứ hai"],
    2: ["火曜", "星期二", "周二", "礼拜二", "tuesday", "thứ ba"],
    3: ["水曜", "星期三", "周三", "礼拜三", "wednesday", "thứ tư"],
    4: ["木曜", "星期四", "周四", "礼拜四", "thursday", "thứ năm"],
    5: ["金曜", "星期五", "周五", "礼拜五", "friday", "thứ sáu"],
    6: ["土曜", "星期六", "周六", "礼拜六", "saturday", "thứ bảy"],
    7: ["日曜", "星期日", "星期天", "周日", "礼拜天", "sunday", "chủ nhật"],
}


def time_tokens(text):
    low = unicodedata.normalize("NFKC", text or "").lower()
    out = set()
    for tok, words in _TIME_WORDS.items():
        for w in sorted(words, key=len, reverse=True):
            if w in low:
                out.add(tok)
                low = low.replace(w, " ")  # 「明後日」を「明日」としても数えない
    for d, words in _WEEKDAYS.items():
        if any(w in low for w in words):
            out.add(f"wd{d}")
        # 英語の「next Monday」は来週の月曜、「this Monday」は今週の月曜
        if re.search(rf"\bnext\s+{words[4]}\b", low):
            out.add("week+1")
        if re.search(rf"\bthis\s+{words[4]}\b", low):
            out.add("week+0")
    return out


_NEG = {
    # 「〜ず」は動詞の否定（帰らず）だけ。「ずつ」「必ず」は否定ではない
    "ja": r"(?<!少)(?<!危)ない|なく|ません|[らかさたなまがばわ]ず(?!つ)|禁止|いけない|いけません|だめ|ダメ|無い",
    "zh-CN": r"不|没|沒|别|別|勿|未|无|無|禁止|甭",
    "en": r"\bnot\b|n't\b|\bno\b|\bnever\b|\bwithout\b|\bavoid\b|\bnone\b|\bnothing\b",
    "vi": r"không|chưa|đừng|chẳng|cấm|chớ",
}


def has_negation(text, lang):
    pat = _NEG.get(lang)
    return bool(pat and re.search(pat, unicodedata.normalize("NFKC", text or "").lower()))


def rule_check(source, src_lang, translation, tgt_lang):
    """D: ルールによる照合。数・時刻・相対日付・曜日は強い証拠（違えば MISMATCH）。否定の有無は弱い（枠の比較と合わせて判断）。"""
    issues = []
    ns, nt = numbers(source), numbers(translation)
    for v in sorted(ns - nt, key=str):
        issues.append({"type": "quantity_change", "strength": "strong", "layer": "rule",
                       "detail": f"原文の数「{_fmt(v)}」が訳文に無い"})
    for v in sorted(nt - ns, key=str):
        issues.append({"type": "addition", "strength": "strong", "layer": "rule",
                       "detail": f"訳文に原文に無い数「{_fmt(v)}」がある"})
    ts, tt = time_tokens(source), time_tokens(translation)
    if ts != tt:
        issues.append({"type": "time_change", "strength": "strong", "layer": "rule",
                       "detail": f"日時が違う（原文 {_when(sorted(ts))} / 訳文 {_when(sorted(tt))}）"})
    if has_negation(source, src_lang) != has_negation(translation, tgt_lang):
        issues.append({"type": "polarity_flip", "strength": "weak", "layer": "rule",
                       "detail": "否定の表現が片方にしかない（言い方の違いのこともある）"})
    return issues


def _fmt(v):
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


# ───────────────────────── Verification Engine: 枠の比較（A と B）─────────────────────────
_PART = {"morning": "am", "breakfast": "am", "noon": "mid", "afternoon": "mid", "lunch": "mid",
         "evening": "pm", "night": "pm", "bedtime": "pm", "dinner": "pm"}
_HARD_TIME_KINDS = {"relative_day", "relative_week", "weekday", "clock", "date", "duration"}


def _norm(s):
    return re.sub(r"[\s_\-]+", " ", (s or "").lower()).strip()


def compare_frames(a, b):
    """A（原文の枠）と B（訳文から独立に取った枠）を比べる。言語に依存しない項目（列挙・数・正規化した時刻）だけを比べる。
    文字列の概念（action など）は抽出のゆれが大きいので、一致するときの補助にだけ使う。"""
    issues = []

    def add(t, strength, detail):
        issues.append({"type": t, "strength": strength, "layer": "frame", "detail": detail})

    same_action = bool(a["action"]) and _norm(a["action"]) == _norm(b["action"])
    if {a["polarity"], b["polarity"]} == {"affirmative", "negative"}:
        add("polarity_flip", "strong" if same_action else "medium", f"肯定・否定が違う（原文 {_ja(a['polarity'])} / 訳文 {_ja(b['polarity'])}）")
    if (a["modality"] in ("must_not", "should_not", "cannot")) != (b["modality"] in ("must_not", "should_not", "cannot")) \
            and "unknown" not in (a["modality"], b["modality"]) and "none" not in (a["modality"], b["modality"]):
        add("polarity_flip", "strong", f"禁止・不可が片方だけ（原文 {_ja(a['modality'])} / 訳文 {_ja(b['modality'])}）")
    yes, no = {"consent", "accept"}, {"refuse", "decline"}
    if (a["speech_act"] in yes and b["speech_act"] in no) or (a["speech_act"] in no and b["speech_act"] in yes):
        add("consent_refusal_confusion", "strong", f"同意と拒否が違う（原文 {_ja(a['speech_act'])} / 訳文 {_ja(b['speech_act'])}）")
    if (a["speech_act"] == "question") != (b["speech_act"] == "question") and "other" not in (a["speech_act"], b["speech_act"]):
        add("other", "strong", f"質問かどうかが違う（原文 {_ja(a['speech_act'])} / 訳文 {_ja(b['speech_act'])}）")
    for unit in {q["unit"] for q in a["quantities"]} | {q["unit"] for q in b["quantities"]}:
        va = sorted(q["value"] for q in a["quantities"] if q["unit"] == unit)
        vb = sorted(q["value"] for q in b["quantities"] if q["unit"] == unit)
        if va and not vb:
            add("quantity_change", "strong", f"量（{_amount(va, unit)}）が訳文に無い")
        elif vb and not va:
            add("addition", "strong", f"訳文に原文に無い量（{_amount(vb, unit)}）がある")
        elif set(va) != set(vb):
            add("quantity_change", "strong", f"量が違う（原文 {_amount(va, unit)} / 訳文 {_amount(vb, unit)}）")
    for per in {f["per"] for f in a["frequency"]} | {f["per"] for f in b["frequency"]}:
        ca = sorted(f["count"] for f in a["frequency"] if f["per"] == per)
        cb = sorted(f["count"] for f in b["frequency"] if f["per"] == per)
        if set(ca) != set(cb):
            add("frequency_change", "strong", f"{PER_JA.get(per, per)}あたりの回数が違う（原文 {_amount(ca, 'times')} / 訳文 {_amount(cb, 'times')}）")
    ta = {(t["kind"], t["value"]) for t in a["times"] if t["kind"] in _HARD_TIME_KINDS}
    tb = {(t["kind"], t["value"]) for t in b["times"] if t["kind"] in _HARD_TIME_KINDS}
    if ta != tb:
        add("time_change", "strong", f"日時が違う（原文 {_when(v for _, v in sorted(ta))} / 訳文 {_when(v for _, v in sorted(tb))}）")
    pa = {_PART.get(t["value"]) for t in a["times"] if t["kind"] == "part_of_day"} - {None}
    pb = {_PART.get(t["value"]) for t in b["times"] if t["kind"] == "part_of_day"} - {None}
    if pa and pb and pa != pb:
        add("time_change", "strong", f"時間帯が違う（原文 {'・'.join(PART_JA[x] for x in sorted(pa))} / 訳文 {'・'.join(PART_JA[x] for x in sorted(pb))}）")
    elif bool(pa) != bool(pb):
        add("time_change", "medium", "時間帯が片方にしかない")
    if a["conditions"] and not b["conditions"]:
        add("condition_missing", "strong", "条件（〜まで・〜なら など）が訳文に無い")
    if {a["actor"], b["actor"]} == {"speaker", "addressee"}:
        add("actor_change", "medium", f"誰がするかが違う（原文 {_ja(a['actor'])} / 訳文 {_ja(b['actor'])}）")
    if {a["certainty"], b["certainty"]} == {"certain", "uncertain"}:
        add("certainty_change", "medium", f"確からしさが違う（原文 {_ja(a['certainty'])} / 訳文 {_ja(b['certainty'])}）")
    eb = {e["concept"]: e["presence"] for e in b["medical_entities"]}
    for e in a["medical_entities"]:
        p = eb.get(e["concept"])
        if p and {p, e["presence"]} == {"present", "absent"}:
            add("symptom_presence_flip", "strong", f"「{e['concept']}」の有無が違う")
    return issues


def build_llm_check(raw, *, source, translation, schema=None):
    """C: 原文と訳文を直接照合した LLM の結果。引用が原文・訳文に実在する指摘だけを強く扱う。"""
    if schema is not None:
        clinical.validate(raw, schema)
    out = []
    for i in raw.get("issues", [])[:8]:
        gs = clinical.ground(i.get("source_quote", ""), source)
        gt = clinical.ground(i.get("translation_quote", ""), translation)
        grounded = bool(gs or gt)
        critical = i.get("severity") == "critical"
        out.append({
            "type": _enum(i.get("type"), VERIFY_TYPES, "other"),
            "strength": "strong" if critical and grounded else "medium",
            "layer": "llm",
            "source_quote": source[gs[0]:gs[1]] if gs else "",
            "translation_quote": translation[gt[0]:gt[1]] if gt else "",
            "detail": _clip(i.get("explanation_doctor")),
            "detail_speaker": _clip(i.get("explanation_speaker")),
        })
    return {"equivalent": bool(raw.get("equivalent")), "issues": out}


def combine(rule_issues, frame_issues, llm, failures=()):
    """D・A/B・C の結果をまとめて状態を決める（コードで決める。LLM の「同じ意味」で強い不一致は消えない）。

      いずれかの検証が失敗した                → VERIFICATION_FAILED
      強い不一致がある                        → MISMATCH
      中くらいの不一致があり、C も同じ意味と言わない → AMBIGUOUS
      それ以外                                → VERIFIED（「検出した相違なし」であって正しさの証明ではない）
    否定の有無（ルールの弱い証拠）は、枠の比較で肯定・否定が一致していれば捨てる。
    """
    frame_polarity_known = not any(i["type"] == "polarity_flip" for i in frame_issues)
    rules = [i for i in rule_issues if not (i["strength"] == "weak" and frame_polarity_known)]
    rules = [{**i, "strength": "medium"} if i["strength"] == "weak" else i for i in rules]
    issues = rules + frame_issues + (llm["issues"] if llm else [])
    if failures:
        status = VERIFICATION_FAILED
    elif any(i["strength"] == "strong" for i in issues):
        status = MISMATCH
    elif any(i["strength"] == "medium" for i in issues) and not (llm and llm["equivalent"]):
        status = AMBIGUOUS
    else:
        status = VERIFIED
    return {"status": status, "issues": issues, "failures": list(failures), "at": now_iso()}


# ───────────────────────── Safety Gate ─────────────────────────
SOURCE_CONFIRMED, ANALYZING, AWAITING_INTENT, VERIFYING, NEEDS_REVIEW, READY, SENT, CANCELLED, FAILED = (
    "SOURCE_CONFIRMED", "ANALYZING", "AWAITING_INTENT_CLARIFICATION", "VERIFYING", "NEEDS_REVIEW", "READY_TO_SEND",
    "SENT", "CANCELLED", "FAILED")
# 端末の「下書き」（DRAFT）は送信操作の前なので Firestore に無い。送信操作で発言ができた時点が SOURCE_CONFIRMED
GATE_TRANSITIONS = {
    SOURCE_CONFIRMED: {ANALYZING, CANCELLED},
    ANALYZING: {AWAITING_INTENT, VERIFYING, FAILED, CANCELLED},
    AWAITING_INTENT: {ANALYZING, CANCELLED},
    VERIFYING: {READY, NEEDS_REVIEW, FAILED, CANCELLED},
    NEEDS_REVIEW: {READY, CANCELLED},
    READY: {SENT},
    FAILED: {ANALYZING, CANCELLED},
    SENT: set(),
    CANCELLED: set(),
}
# 画面とルールが見る粗い状態（messages.status）
STATUS_OF = {SOURCE_CONFIRMED: "analyzing", ANALYZING: "analyzing", VERIFYING: "analyzing", READY: "analyzing",
             AWAITING_INTENT: "awaiting_intent", NEEDS_REVIEW: "needs_review", SENT: "sent", CANCELLED: "cancelled",
             FAILED: "error"}


class GateError(ValueError):
    pass


def gate_move(gate, to, *, by="system", reason=None, at=None):
    frm = gate.get("state", SOURCE_CONFIRMED)
    if to not in GATE_TRANSITIONS.get(frm, set()):
        raise GateError(f"{frm} から {to} には進めません")
    h = {"at": at or now_iso(), "from": frm, "to": to, "by": by}
    if reason:
        h["reason"] = reason
    gate["state"] = to
    gate.setdefault("history", []).append(h)
    return gate


def send_checks(msg, *, run_id, author_role):
    """送信直前の最終チェック（Safety Gate）。通らなかった項目のリストを返す（空なら送ってよい）。
    msg はトランザクションの中で読み直した最新の発言。LLM の判断は「verification.status」という記録としてしか見ない。"""
    g = msg.get("gate") or {}
    fails = []
    if msg.get("from") != author_role:
        fails.append("sender")                       # 送る人がその発言の発言者
    if not g.get("source_confirmed_at"):
        fails.append("explicit_send")                # 発言者が送信操作をした（発言を作った）
    if g.get("source_hash") != source_hash(msg.get("original")):
        fails.append("source_fixed")                 # 原文が解析した時のまま
    if g.get("run_id") != run_id:
        fails.append("stale_run")                    # 古い解析の結果ではない
    if g.get("state") != READY:
        fails.append(f"state:{g.get('state')}")      # 取り消し・送信済み・確認待ちではない
    if (g.get("intent") or {}).get("state") not in INTENT_OK:
        fails.append("intent")                       # 意図が決まっている（AI の解釈か、本人の確認）
    v = (g.get("verification") or {}).get("status")
    if v not in (VERIFIED, AMBIGUOUS, PRETRANSLATED) and g.get("override") != "speaker_ack":
        fails.append("verification")                 # 検証で重要な不一致が無い（または発言者が「未検証として送る」を選んだ）
    if not ((g.get("draft") or {}).get("translation")):
        fails.append("translation")                  # 送る訳がある
    return fails


# ───────────────────────── Conversation Auditor ─────────────────────────
OPEN, CLAR_REQ, ANSWERED, RESOLVED_I, DISMISSED = "OPEN", "CLARIFICATION_REQUESTED", "ANSWERED", "RESOLVED", "DISMISSED"
ISSUE_TYPES = ["contradiction", "understanding_mismatch", "unanswered_question", "unresolved_clarification",
               "quantity_time_mismatch", "insufficient_explanation", "inconsistent_with_past", "meaning_drift",
               "instruction_misunderstanding"]
ISSUE_EVENT_ACTOR = {"question_sent": "doctor", "answered": "system", "resolve": "doctor", "dismiss": "doctor",
                     "reopen": "doctor"}
ISSUE_TRANSITIONS = {
    (OPEN, "question_sent"): CLAR_REQ, (ANSWERED, "question_sent"): CLAR_REQ, (CLAR_REQ, "question_sent"): CLAR_REQ,
    (OPEN, "answered"): ANSWERED, (CLAR_REQ, "answered"): ANSWERED, (ANSWERED, "answered"): ANSWERED,
    **{(s, "resolve"): RESOLVED_I for s in (OPEN, CLAR_REQ, ANSWERED)},
    **{(s, "dismiss"): DISMISSED for s in (OPEN, CLAR_REQ, ANSWERED)},
    (RESOLVED_I, "reopen"): OPEN, (DISMISSED, "reopen"): OPEN,
}
ACTIVE_ISSUE = {OPEN, CLAR_REQ, ANSWERED}
MAX_ISSUES = 40
MEMORY_SIZE = 20


def empty_audit():
    return {"issues": [], "memory": [], "seq": 0, "updated_at": now_iso()}


def issue_event(issue, event, *, at=None, ref=None, note=None):
    nxt = ISSUE_TRANSITIONS.get((issue["status"], event))
    if nxt is None:
        raise clinical.TransitionError(f"未解決事項が {issue['status']} なので {event} はできません")
    h = {"at": at or now_iso(), "by": ISSUE_EVENT_ACTOR[event], "event": event, "from": issue["status"], "to": nxt}
    if ref:
        h["ref"] = ref
    if note:
        h["note"] = _clip(note, 200)
    issue["status"] = nxt
    issue["last_updated"] = h["at"]
    issue.setdefault("history", []).append(h)
    return issue


# 同じ食い違いを別の名前で2回出さないための仲間分け（teach-back の判定・ルールの照合・LLM がそれぞれ見つけることがある）
ISSUE_FAMILY = {"understanding_mismatch": "understanding", "instruction_misunderstanding": "understanding",
                "quantity_time_mismatch": "understanding", "contradiction": "contradiction",
                "inconsistent_with_past": "contradiction"}


def _seen(audit, itype, related):
    """同じ仲間・同じ発言についての事項がもうある（解決済み・無視も含む）か。医師が片付けたものを繰り返し出さない。"""
    rel = set(related)
    fam = ISSUE_FAMILY.get(itype, itype)
    return next((i for i in audit["issues"] if ISSUE_FAMILY.get(i["type"], i["type"]) == fam
                 and (rel <= set(i["related_utterances"]) or set(i["related_utterances"]) <= rel)), None)


def add_issue(audit, *, itype, description, related, evidence, source, severity="medium", suggested_question="",
              at=None):
    """未解決事項を足す。証拠（発言 id と、その発言に実在する引用）が無いもの・すでにあるものは足さない。"""
    if itype not in ISSUE_TYPES or not related or not evidence:
        return None
    if _seen(audit, itype, related):
        return None
    if sum(i["status"] in ACTIVE_ISSUE for i in audit["issues"]) >= MAX_ISSUES:
        return None
    at = at or now_iso()
    audit["seq"] += 1
    issue = {"issue_id": f"u{audit['seq']}", "type": itype, "description": _clip(description),
             "related_utterances": list(dict.fromkeys(related)), "evidence": evidence, "status": OPEN, "held": False,
             "severity": severity if severity in ("high", "medium", "low") else "medium",
             "suggested_question": _clip(suggested_question), "source": source, "resolution": "",
             "last_updated": at, "history": [{"at": at, "by": source, "event": "opened", "to": OPEN}]}
    audit["issues"].append(issue)
    return issue


def ground_evidence(evidence, utterances):
    """証拠の引用が、指した発言の原文か訳文に実在するものだけを残す。utterances: id → {original, translation}"""
    out = []
    for e in evidence or []:
        u = utterances.get((e or {}).get("utterance_id"))
        if not u:
            continue
        for field in ("original", "translation"):
            g = clinical.ground(e.get("quote", ""), u.get(field) or "")
            if g:
                out.append({"utterance_id": e["utterance_id"], "field": field, "quote": u[field][g[0]:g[1]]})
                break
    return out


def merge_audit(audit, raw, *, latest_id, utterances, schema=None, at=None):
    """Auditor の LLM 出力を検証して、未解決事項に差分で反映する。返り値: (audit, 追加した数, 回答済みにした数, 捨てた数)。

      - 指す発言がこの問診に無い・引用が実在しない → 捨てる
      - 同じ種類・同じ発言の事項がある（解決済み・無視を含む）→ 足さない
      - 回答済み（ANSWERED）にできるのは、答えになった発言と実在する引用があるときだけ。解決済みにはしない（医師だけ）
    """
    if schema is not None:
        clinical.validate(raw, schema)
    at = at or now_iso()
    added = answered = dropped = 0
    for n in raw.get("new_issues", [])[:5]:
        related = [r for r in n.get("related_utterance_ids", []) if r in utterances]
        if latest_id not in related:
            related.append(latest_id)
        ev = ground_evidence(n.get("evidence", []), utterances)
        if not ev or not add_issue(audit, itype=n.get("type"), description=n.get("description_doctor"),
                                   related=related, evidence=ev, source="ai", severity=n.get("severity"),
                                   suggested_question=n.get("suggested_question_doctor"), at=at):
            dropped += 1
        else:
            added += 1
    for u in raw.get("answered", [])[:5]:
        issue = next((i for i in audit["issues"] if i["issue_id"] == u.get("issue_id")), None)
        ev = ground_evidence([{"utterance_id": latest_id, "quote": u.get("quote", "")}], utterances)
        if not issue or issue["status"] not in (OPEN, CLAR_REQ) or not ev:
            dropped += 1
            continue
        issue_event(issue, "answered", at=at, ref=latest_id, note=u.get("note"))
        issue["evidence"].extend(ev)
        if latest_id not in issue["related_utterances"]:
            issue["related_utterances"].append(latest_id)
        answered += 1
    audit["updated_at"] = at
    return audit, added, answered, dropped


def cross_check(instruction, restatement):
    """説明（医師の指示の枠）と、患者の言い直し（理解の確認）の枠の数量・回数・時間帯を照合する（11.2 の例）。
    食い違いの説明のリストを返す（空なら食い違いなし）。どちらかに無い項目は「触れていない」として数えない。"""
    out = []
    for unit in {q["unit"] for q in instruction["quantities"]} & {q["unit"] for q in restatement["quantities"]}:
        a = {q["value"] for q in instruction["quantities"] if q["unit"] == unit}
        b = {q["value"] for q in restatement["quantities"] if q["unit"] == unit}
        if a != b:
            out.append(f"量: 説明 {_amount(sorted(a), unit)} / 理解 {_amount(sorted(b), unit)}")
    for per in {f["per"] for f in instruction["frequency"]} & {f["per"] for f in restatement["frequency"]}:
        a = {f["count"] for f in instruction["frequency"] if f["per"] == per}
        b = {f["count"] for f in restatement["frequency"] if f["per"] == per}
        if a != b:
            out.append(f"回数（{PER_JA.get(per, per)}）: 説明 {_amount(sorted(a), 'times')} / 理解 {_amount(sorted(b), 'times')}")
    pa = {_PART.get(t["value"]) for t in instruction["times"] if t["kind"] == "part_of_day"} - {None}
    pb = {_PART.get(t["value"]) for t in restatement["times"] if t["kind"] == "part_of_day"} - {None}
    if pa and pb and not pb <= pa:
        out.append(f"時間帯: 説明 {'・'.join(PART_JA[x] for x in sorted(pa))} / 理解 {'・'.join(PART_JA[x] for x in sorted(pb))}")
    if pa and pb and pa != pb and len(pa) > len(pb):
        out.append(f"時間帯: 説明は {'・'.join(PART_JA[x] for x in sorted(pa))}、理解は {'・'.join(PART_JA[x] for x in sorted(pb))} だけ")
    ta = {t["value"] for t in instruction["times"] if t["kind"] in ("relative_day", "relative_week", "weekday")}
    tb = {t["value"] for t in restatement["times"] if t["kind"] in ("relative_day", "relative_week", "weekday")}
    if ta and tb and ta != tb:
        out.append(f"日: 説明 {_when(sorted(ta))} / 理解 {_when(sorted(tb))}")
    # 「してよい／してはいけない」の逆: 同じ行為そのものが、片方では禁止・否定され、もう片方ではされていない。
    # 文の否定が別の行為に向いている（「帰らずに待つ」の否定は「帰る」）ときは数えない
    def negates_action(f):
        act = _norm(f["action"])
        return f["modality"] in ("must_not", "should_not") or act in {_norm(x) for x in f.get("negated", [])}
    if instruction["action"] and _norm(instruction["action"]) == _norm(restatement["action"]) \
            and negates_action(instruction) != negates_action(restatement):
        out.append("してよい／してはいけないが逆")
    return list(dict.fromkeys(out))


def memory_item(mid, msg, frame):
    """差分更新のための記憶（会話全体ではなく、重要な発言の要点と枠）。根拠の発言 id を必ず持つ。"""
    text = msg.get("original", "") if msg.get("from") == "doctor" else ((msg.get("translation") or {}).get("text") or "")
    return {"utterance_id": mid, "speaker": msg.get("from"), "speech_act": frame.get("speech_act", "other"),
            "text_doctor_lang": _clip(text, 200),
            "frame": {k: frame.get(k) for k in ("quantities", "frequency", "times", "conditions", "modality", "action",
                                                "polarity", "speech_act")}}


def remember(audit, item):
    """記憶に足す。指示（instruction / request）は残し、それ以外は新しい順に MEMORY_SIZE まで。"""
    mem = [m for m in audit["memory"] if m["utterance_id"] != item["utterance_id"]] + [item]
    keep = {"instruction", "request"}
    instr = [m for m in mem if m["speech_act"] in keep][-10:]
    other = [m for m in mem if m["speech_act"] not in keep][-(MEMORY_SIZE - len(instr)):]
    order = {m["utterance_id"]: i for i, m in enumerate(mem)}
    audit["memory"] = sorted(instr + other, key=lambda m: order[m["utterance_id"]])
    return audit
