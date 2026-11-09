# Kumu API（Cloud Run）と画面側の約束

画面（Yusei）が知っておくことは「Firestore に発言を作る → その id で API を呼ぶ → あとは onSnapshot で待つ」だけ。
API のレスポンスを使わなくても、結果は同じメッセージに書き戻されるので画面は勝手に更新される。

## 全体の流れ

音声入力のときは、この前に `POST /transcribe` で文字にして、患者が確認してから下の `addDoc` をする（docs/stt.md）。

```
患者の発言:
  端末   addDoc {from:"patient", original, status:"analyzing"}  → POST /analyze
  API    確認質問を書く（facts は少し後から同じメッセージに書き足す）
           確認質問あり → status "clarifying"（clarification.answer = null）
           確認質問なし → 翻訳まで済ませて status "confirmed"
  端末   （clarifying のとき）clarification.answer を書く        → POST /translate
  API    translation を書く → status "confirmed"
  端末   逆翻訳を見て「送信」→ status "sent"
  医師   受け取った → status "received"

医師の発言:
  端末   addDoc {from:"doctor", original, status:"analyzing"}   → POST /translate
  API    translation を書く → status "sent"（医師の発言は確認なし）
```

## エンドポイント

ベース URL は `gcloud run deploy` の最後に出る `https://unsaid-api-xxxx.asia-northeast1.run.app`。

| メソッド | パス | いつ呼ぶ | 成功時 |
|---|---|---|---|
| POST | `/analyze` | 患者の発言を作った直後 | 200。status は `clarifying` か `confirmed` |
| POST | `/translate` | 患者: `clarification.answer` を書いた後 ／ 医師: 発言を作った直後 | 200。患者は `confirmed`、医師は `sent` |
| POST | `/transcribe?sessionId=&speaker=patient\|doctor` | 音声入力のとき、発言を作る前（ボディは WAV） | 200。`{text, lang}`。Firestore には書かない。詳しくは docs/stt.md |
| POST | `/end` | 医師が「診察を終える」とき（ボディは `{"sessionId"}` だけ） | 200。`{deleted}`。その session の発言と session を消す。医師（`members[0]`）以外は 403 |
| GET | `/health` | 動作確認 | `{"ok": true}` |

- ヘッダー: `Authorization: Bearer <Firebase の ID トークン>`（匿名ログインの `auth.currentUser.getIdToken()`）
- ボディ: `{"sessionId": "...", "messageId": "..."}` だけ。本文や言語は API が Firestore から読み直す（端末の言うことをそのまま信じない）。
- レスポンス: Firestore に書いた内容と同じ（デバッグ用。画面は onSnapshot を使えばよい）。
- 上限: 1つの session で /analyze・/translate・/transcribe を合わせて `MAX_CALLS_PER_SESSION` 回（既定 200）。超えると 429。
- サーバーは session と発言に `apiCalls`（呼んだ回数）と `expireAt`（Firestore の TTL で消える期限）を書き足す。画面は使わない。
- 待ち時間: `/analyze` は確認質問だけを先に Firestore に書く（facts と先回りの翻訳は並列）。質問ありでも質問なしでも、画面に出るまで Gemini 1回分。
  HTTP のレスポンスは facts まで書き終えてから返るので、**質問の表示は onSnapshot で行う**（`await callApi()` の後に表示すると速くならない）。

| エラー | 意味 | 画面での扱い |
|---|---|---|
| 401 | ID トークンが無い・無効 | ログインし直す |
| 403 | その session のメンバーでない | |
| 404 | session / message が無い | |
| 409 | もう処理済み（二重押し）／同じ発言を処理中（`lockedUntil`）／回答前に /translate を呼んだ | 無視してよい |
| 400 | from が違う、本文が空か 1000 文字超、answer.type が不正 | |
| 502 | Gemini が再試行しても失敗。メッセージの status は `error` になる | 「もう一度」ボタン → 下のコード例の `retry()` |

通信が切れて API に届かなかった・サーバーが途中で落ちたときは、status が `analyzing`（や回答済みの `clarifying`）のまま変わらない。画面は 25 秒進まなければ「もう一度」を出す（`web/ui.js` の `stuckWatch`）。`/analyze` で確認質問の呼び出しだけが失敗したときは、確認なしの翻訳で `confirmed` まで進め、医師に見せる `translation.note` に「自動の確認ができませんでした」と書く。

## メッセージのフィールド（sessions/{sessionId}/messages/{messageId}）

| フィールド | 書く人 | 中身 |
|---|---|---|
| `from` | 端末（作成時） | `"patient"` / `"doctor"` |
| `original` | 端末（作成時） | 話した内容（1〜1000 文字） |
| `status` | 下の表 | `analyzing` → `clarifying` → `confirmed` → `sent` → `received`、失敗時 `error` |
| `createdAt` | 端末（作成時） | `serverTimestamp()`（ルールでサーバーの時刻以外は弾く） |
| `facts` | API | `[{text, doctor_gloss, kind, negated, uncertain, time?}]`。医師画面の補助表示。`status` が `clarifying` / `confirmed` になった直後はまだ無いことがある（1〜数秒後に届く。失敗したら `[]`） |
| `clarification` | API（`answer` だけ患者の端末） | `null` か `{target, question, ui_type, options[], fixed_options{unsure, free_text}, answer}` |
| `translation` | API | `null` か `{lang, text, back_translation, note, clarified_target, clarified_answer}`（`clarified_*` は確認に答えた患者の発言だけ。医師の言語で「しばらく」→「約1週間」。それ以外は `""`） |
| `error` | API | 失敗したときだけ。例外名 |
| `lockedUntil` | API | 処理中だけ。同じ発言を2つのリクエストが同時に処理しないための印（60 秒で切れる） |
| `sentAt` / `receivedAt` | 患者の端末 / 医師の端末（医師の発言の `sentAt` は API） | `serverTimestamp()` |

`clarification` の使い方:
- `target` は必ず `original` の中にある文字列。原文のその部分をハイライトする。
- 選択肢 = `options`（モデルが作る、患者の言語）＋ `fixed_options.unsure`「うまく説明できない」＋ `fixed_options.free_text`「自分で書く」。
- 回答は `clarification.answer = {type, text}` として書く。
  - 選択肢を選んだ: `{type: "option", text: "<選んだ選択肢の文字>"}`
  - 自分で書いた: `{type: "free_text", text: "<入力>"}`
  - 説明できない: `{type: "unsure", text: ""}`（曖昧なまま訳し、`translation.note` で医師に知らせる）

`translation` の使い方:
- 医師画面: `text`（日本語）を主表示、`note` があれば注意書き、`facts[].doctor_gloss` は補助。
- 患者画面（送信前の確認）: `back_translation`（患者の言語に訳し戻したもの）を「医師にはこう伝わります」として見せる。

## 状態ごとの画面

| status | 患者画面 | 医師画面 |
|---|---|---|
| `analyzing` | 「確認しています…」 | 表示しない |
| `clarifying`（`answer` が null） | 確認質問と選択肢 | 表示しない |
| `clarifying`（`answer` あり） | 「翻訳しています…」 | 表示しない |
| `confirmed` | 逆翻訳と「送信」ボタン | 表示しない |
| `sent` | 送信済み | 翻訳を表示、開いたら `received` にする |
| `received` | 既読 | 表示 |
| `error` | 「もう一度」ボタン | 表示しない |

医師の発言は `analyzing` → `sent` なので、患者画面には `sent` になったら `translation.text` を出す。医師画面には `translation.back_translation` を「患者にはこう伝わります」として出せる。

## 画面側のコード例（Firebase JS SDK v10）

```js
import { addDoc, collection, doc, updateDoc, serverTimestamp } from "firebase/firestore";

const API_URL = "https://unsaid-api-xxxx.asia-northeast1.run.app";

async function callApi(path, sessionId, messageId) {
  const token = await auth.currentUser.getIdToken();
  const res = await fetch(API_URL + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ sessionId, messageId }),
  });
  if (!res.ok && res.status !== 409) throw new Error((await res.json()).detail);
}

// 発言する（from は "patient" か "doctor"）
async function say(sessionId, from, text) {
  const ref = await addDoc(collection(db, "sessions", sessionId, "messages"), {
    from, original: text, status: "analyzing", createdAt: serverTimestamp(),
  });
  await callApi(from === "patient" ? "/analyze" : "/translate", sessionId, ref.id);
}

// 患者: 確認質問に答える（type は "option" / "free_text" / "unsure"）
async function answer(sessionId, messageId, type, text = "") {
  const ref = doc(db, "sessions", sessionId, "messages", messageId);
  await updateDoc(ref, { "clarification.answer": { type, text } });
  await callApi("/translate", sessionId, messageId);
}

// 患者: 逆翻訳を見て送信 ／ 医師: 受け取った
const send = (sid, mid) => updateDoc(doc(db, "sessions", sid, "messages", mid), { status: "sent", sentAt: serverTimestamp() });
const receive = (sid, mid) => updateDoc(doc(db, "sessions", sid, "messages", mid), { status: "received", receivedAt: serverTimestamp() });

// 「もう一度」: status が error のメッセージを、止まった所から呼び直す
function retry(sessionId, m) {
  const path = m.from === "patient" && !m.clarification?.answer ? "/analyze" : "/translate";
  return callApi(path, sessionId, m.id);
}
```

`session` は `{patientLang, doctorLang, members: [uid, ...], createdAt}`。`patientLang` は `unsaid_ai.py` の `LANGS` にある言語（`en` / `zh-CN` / `vi`）。医師が QR で作った直後は `null` で、患者が参加するときに1回だけ書く（それまで API は 409）。`doctorLang` は `ja`。
`members[0]` が医師（作った人）、`members[1]` が患者（QR で参加した人）。`from: "doctor"` の発言は医師だけ、`from: "patient"` の発言・回答・送信は患者だけ、既読は医師だけが書ける（`firestore.rules`）。
Firestore のルール案は `firestore.rules`（端末が書けるのは上の表の「端末」の項目だけ）。

## 構成の選び方（Own the Why）

**採用: Cloud Run 上の FastAPI を、端末が発言を作った直後に HTTP で呼ぶ。**

| 案 | 良い点 | 悪い点 | 判断 |
|---|---|---|---|
| **A. HTTP API（FastAPI / Cloud Run）を端末が呼ぶ** | 失敗がその場で返る（「もう一度」が作りやすい）。ローカルで `uvicorn` だけで動かせる。追加のインフラが無い。使わない時は 0 台で課金されない。検証済みの `unsaid_ai.py` をそのまま import できる | 端末の処理が「書く＋呼ぶ」の2段。呼ぶ前に端末が落ちると `analyzing` のまま残る | **採用**。2人・5日で E2E を確実に通すには、詰まったときに原因が見える構成が最優先 |
| B. Firestore トリガー（Eventarc → Cloud Run / Functions） | 端末は書くだけでよい | Eventarc と IAM の設定が増える。イベントは「最低1回」届くので二重処理対策が要る。API 自身の書き込みで再発火しないよう注意が要る。届くまで1秒前後の上乗せ。ローカルで再現しにくい | 不採用。仕組みはきれいだが、デモ前に詰まる箇所が増える |
| C. Cloud Run で Firestore を常時 listen | 端末は書くだけでよい | 常時起動（CPU 常時割り当て）が必要で課金が続く。台数が増えると同じ発言を複数台が処理する | 不採用。Cloud Run の「リクエストが来たら動く」モデルと合わない |
| D. 端末から Gemini を直接（Firebase AI Logic） | サーバーが要らない | プロンプト・スキーマ・再試行が端末側に出る。言語追加や修正のたびにアプリ更新。結果を誰が書いたか（改ざん）をルールで守りにくい | 不採用。Gemini の結果は信頼できる側（サーバー）だけが書く |

その他の判断:
- **API は id だけ受け取り、本文と言語は Firestore から読む。** 端末から本文を受け取ると、Firestore の本文と訳した本文がずれる・他人の session を訳させる、といった穴になる。
- **端末が先に発言を作る。** 自分の吹き出しが 0 秒で出る（Firestore の画面への即時反映）。Gemini の 3〜7 秒の待ちを「確認しています…」として見せられる。
- **確認質問なしの発言は、analyze の流れで翻訳まで済ませる。** 端末の呼び出しが1回で済み、患者は逆翻訳を見てから送れる。
- **失敗は `status: "error"`** にして、同じ API の呼び直しで復帰できるようにした（合意したスキーマへの追加）。
- **`translation` に `back_translation` と `note` を追加**（合意したスキーマは `{lang, text}`）。逆翻訳は VoiceTra 等との差別化ポイント。
