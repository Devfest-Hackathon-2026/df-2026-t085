"""本物の Gemini で、複数の節で様相が違う文（「入ってよいが、濡らさないで」）の意味の枠が、原文側（A: Source Agent）と
訳文側（B: extract_frame）で同じ様相（禁止を優先）になるかを測る。食い違うと、正しい訳を「禁止が片方だけ」として止めてしまう。

2026-11-10: 規則を入れる前 5/15 が食い違い（B が「may」を選んだ）→ 入れた後 0/15。

  UNSAID_VERTEX_PROJECT=<プロジェクト ID> GOOGLE_APPLICATION_CREDENTIALS=<ADC> python eval/live_modality_check.py [回数]
"""
import collections
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unsaid_ai as u  # noqa: E402
import ussa  # noqa: E402

CASES = [
    ("明日の朝8時までは何も食べないでください。水は飲んでもかまいません。", "ja", "明天早上8点之前请不要吃任何东西。可以喝水。", "zh-CN"),
    ("お風呂は入っていいですが、傷口は濡らさないでください。", "ja", "可以洗澡，但是请不要弄湿伤口。", "zh-CN"),
    ("You can walk around, but don't lift anything heavy for two weeks.", "en",
     "歩き回ってもかまいませんが、2週間は重いものを持ち上げないでください。", "ja"),
]
PROHIBIT = ("must_not", "should_not", "cannot")


def one(c):
    src, sl, tr, tl = c
    a, _ = u.source_agent(src, "doctor", sl, tl, [])
    A = ussa.build_intent(a, source_text=src, speaker_lang=sl, listener_lang=tl)["frame"]["modality"]
    b, _ = u.extract_frame(tr, tl, "doctor")
    B = (b.get("frame") or b).get("modality")
    return src[:14], A, B


def main_():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    res = collections.defaultdict(list)
    with ThreadPoolExecutor(3) as ex:
        for k, A, B in ex.map(one, [c for c in CASES for _ in range(n)]):
            res[k].append((A, B))
    dis = total = notproh = 0
    for k, v in res.items():
        print(k, v)
        for A, B in v:
            total += 1
            dis += (A in PROHIBIT) != (B in PROHIBIT)
            notproh += A not in PROHIBIT
    print(f"prohibition disagreement A vs B: {dis}/{total}; A not prohibition: {notproh}/{total}")


if __name__ == "__main__":
    main_()
