"""capture_clinical_flow.py が書いた index.json から、SCREENSHOT_INDEX.md の表を作る（画像を開かなくても、どの瞬間か分かるように）。

  .venv/bin/python devtools/flow_index.py docs/presentation-assets/clinical-flow
"""
import json
import struct
import sys
from pathlib import Path

DEVICE = {"doctor": "医師（MacBook 2560×1664）", "patient": "患者（iPhone 16 Pro 1206×2622）"}


def size(p):
    with open(p, "rb") as fh:
        fh.read(16)
        return struct.unpack(">II", fh.read(8))


def table(idx, base, prefix=""):
    rows = ["| 順番 | ファイル | 端末 | 実際の操作 | 画面状態 | 表示される重要情報 | 実Gemini/モック | 備考 |", "|---|---|---|---|---|---|---|---|"]
    for s in idx["shots"]:
        w, h = size(base / s["file"])
        ai = "実Gemini" if idx["ai"] == "live" else "モック"
        rows.append(f"| {s['no']} | `{prefix}{s['file']}` | {DEVICE[s['device']]} | {s['action']} | {s['state']} | "
                    f"{s['info'].replace('|', '／')} | {ai} | {s['note']}{'' if (w, h) in ((2560, 1664), (1206, 2622)) else f' 大きさ {w}×{h}'} |")
    return "\n".join(rows)


def main():
    base = Path(sys.argv[1])
    main_idx = json.loads((base / "index.json").read_text(encoding="utf-8"))
    sup = base / "supplementary" / "index.json"
    sup_idx = json.loads(sup.read_text(encoding="utf-8")) if sup.exists() else None
    inputs = "\n".join(f"| {k} | {v} |" for k, v in main_idx["inputs"].items())
    md = [f"""# 診察の流れ（全画面）— スクリーンショットの一覧

- 撮影: {main_idx['captured_at']}。{main_idx['ai_note']}。医師と患者は同じ問診（{next((n for n in main_idx['notes'] if n.startswith('session id')), '')}）
- 撮り方: `devtools/capture_clinical_flow.py`（Playwright・Chromium）。医師は CSS 1280×832・DPR 2、患者は CSS 402×874・DPR 3・モバイル（iPhone 16 Pro 相当）。
  ボタンを実際に押し、画面の状態を待ってから撮った。DOM は書き換えていない（問診メモのスクロールだけ）
- 症例は架空。Gemini の出力は撮るたびに少し変わる
- 失敗した場所: {main_idx['failed_at'] or 'なし（最後まで完走）'}。画面のエラー: {len(main_idx['page_errors'])} 件。大きさの不一致: {main_idx['size_mismatch'] or 'なし'}

画面のエラー（ブラウザのコンソールの 403）は、どれも想定どおりの動き: 参加前の患者が問診を読もうとして断られる
（メンバーでない人は読めないルール。画面は「まだ参加していない」として扱う）、診察終了で問診を消した後の購読が断られる
（これで両方の画面が終了に切り替わる）。画面に出るエラーは無い。

## 送信の流れについて（実装どおり）

UNSAID には「送信前の確認画面（ダイアログ）」は**無い**。入力しただけでは送られず、「发送／送る」を押すと Safety Gate の流れに入る:
入力済み・未送信 → 処理中（意図の解析・翻訳・照合。相手にはまだ何も届かない）→ 次のどれか
- 照合済みで届く（送信済み）
- 意図が決まらない → 発言者本人が意味を選ぶまで止まる（意図確認待ち）
- 照合で相違が残る → 発言者が「言い直す／未検証として送る」を選ぶまで止まる（送信保留）

医師の画面には、処理中の患者の発言は訳が出ない（「確認中」だけ。緊急時は「未検証の内容を見る」で原文を見られる）。
医師が明示的に「受け取った」を押す操作は無い。医師の画面に表示されると自動で既読になり、患者の画面に「医生已读」が出る。
送信後、患者は医師が読んだ内容（訳を中国語に戻したもの）を見て、違えば「不对？重新说」で言い直せる。

## 入力した文（毎回同じ）

| 場面 | 文 |
|---|---|
{inputs}

## メインの流れ（{len(main_idx['shots'])} 枚）

{table(main_idx, base)}

## 撮影時のメモ

""" + "\n".join(f"- {n}" for n in main_idx["notes"])]
    if sup_idx:
        md.append(f"""

## 補助デモ（`supplementary/`、{len(sup_idx['shots'])} 枚）

メインの流れでは、照合が誤訳を止める場面と、意図の確認が出る場面が起きなかったため、別の問診で撮った。
**誤訳は人工的に注入したもの**（開発サーバーの `UNSAID_INJECT_ERRORS=1`。決まった 2 文の翻訳だけを置き換える。本番には無い）。
本物の Gemini の誤訳ではない。照合・Safety Gate・言い直し・意図の確認は本物の Gemini とサーバーのコードで動いている。
撮影: {sup_idx['captured_at']}。失敗した場所: {sup_idx['failed_at'] or 'なし'}

{table(sup_idx, base / 'supplementary', 'supplementary/')}

""" + "\n".join(f"- {n}" for n in sup_idx["notes"]))
    (base / "SCREENSHOT_INDEX.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("wrote", base / "SCREENSHOT_INDEX.md")


if __name__ == "__main__":
    main()
