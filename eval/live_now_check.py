"""本物の Gemini で、「今も・今は・现在・now」（現在のこと）を日付「今日（day+0）」として枠に入れないかを測る。
入れると、訳文側が「现在」を日付にしなかったときに「日時が違う」として正しい訳を止める
（2026-11-10、診察の流れの撮影で、医師の質問「今も胸が詰まる感じ…」が止まった）。「今日」は day+0 のまま（対照）。

  UNSAID_VERTEX_PROJECT=<プロジェクト ID> GOOGLE_APPLICATION_CREDENTIALS=<ADC> python eval/live_now_check.py [回数]
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
import ussa  # noqa: E402
import unsaid_ai as u  # noqa: E402

# (原文, 言語, 訳, 訳の言語, 原文の枠に day+0 があるべきか)
CASES = [
    ("今も胸が詰まる感じや息苦しさは続いていますか？", "ja", "你现在还觉得胸口发闷或者呼吸困难吗？", "zh-CN", False),
    ("今は痛みはありますか？", "ja", "你现在还疼吗？", "zh-CN", False),
    ("我现在还是觉得头晕。", "zh-CN", "今もまだめまいがします。", "ja", False),
    ("今日は検査の結果が出るまで帰らないでください。", "ja", "今天请先不要回家，等检查结果出来。", "zh-CN", True),
]


def one(c):
    src, sl, tr, tl, want = c
    raw, _ = u.source_agent(src, "doctor", sl, tl, [])
    it = ussa.build_intent(raw, source_text=src, speaker_lang=sl, listener_lang=tl)
    has = any(t["kind"] == "relative_day" and t["value"] == "day+0" for t in it["frame"]["times"])
    v = main._verify(src, sl, tr, tl, "doctor", it, None, {}, "x")
    return src[:12], has, v["status"]


def main_():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(one, [c for c in CASES for _ in range(n)]))
    for src, *_, want in CASES:
        rs = [r for r in res if r[0] == src[:12]]
        print(f"{src[:14]}: day+0 {sum(r[1] for r in rs)}/{len(rs)}（期待: {'あり' if want else 'なし'}）、"
              f"止めた {sum(r[2] not in ('VERIFIED', 'AMBIGUOUS') for r in rs)}/{len(rs)}")


if __name__ == "__main__":
    main_()
