"""デモ動画の編集用ショットリスト（docs/presentation-assets/clinical-flow/VIDEO_SHOTLIST.md）を作る。

各行の医師側・患者側の PNG が実在するかを確かめ、尺から開始・終了時刻を計算し、使わなかった画像を一覧にする。
画面の状態の整合（同じ時点の左右）は、行ごとの「注意」に書く（撮影していない状態を別の画像で補わない）。

  .venv/bin/python devtools/make_shotlist.py
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "docs" / "presentation-assets" / "clinical-flow"
D, P, BOTH, NONE = "左（医師）", "右（患者）", "両方", "なし"

# (尺 秒, 医師側 PNG, 患者側 PNG, 変更, マスク, 操作・アニメーション, テロップ, 説明原稿, 注意)
MAIN = [
    # ── 1. 問診を始める・QR ──
    (3, "00-doctor-start.png", "02-patient-join-language.png", BOTH, "患者側（黒100%: まだ開いていない）",
     "「問診を始める」をクリック強調", "医師: 問診を始める", "医師が問診を始めます。",
     "患者側の PNG は参加前のもの。この時点では患者はまだ開いていないので黒100%で隠す"),
    (4, "01-doctor-qr.png", "02-patient-join-language.png", D, "患者側（黒100%）",
     "QR を表示（短いフェード）", "QR を読むだけ。アプリのインストールは不要",
     "患者さんにはスマートフォンで QR コードを読んでもらいます。", ""),
    # ── 2. 患者が参加 ──
    (4, "01-doctor-qr.png", "02-patient-join-language.png", P, "医師側（50%）",
     "患者側の黒を外す。「中文（简体）」をタップ強調", "患者: 自分の言語を選ぶ",
     "患者さんは中国語を選びます。", ""),
    (5, "01-doctor-qr.png", "03-patient-consent.png", P, "医師側（50%）",
     "「同意并开始」をタップ強調", "AI で訳すこと・間違えうること・診断しないこと・終われば記録が消えることに同意",
     "AI で訳すこと、診断はしないこと、終わったら記録が消えることを確認してから始めます。", ""),
    (3, "05-doctor-connected.png", "04-patient-connected.png", BOTH, NONE,
     "短いフェード。医師側の QR が閉じる", "同じ問診につながった", "これで医師と患者が同じ問診につながりました。", ""),
    # ── 3〜5. 最初の訴え ──
    (7, "05-doctor-connected.png", "06-patient-first-typed.png", P, "医師側（50%）",
     "入力欄に文字が入る（タイプのアニメーション可）。まだ送らない", "入力しただけでは送られない",
     "患者さんが症状を伝えます。駅で急に「心慌」、胸が詰まるよう、全身に力が入らない。水土不服かと思ったが、休んでも良くならない。", ""),
    (2, "05-doctor-connected.png", "06-patient-first-typed.png", P, "医師側（50%）",
     "「发送」をタップ強調", "患者が送信", "送信します。",
     "UNSAID に送信前の確認ダイアログは無い。送信操作の後に Safety Gate が確かめる"),
    (5, "08-doctor-first-pending.png", "07-patient-first-checking.png", BOTH, NONE,
     "両画面の「確認中」の表示（スピナー）", "意図・翻訳・照合を確かめ終わるまで、医師にはまだ訳を出さない",
     "ここで UNSAID は、意図の解析、翻訳、意味の照合をしています。確かめ終わるまで、医師の画面には未確定の訳を出しません。", ""),
    (6, "09-doctor-first-received.png", "10-patient-first-delivered.png", BOTH, NONE,
     "届いた瞬間に両画面を明るく", "照合済みの日本語訳が届く／患者は医師が読んだ内容を中国語で確認",
     "照合を通った日本語訳が届きました。患者さんの画面には、医師が読んだ内容が中国語で表示されます。違っていれば言い直せます。", ""),
    # ── 6〜7. 意味の確認・問診メモ・確認候補 ──
    (6, "09-doctor-first-received.png", "10-patient-first-delivered.png", NONE, "患者側（50%）",
     "問診メモの「未確認」タグを枠で強調", "「心慌」を動悸と決めつけない（未確認）",
     "問診メモは原文に根拠のある項目だけです。「心慌」は動悸とも不安感とも取れるので、AI の解釈は未確認のままです。", ""),
    (6, "11-doctor-candidates.png", "10-patient-first-delivered.png", D, "患者側（50%）",
     "確認候補「心慌」のカードを枠で強調", "AI の確認候補（提案だけ。決めるのは医師）",
     "AI が、医師が確かめるべき点を候補として出します。聞くかどうかは医師が決めます。", ""),
    (5, "12-doctor-evidence.png", "10-patient-first-delivered.png", D, "患者側（50%）",
     "問診メモの項目をクリック → 原文の該当箇所のハイライトを強調", "根拠は患者の原文",
     "どの項目も、患者さんの原文のどこから来たかをたどれます。", ""),
    # ── 8. 医師が質問を編集して送る ──
    (5, "13-doctor-question-edit.png", "10-patient-first-delivered.png", D, "患者側（50%）",
     "確認候補の「編集」をクリック → 編集欄を強調", "医師が質問を選び、言葉を直す",
     "医師は質問を選び、自分の言葉に直せます。", ""),
    (2, "13-doctor-question-edit.png", "10-patient-first-delivered.png", D, "患者側（50%）",
     "「質問を送る」をクリック強調", "医師が送信", "送ります。編集した質問も、訳し直して照合してから届きます。",
     "編集した質問の「照合中」の画面は撮れていない（すぐ終わった）"),
    # ── 9. 患者が受け取り、答える ──
    (6, "14-doctor-question-sent.png", "15-patient-question-received.png", BOTH, NONE,
     "届いた瞬間に両画面を明るく", "質問が中国語で届く（選択肢・分からない・どれでもない・自分の言葉）",
     "質問が中国語で届きました。選択肢から選ぶことも、自分の言葉で答えることもできます。", ""),
    (5, "14-doctor-question-sent.png", "16-patient-answer1-typed.png", P, "医師側（50%）",
     "入力欄に文字が入る。まだ送らない", "患者が自分の言葉で答える",
     "患者さんは、心臓が急に速く打つ感じで、10 分休んでも良くならなかったと答えます。", ""),
    (2, "14-doctor-question-sent.png", "16-patient-answer1-typed.png", P, "医師側（50%）",
     "「发送」をタップ強調", "患者が送信", "", ""),
    (3, "18-doctor-answer1-pending.png", "17-patient-answer1-checking.png", BOTH, NONE,
     "両画面の「確認中」", "照合中（医師にはまだ出ない）", "", ""),
    # ── 10. 問診メモの更新 ──
    (6, "19-doctor-answer1-received.png", "20-patient-answer1-delivered.png", BOTH, NONE,
     "届いた瞬間に両画面を明るく。「患者が回答」タグを強調", "答えが届き、問診メモに反映（医師はまだ確認していない）",
     "答えが届き、「心慌」は心臓が速く打つ感覚だったと記録されます。これは患者回答で、医師の確認とは区別されます。", ""),
    (6, "21-doctor-followup-typed.png", "20-patient-answer1-delivered.png", D, "患者側（50%）",
     "問診メモ「胸部の閉塞感」から「患者に質問する」→ 質問欄の文字を強調", "胸の症状は医師が自分で確かめる",
     "胸の症状は重要なので、医師が自分で質問を書きます。今も続いているか、意識を失ったことはないか。", ""),
    (2, "21-doctor-followup-typed.png", "20-patient-answer1-delivered.png", D, "患者側（50%）",
     "「質問を送る」をクリック強調", "医師が送信", "", "照合中の画面は撮れていない（すぐ終わった）"),
    (5, "22-doctor-followup-sent.png", "23-patient-followup-received.png", BOTH, NONE,
     "届いた瞬間に両画面を明るく", "追加の質問が届く", "", ""),
    (5, "22-doctor-followup-sent.png", "24-patient-answer2-typed.png", P, "医師側（50%）",
     "入力欄に文字が入る。まだ送らない", "患者: 今も胸が苦しい・息が少し苦しい・失神はない",
     "今も胸が苦しく、息が少し苦しい。倒れたことはない、と答えます。", ""),
    (2, "22-doctor-followup-sent.png", "24-patient-answer2-typed.png", P, "医師側（50%）",
     "「发送」をタップ強調", "患者が送信", "", ""),
    (3, "26-doctor-answer2-pending.png", "25-patient-answer2-checking.png", BOTH, NONE,
     "両画面の「確認中」", "照合中", "", ""),
    (5, "27-doctor-answer2-received.png", "25-patient-answer2-checking.png", D, "患者側（50%）",
     "医師側に答えが届く。訳と「患者が回答」を強調", "胸部の圧迫感が続く・失神はない（否定も保持）",
     "答えが届きました。続いている症状と、失神がないという否定の両方が残っています。",
     "※ 届いた後の患者の画面（你的回答・医生已读）は撮影していない。患者側は最後に撮った 25 をマスクで暗くしたまま。届いた瞬間の「両方を明るく」はこの行では行わない"),
    # ── 11. 未確認のまま残る項目 ──
    (6, "28-doctor-memo-after-questions.png", "25-patient-answer2-checking.png", NONE, "患者側（50%）",
     "問診メモの下の方（否定された項目と、確認候補「浑身发虚」）を枠で強調", "未確認のまま残る項目（「浑身发虚」の確認候補）",
     "否定された項目も記録され、「浑身发虚」はまだ確かめていない項目として残っています。会話全体の食い違いは、後で Conversation Auditor が未解決事項として出します。",
     "この時点で Conversation Auditor の未解決事項は出ていない（実際に出たのは 38）。未確認の項目は確認候補と「未確認」タグで見せる。患者側は上と同じ"),
    # ── 12. 医師の説明と理解の確認 ──
    (6, "29-doctor-explain-typed.png", "25-patient-answer2-checking.png", D, "患者側（50%）",
     "入力欄の文字を強調。まだ送らない", "医師の説明（AI は医療の指示を作らない）",
     "医師が説明します。今はそのまま座っていてください、すぐに医療スタッフを呼びます、自分で移動しないでください。", "患者側は上と同じ（撮影していない状態）"),
    (2, "29-doctor-explain-typed.png", "25-patient-answer2-checking.png", D, "患者側（50%）",
     "「送る」をクリック強調", "医師が送信", "", "患者側は上と同じ"),
    (3, "30-doctor-explain-checking.png", "25-patient-answer2-checking.png", D, "患者側（50%）",
     "医師の発言に「訳を照合中…」", "説明も照合してから届く", "医師の説明も、照合してから届きます。", "患者側は上と同じ"),
    (5, "31-doctor-explain-sent.png", "32-patient-explain-received.png", BOTH, NONE,
     "届いた瞬間に両画面を明るく", "説明が中国語で届く", "", ""),
    (2, "31-doctor-explain-sent.png", "32-patient-explain-received.png", D, "患者側（50%）",
     "医師の説明の「理解を確認」をクリック強調", "医師: 理解を確認", "伝わったかを確かめます。", ""),
    (4, "31-doctor-explain-sent.png", "33-patient-teachback-prompt.png", P, "医師側（50%）",
     "「确认理解」のカードを強調", "患者に自分の言葉で言い直してもらう（teach-back）",
     "患者さんに、理解した内容を自分の言葉で言ってもらいます。",
     "※ 医師側の「理解の確認 送信済み」の吹き出しは 31 には無い（36 から見える）"),
    # ── 13. teach-back の相違 ──
    (5, "31-doctor-explain-sent.png", "34-patient-teachback-wrong-typed.png", P, "医師側（50%）",
     "入力欄に文字が入る。まだ送らない", "模擬患者の誤った理解: 自分で看護師を探しに行く",
     "模擬患者は、まず自分で歩いて看護師を探しに行くと理解しました。", "医師側は上と同じ"),
    (2, "31-doctor-explain-sent.png", "34-patient-teachback-wrong-typed.png", P, "医師側（50%）",
     "「发送」をタップ強調", "患者が送信", "", "医師側は上と同じ"),
    (3, "36-doctor-teachback-wrong-pending.png", "35-patient-teachback-wrong-checking.png", BOTH, NONE,
     "両画面の「確認中」", "照合中", "", ""),
    (8, "37-doctor-teachback-result.png", "35-patient-teachback-wrong-checking.png", D, "患者側（50%）",
     "「認識の相違の可能性」カードを枠で強調（説明 / 患者の理解）。少し止める", "認識の相違（AI の判定。決めるのは医師）",
     "医師の説明は「座ったまま、自分では動かない」。患者さんの理解は「自分で看護師を探しに行く」。この食い違いを項目ごとに示します。判定は AI、決めるのは医師です。",
     "※ 届いた後の患者の画面は撮影していない。患者側は 35 を暗くしたまま"),
    # ── 11'. Conversation Auditor ──
    (6, "38-doctor-auditor-mismatch.png", "35-patient-teachback-wrong-checking.png", D, "患者側（50%）",
     "問診メモ側の「未解決事項」カードを枠で強調", "Conversation Auditor: 未解決事項として根拠つきで記録",
     "会話全体を見ている Conversation Auditor も、この食い違いを未解決事項として記録します。解決にできるのは医師だけです。", "患者側は上と同じ"),
    # ── 14. 再説明 ──
    (2, "38-doctor-auditor-mismatch.png", "35-patient-teachback-wrong-checking.png", D, "患者側（50%）",
     "「再説明する」をクリック強調", "医師: 再説明する", "", "患者側は上と同じ"),
    (5, "39-doctor-reexplain-typed.png", "35-patient-teachback-wrong-checking.png", D, "患者側（50%）",
     "入力欄の再説明案を強調", "AI の再説明案は入力欄に入るだけ。医師が読んで送る",
     "再説明の案が入力欄に入ります。医師が読んで、必要なら直してから送ります。", "患者側は上と同じ"),
    (2, "39-doctor-reexplain-typed.png", "35-patient-teachback-wrong-checking.png", D, "患者側（50%）",
     "「送る」をクリック強調", "医師が送信", "", "患者側は上と同じ"),
    (3, "40-doctor-reexplain-checking.png", "35-patient-teachback-wrong-checking.png", D, "患者側（50%）",
     "「訳を照合中…」", "照合中", "", "患者側は上と同じ"),
    (5, "41-doctor-reexplain-sent.png", "42-patient-reexplain-received.png", BOTH, NONE,
     "届いた瞬間に両画面を明るく", "再説明が届く", "", ""),
    # ── 15. 正しい言い直し・理解一致 ──
    (2, "41-doctor-reexplain-sent.png", "42-patient-reexplain-received.png", D, "患者側（50%）",
     "「理解を確認」をクリック強調", "医師: もう一度理解を確認", "", ""),
    (4, "41-doctor-reexplain-sent.png", "43-patient-teachback-prompt2.png", P, "医師側（50%）",
     "「确认理解」のカードを強調", "もう一度言い直してもらう", "",
     "※ 医師側の 2 回目の「理解の確認」の吹き出しは 41 には無い（46 から見える）"),
    (5, "41-doctor-reexplain-sent.png", "44-patient-teachback-right-typed.png", P, "医師側（50%）",
     "入力欄に文字が入る。まだ送らない", "患者: ここに座って待つ。自分では動かない",
     "今度は、ここに座って待つ、自分では動かない、と言い直しました。", "医師側は上と同じ"),
    (2, "41-doctor-reexplain-sent.png", "44-patient-teachback-right-typed.png", P, "医師側（50%）",
     "「发送」をタップ強調", "患者が送信", "", "医師側は上と同じ"),
    (3, "46-doctor-teachback-right-pending.png", "45-patient-teachback-right-checking.png", BOTH, NONE,
     "両画面の「確認中」", "照合中", "", ""),
    (6, "47-doctor-teachback-result2.png", "55-patient-before-end.png", BOTH, NONE,
     "届いた瞬間に両画面を明るく。「説明と一致」を強調", "理解が一致",
     "説明と一致しました。",
     "患者側の 55 は終了前に撮った画像だが、この時点から終了まで患者の画面は変わらない（你的回答・医生已读）"),
    (4, "48-doctor-teachback-confirmed.png", "55-patient-before-end.png", D, "患者側（50%）",
     "「理解を確認済みにする」をクリック強調 → 「医師が理解を確認」", "医師が理解を確認（前の相違も解決）",
     "医師が理解を確認しました。さっきの相違も解決になります。", ""),
    # ── 16. 最終の問診メモ・カルテ・終了 ──
    (4, "49-doctor-confirm-before.png", "55-patient-before-end.png", D, "患者側（50%）",
     "問診メモの項目をクリック → 根拠と履歴を強調", "医師が確認する前（患者回答）", "", ""),
    (3, "50-doctor-confirm-after.png", "55-patient-before-end.png", D, "患者側（50%）",
     "「確認済みにする」をクリック強調 → チェックの印", "医師が確認したものだけ「医師確認済み」",
     "医師が確認したものだけが、医師確認済みになります。", ""),
    (6, "51-doctor-summary-top.png", "55-patient-before-end.png", D, "患者側（50%）",
     "問診メモを強調（医師確認済み・患者回答・未確認・否定）", "最終の問診メモ",
     "最終の問診メモです。医師が確認したもの、患者の答え、まだ未確認のものが区別されています。", ""),
    (4, "52-doctor-summary-bottom.png", "55-patient-before-end.png", D, "患者側（50%）",
     "問診メモを下へスクロール（未解決事項・確認候補）", "未確認の項目も残す", "", ""),
    (6, "53-doctor-karte.png", "55-patient-before-end.png", D, "患者側（50%）",
     "「カルテにコピー」をクリック → 下書きのダイアログ", "カルテ用の下書き（医師が編集してから貼る）",
     "カルテ用の下書きを作ります。これは医師が直してから使う下書きで、病名は足しません。", ""),
    (2, "52-doctor-summary-bottom.png", "55-patient-before-end.png", D, "患者側（50%）",
     "ダイアログを閉じ、「診察を終える」をクリック強調", "診察を終える", "",
     "閉じた後の画面として 52 を使う（ダイアログを閉じた後の状態と同じ）"),
    (4, "56-doctor-ended.png", "57-patient-ended.png", BOTH, NONE,
     "両画面が終了に切り替わる（短いフェード）", "記録は両方から消える",
     "診察を終えると、記録は医師と患者の両方から消えます。", ""),
]

S = "supplementary/"
SUPP = [
    (4, S + "06-doctor-lossy-typed.png", S + "09-patient-lossy-not-delivered.png", BOTH, "患者側（50%）",
     "本編からの切り替え（フェード）。境界のテロップを大きく", "補足デモ: 人工的に注入した誤訳（本物の Gemini の誤訳ではありません）",
     "ここからは補足です。照合が誤訳を止める様子を見せるため、訳にわざと誤りを入れています。",
     "補足デモは別の問診。患者側 09 は 08 と同時に撮った画像だが、それまでの患者の画面も同じ（何も届いていない）"),
    (2, S + "06-doctor-lossy-typed.png", S + "09-patient-lossy-not-delivered.png", D, "患者側（50%）",
     "「送る」をクリック強調", "医師: 検査の前は8時間何も食べないでください", "", ""),
    (3, S + "07-doctor-lossy-checking.png", S + "09-patient-lossy-not-delivered.png", D, "患者側（50%）",
     "「訳を照合中…」", "照合中", "", ""),
    (8, S + "08-doctor-lossy-review.png", S + "09-patient-lossy-not-delivered.png", D, NONE,
     "相違のカード（8時間が無い）を強調。患者側に何も届いていないことも見せるため、マスクなし", "人工的な誤訳: 「8時間」が落ちた → まだ患者には届いていない",
     "訳から「8時間」が落ちています。照合がそれを見つけ、患者さんにはまだ届けません。言い直すか、未検証として送るかを医師が決めます。", "誤訳は人工的に注入"),
    (3, S + "10-doctor-lossy-rephrase.png", S + "09-patient-lossy-not-delivered.png", D, "患者側（50%）",
     "「言い直す」をクリック強調 → 元の文が入力欄に戻る", "医師が言い直す", "", ""),
    (3, S + "11-doctor-lossy-fixed-typed.png", S + "09-patient-lossy-not-delivered.png", D, "患者側（50%）",
     "言い直した文と「送る」をクリック強調", "言い直して送信", "", ""),
    (2, S + "12-doctor-lossy-fixed-checking.png", S + "09-patient-lossy-not-delivered.png", D, "患者側（50%）",
     "「訳を照合中…」", "照合中", "", ""),
    (4, S + "13-doctor-lossy-fixed-result.png", S + "14-patient-lossy-fixed-received.png", BOTH, NONE,
     "届いた瞬間に両画面を明るく", "照合を通った訳だけが届く", "", ""),
    (3, S + "13-doctor-lossy-fixed-result.png", S + "15-patient-pt-lossy-typed.png", P, "医師側（50%）",
     "患者が入力（我对青霉素过敏）。まだ送らない", "患者: ペニシリンにアレルギーがある", "患者側でも同じです。", ""),
    (2, S + "13-doctor-lossy-fixed-result.png", S + "15-patient-pt-lossy-typed.png", P, "医師側（50%）",
     "「发送」をタップ強調", "患者が送信", "", ""),
    (3, S + "17-doctor-pt-lossy-pending.png", S + "16-patient-pt-lossy-checking.png", BOTH, NONE,
     "両画面の「確認中」", "照合中", "", ""),
    (8, S + "19-doctor-pt-lossy-waiting.png", S + "18-patient-pt-lossy-review.png", P, NONE,
     "患者側の「翻译可能和你的意思不一样」と「医生会看到」を強調。医師側に訳が出ていないことも見せるため、マスクなし",
     "人工的な誤訳: 「アレルギーはありません」に反転 → 医師には届かない",
     "訳で否定が反転しています。患者さんには、医師が読むことになる内容が表示され、言い直すことができます。医師には届いていません。", "誤訳は人工的に注入"),
    (3, S + "13-doctor-lossy-fixed-result.png", S + "20-patient-pt-lossy-rephrase.png", P, "医師側（50%）",
     "「换个说法」をタップ強調 → 元の文が入力欄に戻る", "患者が言い直す", "誤った訳は、最後まで医師に届きませんでした。",
     "言い直すと元の発言は取り消され、医師の画面から「確認中」が消える。その状態の医師の画面として 13（患者の発言が届く前）を使う"),
]


# 尺の目安（本編 約 3:00、補足 約 0:25 → 合計 3:00〜3:30）に合わせて、上の尺を一律に縮める（最短 1 秒）
SCALE = {"本編": 0.72, "補足デモ": 0.5}


def scaled(items, k):
    return [(max(1, round(r[0] * k)), *r[1:]) for r in items]


def fmt(t):
    return f"{t // 60}:{t % 60:02d}"


def main():
    bad = []
    used = set()
    rows = []
    t = 0
    main_rows, supp_rows = scaled(MAIN, SCALE["本編"]), scaled(SUPP, SCALE["補足デモ"])
    for part, items in (("本編", main_rows), ("補足デモ", supp_rows)):
        if part == "補足デモ":
            rows.append("| | | **── ここから補足デモ（別の問診・人工的に注入した誤訳）──** | | | | | | |")
        for dur, doc, pat, change, mask, action, telop, script, note in items:
            for f in (doc, pat):
                if not (BASE / f).exists():
                    bad.append(f)
                used.add(f)
            if not doc.endswith(".png") or "doctor" not in doc or "patient" not in pat:
                bad.append(f"左右の取り違え: {doc} / {pat}")
            cell = " ".join(x for x in (action, f"（注意: {note}）" if note else "") if x)
            rows.append(f"| {fmt(t)} | {fmt(t + dur)} | `{doc}` | `{pat}` | {change} | {mask} | {cell} | {telop} | {script} |")
            t += dur
    if bad:
        print("見つからない・取り違え:", bad)
        sys.exit(1)
    all_png = sorted(p.relative_to(BASE).as_posix() for p in BASE.rglob("*.png") if not p.name.startswith("_"))
    unused = [p for p in all_png if p not in used]
    main_len = sum(r[0] for r in main_rows)
    starts = [sum(r[0] for r in main_rows[:i]) for i in range(len(main_rows))]

    def span(pred):
        idx = [i for i, r in enumerate(main_rows) if pred(r)]
        return f"{fmt(starts[idx[0]])}〜{fmt(starts[idx[-1]] + main_rows[idx[-1]][0])}" if idx else "—"
    md = f"""# デモ動画のショットリスト（編集用）

- 画面: 1920×1080（16:9）。左に医師の MacBook（2560×1664 を縮小）、すぐ右に患者の iPhone（1206×2622 を縮小）。配置は最後まで固定
- マスク: 操作している側の相手の画面に黒 50%。相手に届いた瞬間（「両方」の行）はマスクなし。参加前の患者側は黒 100%
- 切り替え: カット、または短いフェード。無音（発表者が説明する）
- 長さ: 本編 {fmt(main_len)}、補足デモ {fmt(t - main_len)}、合計 {fmt(t)}
- 画像: すべて `docs/presentation-assets/clinical-flow/` からの相対パス。本物の Gemini で撮った 1 つの問診（補足デモは別の問診、誤訳は人工的に注入）
- 送信前の確認ダイアログは UNSAID に無いので使っていない。「入力（未送信）→ 送信の操作 → 確認中 → 届いた」の順で見せる

## 左右の整合について（撮影していない状態）

撮影は「状態が変わった側」を撮ったので、次の時間は、片方の画面の正確な状態の画像が無い。別の時点の画像で補わず、
最後に撮った画像をマスクで暗くしたままにする（行の「注意」にも書いた）。

| 時間帯 | 画面 | 実際の状態 | 使う画像 |
|---|---|---|---|
| {span(lambda r: "黒100%" in r[4])} | 患者 | まだ QR を読んでいない | `02-patient-join-language.png` を黒 100% |
| {span(lambda r: r[2] == "25-patient-answer2-checking.png" and r[1] != "26-doctor-answer2-pending.png")} | 患者 | 2 つ目の答えを送った後（你的回答・医生已读）〜 医師の説明が届く前 | `25-patient-answer2-checking.png` を暗く（この間に「両方を明るく」はしない） |
| {span(lambda r: r[1] == "31-doctor-explain-sent.png" and r[2] in ("33-patient-teachback-prompt.png", "34-patient-teachback-wrong-typed.png"))} | 医師 | 「理解を確認」を押した後（依頼の吹き出し） | `31-doctor-explain-sent.png` のまま（吹き出しは `36` から） |
| {span(lambda r: r[2] == "35-patient-teachback-wrong-checking.png" and r[1] != "36-doctor-teachback-wrong-pending.png")} | 患者 | 誤った言い直しを送った後 〜 再説明が届く前 | `35-patient-teachback-wrong-checking.png` を暗く |
| {span(lambda r: r[1] == "41-doctor-reexplain-sent.png" and r[2] in ("43-patient-teachback-prompt2.png", "44-patient-teachback-right-typed.png"))} | 医師 | 2 回目の「理解を確認」を押した後 | `41-doctor-reexplain-sent.png` のまま（吹き出しは `46` から） |

ほかの行は、左右が同じ時点の状態（同時に撮った画像、またはその間に画面が変わらない画像）。

## ショットリスト

| 開始時刻 | 終了時刻 | 医師側PNGの正確なファイル名 | 患者側PNGの正確なファイル名 | 左右どちらを変更するか | どちらにマスクをかけるか | 画面上の操作・アニメーション | テロップ | 発表者の説明原稿 |
|---|---|---|---|---|---|---|---|---|
""" + "\n".join(rows) + f"""

## 使わなかった画像（{len(unused)} 枚）

""" + "\n".join(f"- `{u}`" for u in unused) + "\n"
    (BASE / "VIDEO_SHOTLIST.md").write_text(md, encoding="utf-8")
    print(f"{len(MAIN)} + {len(SUPP)} 行、本編 {fmt(main_len)}、合計 {fmt(t)}、使わなかった {len(unused)} 枚。ファイル名の誤り 0")


if __name__ == "__main__":
    main()
