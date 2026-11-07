# AI パイプライン（analyze → 患者が確認 → translate）

```
患者の発言 ──analyze()──> facts + 確認質問(最大1, 患者の言語) ──患者が回答──> translate(patient_to_doctor) ──> 医師(ja)
医師の発言 ─────────────────────────────────────────────────> translate(doctor_to_patient) ──> 患者
```

| 呼び出し | プロンプト | スキーマ | Firestore への書き込み |
|---|---|---|---|
| `clarify(utterance, patient_lang, doctor_lang)` | `prompts/clarify_system.txt` | `schemas/clarify.json` | `clarification`（`clarifications[0]` か null）, `status`（先に書く） |
| `facts(utterance, patient_lang, doctor_lang)` | `prompts/facts_system.txt` | `schemas/facts.json` | `facts`（後から書き足す） |
| `analyze_single(...)`（`ANALYZE_MODE=single` のときだけ） | `prompts/analyze_system.txt` | `schemas/analyze.json` | 上の2つを1回で |
| `translate(original, src, dst, direction, clarification)` | `prompts/translate_system.txt` | `schemas/translate.json` | `translation {lang, text}` |

## 多言語化のための設計判断（Own the Why）

- **システムプロンプトは英語で1本、言語はリクエストの JSON で渡す。**
  日本語のプロンプトだとモデルが日本語で質問を書きがち（患者に日本語が出るのが一番まずい失敗）。
  言語ごとにプロンプトを作る案は、言語数 × プロンプト修正の手間になるので不採用。
  プロンプトが固定なので Gemini の暗黙キャッシュも効きやすい。
- **新しい言語の追加 = `unsaid_ai.py` の `LANGS` と `FIXED_OPTIONS` に1行ずつ。** プロンプトは触らない。
- **「うまく説明できない」「自分で書く」はモデルに作らせず UI が固定で足す。**
  モデルが毎回違う言い回しにすると、「自分で書く」→テキスト入力 の分岐が言語ごとに壊れる。id（`unsure` / `free_text`）で判定する。
- **確認質問は最大1つ**（スキーマの `maxItems: 1` ＋ コード側でも切り詰め）。
- **翻訳は確認の後に別呼び出し。** 確認前に訳すと曖昧な箇所を訳し直す必要があり、確認で確定した意味を訳に反映できない。
  「説明できない」が選ばれたら曖昧なまま訳し、`note` で医師に知らせる。
- **`back_translation`（逆翻訳）** を両方向で返す。話し手が「相手にこう伝わる」を自分の言語で確認できる。VoiceTra 等との差別化ポイントにもなる。
- **facts の `doctor_gloss`** は医師画面の補助表示用。確認対象の箇所は曖昧なまま訳すので、確認後は `translation` を主表示にする。

## テスト

```
python test.py            # 全部
python test.py analyze    # 確認質問だけ
python test.py translate  # 翻訳だけ
```

英語11件・中国語3件・ベトナム語3件・翻訳5件。期待結果と違うと FAIL と理由を出す。
質問・選択肢に日本語（かな）が混ざったら FAIL にしている。

## 遅延対策（11/5 実測: 普段 2〜7 秒、まれに 40〜70 秒の外れ値）

- `TIMEOUT_SEC = 12` で打ち切って `RETRIES = 1` 回だけ打ち直す。外れ値を待つより、もう一度呼ぶ方がほぼ確実に速い。
- test.py の各行に `thoughts`（思考トークン数）を表示。遅い回で思考トークンが多ければモデルが考えすぎ、普通なら待たされているだけ（混雑・ルーティング）と切り分けられる。
- `TRANSLATE_MODEL` を分けたので、翻訳だけ Flash-Lite 系に替えて速度と品質を比べられる。
- `warm_up()` をサーバー起動時に呼び、最初の1回が遅い問題をデモ前に済ませる。
- 429（RESOURCE_EXHAUSTED）は Gemini の共有キャパシティの一時的な混雑なので、1→2→4→8秒（＋揺らぎ）の間隔で最大4回再試行する。400/403 は打ち直しても直らないので即エラー。

## 待ち時間を縮める（11/6）

11/6 の実測: STT 1.3 秒 ＋ `/analyze` 3〜7 秒。質問なしの発言は analyze の後に translate を直列に呼ぶので 〜10 秒。
Gemini の応答時間はほぼ出力トークン数に比例するのに、患者が待っていた analyze の出力の大半は、患者には見せない
facts（医師向けの日本語 gloss つき）だった。

**採用: `/analyze` で Gemini を3本並列に投げ、患者が待つのは出力の短い1本だけにする。**

```
/analyze ─┬─ clarify   質問だけ（短い）     → 返ったらすぐ Firestore に書く（画面に質問が出る）
          ├─ facts     医師画面の補助       → 後から書き足す（医師が見るのは患者が送信した後）
          └─ translate 質問なしに備えた先回り → 質問なしなら使う。質問ありなら捨てる
```

- 質問あり: 待ち時間 = clarify 1本分。質問なし: clarify と translate の遅い方（直列の和だったものが max になる）。
- 切り替え: Cloud Run の環境変数 `ANALYZE_MODE=single` で従来の1回呼び出しに戻せる（品質が落ちたときの逃げ道）。
- 比べ方: `python bench.py`（旧と新を交互に呼んで中央値を出す）、品質は `python test.py analyze`。

| 案 | 長所 | 短所 | 判断 |
|---|---|---|---|
| **clarify / facts を分けて並列＋先回り翻訳** | 待つのは短い出力1本。API の呼び方は変わらない。env で戻せる | 1発言あたりの呼び出しが 1〜2 → 3 本（質問ありだと翻訳1本は捨てる。入出力とも数百トークンの短い呼び出し）。質問の判断が facts の分解を「下書き」にできなくなる | **採用**。費用はクレジットに対して誤差。判断の品質は test.py で旧と同じか確認する |
| ストリーミングで質問だけ先に読む | 呼び出し1本のまま | JSON の途中を解析する必要があり、スキーマ順（質問を先に）を守らせる保証がない。facts の生成はやはり待つ | 不採用 |
| thinking_level を下げる | 簡単 | `minimal` は gemini-3.8-flash で使えない（11/5 確認）。low 以下に下げる手がない | 対象外 |
| 軽いモデル（Flash-Lite 系）に替える | 速い | 「どこが曖昧か」の判断が製品の核。品質を落としてまで縮める所ではない | 翻訳だけなら `TRANSLATE_MODEL` で試せる |
| Cloud Run の `--min-instances 1` | コールドスタート（〜10 秒）が消える | 常時課金 | デモ当日だけ（docs/deploy.md） |
| 待ち時間は UI で見せ方を工夫する | 実時間は変わらない | — | 併用（文字起こしを即表示、「確認しています…」） |
