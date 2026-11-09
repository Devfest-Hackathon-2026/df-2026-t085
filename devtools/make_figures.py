"""プレゼン用の図（SVG と、Chromium で描いた PNG）を docs/presentation-assets/ に作る。

  .venv/bin/python devtools/make_figures.py

図の中の例と数字は、本物の Gemini での実行結果（eval/live_checks_results.json・eval/compare_results.json）から取る。
ここに無い数字は描かない。PNG は 2 倍（1920×1080 → 3840×2160）。文字は Noto Sans JP（Google Fonts）。
"""
import html
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "presentation-assets"
FONT = "https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Noto+Sans+SC:wght@400;500;700&display=swap"
FAMILY = "'Noto Sans JP','Noto Sans SC','Hiragino Sans','Yu Gothic',sans-serif"
C = {"ink": "#15191e", "sub": "#4a535e", "faint": "#6b747f", "line": "#d5dae0", "soft": "#f6f7f9", "accent": "#0b5c80",
     "accent_soft": "#e6f1f6", "ok": "#1f6b45", "ok_soft": "#e3f3ea", "warn_soft": "#fff6db", "warn": "#5c4300",
     "danger": "#b42318", "danger_soft": "#fdecea", "gem": "#6b3fa0", "gem_soft": "#f1eafa"}
W, H = 1920, 1080


def esc(s):
    return html.escape(str(s), quote=True)


def svg(body, w=W, h=H):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<style>@import url("{FONT}"); text {{ font-family: {FAMILY}; }}</style>'
            f'<rect width="{w}" height="{h}" fill="#ffffff"/>{body}</svg>')


def text(x, y, s, size=24, weight=400, fill=C["ink"], anchor="start"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}">{esc(s)}</text>')


def lines(x, y, rows, size=22, gap=1.45, **kw):
    return "".join(text(x, y + i * size * gap, r, size, **kw) for i, r in enumerate(rows))


def box(x, y, w, h, fill="#ffffff", stroke=C["line"], r=18, sw=2):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'


def badge(x, y, label, fill, ink, size=17):
    w = size * 0.95 * len(label) + 22
    return box(x, y, w, size + 14, fill, fill, r=(size + 14) / 2) + text(x + w / 2, y + size + 3, label, size, 700, ink, "middle")


def arrow(x1, y1, x2, y2, color=C["faint"], dash=False):
    d = ' stroke-dasharray="8 7"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="3"{d} marker-end="url(#ah)"/>')


DEFS = (f'<defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{C["faint"]}"/></marker></defs>')
GEM = lambda x, y: badge(x, y, "Gemini", C["gem_soft"], C["gem"])  # noqa: E731
CODE = lambda x, y: badge(x, y, "コード", C["accent_soft"], C["accent"])  # noqa: E731


# ───────────── 1. 技術図 ─────────────
def architecture():
    b = [DEFS, text(80, 92, "UNSAID の仕組み — 訳す前に意味を確かめ、確かめてから届ける", 40, 700),
         text(80, 140, "UNSAID Semantic Safety Architecture（USSA）。発言ごとに次の順で処理する（医師→患者・患者→医師で同じ）", 22, 400, C["sub"])]
    # 1 入力
    b += [box(80, 200, 300, 250, C["soft"]), text(105, 245, "患者・医師", 28, 700),
          lines(105, 290, ["患者: スマホ（中国語など）", "医師: PC（日本語）"], 20, fill=C["sub"]),
          text(105, 370, "Speech & Input", 22, 700, C["accent"]),
          lines(105, 405, ["音声は文字になるだけ", "直して自分で「送る」"], 19, fill=C["sub"])]
    b.append(arrow(385, 325, 445, 325))
    # 2 Source + Translation（並列）
    b += [box(450, 200, 380, 115, "#fff"), text(475, 245, "Source Agent", 26, 700), GEM(706, 222),
          lines(475, 285, ["発話意図・意味の枠を解析"], 19, fill=C["sub"]),
          box(450, 335, 380, 115, "#fff"), text(475, 380, "Translation Agent", 26, 700), GEM(706, 359),
          lines(475, 420, ["原文から直接訳す（下書き）"], 19, fill=C["sub"]),
          text(625, 488, "並列に実行", 18, 500, C["faint"], "end")]
    b.append(arrow(835, 325, 895, 325))
    # 3 Semantic Bridge
    b += [box(900, 200, 330, 250, C["accent_soft"], C["accent"]), text(925, 245, "Semantic Bridge", 26, 700, C["accent"]),
          lines(925, 290, ["言語に依存しない意味の枠", "否定・数量・日時・条件・", "回数・同意/拒否・質問か"], 19, fill=C["ink"]),
          lines(925, 395, ["枠は Gemini が取り、", "比べるのはコード"], 18, fill=C["sub"])]
    b.append(arrow(1235, 325, 1295, 325))
    # 4 Verification
    b += [box(1300, 200, 540, 250, "#fff"), text(1325, 245, "Verification Engine", 26, 700),
          lines(1325, 290, ["① ルール（数・時刻・日付・曜日・否定）", "② 原文の枠 ↔ 訳文だけから取った枠", "③ 原文と訳文の直接の照合"], 19, fill=C["sub"]),
          CODE(1325, 395), GEM(1430, 395),
          lines(1590, 412, ["強い相違は訳し直し、", "残れば発言者が決める"], 17, fill=C["sub"])]
    # 意図の確認（分岐）
    b += [arrow(640, 455, 640, 535, C["accent"]),
          box(450, 540, 380, 150, C["warn_soft"], "#e9cf7a"), text(475, 585, "意図が決まらないとき", 24, 700, C["warn"]),
          lines(475, 625, ["訳を送らず、発言者本人に", "意味を選んでもらう（双方向）"], 19, fill=C["warn"])]
    # 5 Safety Gate
    b += [arrow(1570, 455, 1570, 535),
          box(1300, 540, 540, 150, C["ok_soft"], C["ok"], sw=3), text(1325, 585, "Safety Gate", 28, 700, C["ok"]), CODE(1500, 562),
          lines(1325, 625, ["送る直前にコードで 8 項目を確認（本人の送信・", "原文が同じ・意図・照合・最新の解析 など）"], 19, fill=C["ink"])]
    # 6 出口
    b.append(arrow(1570, 695, 1570, 760))
    outs = [("相手に届ける", "訳と照合の状態つき"), ("追加の確認質問", "医師が選び、患者が答える"),
            ("Teach-back", "患者の言い直しと説明を比べる"), ("Clinical Memory", "原文に根拠のある問診メモ")]
    x = 80
    for t, s in outs:
        b += [box(x, 770, 410, 120, "#fff"), text(x + 25, 815, t, 26, 700), text(x + 25, 855, s, 19, 400, C["sub"])]
        x += 440
    b += [box(80, 915, 1760, 110, C["soft"], C["soft"]),
          text(105, 958, "Conversation Auditor（届いた後に会話全体を見る）", 24, 700), GEM(700, 935), CODE(815, 935),
          lines(105, 997, ["説明と理解の相違・矛盾・未回答を、根拠の発言つきで未解決事項にする。解決にできるのは医師だけ。AI は診断しない"], 19, fill=C["sub"])]
    return svg("".join(b))


# ───────────── 2. 意味の照合（本物の Gemini の結果）─────────────
def verification():
    data = json.loads((ROOT / "eval/live_checks_results.json").read_text(encoding="utf-8"))
    cmp = json.loads((ROOT / "eval/compare_results.json").read_text(encoding="utf-8"))
    rs = {r["check"]: r for r in data["results"]}
    rows = [("1日3回に変えた訳", "这个药一天吃三次，早上和晚上各吃一片。"), ("否定を足した訳", "这个药不要一天吃两次，早上和晚上各吃一片。"),
            ("夜を落とした訳", "这个药一天吃两次，早上吃一片。"), ("正しい訳", "这个药一天吃两次，早上和晚上各吃一片。")]
    names = {"quantity_change": "数量", "frequency_change": "回数", "polarity_flip": "否定・禁止", "time_change": "日時・時間帯",
             "omission": "欠落", "addition": "追加", "condition_missing": "条件"}
    pr = [r for r in cmp["precision"] if "injected" in r]
    miss = sum(r["injected"]["corrupted"] in ("VERIFIED", "AMBIGUOUS") for r in pr)
    fa = sum(r["injected"]["reference"] not in ("VERIFIED", "AMBIGUOUS") for r in pr)
    b = [text(80, 92, "Semantic Bridge と Verification Engine — 意味の要素を比べて、相違があれば届けない", 38, 700),
         text(80, 140, "医師の説明「この薬は1日2回、朝と夜に1錠ずつ飲んでください。」に、誤りを1つ入れた訳を照合させた（本物の Gemini）", 22, 400, C["sub"]),
         box(80, 180, 1760, 90, C["soft"], C["soft"]), text(110, 222, "原文の意味の枠", 22, 700, C["accent"]),
         text(330, 222, "指示 ・ 回数 1日2回 ・ 量 1錠 ・ 時間帯 朝・夜 ・ 禁止なし", 24, 500), text(110, 255, "（Gemini が取り、コードで比べる）", 17, 400, C["faint"])]
    y = 300
    for name, tr in rows:
        r = rs.get(f"Verification Engine: {name}", {})
        v = r.get("verification", {})
        st = v.get("status", "?")
        good = st in ("VERIFIED", "AMBIGUOUS")
        kinds = []
        for i in v.get("issues", []):
            if i.get("strength") == "strong" and names.get(i["type"]) not in kinds:
                kinds.append(names.get(i["type"], i["type"]))
        b += [box(80, y, 1760, 130, C["ok_soft"] if good else C["danger_soft"], C["ok"] if good else C["danger"]),
              text(110, y + 50, name, 26, 700, C["ok"] if good else C["danger"]),
              f'<text x="110" y="{y + 95}" font-size="26" fill="{C["ink"]}" font-family="\'Noto Sans SC\',sans-serif">{esc(tr)}</text>',
              text(1180, y + 50, "照合: " + ("通す（VERIFIED）" if good else "止める（MISMATCH）"), 24, 700, C["ok"] if good else C["danger"]),
              text(1180, y + 92, ("見つけた相違: " + "・".join(kinds[:4])) if kinds else "相違なし", 21, 500, C["sub"])]
        y += 150
    b += [text(80, 935, f"108 件の比較評価（同じ条件・本物の Gemini）: 誤りを入れた訳 {len(pr)} 件の見逃し {miss} 件、正しい訳を止めた {fa} 件。"
                         "A（通常の翻訳）は照合しないので、誤りはそのまま届く", 21, 500, C["ink"]),
          text(80, 975, f"実行: {data['meta']['started']}（各照合）・{cmp['meta']['started']}（比較評価）、{cmp['meta']['model']}・{cmp['meta']['location']}。"
                         "「VERIFIED」は相違を見つけなかったという意味で、正しさの証明ではない", 18, 400, C["faint"])]
    return svg("".join(b))


# ───────────── 3. 「大丈夫です」は文脈で意味が変わる（本物の Gemini の結果）─────────────
def intent_context():
    cmp = json.loads((ROOT / "eval/compare_results.json").read_text(encoding="utf-8"))
    rs = {r["id"]: r for r in cmp["intent"]}
    rows = [("ja-help", "立ち上がるのを手伝いましょうか？", "断る"), ("ja-wait", "お待たせしてすみません。", "気にしないで"),
            ("ja-continue", "このまま診察を続けてもいいですか？", "同意"), ("ja-injection", "痛み止めの注射をしましょうか？", "決まらない")]
    b = [text(80, 92, "「大丈夫です」— 同じ言葉でも、文脈で意味が変わる", 40, 700),
         text(80, 140, "患者の「大丈夫です。」を英語の医師に。A は文脈を見ない通常の翻訳、UNSAID は直前の会話から意図を解析する（本物の Gemini）", 22, 400, C["sub"]),
         text(400, 205, "直前の医師の発言", 20, 700, C["faint"]), text(980, 205, "A 通常の翻訳", 20, 700, C["faint"]),
         text(1380, 205, "UNSAID", 20, 700, C["accent"])]
    y = 230
    for cid, ctx, label in rows:
        r = rs[cid]
        asked = r["D"].get("outcome") == "asked_intent"
        b += [box(80, y, 1760, 150, C["warn_soft"] if asked else "#fff", "#e9cf7a" if asked else C["line"]),
              text(110, y + 85, "大丈夫です。", 30, 700), text(400, y + 70, ctx, 24, 500),
              text(400, y + 110, f"→ 意図: {label}", 20, 500, C["sub"]),
              text(980, y + 85, r["A"]["translation"].split(" / ")[0], 26, 500, C["sub"])]
        if asked:
            b += [text(1380, y + 70, "訳さずに本人に確認", 26, 700, C["warn"]),
                  text(1380, y + 110, "「断る／お願いする」を本人が選ぶ", 20, 500, C["warn"])]
        else:
            b += [text(1380, y + 85, r["D"]["translation"], 26, 700, C["accent"])]
        y += 170
    it = [r for r in cmp["intent"]]
    need = [r for r in it if r["expect"] == "clarify"]
    nn = [r for r in it if r["expect"] == "resolved"]
    b += [text(80, 935, f"25 件の評価: 本人に聞くべき {len(need)} 件で、未確認のまま送った A {sum(not r['A']['asked'] for r in need)} 件 → UNSAID "
                         f"{sum((not r['D']['asked']) and bool(r['D']['translation']) for r in need)} 件。文脈で決まる {len(nn)} 件で不要に聞いた "
                         f"{sum(r['D']['asked'] for r in nn)} 件", 21, 500),
          text(80, 975, f"実行: {cmp['meta']['started']}、{cmp['meta']['model']}・{cmp['meta']['location']}。症例は架空、正解は開発チームが事前に決めた", 18, 400, C["faint"])]
    return svg("".join(b))


def render(pairs):
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        for name, s in pairs:
            (OUT / f"{name}.svg").write_text(s, encoding="utf-8")
            page.set_content(f'<html><head><link rel="stylesheet" href="{FONT}"><style>body{{margin:0}}</style></head>'
                             f'<body>{s}</body></html>')
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / f"{name}.png"), clip={"x": 0, "y": 0, "width": W, "height": H})
            print(f"  {name}.svg / .png")
        b.close()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    render([("architecture-overview", architecture()), ("05-semantic-verification", verification()),
            ("03x-intent-context", intent_context())])
