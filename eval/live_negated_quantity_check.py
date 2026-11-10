"""本物の Gemini で、否定・訂正された量（「2錠まとめてではなく、朝1錠・夜1錠」の 2錠）を、指示の量として枠に入れないかを測る。
入れると、正しく言い直した患者に「説明 1錠・2錠 / 理解 1錠」の理解の相違を出してしまう（2026-11-10、プレゼン用の画面の撮影で発見）。

  UNSAID_VERTEX_PROJECT=<プロジェクト ID> GOOGLE_APPLICATION_CREDENTIALS=<ADC> python eval/live_negated_quantity_check.py [回数]
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ussa  # noqa: E402
import unsaid_ai as u  # noqa: E402

# (説明, 正しい言い直し, 説明の枠に入ってはいけない量)
CASES = [
    ("朝に2錠まとめて飲むのではなく、朝に1錠、夜に1錠の1日2回飲んでください。", "一天两次，早上和晚上各吃一片，对吗？", {2.0}),
    ("3日分ではなく、5日分を全部飲みきってください。", "要把五天的药全部吃完，对吗？", {3.0}),
    ("1回2錠ではなく1錠です。1日3回、食後に飲んでください。", "一次一片，一天三次，饭后吃，对吗？", {2.0}),
]


def one(c):
    exp, pat, banned = c
    a, _ = u.source_agent(exp, "doctor", "ja", "zh-CN", [])
    fa = ussa.build_intent(a, source_text=exp, speaker_lang="ja", listener_lang="zh-CN")["frame"]
    b, _ = u.source_agent(pat, "patient", "zh-CN", "ja", [{"speaker": "doctor", "original": exp, "translation": ""}])
    fb = ussa.build_intent(b, source_text=pat, speaker_lang="zh-CN", listener_lang="ja")["frame"]
    leaked = {q["value"] for q in fa["quantities"]} & banned
    return exp[:16], bool(leaked), bool(ussa.cross_check(fa, fb))


def main_():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(one, [c for c in CASES for _ in range(n)]))
    for k in dict.fromkeys(r[0] for r in res):
        rs = [r for r in res if r[0] == k]
        print(f"{k}: 否定された量が枠に入った {sum(r[1] for r in rs)}/{len(rs)}、正しい言い直しに相違を出した {sum(r[2] for r in rs)}/{len(rs)}")


if __name__ == "__main__":
    main_()
