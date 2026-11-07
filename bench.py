"""患者の待ち時間（発言 → 確認質問 or 逆翻訳が画面に出るまで）の Gemini 部分を、旧方式と新方式で比べる。

使い方: python bench.py        （1発言につき各方式 3 回。回数を変えるなら python bench.py 5）

  旧 = analyze 1回（facts と質問をまとめて作る）。質問なしなら、その後に translate（直列）
  新 = clarify（質問だけ）・facts・先回り translate を同時に投げる。
       質問ありなら clarify が返った時点、質問なしなら clarify と translate の遅い方で画面に出る

旧と新を交互に呼ぶので、混雑の時間帯による差は両方に同じように乗る。
Firestore の読み書き（合わせて 0.1〜0.3 秒）と STT はどちらも同じなので含めていない。
"""
import statistics
import sys
import time

import unsaid_ai as ai

sys.stdout.reconfigure(encoding="utf-8")

CASES = [  # 質問が出る発言と出ない発言を半々
    ("en", "I've had a headache for a while.", True),
    ("en", "My stomach hurts sometimes after I eat.", True),
    ("en", "I don't have a fever.", False),
    ("en", "I'm allergic to penicillin.", False),
]


def old(text, lang):
    t0 = time.time()
    out, _ = ai.analyze_single(text, lang)
    if not out["clarifications"]:
        ai.translate(text, lang, "ja", "patient_to_doctor")
    return time.time() - t0, out["needs_clarification"], out["_meta"]


def new(text, lang):
    t0 = time.time()
    f_tr = ai.submit(ai.translate, text, lang, "ja", "patient_to_doctor")
    f_facts = ai.submit(ai.facts, text, lang)
    out, _ = ai.clarify(text, lang)
    if not out["clarifications"]:
        f_tr.result()
    shown = time.time() - t0
    f_facts.result()  # 次の計測に食い込まないように待つ（患者の待ち時間には入れない）
    return shown, out["needs_clarification"], out["_meta"]


def main(rounds):
    print("準備運動（最初の1回は遅いので計測に入れない）...")
    ai.analyze_single("I have a headache.", "en")
    ai.clarify("I have a headache.", "en")
    res = {"旧": {True: [], False: []}, "新": {True: [], False: []}}
    for r in range(rounds):
        for lang, text, _ in CASES:
            for name, fn in (("旧", old), ("新", new)) if r % 2 == 0 else (("新", new), ("旧", old)):
                try:
                    sec, clar, meta = fn(text, lang)
                except Exception as e:
                    print(f"  ERROR {name} {text!r}: {type(e).__name__}")
                    continue
                res[name][clar].append(sec)
                print(f"  {name} {sec:4.1f}秒 {'質問あり' if clar else '質問なし'} "
                      f"thoughts={meta.get('thoughts')} out={meta.get('out_tokens')}  {text}")

    print("\n患者の待ち時間（Gemini 部分）  中央値 / 最大")
    for clar in (True, False):
        row = []
        for name in ("旧", "新"):
            xs = res[name][clar]
            row.append(f"{name} {statistics.median(xs):.1f} / {max(xs):.1f}秒（{len(xs)}回）" if xs else f"{name} -")
        print(f"  {'質問あり' if clar else '質問なし'}: " + "   ".join(row))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
