"""UNSAID の「意味」と「確認」の状態（Meaning Engine の検証・確認候補・状態遷移・理解確認）。

Gemini も Firestore も使わない純粋な関数だけを置く（main.py が呼ぶ。テストは test_clinical.py）。

守っていること:
  - 患者が言ったこと（原文）・AI の翻訳・AI の解釈・患者の追加の回答・医師の確認を混ぜない。
  - AI が出した根拠（source_quote）は、原文に実在する文字列のときだけ採用する。無ければ捨てて記録だけ残す。
  - 状態は明示的なイベントでしか変わらない。AI や患者のイベントで「医師確認済み」にはならない（TRANSITIONS）。
"""
import re
import unicodedata
from datetime import datetime, timezone

import jsonschema

# ── 情報の種類（schemas/meaning.json の category と同じ）──
CATEGORIES = [
    "chief_complaint",    # 主訴
    "symptom",            # 症状
    "associated_symptom", # 関連症状
    "onset",              # 発症時期
    "duration",           # 持続時間
    "frequency",          # 頻度
    "course",             # 経過
    "quality",            # 症状の性質
    "location",           # 部位
    "severity",           # 程度
    "modifying_factor",   # 増悪・軽快要因
    "history",            # 既往歴
    "medication",         # 服薬
    "allergy",            # アレルギー
    "background",         # 背景情報（旅行・仕事など本人が言ったこと）
    "psychological",      # 心理状態（本人がはっきり言った気持ちだけ。推定はしない）
    "other",
]
ATTRIBUTE_CATEGORIES = {"onset", "duration", "frequency", "course", "quality", "location", "severity", "modifying_factor"}
SYMPTOM_CATEGORIES = {"chief_complaint", "symptom", "associated_symptom"}
POLARITIES = ["present", "denied", "uncertain"]  # 言った / 本人が否定した / 本人も不確か（「言及なし」は fact が無いこと）

# 旧い facts（web の「自分のメモ」・eval/run.py が読む形）に入れる kind
LEGACY_KIND = {
    "chief_complaint": "symptom", "symptom": "symptom", "associated_symptom": "symptom",
    "onset": "onset", "duration": "onset", "frequency": "frequency", "severity": "severity",
    "medication": "medication", "allergy": "allergy",
}

# ── 情報（fact）の状態 ──
AI_EXTRACTED = "ai_extracted"          # AI抽出（原文の根拠あり・医師は未確認）
UNCONFIRMED = "unconfirmed"            # 未確認（意味が確定していない部分を含む）
PATIENT_ANSWERED = "patient_answered"  # 患者回答あり（確認質問に患者が答えた）
DOCTOR_CONFIRMED = "doctor_confirmed"  # 医師確認済み（医師が押したときだけ）
NEEDS_CORRECTION = "needs_correction"  # 要修正（医師が押したときだけ）
FACT_STATUSES = [AI_EXTRACTED, UNCONFIRMED, PATIENT_ANSWERED, DOCTOR_CONFIRMED, NEEDS_CORRECTION]

# イベント → 誰のイベントか
EVENT_ACTOR = {
    "extracted": "ai",
    "ambiguity_found": "ai",
    "question_sent": "doctor",
    "patient_answered": "patient",
    "patient_unsure": "patient",
    "doctor_confirm": "doctor",
    "doctor_flag": "doctor",
    "doctor_reset": "doctor",
    "candidate_dismissed": "doctor",
}

# (今の状態, イベント) → 次の状態。表に無い組み合わせは TransitionError。
# AI と患者のイベントの行き先に DOCTOR_CONFIRMED / NEEDS_CORRECTION が無いことは test_clinical.py で確かめる
TRANSITIONS = {
    (AI_EXTRACTED, "ambiguity_found"): UNCONFIRMED,
    (AI_EXTRACTED, "question_sent"): AI_EXTRACTED,   # 医師が自分で質問した（曖昧さの指摘なし）
    (UNCONFIRMED, "question_sent"): UNCONFIRMED,
    (PATIENT_ANSWERED, "question_sent"): PATIENT_ANSWERED,
    (NEEDS_CORRECTION, "question_sent"): NEEDS_CORRECTION,
    (DOCTOR_CONFIRMED, "question_sent"): DOCTOR_CONFIRMED,
    (AI_EXTRACTED, "patient_answered"): PATIENT_ANSWERED,
    (UNCONFIRMED, "patient_answered"): PATIENT_ANSWERED,
    (PATIENT_ANSWERED, "patient_answered"): PATIENT_ANSWERED,  # 患者が回答を直した
    (NEEDS_CORRECTION, "patient_answered"): PATIENT_ANSWERED,
    (DOCTOR_CONFIRMED, "patient_answered"): PATIENT_ANSWERED,  # 確認後に答えが変わった → 医師はもう一度確認する
    (AI_EXTRACTED, "patient_unsure"): UNCONFIRMED,
    (UNCONFIRMED, "patient_unsure"): UNCONFIRMED,
    (PATIENT_ANSWERED, "patient_unsure"): UNCONFIRMED,
    (NEEDS_CORRECTION, "patient_unsure"): NEEDS_CORRECTION,
    (DOCTOR_CONFIRMED, "patient_unsure"): UNCONFIRMED,
    (UNCONFIRMED, "candidate_dismissed"): UNCONFIRMED,  # 医師が確認を省いた。確定したわけではない
    **{(s, "doctor_confirm"): DOCTOR_CONFIRMED for s in (AI_EXTRACTED, UNCONFIRMED, PATIENT_ANSWERED, NEEDS_CORRECTION)},
    **{(s, "doctor_flag"): NEEDS_CORRECTION for s in (AI_EXTRACTED, UNCONFIRMED, PATIENT_ANSWERED, DOCTOR_CONFIRMED)},
}

# ── 確認候補（candidate）の状態 ──
C_PROPOSED, C_HELD, C_DISMISSED, C_ASKED, C_ANSWERED, C_UNRESOLVED = (
    "proposed", "held", "dismissed", "asked", "answered", "unresolved")
CANDIDATE_TRANSITIONS = {
    (C_PROPOSED, "hold"): C_HELD,
    (C_PROPOSED, "dismiss"): C_DISMISSED,
    (C_HELD, "dismiss"): C_DISMISSED,
    (C_HELD, "restore"): C_PROPOSED,
    (C_DISMISSED, "restore"): C_PROPOSED,
    (C_UNRESOLVED, "dismiss"): C_DISMISSED,
    (C_PROPOSED, "question_sent"): C_ASKED,
    (C_HELD, "question_sent"): C_ASKED,
    (C_UNRESOLVED, "question_sent"): C_ASKED,  # 聞き直す
    (C_ANSWERED, "question_sent"): C_ASKED,
    (C_ASKED, "answer_resolved"): C_ANSWERED,
    (C_ASKED, "answer_unresolved"): C_UNRESOLVED,
    (C_ANSWERED, "answer_resolved"): C_ANSWERED,     # 回答の修正
    (C_ANSWERED, "answer_unresolved"): C_UNRESOLVED,
    (C_UNRESOLVED, "answer_resolved"): C_ANSWERED,   # 「どれでもない」の後に自分の言葉で答えた
    (C_UNRESOLVED, "answer_unresolved"): C_UNRESOLVED,
}
OPEN_CANDIDATE = {C_PROPOSED, C_HELD, C_ASKED, C_UNRESOLVED}

# ── 理解の確認（teach-back）の状態 ──
T_ASKED, T_ANSWERED, T_ASSESSED, T_CONFIRMED, T_REEXPLAIN = (
    "asked", "answered", "assessed", "doctor_confirmed", "needs_reexplain")
TEACHBACK_TRANSITIONS = {
    (T_ASKED, "answer_sent"): T_ANSWERED,
    (T_ASKED, "assessed"): T_ASSESSED,
    (T_ANSWERED, "assessed"): T_ASSESSED,
    (T_ASSESSED, "assessed"): T_ASSESSED,  # 患者が答え直した
    (T_REEXPLAIN, "answer_sent"): T_ANSWERED,
    (T_ASSESSED, "answer_sent"): T_ANSWERED,   # 患者が言い直した（新しい答えの判定を待つ）
    (T_CONFIRMED, "answer_sent"): T_ANSWERED,
    (T_REEXPLAIN, "assessed"): T_ASSESSED,
    (T_CONFIRMED, "assessed"): T_ASSESSED,
    **{(s, "doctor_confirm"): T_CONFIRMED for s in (T_ASKED, T_ANSWERED, T_ASSESSED, T_REEXPLAIN)},
    **{(s, "doctor_reexplain"): T_REEXPLAIN for s in (T_ASKED, T_ANSWERED, T_ASSESSED, T_CONFIRMED)},
}

MAX_CANDIDATES = 6       # 1発言で残す確認候補の上限（画面は重要な3件だけを先に出す）
MAX_TEXT = 300           # AI が返す1項目の文字数の上限（長すぎるものは切る）
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
KANA = re.compile(r"[぀-ヿ]")


class TransitionError(ValueError):
    pass


class InvalidAIOutput(ValueError):
    """AI の出力がスキーマに合わない。患者の文章を例外メッセージやログに出さないため、場所（path）だけを持つ。"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate(out, schema):
    """JSON Schema で確かめる（Gemini 側でもスキーマを指定しているが、サーバーでも念のため確かめる）。"""
    try:
        jsonschema.validate(out, schema)
    except jsonschema.ValidationError as e:
        raise InvalidAIOutput("/".join(str(p) for p in e.absolute_path) or "(root)") from None


def _clip(s):
    s = (s or "").strip()
    return s[:MAX_TEXT]


# ── 原文との突き合わせ ──
_EDGE_PUNCT = " \t\r\n\"'“”‘’「」『』（）()[]、。，．,.!！?？…:：;；"


def _norm_with_map(text):
    """全角・半角と大文字小文字をそろえ、空白を落とした文字列と、各文字の元の位置を返す。"""
    chars, where = [], []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).casefold():
            if c.isspace():
                continue
            chars.append(c)
            where.append(i)
    return "".join(chars), where


def ground(quote, source):
    """quote が source に実在するか。あれば (start, end, evidence)、無ければ None。

    evidence: "exact"（そのまま見つかった）/ "normalized"（全角半角・大文字小文字・空白・前後の句読点の違いだけ）。
    言い換え・翻訳・要約は採用しない（あいまい一致で寄せると、AI が作った根拠を原文と言い張れてしまう）。
    """
    if not quote or not source:
        return None
    i = source.find(quote)
    if i >= 0:
        return i, i + len(quote), "exact"
    q = quote.strip(_EDGE_PUNCT)
    if not q:
        return None
    i = source.find(q)
    if i >= 0:
        return i, i + len(q), "normalized"
    ns, where = _norm_with_map(source)
    nq, _ = _norm_with_map(q)
    if not nq:
        return None
    j = ns.find(nq)
    if j < 0:
        return None
    return where[j], where[j + len(nq) - 1] + 1, "normalized"


def _key(s):
    return re.sub(r"[\s。、．，,.!！?？（）()「」]", "", unicodedata.normalize("NFKC", s or "").casefold())


# ── Meaning Engine の出力を検証して、保存する形にする ──
def build_meaning(raw, *, utterance_id, source_text, source_lang, known_quotes=(), at=None, schema=None):
    """Gemini の meaning 出力（schemas/meaning.json）→ Firestore に書く meaning。

    known_quotes: この問診ですでに確認候補になった原文の語（同じことを2回聞かない）。
    返り値: {facts, candidates, resolution, rejected, at}
    """
    if schema is not None:
        validate(raw, schema)
    at = at or now_iso()
    rejected = []
    facts, seen = [], set()
    for f in raw.get("facts", []):
        meaning = _clip(f.get("normalized_meaning"))
        g = ground(f.get("source_quote", ""), source_text)
        if not meaning:
            rejected.append({"type": "fact", "reason": "empty_meaning"})
            continue
        if g is None:
            # AI が原文に無い根拠を書いた（言い換え・創作）。採用しない
            rejected.append({"type": "fact", "reason": "quote_not_in_source",
                             "category": f.get("category", ""), "normalized_meaning": meaning})
            continue
        cat = f.get("category") if f.get("category") in CATEGORIES else "other"
        k = (cat, _key(meaning))
        if k in seen:
            rejected.append({"type": "fact", "reason": "duplicate", "normalized_meaning": meaning})
            continue
        seen.add(k)
        pol = f.get("polarity") if f.get("polarity") in POLARITIES else "present"
        s, e, ev = g
        facts.append({
            "fact_id": f"f{len(facts) + 1}",
            "utterance_id": utterance_id,
            "category": cat,
            "normalized_meaning": meaning,
            "source_quote": source_text[s:e],
            "span_start": s,
            "span_end": e,
            "source_language": source_lang,
            "evidence_status": ev,
            "polarity": pol,
            "about": _clip(f.get("about")),
            "status": AI_EXTRACTED,
            "clarification_status": "none",
            "resolved_meaning": "",
            "timestamp": at,
            "history": [{"at": at, "by": "ai", "event": "extracted"}],
        })
    # 属性（いつから・程度など）→ どの症状の話か
    symptoms = [f for f in facts if f["category"] in SYMPTOM_CATEGORIES]
    for f in facts:
        about = f.pop("about")
        if f["category"] not in ATTRIBUTE_CATEGORIES:
            continue
        hit = next((s for s in symptoms if about and _key(s["normalized_meaning"]) == _key(about)), None)
        hit = hit or next((s for s in symptoms if about and (_key(about) in _key(s["normalized_meaning"])
                                                           or _key(s["normalized_meaning"]) in _key(about))), None)
        if hit:
            f["about_fact_id"] = hit["fact_id"]

    known = {_key(q) for q in known_quotes if q}
    candidates = []
    for a in raw.get("ambiguities", []):
        if not a.get("clinically_significant", False):
            rejected.append({"type": "ambiguity", "reason": "not_significant"})
            continue
        g = ground(a.get("source_quote", ""), source_text)
        if g is None:
            rejected.append({"type": "ambiguity", "reason": "quote_not_in_source"})
            continue
        s, e, ev = g
        quote = source_text[s:e]
        if _key(quote) in known:
            rejected.append({"type": "ambiguity", "reason": "already_asked", "source_quote": quote})
            continue
        q_pt, q_dr = _clip(a.get("question_patient")), _clip(a.get("question_doctor"))
        if not q_pt or not q_dr:
            rejected.append({"type": "ambiguity", "reason": "no_question", "source_quote": quote})
            continue
        if source_lang != "ja" and KANA.search(q_pt + "".join(a.get("options_patient", []))):
            # 患者に日本語が出るのが一番まずい失敗（docs/ai-pipeline.md）
            rejected.append({"type": "ambiguity", "reason": "question_language", "source_quote": quote})
            continue
        o_pt = [_clip(o) for o in a.get("options_patient", []) if _clip(o)][:4]
        o_dr = [_clip(o) for o in a.get("options_doctor", []) if _clip(o)][:4]
        if len(o_pt) != len(o_dr) or len(o_pt) < 2:
            o_pt, o_dr = [], []  # 選択肢がそろわなければ、自由回答だけの質問にする
        known.add(_key(quote))
        linked = [f["fact_id"] for f in facts if f["span_start"] < e and s < f["span_end"]]
        candidates.append({
            "candidate_id": f"c{len(candidates) + 1}",
            "utterance_id": utterance_id,
            "source_quote": quote,
            "span_start": s,
            "span_end": e,
            "evidence_status": ev,
            "meaning_doctor": _clip(a.get("meaning_doctor")),
            "reason": _clip(a.get("reason")),
            "unresolved": _clip(a.get("unresolved")),
            "question_patient": q_pt,
            "question_doctor": q_dr,
            "options_patient": o_pt,
            "options_doctor": o_dr,
            "priority": a.get("priority") if a.get("priority") in PRIORITY_RANK else "medium",
            "fact_ids": linked,
            "origin": "ai",
            "status": C_PROPOSED,
            "question_message_id": "",
            "answers": [],
            "history": [{"at": at, "by": "ai", "event": "proposed"}],
        })
    candidates.sort(key=lambda c: PRIORITY_RANK[c["priority"]])
    for c in candidates[MAX_CANDIDATES:]:
        rejected.append({"type": "ambiguity", "reason": "too_many", "source_quote": c["source_quote"]})
    candidates = candidates[:MAX_CANDIDATES]
    for i, c in enumerate(candidates):
        c["rank"] = i + 1
        for fid in c["fact_ids"]:
            f = next(x for x in facts if x["fact_id"] == fid)
            if f["status"] == AI_EXTRACTED:
                apply_fact_event(f, "ambiguity_found", at=at, ref=c["candidate_id"])
            f["clarification_status"] = "open"

    r = raw.get("resolution") or {}
    resolution = {"applies": False, "resolves": "no", "meaning_doctor": "", "source_quote": "", "evidence_status": "none"}
    if r.get("applies"):
        g = ground(r.get("source_quote", ""), source_text)
        resolution = {
            "applies": True,
            "resolves": r.get("resolves") if r.get("resolves") in ("yes", "partly", "no") else "no",
            "meaning_doctor": _clip(r.get("meaning_doctor")),
            "source_quote": source_text[g[0]:g[1]] if g else "",
            "evidence_status": g[2] if g else "none",
        }
        # 根拠の無い「確定した」は採用しない（答えのどこにも無い意味を AI が作った可能性）
        if resolution["resolves"] != "no" and (not g or not resolution["meaning_doctor"]):
            rejected.append({"type": "resolution", "reason": "quote_not_in_source"})
            resolution.update(resolves="no", meaning_doctor="")
    return {"status": "ready", "facts": facts, "candidates": candidates, "resolution": resolution,
            "rejected": rejected, "at": at}


def legacy_facts(meaning):
    """旧い facts の形（{text, doctor_gloss, kind, negated, uncertain}）。患者の「自分のメモ」と eval/run.py 用。"""
    return [{
        "text": f["source_quote"],
        "doctor_gloss": f["normalized_meaning"],
        "kind": LEGACY_KIND.get(f["category"], "other"),
        "negated": f["polarity"] == "denied",
        "uncertain": f["polarity"] == "uncertain",
        "fact_id": f["fact_id"],
    } for f in meaning.get("facts", [])]


# ── 状態を変える（必ずここを通す）──
def apply_fact_event(fact, event, *, at=None, detail=None, ref=None):
    """fact の状態をイベントで変え、履歴に残す。表に無い遷移は TransitionError。"""
    nxt = TRANSITIONS.get((fact["status"], event))
    if nxt is None:
        raise TransitionError(f"{fact['status']} から {event} はできません")
    h = {"at": at or now_iso(), "by": EVENT_ACTOR[event], "event": event, "from": fact["status"], "to": nxt}
    if detail:
        h["detail"] = detail
    if ref:
        h["ref"] = ref
    fact["status"] = nxt
    fact.setdefault("history", []).append(h)
    return fact


def doctor_reset(fact, at=None):
    """医師の「確認済み／要修正」を取り消す。AI・患者のイベントで決まった最後の状態に戻す。"""
    if fact["status"] not in (DOCTOR_CONFIRMED, NEEDS_CORRECTION):
        raise TransitionError(f"{fact['status']} は取り消せません")
    back = AI_EXTRACTED
    for h in fact.get("history", []):
        if h.get("by") in ("ai", "patient") and h.get("to"):
            back = h["to"]
    h = {"at": at or now_iso(), "by": "doctor", "event": "doctor_reset", "from": fact["status"], "to": back}
    fact["status"] = back
    fact["history"].append(h)
    return fact


def apply_candidate_event(cand, event, *, by, at=None, detail=None, ref=None):
    nxt = CANDIDATE_TRANSITIONS.get((cand["status"], event))
    if nxt is None:
        raise TransitionError(f"確認候補が {cand['status']} なので {event} はできません")
    h = {"at": at or now_iso(), "by": by, "event": event, "from": cand["status"], "to": nxt}
    if detail:
        h["detail"] = detail
    if ref:
        h["ref"] = ref
    cand["status"] = nxt
    cand.setdefault("history", []).append(h)
    return cand


def apply_teachback_event(tb, event, *, by, at=None, ref=None):
    nxt = TEACHBACK_TRANSITIONS.get((tb["status"], event))
    if nxt is None:
        raise TransitionError(f"理解の確認が {tb['status']} なので {event} はできません")
    h = {"at": at or now_iso(), "by": by, "event": event, "from": tb["status"], "to": nxt}
    if ref:
        h["ref"] = ref
    tb["status"] = nxt
    tb.setdefault("history", []).append(h)
    return tb


def find(items, key, value):
    return next((x for x in items if x.get(key) == value), None)


def mark_question_sent(meaning, candidate_id, question_message_id, *, edited=False, at=None):
    """医師が確認質問を送った: 候補を asked に、つながる fact の clarification_status を asked にする。"""
    at = at or now_iso()
    c = find(meaning["candidates"], "candidate_id", candidate_id)
    if c is None:
        raise KeyError(candidate_id)
    apply_candidate_event(c, "question_sent", by="doctor", at=at, ref=question_message_id,
                          detail="edited" if edited else None)
    c["question_message_id"] = question_message_id
    for fid in c["fact_ids"]:
        f = find(meaning["facts"], "fact_id", fid)
        if f:
            apply_fact_event(f, "question_sent", at=at, ref=question_message_id)
            f["clarification_status"] = "asked"
    return c


# 答えを送った時刻と医師の判断の時刻の比較で見込む時計のずれ。この幅に入るときは「医師は答えを見ていない」とみなす（安全側）
SEEN_MARGIN_SEC = 1.0


def _parse_at(v):
    try:
        t = datetime.fromisoformat(v)
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def last_doctor_decision(fact):
    """fact に対する医師の最後の判断（確認済み・要修正）の履歴。無ければ None。"""
    return next((h for h in reversed(fact.get("history", []))
                 if h.get("by") == "doctor" and h.get("event") in ("doctor_confirm", "doctor_flag")), None)


def _seen_by_doctor(sent_at, decision):
    """答え（sent_at に送った）が、医師の判断の前に届いていたか。時刻が分からなければ「見ていない」（再確認へ）。"""
    s, d = _parse_at(sent_at), _parse_at((decision or {}).get("at"))
    return bool(s and d) and (d - s).total_seconds() >= SEEN_MARGIN_SEC


def recheck_required(fact):
    return any(r.get("required") for r in fact.get("rechecks", []))


def apply_answer(meaning, candidate_id, *, message_id, answer_type, resolves, meaning_doctor, source_quote="", at=None,
                 sent_at=""):
    """患者の回答（送信済み）を元の発言の確認候補と fact に結びつける。同じ回答を2回当てても変わらない。

    answer_type: option / free_text / unsure / none。resolves: yes / partly / no。
    「分からない」「どれでもない」・答えになっていない回答では、fact を確定しない（未確認のまま）。
    sent_at（答えを送った時刻）:
      - answers は送った順に並べる（最後が今の答え）。後から送った答えがもう当たっていれば（解析のやり直しが、
        患者が答えを直した後に終わったとき）、記録だけ送った順の位置に残し、候補と fact は変えない。
      - 医師が確認済み・要修正にした fact:
          医師の判断より前に届いていた答え（医師は原文と訳を見て判断した。解析が遅れて終わっただけ）
            → 状態と内容は変えず、届いたことを履歴に残す（patient_answered_late）
          医師の判断の後に届いた答え（または時刻が分からない）
            → 患者回答ありに戻し、「再確認が必要」（rechecks）を付ける。医師の判断の履歴は消さず、
              医師が判断したときの答え・新しい答え・医師の判断を rechecks に結びつけて残す
    """
    at = at or now_iso()
    c = find(meaning["candidates"], "candidate_id", candidate_id)
    if c is None:
        raise KeyError(candidate_id)
    if any(a["message_id"] == message_id for a in c["answers"]):
        return c  # もう当てた（/send とバックグラウンドの解析の両方から呼ばれる）
    settled = answer_type in ("option", "free_text") and resolves in ("yes", "partly") and bool(meaning_doctor)
    entry = {"message_id": message_id, "type": answer_type, "resolves": resolves if settled else "no",
             "meaning_doctor": meaning_doctor if settled else "", "source_quote": source_quote, "at": at,
             "sent_at": sent_at}
    newer = next((a for a in c["answers"] if sent_at and a.get("sent_at") and a["sent_at"] > sent_at), None)
    if newer:
        entry["superseded_by"] = newer["message_id"]
        c["answers"].insert(c["answers"].index(newer), entry)
        return c  # 古い答え: 新しい答えで決まった候補と fact を巻き戻さない
    previous = c["answers"][-1] if c["answers"] else None
    c["answers"].append(entry)
    if c["status"] not in (C_ASKED, C_ANSWERED, C_UNRESOLVED):
        return c  # 医師が「無視」した後に届いた回答。記録だけ残す
    apply_candidate_event(c, "answer_resolved" if settled else "answer_unresolved", by="patient", at=at, ref=message_id)
    for fid in c["fact_ids"]:
        f = find(meaning["facts"], "fact_id", fid)
        if not f:
            continue
        before = None
        if f["status"] in (DOCTOR_CONFIRMED, NEEDS_CORRECTION):
            decision = last_doctor_decision(f)
            if _seen_by_doctor(sent_at, decision):
                f.setdefault("history", []).append({"at": at, "by": "patient", "event": "patient_answered_late",
                                                    "ref": message_id,
                                                    "detail": meaning_doctor if settled else answer_type})
                continue
            seen = [a for a in c["answers"] if a is not entry and _seen_by_doctor(a.get("sent_at"), decision)]
            before = {"doctor_status": f["status"], "doctor_event": (decision or {}).get("event", ""),
                      "doctor_at": (decision or {}).get("at", ""), "doctor_note": (decision or {}).get("detail", ""),
                      "doctor_answer_id": seen[-1]["message_id"] if seen else "",
                      "doctor_resolved_meaning": f.get("resolved_meaning", ""),
                      "previous_answer_id": previous["message_id"] if previous else ""}
        if settled:
            apply_fact_event(f, "patient_answered", at=at, ref=message_id, detail=meaning_doctor)
            f["resolved_meaning"] = meaning_doctor if resolves == "yes" else f"{meaning_doctor}（一部未確定）"
            f["clarification_status"] = "answered" if resolves == "yes" else "partly"
        else:
            apply_fact_event(f, "patient_unsure", at=at, ref=message_id, detail=answer_type)
            f["clarification_status"] = "unresolved"
        if before:
            f.setdefault("rechecks", []).append({**before, "required": True, "new_answer_id": message_id,
                                                 "new_meaning": meaning_doctor if settled else "", "at": at,
                                                 "resolved_at": "", "resolved_by": ""})
            f["history"].append({"at": at, "by": "system", "event": "recheck_required", "ref": message_id,
                                 "detail": before["doctor_event"]})
    return c


def doctor_fact_action(meaning, fact_id, action, at=None, note=""):
    """医師が fact を「確認済み」「要修正」にする／取り消す。"""
    f = find(meaning["facts"], "fact_id", fact_id)
    if f is None:
        raise KeyError(fact_id)
    at = at or datetime.now(timezone.utc).isoformat()  # 答えの送信時刻と比べるので秒より細かく
    if action in ("confirm", "flag", "reset"):
        for r in f.get("rechecks", []):
            if r.get("required"):  # 医師がもう一度判断した（記録は残す）
                r.update(required=False, resolved_at=at, resolved_by=action)
    if action == "confirm":
        apply_fact_event(f, "doctor_confirm", at=at, detail=note or None)
    elif action == "flag":
        apply_fact_event(f, "doctor_flag", at=at, detail=note or None)
    elif action == "reset":
        doctor_reset(f, at=at)
    else:
        raise TransitionError(f"不明な操作: {action}")
    return f


def doctor_candidate_action(meaning, candidate_id, action, at=None):
    """医師が確認候補を「保留」「無視」「戻す」。無視しても fact は確定しない（未確認のまま、確認省略の印を付ける）。"""
    c = find(meaning["candidates"], "candidate_id", candidate_id)
    if c is None:
        raise KeyError(candidate_id)
    if action not in ("hold", "dismiss", "restore"):
        raise TransitionError(f"不明な操作: {action}")
    apply_candidate_event(c, action, by="doctor", at=at)
    for fid in c["fact_ids"]:
        f = find(meaning["facts"], "fact_id", fid)
        if not f or f["status"] != UNCONFIRMED:
            continue
        if action == "dismiss":
            apply_fact_event(f, "candidate_dismissed", at=at, ref=candidate_id)
            f["clarification_status"] = "skipped"
        elif action == "restore":
            f["clarification_status"] = "open"
    return c


def custom_candidate(meaning, *, utterance_id, source_text, fact_id, question_doctor, question_patient, at=None):
    """医師が自分で書いた確認質問を、元の発言の fact にひもづける候補として足す。"""
    at = at or now_iso()
    f = find(meaning["facts"], "fact_id", fact_id) if fact_id else None
    if fact_id and f is None:
        raise KeyError(fact_id)
    s, e = (f["span_start"], f["span_end"]) if f else (0, 0)
    c = {
        "candidate_id": f"d{sum(1 for x in meaning['candidates'] if x['origin'] == 'doctor') + 1}",
        "utterance_id": utterance_id,
        "source_quote": source_text[s:e] if f else "",
        "span_start": s, "span_end": e, "evidence_status": f["evidence_status"] if f else "none",
        "meaning_doctor": f["normalized_meaning"] if f else "",
        "reason": "", "unresolved": "",
        "question_patient": question_patient, "question_doctor": question_doctor,
        "options_patient": [], "options_doctor": [],
        "priority": "medium", "fact_ids": [f["fact_id"]] if f else [], "origin": "doctor",
        "status": C_PROPOSED, "question_message_id": "", "answers": [],
        "history": [{"at": at, "by": "doctor", "event": "proposed"}],
        "rank": len(meaning["candidates"]) + 1,
    }
    meaning["candidates"].append(c)
    return c


# ── 理解の確認（teach-back）の結果 ──
def build_teachback(raw, *, restatement, schema=None):
    """Gemini の teachback 出力 → 保存する形。全体の判定はモデルに任せず、各点の判定から決める。

      どれかが mismatch → inconsistent（認識の相違の可能性。医師に強めに出す）
      mismatch は無いが missing / unclear がある → partial / unclear（控えめに出す）
      すべて match → consistent（警告は出さない）
    """
    if schema is not None:
        validate(raw, schema)
    points, rejected = [], 0
    for p in raw.get("points", [])[:6]:
        verdict = p.get("verdict") if p.get("verdict") in ("match", "mismatch", "missing", "unclear") else "unclear"
        g = ground(p.get("restatement_quote", ""), restatement)
        quote = restatement[g[0]:g[1]] if g else ""
        if verdict == "mismatch" and not quote:
            # 患者の言葉のどこが食い違うのか示せない「不一致」は、確かとは言えないので unclear に下げる
            verdict, rejected = "unclear", rejected + 1
        points.append({
            "doctor_point": _clip(p.get("doctor_point")),
            "patient_understanding": _clip(p.get("patient_understanding")),
            "verdict": verdict,
            "explanation": _clip(p.get("explanation")),
            "restatement_quote": quote,
        })
    vs = {p["verdict"] for p in points}
    if not points:
        overall = "unclear"
    elif "mismatch" in vs:
        overall = "inconsistent"
    elif "unclear" in vs:
        overall = "unclear"
    elif "missing" in vs:
        overall = "partial"
    else:
        overall = "consistent"
    return {"overall": overall, "points": points,
            "suggested_reexplanation": _clip(raw.get("suggested_reexplanation")) if overall != "consistent" else "",
            "rejected": rejected, "decided_by": "ai", "at": now_iso()}
