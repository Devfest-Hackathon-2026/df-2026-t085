"""プレゼン用の画面を、本物のブラウザ（Chromium）で1つの問診として通しながら撮る。

  UNSAID_LIVE=1 UNSAID_VERTEX_PROJECT=<プロジェクト ID> GOOGLE_APPLICATION_CREDENTIALS=<ADC> \\
    .venv/bin/python -m devtools.devserver &                       # Gemini は本物、Firestore はメモリ上の偽物
  .venv/bin/python devtools/capture_presentation.py --out docs/presentation-assets [--mode live|mock]

画面の大きさ（プレゼンの前提）:
  医師  MacBook  2560 × 1664 px（CSS 1280 × 832、2 倍）
  患者  iPhone 16 Pro  1206 × 2622 px（CSS 402 × 874、3 倍）

シナリオ（すべて架空）: 中国語の旅行者のめまい → 追加の確認質問 → 「没事」の意図の確認 → 服薬の説明と理解の確認（相違）→
再説明 → 理解の確認 → 問診メモ・未解決事項・カルテ用の文章。撮った画像と、そのときの状態を manifest.json に書く。
Gemini の出力は回ごとに変わるので、文言は撮るたびに少し違う。
"""
import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8765"
DOCTOR = {"viewport": {"width": 1280, "height": 832}, "device_scale_factor": 2, "locale": "ja-JP"}
PATIENT = {"viewport": {"width": 402, "height": 874}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True,
           "locale": "zh-CN"}
WAIT = 150_000  # 本物の Gemini（意図・訳・照合・訳し直し）を待つ上限（ミリ秒）

A_TEXT = "医生，我是来日本旅游的，昨天刚到。今天在车站突然觉得头晕，眼前发黑，差点晕倒。坐下来休息了一会儿好一些，但现在还是有点不舒服，我有点害怕。"
D_ASK = "痛み止めの注射をしましょうか？"
D_FINE = "没事。"
E_DOSE = "この薬は1日2回、朝と夜に1錠ずつ飲んでください。"
E_WRONG = "早上吃两片就可以了，对吗？"
E_RIGHT = "一天两次，早上和晚上各吃一片，对吗？"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["live", "mock"], required=True, help="manifest に書く（サーバーの UNSAID_LIVE と合わせる）")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    shots, notes = [], []

    def shot(page, name, what, el=None, pad=24):
        path = out / f"{name}.png"
        if el:
            page.locator(el).first.scroll_into_view_if_needed()
            page.wait_for_timeout(200)
            box = page.locator(el).first.bounding_box()
            vw = page.viewport_size
            clip = {"x": max(0, box["x"] - pad), "y": max(0, box["y"] - pad)}
            clip["width"] = min(vw["width"] - clip["x"], box["width"] + 2 * pad)
            clip["height"] = min(vw["height"] - clip["y"], box["height"] + 2 * pad)
            page.screenshot(path=str(path), clip=clip)
        else:
            page.screenshot(path=str(path))
        shots.append({"file": path.name, "shows": what})
        print(f"  {path.name}: {what}")

    def debug(page, name):
        try:
            page.screenshot(path=str(out / f"_debug-{name}.png"))
        except Exception:  # noqa: BLE001
            pass

    with sync_playwright() as p:
        b = p.chromium.launch()
        d = b.new_context(**DOCTOR).new_page()
        pt = b.new_context(**PATIENT).new_page()
        d.on("dialog", lambda dlg: dlg.accept())
        errors = []
        for pg, who in ((d, "doctor"), (pt, "patient")):
            pg.on("pageerror", lambda e, who=who: errors.append(f"{who}: {e}"))
            pg.set_default_timeout(WAIT)

        # ── 開始 ──
        d.goto(f"{BASE}/doctor.html")
        d.click("text=問診を始める")
        d.wait_for_selector(".qr-overlay svg")
        d.evaluate("document.fonts.ready")
        shot(d, "00-doctor-qr", "医師: 問診を始めると QR が出る（患者はスマホで読む）")
        sid = d.evaluate("new URLSearchParams(location.search).get('s')")
        pt.goto(f"{BASE}/patient.html?role=patient&s={sid}")
        pt.click("text=中文（简体）")
        pt.wait_for_selector("text=开始之前")
        pt.evaluate("document.fonts.ready")
        shot(pt, "00-patient-consent", "患者: 言語を選び、AI の利用と記録の削除を確認して始める")
        pt.click("text=同意并开始")
        pt.wait_for_selector("#draft")
        d.wait_for_selector(".d-app")

        # ── Scene A / C: 患者の訴え → 訳・問診メモ・確認候補 ──
        pt.fill("#draft", A_TEXT)
        pt.click(".p-input button:has-text('发送')")
        d.wait_for_selector(".d-cands .d-cand")
        d.wait_for_timeout(800)
        pt.wait_for_timeout(800)
        shot(pt, "02-patient-overview", "患者: 1回の送信で届く。医師が読んだ内容（訳を逆に訳したもの）を自分の言語で見られる")
        shot(d, "01-doctor-overview", "医師: 患者の発言の日本語訳（照合済み）と、原文に根拠のある問診メモ・確認候補")
        d.click(".d-fact:has-text('めまい')")
        d.wait_for_selector(".d-evidence")
        d.wait_for_timeout(300)
        shot(d, "04-clinical-clarification", "医師: 「头晕」の根拠（原文の該当箇所）・未確認の状態・追加の確認質問の候補")
        shot(d, "04a-doctor-memo-evidence", "医師（拡大）: 問診メモの根拠と、確認候補", el=".d-memo")
        d.click(".d-fact:has-text('めまい')")
        d.click(".d-cand .btn-small:has-text('質問を送る')")
        pt.wait_for_selector(".p-doctor .p-option")
        pt.wait_for_timeout(500)
        shot(pt, "04b-patient-clarify-question", "患者: 医師が選んだ確認質問に、選択肢・分からない・どれでもない・自分の言葉で答えられる")
        opt = pt.locator(".p-doctor .p-option", has_text="转")
        (opt.first if opt.count() else pt.locator(".p-doctor .p-option").first).click()
        d.wait_for_selector(".d-memo .tag:has-text('患者回答')")
        d.wait_for_timeout(500)
        shot(d, "04c-doctor-answer-reflected", "医師: 患者の答えが問診メモに反映される（患者回答・医師はまだ確認していない）")

        # ── Scene B: 「没事」の意図の確認 ──
        d.fill("#dinput", D_ASK)
        d.click(".d-input .send")
        pt.wait_for_selector(f".p-doctor:has-text('{'针'}')")
        pt.fill("#draft", D_FINE)
        pt.click(".p-input button:has-text('发送')")
        # 意図の確認の画面（原文「没事」と選択肢）か、医師の画面に届いた（文脈で決まった）か
        pt.wait_for_function("""() => document.querySelector('.p-said')?.textContent.includes('没事')
                                     && document.querySelectorAll('.p-option').length >= 2""", timeout=WAIT) \
            if not d.locator(".d-pt.latest:has-text('没事')").count() else None
        if pt.locator(".p-said:has-text('没事')").count() and pt.locator(".p-option").count() >= 2:
            pt.wait_for_timeout(500)
            shot(pt, "03-intent-clarification", "患者: 「没事」は文脈で意味が決まらない（断る／同意する）。訳す前に本人が意味を選ぶ")
            d.wait_for_selector(".d-pending:has-text('意味を確認しています')")
            d.wait_for_timeout(300)
            shot(d, "03a-doctor-intent-pending", "医師: 患者が意味を確認中。訳はまだ届かない（未確定の訳を送らない）")
            d.click(".d-pending .btn-link:has-text('未検証の内容を見る')")
            d.wait_for_selector(".d-reveal")
            d.wait_for_timeout(300)
            shot(d, "03b-doctor-unverified-reveal", "医師: 緊急時は「未検証」と分かる形で原文を見られる（問診メモには入れない）")
            pick = pt.locator(".p-option", has_text="不")
            (pick.first if pick.count() else pt.locator(".p-option").first).click()
            d.wait_for_selector(".d-pt.latest .d-meta:has-text('本人が意味を確認')")
            d.wait_for_timeout(500)
            shot(d, "03c-doctor-intent-confirmed", "医師: 本人が選んだ意味で訳して届く（「本人が意味を確認」と AI の解釈を区別）")
        else:
            notes.append("「没事」: 本物の Gemini が文脈で意味を決めた（意図の確認は出なかった）")

        # ── Scene E / F: 服薬の説明 → 理解の確認（相違）→ 未解決事項 ──
        d.fill("#dinput", E_DOSE)
        d.click(".d-input .send")
        d.wait_for_selector(".d-mine.last:has-text('送信済み')")
        d.click(".d-mine.last .tb:has-text('理解を確認')")
        pt.wait_for_selector(".p-doctor:has-text('确认理解')")
        pt.wait_for_timeout(500)
        shot(pt, "06a-patient-teachback-request", "患者: 医師の説明を、自分の言葉で言い直すよう頼まれる（理解の確認）")
        pt.fill("#draft", E_WRONG)
        pt.click(".p-input button:has-text('发送')")
        d.wait_for_selector(".d-pt .d-tbres.warn")
        d.wait_for_timeout(1500)
        shot(d, "06-teachback-mismatch", "医師: 説明（1日2回・1錠ずつ）と患者の理解（朝に2錠）の食い違いを、項目ごとに示す。医師が再説明・確認を決める")
        shot(d, "06b-teachback-card", "医師（拡大）: 理解の確認の結果（AI の判定。医師が決める）", el=".d-pt .d-tbres.warn")
        if d.locator(".d-cands:has-text('未解決事項')").count():
            d.locator(".d-cands:has-text('未解決事項') .d-more").first.click()
            d.wait_for_timeout(500)
            shot(d, "07-conversation-auditor", "医師: Conversation Auditor が会話全体から見つけた未解決事項（根拠の発言つき。解決は医師だけ）")
            shot(d, "07a-auditor-card", "医師（拡大）: 未解決事項と根拠の発言", el=".d-cands:has-text('未解決事項')")
        else:
            notes.append("未解決事項: Auditor が理解の相違を出さなかった")

        # ── 再説明 → 再確認 → 医師が確認 ──
        d.click(".d-pt .d-tbres.warn .btn-link:has-text('再説明する')")
        d.wait_for_function("document.querySelector('#dinput').value.trim().length > 0")
        notes.append("再説明の文（AI の提案を医師が送った）: " + d.input_value("#dinput"))
        d.click(".d-input .send")
        try:
            d.wait_for_selector(".d-mine.last .tb:has-text('理解を確認')")
        except Exception:
            debug(d, "reexplain")
            debug(pt, "reexplain-patient")
            raise
        d.click(".d-mine.last .tb:has-text('理解を確認')")
        pt.wait_for_function("document.querySelectorAll('.p-doctor').length > 0 && [...document.querySelectorAll('.p-doctor')].some(e => e.textContent.includes('确认理解'))")
        pt.wait_for_timeout(1500)
        pt.fill("#draft", E_RIGHT)
        pt.click(".p-input button:has-text('发送')")
        d.wait_for_selector(".d-tbres.ok")
        d.wait_for_timeout(800)
        if d.locator(".d-tbres.ok .btn-link:has-text('理解を確認済みにする')").count():
            d.click(".d-tbres.ok .btn-link:has-text('理解を確認済みにする')")
            d.wait_for_timeout(800)
        shot(d, "06c-teachback-resolved", "医師: 再説明 → 再確認で一致 → 医師が理解を確認（前の相違も解決になる）")

        # ── Scene F: 医師が確認 → 最終の問診メモ・カルテ用の文章 ──
        d.click(".d-fact:has-text('めまい')")
        d.click(".d-evidence .btn-small:has-text('確認済みにする')")
        d.wait_for_selector('.d-memo .check[aria-label="医師確認済み"]')
        d.click(".d-fact:has-text('めまい')")
        d.wait_for_timeout(500)
        shot(d, "08-clinical-summary", "医師: 最終の問診メモ（医師確認済み・患者回答・未確認・言及なしを区別。根拠は原文へ）")
        shot(d, "08a-memo-panel", "医師（拡大）: 問診メモ", el=".d-memo")
        d.click("text=カルテにコピー")
        d.wait_for_selector("dialog.d-sheet textarea")
        d.wait_for_timeout(300)
        shot(d, "08b-karte-text", "医師: カルテ用の文章（医師が編集してから貼る。原文に無い病名は足さない）")
        d.click("dialog .btn-quiet")
        shot(pt, "02a-patient-after", "患者: 会話の終わりの画面（自分の記録を開ける）")
        state = d.evaluate("""() => ({memo: document.querySelector('.d-memo')?.innerText.slice(0, 2000),
                                      log: [...document.querySelectorAll('.d-pt .tr, .d-mine .tr')].map(e => e.innerText).slice(-12)})""")
        b.close()

    manifest = {"captured_at": time.strftime("%Y-%m-%d %H:%M %Z"), "ai": a.mode,
                "ai_note": "本物の Gemini（評価用のプロジェクト）。Firestore はメモリ上の偽物" if a.mode == "live"
                else "偽の AI（devtools/fake_ai.py の決まった答え）。モデルの性能ではない",
                "doctor_px": "2560x1664", "patient_px": "1206x2622", "scenario": "架空（中国語の旅行者のめまい・服薬の説明）",
                "page_errors": errors, "notes": notes, "shots": shots, "final_state": state}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(shots)} 枚。画面のエラー {len(errors)}。{notes}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
