"""本物のブラウザエンジン（Playwright の Chromium）で、画面の見た目とレイアウトを確かめる。devtools/devserver.py に対して動かす。

  .venv/bin/python -m devtools.devserver &
  .venv/bin/python -m playwright install chromium
  .venv/bin/python devtools/browser_check.py --out <スクリーンショットを置く場所>

医師は PC（1366×860）、患者はスマホ（390×844・タッチ）。主な状態ごとにスクリーンショットを撮り、次を確かめる:
  - 横スクロールが出ない（PC・スマホ・医師画面をスマホ幅にしたとき）
  - 患者のボタンが押しやすい大きさ（高さ 44px 以上）
  - 追加したカード（意図の確認・照合の確認・未解決事項・未検証の共有）が画面の幅に収まる
  - 長い訳文でもはみ出さない
偽の AI（devtools/fake_ai.py）を使う。見た目の確認であって、AI の性能の確認ではない。
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8765"
A_TEXT = "医生，我是来日本旅游的，昨天刚到。今天在车站突然觉得头晕，眼前发黑，差点晕倒。坐下来休息了一会儿好一些，但现在还是有点不舒服，我有点害怕。"
LONG = "我从三天前开始一直头疼，" * 12 + "现在还是很难受。"
results = []


def ok(cond, what):
    results.append((bool(cond), what))
    print(("PASS " if cond else "FAIL ") + what)


def no_hscroll(page, what):
    w = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    ok(w[0] <= w[1] + 1, f"{what}: 横スクロールなし（{w[0]} ≤ {w[1]}）")


def fits(page, sel, what):
    loc = page.locator(sel).first
    box = loc.bounding_box() if loc.count() else None
    width = page.viewport_size["width"]
    good = box is not None and box["x"] >= -1 and box["x"] + box["width"] <= width + 1
    ok(good, f"{what}: 幅に収まる（{round(box['x']) if box else '-'}〜{round(box['x'] + box['width']) if box else '-'} / {width}）")


def tap_targets(page, sel, what, min_h=44):
    hs = page.evaluate(f"[...document.querySelectorAll({sel!r})].map(e => e.getBoundingClientRect().height)")
    ok(hs and min(hs) >= min_h, f"{what}: 押しやすい高さ（最小 {min(hs) if hs else '-'}px ≥ {min_h}px）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    shot = lambda page, name: page.screenshot(path=str(out / f"{name}.png"), full_page=False)  # noqa: E731

    with sync_playwright() as p:
        b = p.chromium.launch()
        dctx = b.new_context(viewport={"width": 1366, "height": 860}, locale="ja-JP")
        pctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                             device_scale_factor=2, locale="zh-CN")
        for ctx in (dctx, pctx):
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
        d, pt = dctx.new_page(), pctx.new_page()
        d.on("dialog", lambda dlg: dlg.accept())
        errors = []
        for pg, who in ((d, "doctor"), (pt, "patient")):
            pg.on("pageerror", lambda e, who=who: errors.append(f"{who}: {e}"))

        d.goto(f"{BASE}/doctor.html")
        d.wait_for_selector("text=問診を始める")
        d.evaluate("document.fonts.ready")
        shot(d, "01_doctor_start")
        d.click("text=問診を始める")
        d.wait_for_selector(".qr-overlay svg")
        shot(d, "02_doctor_qr")
        sid = d.evaluate("new URLSearchParams(location.search).get('s')")

        pt.goto(f"{BASE}/patient.html?role=patient&s={sid}")
        pt.wait_for_selector(".p-option")
        pt.evaluate("document.fonts.ready")
        shot(pt, "03_patient_language")
        tap_targets(pt, ".p-option", "患者: 言語の選択肢")
        pt.click("text=中文（简体）")
        pt.wait_for_selector("text=开始之前")
        shot(pt, "04_patient_consent")
        no_hscroll(pt, "患者: 同意")
        pt.click("text=同意并开始")
        pt.wait_for_selector("#draft")
        d.wait_for_selector(".d-app")

        # ── A ──
        pt.fill("#draft", A_TEXT)
        shot(pt, "05_patient_speak_draft")
        tap_targets(pt, ".p-speak .mic, .p-input button", "患者: マイクと送信")
        pt.click(".p-input button:has-text('发送')")
        d.wait_for_selector(".d-cands")
        d.click(".d-fact:has-text('めまい')")
        d.wait_for_selector(".d-evidence")
        shot(d, "06_doctor_memo_evidence_candidates")
        no_hscroll(d, "医師: 問診メモと確認候補")
        fits(d, ".d-memo", "医師: 問診メモ")
        d.click(".d-fact:has-text('めまい')")
        d.click(".d-cand .btn-small:has-text('質問を送る')")
        pt.wait_for_selector(".p-doctor:has-text('医生想确认') .p-option")
        shot(pt, "07_patient_clarify_question")
        tap_targets(pt, ".p-doctor .p-option", "患者: 確認質問の選択肢")
        no_hscroll(pt, "患者: 確認質問")
        pt.click(".p-doctor .p-option:has-text('天旋地转')")

        # ── D: 意図の確認と、未検証の共有 ──
        d.fill("#dinput", "痛み止めの注射をしましょうか？")
        d.click(".d-input .send")
        pt.wait_for_selector(".p-doctor:has-text('要给你打止痛针吗')")
        pt.fill("#draft", "没事。")
        pt.click(".p-input button:has-text('发送')")
        pt.wait_for_selector("text=您说的“没事”是什么意思？")
        shot(pt, "08_patient_intent")
        tap_targets(pt, ".p-option", "患者: 意図の選択肢")
        no_hscroll(pt, "患者: 意図の確認")
        d.wait_for_selector(".d-pending:has-text('意味を確認しています')")
        d.click(".d-pending .btn-link:has-text('未検証の内容を見る')")
        d.wait_for_selector(".d-reveal")
        shot(d, "09_doctor_pending_reveal")
        fits(d, ".d-reveal", "医師: 未検証の共有")
        pt.click(".p-option:has-text('可以，请给我打针。')")
        d.wait_for_selector("text=はい、注射をお願いします（同意）。")

        # ── 医師の意図の確認（「大丈夫です。」を文脈なしで…ではなく直後なので、別の問診にはしない）──
        # ── G: 照合で相違が残る → 医師が決める ──
        d.fill("#dinput", "検査の前は8時間何も食べないでください。")
        d.click(".d-input .send")
        d.wait_for_selector(".d-mine .d-tbres.warn")
        shot(d, "10_doctor_review_card")
        fits(d, ".d-mine .d-tbres.warn", "医師: 照合の確認カード")
        no_hscroll(d, "医師: 照合の確認カード")
        d.click(".d-mine .d-tbres.warn .btn-link:has-text('未検証として送る')")
        pt.wait_for_selector(".p-unverified")
        shot(pt, "11_patient_unverified_label")

        # ── E: 未解決事項 ──
        d.fill("#dinput", "この薬は1日2回、朝と夜に1錠ずつ飲んでください。")
        d.click(".d-input .send")
        pt.wait_for_selector(".p-doctor:has-text('这个药一天吃两次')")
        pt.fill("#draft", "早上吃两片就可以了，对吗？")
        pt.click(".p-input button:has-text('发送')")
        d.wait_for_selector(".d-cands:has-text('未解決事項')")
        d.click(".d-cands:has-text('未解決事項') .d-more")
        d.wait_for_timeout(300)
        shot(d, "12_doctor_issues")
        fits(d, ".d-cands:has-text('未解決事項')", "医師: 未解決事項")

        # ── 長い訳文 ──
        pt.fill("#draft", LONG)
        pt.click(".p-input button:has-text('发送')")
        d.wait_for_function("document.querySelector('.d-pt.latest .tr')?.textContent.includes('我从三天前开始')", timeout=20000)
        shot(d, "13_doctor_long_text")
        no_hscroll(d, "医師: 長い訳文")
        shot(pt, "14_patient_after_long")
        no_hscroll(pt, "患者: 長い文")

        # ── 医師画面をスマホ幅に ──
        d.set_viewport_size({"width": 390, "height": 844})
        d.wait_for_timeout(300)
        shot(d, "15_doctor_narrow")
        no_hscroll(d, "医師: スマホ幅")
        d.set_viewport_size({"width": 1366, "height": 860})

        # ── カルテ ──
        d.click("text=カルテにコピー")
        d.wait_for_selector("dialog.d-sheet textarea")
        shot(d, "16_doctor_karte")
        fits(d, "dialog.d-sheet", "医師: カルテ用の文章")
        d.click("dialog .btn-quiet")
        ok(not errors, f"画面のエラーなし {errors[:3]}")
        b.close()
    fails = [w for good, w in results if not good]
    print(f"\n{len(results) - len(fails)} / {len(results)} PASS" + (f"\nFAIL: {fails}" if fails else ""))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
