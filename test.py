"""プロンプトの回帰テスト。使い方: python test.py   （analyze だけ: python test.py analyze）"""
import re
import sys

from unsaid_ai import analyze, translate

sys.stdout.reconfigure(encoding="utf-8")  # Windows でベトナム語・中国語を出しても落ちないように

KANA = re.compile(r"[\u3040-\u30ff]")
HAN = re.compile(r"[\u4e00-\u9fff]")

# clarify: 確認質問が出るべきか / target: 確認対象に含まれるべき語（どれか1つ）
# keep: facts のどこかに残っているべき文字列（リストなら「どれか1つ」。例: ["3", "three"]） / negated・uncertain: どれかの fact で True になるべきか
ANALYZE_CASES = [
    # --- English（デモ想定） ---
    dict(lang="en", text="I've had a headache for a while.", clarify=True, target=["for a while"]),
    dict(lang="en", text="I don't have a fever.", clarify=False, negated=True),
    dict(lang="en", text="I fell a little while ago, and I think my leg hurts.", clarify=True,
         target=["a little while ago"], uncertain=True),  # 「思う」は聞かずに保持、時間を優先して1問
    dict(lang="en", text="I can't remember if I took my medicine this morning.", clarify=False,
         uncertain=True),  # 薬の名前を聞き出さない
    dict(lang="en", text="I'm allergic to penicillin.", clarify=False, keep=["penicillin"]),
    dict(lang="en", text="My stomach hurts sometimes after I eat.", clarify=True, target=["sometimes"]),
    dict(lang="en", text="I feel dizzy.", clarify=True, target=["dizzy"]),  # 回転性/ふらつき等の意味の確認
    dict(lang="en", text="I had a fever of 38.5 two days ago, but it's gone now.", clarify=False,
         keep=["38.5"]),
    dict(lang="en", text="I might be pregnant, but I'm not sure.", clarify=False, uncertain=True),
    dict(lang="en", text="I've been skipping my blood pressure pills for the last few days.", clarify=True,
         target=["few days"]),
    dict(lang="en", text="It hurts here.", clarify=False),  # 部位は医師が見て聞く。新情報を聞き出さない
    # --- 中国語 ---
    dict(lang="zh-CN", text="我头疼好几天了。", clarify=True, target=["好几天"]),
    dict(lang="zh-CN", text="我没有发烧。", clarify=False, negated=True),
    dict(lang="zh-CN", text="我有时候吃完饭胃疼。", clarify=True, target=["有时候"]),
    # --- ベトナム語 ---
    dict(lang="vi", text="Tôi bị chóng mặt.", clarify=True, target=["chóng mặt"]),
    dict(lang="vi", text="Tôi không nhớ đã uống thuốc chưa.", clarify=False, uncertain=True),
    dict(lang="vi", text="Dạo này tôi hay bị đau đầu.", clarify=True, target=["dạo này", "hay"]),
]

TRANSLATE_CASES = [
    dict(name="患者→医師: 選択肢で確定", direction="patient_to_doctor", src="en", dst="ja",
         original="I've had a headache for a while.",
         clarification=dict(target="for a while", question="How long have you had the headache?",
                            answer_type="option", answer="About 1 to 3 days"),
         keep=[["3", "three"]]),
    dict(name="患者→医師: 説明できない", direction="patient_to_doctor", src="en", dst="ja",
         original="My stomach hurts sometimes after I eat.",
         clarification=dict(target="sometimes", question="How often does it happen?",
                            answer_type="unsure", answer=""),
         need_note=True),
    dict(name="患者→医師: 否定を保持(中国語)", direction="patient_to_doctor", src="zh-CN", dst="ja",
         original="我没有发烧。", clarification=None, keep=["熱"]),
    dict(name="医師→患者: 英語", direction="doctor_to_patient", src="ja", dst="en",
         original="この薬は1日3回、食後に飲んでください。眠くなることがあるので、車の運転はしないでください。",
         keep=[["3", "three"]]),
    dict(name="医師→患者: ベトナム語", direction="doctor_to_patient", src="ja", dst="vi",
         original="この薬は1日3回、食後に飲んでください。眠くなることがあるので、車の運転はしないでください。",
         keep=[["3", "ba"]]),
]


def script_ok(lang, s):
    """出力が患者の言語で書かれているかのざっくり判定（日本語の混入を一番に検出したい）。"""
    if lang == "ja":
        return True
    if KANA.search(s):
        return False
    if lang == "zh-CN":
        return bool(HAN.search(s))
    return not HAN.search(s)


def missing(keep, text):
    """keep のうち text に見つからないものを返す（大文字小文字は無視）。"""
    text = text.lower()
    alts = [[k] if isinstance(k, str) else k for k in keep]
    return [a for a in alts if not any(x.lower() in text for x in a)]


def check_analyze(c, out):
    errs = []
    clars = out["clarifications"]
    if out["needs_clarification"] != c["clarify"]:
        errs.append(f"needs_clarification={out['needs_clarification']}（期待 {c['clarify']}）")
    if clars:
        q = clars[0]
        if q["target"] not in c["text"]:
            errs.append(f"target が原文の部分文字列でない: {q['target']!r}")
        if c.get("target") and not any(t.lower() in q["target"].lower() for t in c["target"]):
            errs.append(f"target={q['target']!r}（期待 {c['target']}）")
        if not script_ok(c["lang"], q["question"] + "".join(q["options"])):
            errs.append("質問/選択肢が患者の言語になっていない")
    facts = out["facts"]
    for key in ("negated", "uncertain"):
        if c.get(key) and not any(f[key] for f in facts):
            errs.append(f"{key} が保持されていない")
    blob = " ".join(f["text"] + " " + f["doctor_gloss"] for f in facts)
    for k in missing(c.get("keep", []), blob):
        errs.append(f"{k} が facts から消えた")
    return errs


def check_translate(c, out):
    errs = []
    for k in missing(c.get("keep", []), out["translation"]):
        errs.append(f"{k} が訳から消えた")
    if c.get("need_note") and not out["note"]:
        errs.append("note が空（説明できなかったことが医師に伝わらない）")
    if not script_ok(c["dst"], out["translation"]):
        errs.append("訳文が目的言語になっていない")
    return errs


def run(cases, fn, check, label):
    fails = 0
    times = []
    for c in cases:
        out, sec = fn(c)
        errs = check(c, out)
        fails += bool(errs)
        times.append(sec)
        meta = out.pop("_meta", {})
        print(f"{'PASS' if not errs else 'FAIL'} {sec:.1f}秒 [{label(c)}] {meta}")
        for e in errs:
            print("   -", e)
        for line in view(out):
            print("   ", line)
    times.sort()
    print(f"-- 中央値 {times[len(times) // 2]:.1f}秒 / 最大 {times[-1]:.1f}秒\n")
    return fails


def view(out):
    if "facts" in out:
        for f in out["facts"]:
            flags = "".join(["否" if f["negated"] else "", "?" if f["uncertain"] else ""])
            yield f"fact[{f['kind']}{flags}] {f['text']} / {f['doctor_gloss']}"
        for q in out["clarifications"]:
            yield f"Q({q['ui_type']}) [{q['target']}] {q['question']} -> {q['options']}"
            if "target_raw" in q:
                yield f"   ※ target をコードで補正: {q['target_raw']!r} -> {q['target']!r}"
    else:
        yield f"訳: {out['translation']}"
        yield f"逆訳: {out['back_translation']}"
        if out["note"]:
            yield f"注: {out['note']}"


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    fails = total = 0
    if only in (None, "analyze"):
        fails += run(ANALYZE_CASES, lambda c: analyze(c["text"], c["lang"], "ja"), check_analyze,
                     lambda c: c["lang"] + " " + c["text"])
        total += len(ANALYZE_CASES)
    if only in (None, "translate"):
        fails += run(TRANSLATE_CASES,
                     lambda c: translate(c["original"], c["src"], c["dst"], c["direction"], c.get("clarification")),
                     check_translate, lambda c: c["name"])
        total += len(TRANSLATE_CASES)
    print(f"\n{total - fails}/{total} PASS")
