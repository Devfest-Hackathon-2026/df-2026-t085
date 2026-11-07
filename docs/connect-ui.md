# Yusei の画面を裏側につなぐ手順

裏側（Cloud Run の API・Firestore・Gemini・STT）はできていて、smoke.py で端から端まで通っている。
残りは「画面から Firebase にログインして Firestore を読み書きし、API を呼ぶ」部分で、これは `web/unsaid.js` にまとめてある。
画面はこのファイルの関数を呼んで、`watchMessages` で届く発言の一覧を描くだけでよい。

## 1. kosora がやること（ブラウザで 10 分）

### 1-1. Firebase にウェブアプリを登録して firebaseConfig を取る

firebaseConfig は「ウェブアプリ」を登録しないと出てこない（プロジェクトを作っただけでは無い）。

1. https://console.firebase.google.com/ で `unsaid-devfest` を開く。
2. 左上の歯車 → **プロジェクトの設定** → **全般** タブ。
3. 下の方の「マイアプリ」に何も無ければ、**`</>`（ウェブ）** のアイコンを押す。
4. アプリのニックネームに `unsaid-web` と入れる。「Firebase Hosting も設定する」はチェックしなくてよい（後で `firebase init` でできる）。→ **アプリを登録**。
5. 「npm を使用」「`<script>` タグを使用」のどちらかが出るので、`const firebaseConfig = { ... }` の部分をコピー。
   後からでも、同じ「マイアプリ」→ `unsaid-web` →「SDK の設定と構成」→ **構成** で見られる。
6. `web/firebase-config.js` の `firebaseConfig` をその値に置き換える。

`apiKey` は秘密ではない（ブラウザに配る前提の値）。リポジトリにコミットしてよい。守っているのは `firestore.rules` と API 側のメンバー確認。

### 1-2. 匿名ログインとルール（smoke.py が通っていれば済んでいる）

- Authentication → Sign-in method → **匿名** が「有効」になっているか確認。
- Firestore Database → ルール に `firestore.rules` の中身が入っているか確認（違っていたら貼って「公開」）。
- Authentication → 設定 → **承認済みドメイン** に、画面を開く場所が入っているか。`localhost` と `unsaid-devfest.web.app` / `.firebaseapp.com` は最初から入っている。それ以外（例: ngrok の URL）で開くならここに足す。

### 1-3. Yusei に渡すもの

- GitHub: リポジトリの Settings → Collaborators → **Add people** で Yusei を招待。
- 渡す値は `web/firebase-config.js` にすべて入っている（firebaseConfig と API の URL）。別途送るものは無い。
- Firebase コンソールを Yusei にも見せたいときは、プロジェクトの設定 → **ユーザーと権限** → メンバーを追加（「閲覧者」で十分）。Firestore の中身を見ながら画面を作れるので便利。

### 1-4. API 側

何もしなくてよい。CORS は今は全許可（`ALLOWED_ORIGINS` 未設定）。本番の URL が決まったら docs/deploy.md の最後のとおり絞る。

## 2. Yusei がやること

### 2-1. まず動くか確かめる

```
cd web
python -m http.server 8000
```

Chrome で http://localhost:8000/stt-test.html を開いて、ログイン → 録音 → 送る が通れば、設定は正しい。
（マイクは `localhost` か `https` でしか動かない。HTML をダブルクリックで開くと動かない）

### 2-2. 画面から呼ぶ関数

画面のファイルを `web/` に置いて、`<script type="module">` から import する（ビルド不要）。
Vite / React などで npm の `firebase` を使う場合は、`web/unsaid.js` の先頭の import の URL を `"firebase/app"` `"firebase/auth"` `"firebase/firestore"` に替えれば、残りはそのまま使える。

```js
import * as unsaid from "./unsaid.js";
```

| 関数 | どの画面で | すること |
|---|---|---|
| `login()` | 両方、最初に | 匿名ログイン。再読み込みしても同じ人のまま |
| `createSession(patientLang)` | 患者 | 言語（`en` / `zh-CN` / `vi`）を選んだら session を作り、id を返す。この id を QR か URL（`?s=<id>`）で医師に見せる |
| `joinSession(sid)` | 医師 | QR / URL の id で参加。参加するまで session は読めない |
| `watchSession(sid, cb)` | 両方 | `patientLang` などを読む（参加した後） |
| `watchMessages(sid, cb)` | 両方 | 発言の一覧（古い順）。**画面の表示はすべてここから**。何か書き足されるたびに cb が呼ばれる |
| `say(sid, from, text)` | 両方 | 発言する。`from` は `"patient"` / `"doctor"` |
| `answer(sid, mid, type, text)` | 患者 | 確認質問に答える。`option`（選んだ文字）/ `free_text`（入力）/ `unsure`（`""`） |
| `send(sid, mid)` | 患者 | 逆翻訳を見て「送信」 |
| `receive(sid, mid)` | 医師 | 患者の発言を画面に出したら呼ぶ（既読） |
| `retry(sid, m)` | 患者・医師 | `status: "error"` の発言の「もう一度」ボタン |
| `new Recorder()` / `transcribe(sid, speaker, wav)` | 両方 | 音声入力。返った文字を入力欄に入れ、本人が見てから `say()` |

API が失敗すると `say` / `answer` / `retry` が `ApiError`（`.status` に HTTP コード）を投げる。`401` はログインし直し、`502` は発言が `error` になるので「もう一度」を出す。

### 2-3. 描き方の約束（ここを外すと遅く見える・表示が欠ける）

1. **表示は `watchMessages` の cb だけで決める。** `await say()` の後に画面を変えない。確認質問は API の返事より先に Firestore に届く（PR #7 で速くした分がここで効く）。
2. **facts は遅れて届く。** `status` が `clarifying` / `confirmed` になった直後は `facts` が無いことがある。「あれば出す」にしておけば 1〜数秒後に勝手に出る。
3. **status ごとの表示**

| status | 患者画面 | 医師画面 |
|---|---|---|
| `analyzing` | 自分の吹き出し＋「確認しています…」 | 出さない |
| `clarifying`（`clarification.answer` が null） | `clarification.question` と選択肢。原文の `clarification.target` をハイライト | 出さない |
| `clarifying`（answer あり） | 「翻訳しています…」 | 出さない |
| `confirmed` | 「医師にはこう伝わります」＋ `translation.back_translation` ＋「送信」 | 出さない |
| `sent` | 送信済み | `translation.text`（日本語）＋ `translation.note`（あれば注意書き）＋ `facts[].doctor_gloss`。出したら `receive()` |
| `received` | 既読 | 表示 |
| `error` | 「もう一度」 | 出さない |

   医師の発言は `analyzing` → `sent` だけ。患者画面には `sent` になったら `translation.text`（患者の言語）を出す。

4. **確認質問の選択肢** = `clarification.options`（モデルが作る、患者の言語）＋ `fixed_options.unsure`（「うまく説明できない」→ `answer(sid, mid, "unsure")`）＋ `fixed_options.free_text`（「自分で書く」→ 入力欄 → `answer(sid, mid, "free_text", 入力)`）。

5. **誰が何を書くか**（`firestore.rules` で決まっているので、これ以外を書くと permission-denied になる）

| 書く人 | 書けるもの |
|---|---|
| 端末（発言を作るとき） | `from` / `original` / `status: "analyzing"` / `createdAt` だけ |
| 患者の端末 | `clarification.answer`、`confirmed` → `sent`（`sentAt`） |
| 医師の端末 | `sent` → `received`（`receivedAt`） |
| API（Cloud Run） | `facts` / `clarification`（answer 以外）/ `translation` / `error` / それ以外の status |

画面側で状態を進めたくなっても、上の関数以外で Firestore を書かないこと。

## 3. 2台でつなぐ最小の確認（11/8 夜の E2E）

まずは **同じ PC の Chrome 2 窓**（片方をシークレットウィンドウ）で、両方 `http://localhost:8000/...` で開く。別の uid になるので患者と医師を1台で試せる。

スマホ ⇔ PC で試すときは https が要る（マイクと匿名ログインのため）。Firebase Hosting に出すのが一番早い:

```
npm install -g firebase-tools
firebase login
firebase init hosting      # プロジェクトは unsaid-devfest、public ディレクトリは web、SPA は No
firebase deploy --only hosting
```

`https://unsaid-devfest.web.app/<画面のファイル名>` で両方の端末から開ける。

### チェックリスト

- [ ] 患者: 言語を選ぶ → session ができ、id（QR / URL）が出る
- [ ] 医師: id で参加できる（ブラウザのコンソールに permission-denied が出ない）
- [ ] 患者: "I've had a headache for a while." を送る → 自分の吹き出しがすぐ出る →「確認しています…」
- [ ] 患者: 数秒で確認質問と選択肢が出て、"for a while" がハイライトされる
- [ ] 患者: 選択肢を選ぶ →「翻訳しています…」→ 逆翻訳と「送信」
- [ ] 患者: 送信 → 医師の画面に日本語訳が出る（facts は少し遅れて出る）→ 患者の画面が既読になる
- [ ] 医師: 「この薬は1日3回、食後に飲んでください。」を送る → 患者の画面に英語で出る
- [ ] 「うまく説明できない」を選んだとき、医師の画面に `translation.note` の注意書きが出る
- [ ] 音声: 押して話す → 文字が入力欄に入る → 直して送れる（患者は英語、医師は日本語で聞き取る）
- [ ] 再読み込みしても同じ session の続きが見える（同じ uid のまま）
- [ ] 待ち時間をメモ（送ってから確認質問が出るまで、目安 3〜5 秒。デモ日は `--min-instances 1`）

### つまずいたとき

| 症状 | 原因と対処 |
|---|---|
| `auth/configuration-not-found` / `auth/admin-restricted-operation` | 匿名ログインが無効。1-2 を確認 |
| `auth/unauthorized-domain` | 開いている URL のドメインが承認済みドメインに無い。1-2 |
| `auth/api-key-not-valid` | `web/firebase-config.js` の値が違う。1-1 の「構成」からコピーし直す |
| `FirebaseError: Missing or insufficient permissions` | 参加していない session を読んでいる（医師は先に `joinSession`）か、決まっていない項目を書いている（2-3 の 5） |
| 確認質問が出ず `analyzing` のまま | `say()` の API 呼び出しが失敗している。コンソールの `ApiError` を見る。401 ならログイン、502 ならメッセージが `error` になる |
| CORS エラー | Cloud Run の `ALLOWED_ORIGINS` を設定した後なら、その URL を足す（docs/deploy.md） |
| マイクが使えない | `localhost` か `https` で開いていない |
