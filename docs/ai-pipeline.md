# AI パイプライン（analyze → 患者が確認 → translate）

```
患者の発言 ──analyze()──> facts + 確認質問(最大1, 患者の言語) ──患者が回答──> translate(patient_to_doctor) ──> 医師(ja)
医師の発言 ─────────────────────────────────────────────────> translate(doctor_to_patient) ──> 患者
```

| 呼び出し | プロンプト | スキーマ | Firestore への書き込み |
|---|---|---|---|
| `analyze(utterance, patient_lang, doctor_lang)` | `prompts/analyze_system.txt` | `schemas/analyze.json` | `facts`, `clarification`（`clarifications[0]` か null）, `status` |
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
