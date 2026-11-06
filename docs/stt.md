# 音声入力（STT）

## 流れ

```
端末  押して話す → 離す → 16kHz モノラル WAV にする（web/stt.js）
      POST /transcribe?sessionId=...&speaker=patient   ボディ = WAV
API   session の patientLang / doctorLang で Speech-to-Text（Chirp 3）に渡す → {text, lang} を返す
      （音声も文字も保存しない。Firestore にも書かない）
端末  文字を入力欄に入れる → 患者が見て、違えば直す → 「送る」
      → 今までどおり addDoc {from, original: text, status: "analyzing"} → /analyze（医師は /translate）
```

`/transcribe` の後ろは docs/api.md の流れそのまま。STT は「キーボードの代わり」で、既存の API と Firestore の形は何も変えていない。

| エラー | 意味 | 画面での扱い |
|---|---|---|
| 200 で `text: ""` | 何も聞き取れなかった | 「聞き取れませんでした」→ もう一度話す |
| 400 | 音声が空 / その言語が STT 未対応 | |
| 401 / 403 / 404 | /analyze と同じ（ログイン・メンバー・session） | |
| 413 | 30 秒より長い | 短く区切って話してもらう |
| 422 | `speaker` が patient / doctor 以外 | |
| 502 | Speech-to-Text の失敗 | 「もう一度話すか、文字で入力」。文字入力は常に残す |

## 方式の選び方（Own the Why）

**採用: Cloud Speech-to-Text v2（Chirp 3）を Cloud Run から呼ぶ。発言を作る前に、文字を患者に見せて直せるようにする。**

UNSAID の価値は「患者の曖昧な言い方（"a little"、"for a while"）をそのまま拾って確認する」こと。
なので STT に求めるのは **言った言葉を言ったとおりに書くこと**（整えない・要約しない・訳さない）と、**書いた結果を本人が確かめられること**。

| 案 | 良い点 | 悪い点 | 判断 |
|---|---|---|---|
| **A. Cloud Speech-to-Text v2 / Chirp 3（サーバー経由）** | 文字起こし専用モデルなので言ったとおりに書く（曖昧語が消えない）。多言語モデル1つで ja / en / zh / vi を同じコードで扱える（言語は `STT_LANGS` に1行）。どのブラウザでも同じ結果（端末で WAV にそろえる）。音声はリクエストに入れて渡すだけで保存しない。Google Cloud の IAM・ログ・課金の中で管理できる | 話し終わってから文字になる（逐次表示は無い）。`/transcribe` の往復が1回増える（目安 1〜2 秒、要計測）。$0.016/分 の従量課金 | **採用**。曖昧さを残すという製品の芯と、デモでの再現性を優先 |
| B. ブラウザの Web Speech API | 無料。話しながら文字が出る。サーバーの実装が要らない | 中身がブラウザ任せで、Chrome は Google、Edge は Microsoft、Safari は Apple の認識になる（同じ発話でも結果が違う・"Google 技術で作った"と言えない）。Firefox は使えない。対応言語・精度・どこに音声が送られるかを自分たちで管理できない。会場のネットや端末で突然動かないときに原因が追えない | 不採用。速いが、デモの再現性と説明責任（音声の行き先）を自分たちで持てない |
| C. 音声をそのまま Gemini に渡す（/analyze で一緒に） | 呼び出しが1回で済む。言い淀みや声色も入力にできる | 生成モデルなので「整えて」書きがち（言い直しを消す、曖昧語を言い換える、訳してしまう）。これは UNSAID が確認したい部分そのもの。文字になる前に解析まで進むので、聞き間違いを患者が直す機会が無い（聞き間違いがそのまま確認質問・翻訳に流れる）。/analyze が今の 3〜7 秒からさらに伸びる | 不採用。Gemini は「意味の確認と翻訳」に集中させ、聞き取りは専用モデルに任せる |
| D. Speech-to-Text のストリーミング（話しながら文字が出る） | A の精度で逐次表示できる | gRPC の双方向ストリームなのでブラウザから直接は呼べず、Cloud Run に WebSocket 中継が要る。1発言が数秒の問診では、逐次表示の得は小さい | 今は不採用。時間が余ったら A の上に足せる（API の形は変わらない） |

その他の判断:
- **端末で 16kHz モノラル WAV にしてから送る。** ブラウザの録音形式は Chrome が WebM/Opus、Safari が MP4/AAC とバラバラで、Speech-to-Text は MP4 を読めない。端末でそろえれば iPhone でも同じに動く。16kHz は音声認識の推奨で、30 秒でも 1MB 弱。
- **言語は端末から受け取らず session から決める。** `/analyze` と同じ考え方（端末の言うことをそのまま信じない）。患者は `patientLang`、医師は `doctorLang`。
- **`/transcribe` も session のメンバー確認をする。** URL を知っているだけの人にクレジットを使わせない。
- **文字は Firestore に書かず、患者が「送る」を押して初めて発言になる。** 聞き間違いを直す画面が「自分の言葉で確認してから送る」という UNSAID の流れと一致する。
- **場所は `us`（Chirp 3 が使えるマルチリージョン）。** 東京からの往復が少し増えるが、1発言に1回だけなので影響は小さい。Cloud Run の環境変数 `STT_LOCATION` / `STT_MODEL` で、コードを変えずに切り替えられる。
- **文字入力は必ず残す。** 声が出しにくい患者、騒がしい場所、STT が落ちたときの逃げ道。

### 費用（$20 のクレジット）

Speech-to-Text v2 の標準料金 $0.016/分。デモ・練習で 1 発言 5 秒 × 1000 回話しても約 83 分 ≈ **$1.3**。Gemini 側より小さい。

## Windows（PowerShell）での手順

コマンドは1つずつ、1行のまま貼り付ける。

### 1. Speech-to-Text を有効にして、API のアカウントに権限を足す（初回だけ）

```powershell
gcloud services enable speech.googleapis.com
```

```powershell
gcloud projects add-iam-policy-binding unsaid-devfest --member "serviceAccount:unsaid-api@unsaid-devfest.iam.gserviceaccount.com" --role roles/speech.client
```

### 2. ローカルのテスト

```powershell
cd C:\DEV\Project-Xingularity
pip install -r requirements.txt
python test_server.py
```

「すべて PASS」と出れば OK（Speech-to-Text は偽物に差し替えているのでクレジットは使わない）。

### 3. デプロイ（いつもと同じ1行）

```powershell
gcloud run deploy unsaid-api --source . --region asia-northeast1 --service-account unsaid-api@unsaid-devfest.iam.gserviceaccount.com --allow-unauthenticated --memory 512Mi --timeout 120
```

### 4. ブラウザでマイクから試す

```powershell
cd C:\DEV\Project-Xingularity\web
python -m http.server 8000
```

Chrome で http://localhost:8000/stt-test.html を開く（マイクは `localhost` か `https` でしか使えないので、ファイルをダブルクリックで開くのではなくこの URL で）。

1. 「Firebase の apiKey」に smoke.py の `--key` と同じ値を入れ、患者の言語を選んで「1. ログインして session を作る」。
2. 「2. 押している間だけ録音」を押したまま話して、離す。初回はマイクの許可を聞かれるので「許可」。
3. 数秒で聞き取った文字が出る。下に「離してから ○ 秒」と出るのが STT の待ち時間。
4. 「3. 発言として送る」で、今までの /analyze が走り、下に Firestore の中身（status が analyzing → clarifying / confirmed）が出る。
5. 話す人を「医師」にすると日本語で聞き取り、/translate に送る。

止めるときは PowerShell で `Ctrl + C`。テストで作った session は `stt-` で始まる名前で Firestore に残る。

### つまずいたとき

| 症状 | 原因と対処 |
|---|---|
| 「失敗: 音声の聞き取りに失敗しました」、ログに `403 PERMISSION_DENIED` (speech) | 手順 1 の `roles/speech.client` か `speech.googleapis.com` の有効化が抜けている |
| ログに `model "chirp_3" ... not supported` / `location` の文字 | その場所で Chirp 3 が使えない。`gcloud run services update unsaid-api --region asia-northeast1 --set-env-vars STT_LOCATION=us,STT_MODEL=chirp_3` の値を変えて試す（`eu` や `chirp_2` + `us-central1` など） |
| 「マイクを使えません」 | `localhost` ではなくファイルとして開いている／Chrome のアドレスバー左の鍵マークでマイクがブロックされている |
| 「聞き取れませんでした」が続く | Windows の「サウンドの設定 → 入力」で使うマイクと音量を確認 |
| CORS エラー | `ALLOWED_ORIGINS` に `http://localhost:8000` が入っていない（未設定なら全許可） |

ログの見方: `gcloud run services logs read unsaid-api --region asia-northeast1 --limit 50` で `transcribe en 1.3s 96000B chars=34` のような行が出る（言語・Speech-to-Text の秒数・音声のサイズ・文字数。中身の文字はログに出さない）。

## Yusei の画面に入れるとき

`web/stt.js` をそのまま import する。使い方はファイルの先頭のコメント。

- ボタンは「押している間だけ録音」（押す → `rec.start()`、離す → `rec.stop()` → `transcribe()`）。スマホでも誤操作が少ない。
- 返ってきた `text` は入力欄に入れるだけ。患者が「送る」を押したら docs/api.md の `say()`。
- 聞き取り中は「聞き取っています…」、`text` が空なら「聞き取れませんでした」。
- 医師の画面は `speaker` を `"doctor"` にする（日本語で聞き取る）。
