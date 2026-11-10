"""実際の診察の流れを、医師と患者の 2 つのブラウザで最初から最後まで操作し、画面の状態が変わる前後をすべて撮る。

  UNSAID_LIVE=1 UNSAID_VERTEX_PROJECT=<プロジェクト ID> GOOGLE_APPLICATION_CREDENTIALS=<ADC> .venv/bin/python -m devtools.devserver &
  .venv/bin/python devtools/capture_clinical_flow.py --out docs/presentation-assets/clinical-flow --mode live
  # 補助デモ（人工的に注入した誤訳・意図の確認）: サーバーを UNSAID_INJECT_ERRORS=1 で起動し直して
  .venv/bin/python devtools/capture_clinical_flow.py --out docs/presentation-assets/clinical-flow/supplementary --mode live --scenario supplementary

医師 2560×1664（CSS 1280×832、DPR 2）、患者 1206×2622（iPhone 16 Pro 相当: CSS 402×874、DPR 3、モバイル）。
固定の sleep ではなく画面の状態を待つ（文字が描かれ切るまでの短い待ちだけ入れる）。DOM は書き換えない（問診メモのスクロールだけ）。
撮った画像・操作・画面の状態は index.json に書く（SCREENSHOT_INDEX.md の元）。失敗したら、その場所と画面を残して止まる。
"""
import argparse
import json
import struct
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8765"
DOCTOR = {"viewport": {"width": 1280, "height": 832}, "device_scale_factor": 2, "locale": "ja-JP"}
PATIENT = {"viewport": {"width": 402, "height": 874}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True,
           "locale": "zh-CN",
           "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                         "Version/18.0 Mobile/15E148 Safari/604.1"}
SIZES = {"doctor": (2560, 1664), "patient": (1206, 2622)}
WAIT = 180_000

MAIN = {
    "first": "医生，我今天在车站突然心慌，胸口像堵住了一样，浑身发虚。我以为只是水土不服，可坐下来休息了一会儿，还是觉得不太对劲。",
    "answer1": "我感觉心脏突然跳得很快，胸口也很闷。坐着休息了大约十分钟，还是没有好转。",
    "followup": "今も胸が詰まる感じや息苦しさは続いていますか？意識が遠くなったり、気を失ったりしたことはありますか？",
    "answer2": "现在胸口还是闷，呼吸有点费力。没有晕倒过，也没有失去意识。",
    "explain": "今はそのまま座っていてください。すぐに医療スタッフを呼びます。ご自身で移動せず、スタッフの案内を待ってください。",
    "tb_wrong": "我先自己走去找护士，对吗？",
    "tb_right": "我留在这里坐着等，医生会马上叫医护人员过来，我不会自己走动。",
}
SUPP = {
    "lossy": "検査の前は8時間何も食べないでください。",           # 人工的に注入した誤訳（8時間が落ちる）
    "lossy_rephrase": "検査を受ける前の8時間は、何も食べないでください。",
    "pt_lossy": "我对青霉素过敏。",                                  # 人工的に注入した誤訳（否定が反転する）
    "pt_rephrase": "我对青霉素有过敏反应。",
    "offer": "痛み止めの注射をしましょうか？",
    "fine": "没事。",
}


class Flow:
    def __init__(self, out, mode):
        self.out, self.mode = out, mode
        self.n = 0
        self.index = []
        self.notes = []

    def shot(self, page, device, slug, action, state, info, note=""):
        name = f"{self.n:02d}-{device}-{slug}.png"
        page.screenshot(path=str(self.out / name))
        self.index.append({"no": self.n, "file": name, "device": device, "action": action, "state": state,
                           "info": info, "ai": self.mode, "note": note})
        print(f"  {name}  [{state}] {action}")
        self.n += 1

    def note(self, s):
        self.notes.append(s)
        print("  note:", s)


def settle(page, ms=600):
    page.wait_for_timeout(ms)  # 描画（フォント・アニメーション）が落ち着くまでの短い待ち


def memo_top(d):
    d.evaluate("document.querySelector('.d-memo') && (document.querySelector('.d-memo').scrollTop = 0)")


def count(page, sel):
    return page.locator(sel).count()


def patient_send(f, pt, d, text, slug, what, typed_state="入力済み・未送信"):
    """患者が入力 → 撮る → 送信 → 処理中を撮る → 結果（届いた／意図の確認／照合の確認）まで待つ。結果の種類を返す。"""
    n_pt = count(d, ".d-pt")
    pt.fill("#draft", text)
    settle(pt, 300)
    f.shot(pt, "patient", f"{slug}-typed", f"{what}を入力（まだ送らない）", typed_state, text)
    pt.click(".p-input button:has-text('发送')")
    try:
        pt.wait_for_selector(".p-screen .spinner", timeout=8000)
        f.shot(pt, "patient", f"{slug}-checking", "「发送」を押した直後", "処理中（意図の解析・翻訳・照合）",
               "患者には確認中の表示。まだ医師に届いていない")
        if count(d, ".d-pending"):
            f.shot(d, "doctor", f"{slug}-pending", "患者が送信した直後の医師の画面", "処理中（医師にはまだ届かない）",
                   "医師には「確認中」の表示だけ。訳は未確定なので出ない")
    except Exception:  # noqa: BLE001
        f.note(f"{slug}: 処理中の画面は撮れなかった（すぐ終わった）")
    pt.wait_for_function("""() => !document.querySelector('.p-screen .spinner')""", timeout=WAIT)
    settle(pt)
    if pt.locator(".p-said").count() and pt.locator(".p-option").count() >= 2 and pt.locator(".p-question").count():
        return "intent"
    if pt.locator("text=翻译可能和你的意思不一样").count():
        return "review"
    d.wait_for_function(f"document.querySelectorAll('.d-pt').length > {n_pt}", timeout=WAIT)
    settle(d)
    return "sent"


def doctor_send(f, d, text, slug, what):
    """医師が入力 → 撮る → 送る → 照合中を撮る → 結果（送信済み／意図の確認／照合の確認）まで待つ。"""
    n = count(d, ".d-mine")
    d.fill("#dinput", text)
    settle(d, 300)
    f.shot(d, "doctor", f"{slug}-typed", f"医師が{what}を入力（まだ送らない）", "入力済み・未送信", text)
    d.click(".d-input .send")
    d.wait_for_function(f"document.querySelectorAll('.d-mine').length > {n}", timeout=WAIT)
    try:
        d.wait_for_selector(".d-mine.last .st.wait", timeout=5000)
        f.shot(d, "doctor", f"{slug}-checking", "「送る」を押した直後", "処理中（意図の解析・翻訳・照合）",
               "医師の発言は「確認中…／訳を照合中…」。患者にはまだ届いていない")
    except Exception:  # noqa: BLE001
        f.note(f"{slug}: 照合中の表示は撮れなかった（すぐ終わった）")
    d.wait_for_function("""() => { const m = [...document.querySelectorAll('.d-mine')].at(-1);
        return m && (m.textContent.includes('送信済み') || m.querySelector('.d-tbres.warn') || m.querySelector('.d-intent')
                     || document.querySelector('.d-intent')); }""", timeout=WAIT)
    settle(d)
    last = d.locator(".d-mine").last
    if last.locator(".d-tbres.warn").count():
        return "review"
    if d.locator(".d-intent").count():
        return "intent"
    return "sent"


def wait_patient_doctor_msg(pt, needle_count):
    pt.wait_for_function(f"document.querySelectorAll('.p-doctor').length >= {needle_count}", timeout=WAIT)
    settle(pt)


def start(f, d, pt):
    d.goto(f"{BASE}/doctor.html")
    d.wait_for_selector("text=問診を始める")
    d.evaluate("document.fonts.ready")
    settle(d)
    f.shot(d, "doctor", "start", "医師がアプリを開いた直後", "入力前（問診の開始前）", "「問診を始める」だけがある")
    d.click("text=問診を始める")
    d.wait_for_selector(".qr-overlay svg")
    settle(d)
    f.shot(d, "doctor", "qr", "「問診を始める」を押した", "QR を表示（患者の参加待ち）", "患者がスマホで読む QR と説明")
    sid = d.evaluate("new URLSearchParams(location.search).get('s')")
    pt.goto(f"{BASE}/patient.html?role=patient&s={sid}")
    pt.wait_for_selector(".p-option")
    pt.evaluate("document.fonts.ready")
    settle(pt)
    f.shot(pt, "patient", "join-language", "QR のリンクを開いた", "参加前（言語の選択）", "患者の言語を選ぶ")
    pt.click("text=中文（简体）")
    pt.wait_for_selector("text=开始之前")
    settle(pt)
    f.shot(pt, "patient", "consent", "中文を選んだ", "同意前", "AI で訳すこと・間違えうること・診断しないこと・終わったら消えること")
    pt.click("text=同意并开始")
    pt.wait_for_selector("#draft")
    d.wait_for_selector(".d-app")
    settle(pt)
    settle(d)
    f.shot(pt, "patient", "connected", "「同意并开始」を押した", "接続済み・入力前", "話す画面（マイクと入力欄）")
    f.shot(d, "doctor", "connected", "患者が参加した", "接続済み・入力前", "QR が閉じ、「患者（中国語）と接続中」")
    return sid


def main_flow(f, d, pt):
    sid = start(f, d, pt)
    f.notes.append(f"session id: {sid}（医師・患者とも同じ）")

    # ── Phase 2: 最初の訴え ──
    r = patient_send(f, pt, d, MAIN["first"], "first", "最初の訴え")
    if r == "intent":
        f.shot(pt, "patient", "first-intent", "Source Agent が意味の確認を求めた", "意図確認待ち（選ぶ前）", "選択肢")
        f.note("最初の訴えで意図の確認が出た")
    elif r == "review":
        f.shot(pt, "patient", "first-review", "照合で相違が残った", "送信保留（本人の確認待ち）", "")
        f.note("最初の訴えで照合の確認が出た")
    else:
        f.note("最初の訴え: Source Agent は意図の確認を求めなかった（心慌などの医学的な曖昧さは、翻訳前の意図ではなく、"
               "届いた後に Meaning Engine が医師への確認候補にする設計。患者に先に聞かない）")
    memo_top(d)
    settle(d)
    f.shot(d, "doctor", "first-received", "患者の訴えが届いた", "受信済み（照合済みの訳）",
           "日本語訳・照合の状態・原文に根拠のある問診メモ（未確認）・確認候補")
    pt.wait_for_function("document.body.textContent.includes('医生看到的是')", timeout=WAIT)
    settle(pt, 1500)
    f.shot(pt, "patient", "first-delivered", "送信後の患者の画面", "送信済み・医師が表示（医生已读）",
           "医師が読んだ内容（訳を中国語に戻したもの）と「不对？重新说」")

    # ── Phase 3: 追加問診（確認候補）──
    d.locator(".d-cands").first.scroll_into_view_if_needed()
    settle(d)
    f.shot(d, "doctor", "candidates", "問診メモの確認候補を見る", "確認候補（AI の提案。まだ聞いていない）",
           "曖昧な語・なぜ確認が必要か・質問と選択肢。送る／編集／保留／無視は医師が選ぶ")
    fact = d.locator(".d-memo .d-fact").first
    fact.click()
    d.wait_for_selector(".d-evidence")
    settle(d)
    f.shot(d, "doctor", "evidence", "問診メモの項目を開いた", "未確認（根拠の表示）", "原文の該当箇所・訳・状態・履歴")
    fact.click()
    cand = d.locator(".d-cand").filter(has=d.locator(".btn-link:has-text('編集')")).first
    cand.locator(".btn-link:has-text('編集')").click()
    d.wait_for_selector("textarea[id^='edit-']")
    cand = d.locator(".d-cand").filter(has=d.locator("textarea[id^='edit-']")).first
    cand.scroll_into_view_if_needed()
    settle(d)
    f.shot(d, "doctor", "question-edit", "確認候補の「編集」を押した", "医師が質問を選び・編集中（未送信）",
           "AI の質問案を医師が直せる。送ると Safety Gate を通る")
    n_q = count(pt, ".p-doctor")
    cand.locator(".btn-small:has-text('質問を送る')").click()
    try:
        d.wait_for_selector(".d-mine.last .st.wait", timeout=5000)
        f.shot(d, "doctor", "question-checking", "医師が質問を送った直後", "処理中（翻訳・照合）",
               "編集した質問は訳し直して照合してから届く。まだ患者には出ない")
    except Exception:  # noqa: BLE001
        f.note("質問の照合中の表示は撮れなかった")
    d.wait_for_function("""() => { const m = [...document.querySelectorAll('.d-mine')].at(-1);
        return m && (m.textContent.includes('送信済み') || m.querySelector('.d-tbres.warn')); }""", timeout=WAIT)
    settle(d)
    if d.locator(".d-mine").last.locator(".d-tbres.warn").count():
        f.shot(d, "doctor", "question-review", "質問の訳の照合で相違", "送信保留（医師の確認待ち）", "")
        f.note("編集した質問で照合の確認が出た → 未検証として送らず、そのまま記録")
        return
    f.shot(d, "doctor", "question-sent", "質問が届いた", "送信済み", "「確認質問」の吹き出しと送信済み")
    wait_patient_doctor_msg(pt, n_q + 1)
    f.shot(pt, "patient", "question-received", "医師の質問が届いた", "受信済み・回答前", "中国語の質問・選択肢・分からない・どれでもない・自分の言葉で")

    r = patient_send(f, pt, d, MAIN["answer1"], "answer1", "質問への答え")
    if r != "sent":
        f.note(f"答え 1 で {r}")
    memo_top(d)
    settle(d)
    f.shot(d, "doctor", "answer1-received", "患者の答えが届いた", "受信済み（患者回答）",
           "答えの訳・「患者が回答」・問診メモの更新（医師はまだ確認していない）")
    settle(pt, 1200)
    f.shot(pt, "patient", "answer1-delivered", "答えを送った後", "送信済み", "医師が読んだ内容と「修改回答」")

    # ── Phase 4: 会話の監査と、医師の追加の確認 ──
    if count(d, ".d-cands:has-text('未解決事項')"):
        d.locator(".d-cands:has-text('未解決事項')").first.scroll_into_view_if_needed()
        settle(d)
        f.shot(d, "doctor", "auditor-open", "未解決事項を見る", "未解決事項（AI の指摘・医師が決める）", "")
    else:
        f.note("この時点で Conversation Auditor の未解決事項は出なかった")
    memo_top(d)
    # 追加の質問は「胸の詰まり（胸口像堵住）」の項目について。動悸（胸騒ぎ）の項目に付けない
    facts = d.locator(".d-memo .d-fact")
    pick = None
    for i in range(facts.count()):
        t = facts.nth(i).inner_text()
        if any(w in t for w in ("圧迫", "つかえ", "詰ま", "閉塞")) and "動悸" not in t:
            pick = facts.nth(i)
            break
    if pick is None:
        f.note("胸の詰まりの項目が問診メモに無かった → 最初の項目に質問を付けた")
        pick = facts.first
    fact = pick
    fact_label = fact.inner_text()
    if not count(d, ".d-evidence"):
        fact.click()
    d.wait_for_selector(".d-evidence")
    d.click(".d-evidence .btn-link:has-text('患者に質問する')")
    d.wait_for_selector("#ask-text")
    d.fill("#ask-text", MAIN["followup"])
    d.locator(".d-ask").scroll_into_view_if_needed()
    settle(d)
    f.shot(d, "doctor", "followup-typed", f"「{fact_label}」について医師が自分で質問を書いた", "入力済み・未送信", MAIN["followup"])
    n_q = count(pt, ".p-doctor")
    d.locator(".d-ask .btn-small:has-text('質問を送る')").click()
    try:
        d.wait_for_selector(".d-mine.last .st.wait", timeout=5000)
        f.shot(d, "doctor", "followup-checking", "医師が質問を送った直後", "処理中（翻訳・照合）", "まだ患者には出ない")
    except Exception:  # noqa: BLE001
        f.note("追加の質問の照合中の表示は撮れなかった")
    d.wait_for_function("""() => { const m = [...document.querySelectorAll('.d-mine')].at(-1);
        return m && (m.textContent.includes('送信済み') || m.querySelector('.d-tbres.warn')); }""", timeout=WAIT)
    settle(d)
    if d.locator(".d-mine").last.locator(".d-tbres.warn").count():
        f.shot(d, "doctor", "followup-review", "追加の質問の訳の照合で相違", "送信保留（医師の確認待ち）", "")
        f.note("追加の質問で照合の確認が出た → ここで止める")
        return
    f.shot(d, "doctor", "followup-sent", "追加の質問が届いた", "送信済み", "")
    wait_patient_doctor_msg(pt, n_q + 1)
    f.shot(pt, "patient", "followup-received", "追加の質問が届いた", "受信済み・回答前", "")
    patient_send(f, pt, d, MAIN["answer2"], "answer2", "追加の質問への答え")
    memo_top(d)
    settle(d)
    f.shot(d, "doctor", "answer2-received", "患者の答えが届いた", "受信済み（患者回答）", "胸の症状が続いていること・失神はないこと")
    d.locator(".d-memo").evaluate("e => e.scrollTop = e.scrollHeight")
    settle(d)
    f.shot(d, "doctor", "memo-after-questions", "問診メモの下の方（確認候補・未解決事項）", "問診メモの更新後",
           "症状・経過・本人の解釈・未確認の項目・未解決事項")
    memo_top(d)

    # ── Phase 5: 医師の説明と teach-back ──
    n_q = count(pt, ".p-doctor")
    r = doctor_send(f, d, MAIN["explain"], "explain", "説明")
    if r != "sent":
        f.shot(d, "doctor", "explain-hold", "説明の送信が保留された", "送信保留（医師の確認待ち）", r)
        f.note(f"説明で {r}（医師の確認待ち）。ここで止める")
        return
    f.shot(d, "doctor", "explain-sent", "説明が届いた", "送信済み", "照合済みの中国語訳で届く")
    wait_patient_doctor_msg(pt, n_q + 1)
    f.shot(pt, "patient", "explain-received", "医師の説明が届いた", "受信済み", "中国語の説明")
    d.click(".d-mine.last .tb:has-text('理解を確認')")
    wait_patient_doctor_msg(pt, n_q + 2)
    pt.wait_for_function("[...document.querySelectorAll('.p-doctor')].some(e => e.textContent.includes('确认理解'))", timeout=WAIT)
    settle(pt)
    f.shot(pt, "patient", "teachback-prompt", "医師が「理解を確認」を押した", "理解の確認（回答前）", "自分の言葉で言い直すよう頼まれる")
    n_tb = count(d, ".d-pt .d-tbres")
    patient_send(f, pt, d, MAIN["tb_wrong"], "teachback-wrong", "理解の言い直し（誤り）")
    d.wait_for_function(f"document.querySelectorAll('.d-pt .d-tbres').length > {n_tb}", timeout=WAIT)
    settle(d, 1200)
    res = d.locator(".d-pt .d-tbres").last
    res.scroll_into_view_if_needed()
    settle(d)
    mism = "warn" in (res.get_attribute("class") or "")
    f.shot(d, "doctor", "teachback-result", "患者の言い直しが届いた", "意味不一致" if mism else "理解の確認の結果（AI の判定）",
           "説明・患者の理解・食い違う点。再説明／理解を確認済みにする（医師が決める）")
    if not mism:
        f.note("teach-back: 誤った言い直しを AI が食い違いと判定しなかった（判定: " + res.inner_text()[:80] + "）")
        return
    if count(d, ".d-cands:has-text('未解決事項')"):
        d.locator(".d-cands:has-text('未解決事項')").first.scroll_into_view_if_needed()
        settle(d)
        f.shot(d, "doctor", "auditor-mismatch", "未解決事項を見る", "未解決事項（理解の相違）", "根拠の発言つき")
    d.locator(".d-pt .d-tbres.warn .btn-link:has-text('再説明する')").last.click()
    d.wait_for_function("document.querySelector('#dinput').value.trim().length > 0")
    re_text = d.input_value("#dinput")
    f.notes.append("再説明の文（AI の提案が入力欄に入り、医師が送った）: " + re_text)
    n_q = count(pt, ".p-doctor")
    r = doctor_send(f, d, re_text, "reexplain", "再説明（AI の提案を入力欄で確認）")
    if r != "sent":
        f.shot(d, "doctor", "reexplain-hold", "再説明の送信が保留された", "送信保留", r)
        return
    f.shot(d, "doctor", "reexplain-sent", "再説明が届いた", "送信済み", "")
    wait_patient_doctor_msg(pt, n_q + 1)
    f.shot(pt, "patient", "reexplain-received", "再説明が届いた", "受信済み", "")
    d.click(".d-mine.last .tb:has-text('理解を確認')")
    wait_patient_doctor_msg(pt, n_q + 2)
    pt.wait_for_function("[...document.querySelectorAll('.p-doctor')].at(-1)?.textContent.includes('确认理解')", timeout=WAIT)
    settle(pt)
    f.shot(pt, "patient", "teachback-prompt2", "医師がもう一度「理解を確認」", "理解の確認（回答前）", "")
    n_tb = count(d, ".d-pt .d-tbres")
    patient_send(f, pt, d, MAIN["tb_right"], "teachback-right", "理解の言い直し（正しい）")
    d.wait_for_function(f"document.querySelectorAll('.d-pt .d-tbres').length > {n_tb}", timeout=WAIT)
    settle(d, 1200)
    res = d.locator(".d-pt .d-tbres").last
    ok = "ok" in (res.get_attribute("class") or "")
    f.shot(d, "doctor", "teachback-result2", "正しい言い直しが届いた", "理解一致" if ok else "理解の確認の結果（AI の判定）", "")
    if ok and count(d, ".d-tbres.ok .btn-link:has-text('理解を確認済みにする')"):
        d.locator(".d-tbres.ok .btn-link:has-text('理解を確認済みにする')").last.click()
        d.wait_for_selector(".d-tbres.ok .badge-ok:has-text('医師が理解を確認')")
        settle(d)
        f.shot(d, "doctor", "teachback-confirmed", "医師が「理解を確認済みにする」", "確認済み（医師）", "前の相違も解決になる")
    elif not ok:
        f.note("teach-back: 正しい言い直しを一致と判定しなかった（判定: " + res.inner_text()[:80] + "）")

    # ── Phase 6: 医師の確認・最終の問診メモ・カルテ・終了 ──
    memo_top(d)
    if count(d, ".d-evidence"):  # 前に開いた項目が開いたままなら閉じる
        d.locator(".d-memo .d-fact[aria-expanded='true']").first.click()
        d.wait_for_function("!document.querySelector('.d-evidence')")
    fact = d.locator(".d-memo .d-fact").first
    fact.click()
    d.wait_for_selector(".d-evidence")
    settle(d)
    f.shot(d, "doctor", "confirm-before", "問診メモの項目を開いた", "医師確認待ち", "原文・訳・状態・履歴（患者の答え）")
    d.click(".d-evidence .btn-small:has-text('確認済みにする')")
    d.wait_for_selector('.d-memo .check[aria-label="医師確認済み"]')
    settle(d)
    f.shot(d, "doctor", "confirm-after", "「確認済みにする」を押した", "確認済み（医師）", "履歴に「医師が確認」")
    fact.click()
    memo_top(d)
    settle(d)
    f.shot(d, "doctor", "summary-top", "最終の問診メモ（上）", "最終", "症状・経過・本人の解釈・医師確認済み/患者回答/未確認")
    d.locator(".d-memo").evaluate("e => e.scrollTop = e.scrollHeight")
    settle(d)
    f.shot(d, "doctor", "summary-bottom", "最終の問診メモ（下）", "最終", "未解決事項・確認候補・カルテにコピー・診察を終える")
    d.click("text=カルテにコピー")
    d.wait_for_selector("dialog.d-sheet textarea")
    settle(d)
    f.shot(d, "doctor", "karte", "「カルテにコピー」を押した", "カルテ用の下書き（医師が編集してから貼る）", "")
    d.locator("dialog.d-sheet textarea").evaluate("e => e.scrollTop = e.scrollHeight")
    settle(d)
    f.shot(d, "doctor", "karte-bottom", "カルテ用の文章の下の方", "カルテ用の下書き", "未確認事項・理解の確認")
    d.click("dialog .btn-quiet")
    settle(pt)
    f.shot(pt, "patient", "before-end", "診察の終わりの前の患者の画面", "受信済み", "")
    d.locator(".d-memo").evaluate("e => e.scrollTop = e.scrollHeight")
    d.click("button:has-text('診察を終える')")
    pt.wait_for_selector("text=问诊已结束", timeout=WAIT)
    settle(pt)
    settle(d)
    f.shot(d, "doctor", "ended", "「診察を終える」を押した", "終了（記録を削除）", "")
    f.shot(pt, "patient", "ended", "医師が診察を終えた", "終了（記録を削除）", "患者の画面も終了")


def supplementary_flow(f, d, pt):
    """補助デモ: 人工的に注入した誤訳（医師→患者・患者→医師）と、意図の確認（没事）。本物の Gemini の誤訳ではない。"""
    start(f, d, pt)
    # 1) 医師の説明の訳で「8時間」が落ちた（人工的に注入）→ 照合で相違 → 自動では送らない → 言い直す
    r = doctor_send(f, d, SUPP["lossy"], "lossy", "説明（訳に人工的な誤りを注入）")
    if r == "review":
        card = d.locator(".d-mine").last.locator(".d-tbres.warn")
        card.scroll_into_view_if_needed()
        settle(d)
        f.shot(d, "doctor", "lossy-review", "照合で相違（人工的に注入した誤訳）", "送信保留（医師の確認待ち）",
               "どこが違うか・患者にはまだ届いていない・言い直す／未検証として送る", "誤訳は人工的に注入（UNSAID_INJECT_ERRORS）")
        f.shot(pt, "patient", "lossy-not-delivered", "その間の患者の画面", "未受信（届いていない）", "何も届いていない")
        card.locator(".btn-link:has-text('言い直す')").click()
        d.wait_for_function("document.querySelector('#dinput').value.trim().length > 0")
        settle(d)
        f.shot(d, "doctor", "lossy-rephrase", "「言い直す」を押した", "再入力（未送信）", "元の文が入力欄に戻る")
        n_q = count(pt, ".p-doctor")
        r2 = doctor_send(f, d, SUPP["lossy_rephrase"], "lossy-fixed", "言い直した説明")
        f.shot(d, "doctor", "lossy-fixed-result", "言い直した説明", "送信済み" if r2 == "sent" else r2, "")
        if r2 == "sent":
            wait_patient_doctor_msg(pt, n_q + 1)
            f.shot(pt, "patient", "lossy-fixed-received", "言い直した説明が届いた", "受信済み", "")
    else:
        f.note(f"注入した誤訳で照合の確認が出なかった（{r}）")
    # 2) 患者の訴えの訳で否定が反転した（人工的に注入）→ 患者が言い直す
    r = patient_send(f, pt, d, SUPP["pt_lossy"], "pt-lossy", "訴え（訳に人工的な誤りを注入）")
    if r == "review":
        f.shot(pt, "patient", "pt-lossy-review", "照合で相違（人工的に注入した誤訳）", "送信保留（本人の確認待ち）",
               "医生会看到（医師が読む内容）と、换个说法／仍然发送", "誤訳は人工的に注入")
        f.shot(d, "doctor", "pt-lossy-waiting", "その間の医師の画面", "未受信（患者が確認中）", "訳は出ない。「未検証の内容を見る」だけ")
        pt.click("button:has-text('换个说法')")
        pt.wait_for_selector("#draft")
        settle(pt)
        f.shot(pt, "patient", "pt-lossy-rephrase", "「换个说法」を押した", "再入力（未送信）", "元の文が入力欄に戻る")
        patient_send(f, pt, d, SUPP["pt_rephrase"], "pt-fixed", "言い直した訴え")
        settle(d)
        f.shot(d, "doctor", "pt-fixed-received", "言い直した訴えが届いた", "受信済み", "")
    else:
        f.note(f"患者側の注入した誤訳で照合の確認が出なかった（{r}）")
    # 3) 意図の確認（没事）
    n_q = count(pt, ".p-doctor")
    doctor_send(f, d, SUPP["offer"], "offer", "提案")
    wait_patient_doctor_msg(pt, n_q + 1)
    r = patient_send(f, pt, d, SUPP["fine"], "fine", "「没事」")
    if r == "intent":
        f.shot(pt, "patient", "fine-intent", "Source Agent が意味の確認を求めた", "意図確認待ち（選ぶ前）", "断る・同意・痛くない など")
        f.shot(d, "doctor", "fine-intent-waiting", "その間の医師の画面", "意図確認待ち（医師には訳が出ない）", "")
        pick = pt.locator(".p-option", has_text="不")
        (pick.first if pick.count() else pt.locator(".p-option").first).click()
        d.wait_for_selector(".d-pt.latest .d-meta:has-text('本人が意味を確認')", timeout=WAIT)
        settle(d)
        settle(pt)
        f.shot(pt, "patient", "fine-chosen", "意味を選んだ", "送信済み", "")
        f.shot(d, "doctor", "fine-confirmed", "本人が選んだ意味で届いた", "受信済み（本人が意味を確認）", "")
    else:
        f.note(f"「没事」で意図の確認が出なかった（{r}）")


def png_size(path):
    with open(path, "rb") as fh:
        fh.read(16)
        return struct.unpack(">II", fh.read(8))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["live", "mock"], required=True)
    ap.add_argument("--scenario", choices=["main", "supplementary"], default="main")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("[0-9][0-9]-*.png"):
        old.unlink()
    f = Flow(out, a.mode)
    started = time.strftime("%Y-%m-%d %H:%M %Z")
    errors, failed = [], None
    with sync_playwright() as p:
        b = p.chromium.launch()
        d = b.new_context(**DOCTOR).new_page()
        pt = b.new_context(**PATIENT).new_page()
        d.on("dialog", lambda dlg: dlg.accept())
        for pg, who in ((d, "doctor"), (pt, "patient")):
            pg.on("pageerror", lambda e, who=who: errors.append(f"{who}: {e}"))
            pg.on("console", lambda m, who=who: errors.append(f"{who} console: {m.text}") if m.type == "error" else None)
            pg.set_default_timeout(WAIT)
        try:
            (main_flow if a.scenario == "main" else supplementary_flow)(f, d, pt)
        except Exception as e:  # noqa: BLE001
            failed = f"{type(e).__name__}: {str(e)[:300]}"
            traceback.print_exc()
            for pg, who in ((d, "doctor"), (pt, "patient")):
                try:
                    pg.screenshot(path=str(out / f"_failed-{who}.png"))
                except Exception:  # noqa: BLE001
                    pass
        b.close()
    bad = [i["file"] for i in f.index if png_size(out / i["file"]) != SIZES[i["device"]]]
    (out / "index.json").write_text(json.dumps({
        "captured_at": started, "scenario": a.scenario, "ai": a.mode,
        "ai_note": "本物の Gemini（評価用のプロジェクト kumu-devfest-2026）。Firestore はメモリ上の偽物" if a.mode == "live"
        else "偽の AI（決まった答え）", "inputs": MAIN if a.scenario == "main" else SUPP, "failed_at": failed,
        "page_errors": errors, "size_mismatch": bad, "notes": f.notes, "shots": f.index}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(f.index)} 枚。失敗: {failed}。画面のエラー {len(errors)}。大きさの不一致 {bad}")
    sys.exit(1 if failed or bad else 0)


if __name__ == "__main__":
    main()
