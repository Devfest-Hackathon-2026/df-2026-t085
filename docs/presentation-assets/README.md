# プレゼン素材

すべて架空の症例（中国語の旅行者のめまい・服薬の説明）。**画面の撮影は本物の Gemini**（2026-11-10 04:11 JST、gemini-3.8-flash・global、
評価用のプロジェクト `kumu-devfest-2026`）。Firestore だけメモリ上の偽物（devtools/devserver.py の `UNSAID_LIVE=1`）。
Gemini の出力は回ごとに変わるので、撮り直すと文言は少し変わる。偽の AI の画面は入れていない。

| 端末 | 画像の大きさ | 前提 |
|---|---|---|
| 医師 | 2560 × 1664 px | MacBook（CSS 1280 × 832、2 倍） |
| 患者 | 1206 × 2622 px | iPhone 16 Pro（CSS 402 × 874、3 倍） |
| 図 | 3840 × 2160 px（SVG は 1920 × 1080） | 16:9 のスライド |

## 画面（1 つの問診を順に通して撮った）

| ファイル | 見せられること |
|---|---|
| `00-doctor-qr.png` | 医師: 問診を始めると QR が出る（患者はスマホで読む） |
| `00-patient-consent.png` | 患者: 言語を選び、AI の利用と記録の削除を確認して始める |
| `02-patient-overview.png` | 患者: 1回の送信で届く。医師が読んだ内容（訳を逆に訳したもの）を自分の言語で見られる |
| `01-doctor-overview.png` | 医師: 患者の発言の日本語訳（照合済み）と、原文に根拠のある問診メモ・確認候補 |
| `04-clinical-clarification.png` | 医師: 「头晕」の根拠（原文の該当箇所）・未確認の状態・追加の確認質問の候補 |
| `04a-doctor-memo-evidence.png` | 医師（拡大）: 問診メモの根拠と、確認候補 |
| `04b-patient-clarify-question.png` | 患者: 医師が選んだ確認質問に、選択肢・分からない・どれでもない・自分の言葉で答えられる |
| `04c-doctor-answer-reflected.png` | 医師: 患者の答えが問診メモに反映される（患者回答・医師はまだ確認していない） |
| `03-intent-clarification.png` | 患者: 「没事」は文脈で意味が決まらない（断る／同意する）。訳す前に本人が意味を選ぶ |
| `03a-doctor-intent-pending.png` | 医師: 患者が意味を確認中。訳はまだ届かない（未確定の訳を送らない） |
| `03b-doctor-unverified-reveal.png` | 医師: 緊急時は「未検証」と分かる形で原文を見られる（問診メモには入れない） |
| `03c-doctor-intent-confirmed.png` | 医師: 本人が選んだ意味で訳して届く（「本人が意味を確認」と AI の解釈を区別） |
| `06a-patient-teachback-request.png` | 患者: 医師の説明を、自分の言葉で言い直すよう頼まれる（理解の確認） |
| `06-teachback-mismatch.png` | 医師: 説明（1日2回・1錠ずつ）と患者の理解（朝に2錠）の食い違いを、項目ごとに示す。医師が再説明・確認を決める |
| `06b-teachback-card.png` | 医師（拡大）: 理解の確認の結果（AI の判定。医師が決める） |
| `07-conversation-auditor.png` | 医師: Conversation Auditor が会話全体から見つけた未解決事項（根拠の発言つき。解決は医師だけ） |
| `07a-auditor-card.png` | 医師（拡大）: 未解決事項と根拠の発言 |
| `06c-teachback-resolved.png` | 医師: 再説明 → 再確認で一致 → 医師が理解を確認（前の相違も解決になる） |
| `08-clinical-summary.png` | 医師: 最終の問診メモ（医師確認済み・患者回答・未確認・言及なしを区別。根拠は原文へ） |
| `08a-memo-panel.png` | 医師（拡大）: 問診メモ |
| `08b-karte-text.png` | 医師: カルテ用の文章（医師が編集してから貼る。原文に無い病名は足さない） |
| `02a-patient-after.png` | 患者: 会話の終わりの画面（自分の記録を開ける） |

## 図（`devtools/make_figures.py`。例と数字は本物の Gemini の実行結果から）

| ファイル | 見せられること |
|---|---|
| `architecture-overview.png` / `.svg` | USSA の構成。Gemini を使う部分とコードで決める部分を区別。Auditor は「届いた後」に動く（実装どおり） |
| `03x-intent-context.png` / `.svg` | 「大丈夫です」が文脈で No, thank you / That's quite alright / Yes, that's fine に変わり、決まらないときは本人に確認（比較評価 25 件の数字つき） |
| `05-semantic-verification.png` / `.svg` | 誤りを 1 つ入れた訳（1日3回・否定・夜の欠落）を止め、正しい訳を通す（各コンポーネントの確認と比較評価の数字） |

SVG は Google Fonts（Noto Sans JP）を読み込む。スライドには PNG を使う方が確実。

## 撮り直す

```bash
UNSAID_LIVE=1 UNSAID_VERTEX_PROJECT=kumu-devfest-2026 UNSAID_VERTEX_LOCATION=global \
  GOOGLE_APPLICATION_CREDENTIALS=<ADC のファイル> .venv/bin/python -m devtools.devserver &
.venv/bin/python devtools/capture_presentation.py --out docs/presentation-assets --mode live
.venv/bin/python devtools/make_figures.py
```

デモ画面を手で操作するときは、同じサーバーで `http://localhost:8765/doctor.html` を開き、「問診を始める」の QR の URL を
別のブラウザ（シークレットウィンドウか、スマホの幅にした別のウィンドウ）で開く。
