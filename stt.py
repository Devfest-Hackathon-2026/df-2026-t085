"""音声 → 文字（Cloud Speech-to-Text v2 / Chirp 3）。Cloud Run の /transcribe から使う。

音声は Speech-to-Text にリクエストの中身として直接渡すだけで、Cloud Storage にもログにも残さない。
文字にした結果は端末に返すだけで、Firestore にも書かない（患者が見て直してから発言として送る）。
なぜ Gemini ではなく Speech-to-Text か、なぜブラウザの音声認識ではないかは docs/stt.md。
"""
import os
import time

from google.api_core.client_options import ClientOptions
from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech as speech

PROJECT = os.environ.get("STT_PROJECT", "unsaid-devfest")
# Chirp 3 が使える場所。マルチリージョン "us" / "eu" か、対応している単一リージョン。
# 変えるときは Cloud Run の環境変数 STT_LOCATION / STT_MODEL で（コードの変更は不要）
LOCATION = os.environ.get("STT_LOCATION", "us")
MODEL = os.environ.get("STT_MODEL", "chirp_3")
TIMEOUT_SEC = 15
MAX_SECONDS = 30  # 1発言の録音の上限（同期認識の上限は 60 秒）

# unsaid_ai.LANGS のコード → Speech-to-Text の言語コード。言語を増やすときは両方に1行ずつ
STT_LANGS = {
    "ja": "ja-JP",
    "en": "en-US",
    "zh-CN": "cmn-Hans-CN",
    "vi": "vi-VN",
}

_client = None


def _client_():
    global _client
    if _client is None:
        endpoint = "speech.googleapis.com" if LOCATION == "global" else f"{LOCATION}-speech.googleapis.com"
        _client = SpeechClient(client_options=ClientOptions(api_endpoint=endpoint))
    return _client


def transcribe(audio: bytes, lang: str):
    """音声（WAV / WebM Opus / OGG Opus / MP3 / FLAC）を文字にする。(文字, 秒) を返す。聞き取れなければ ""。"""
    if lang not in STT_LANGS:
        raise ValueError(f"未対応の言語: {lang}")
    start = time.time()
    req = speech.RecognizeRequest(
        recognizer=f"projects/{PROJECT}/locations/{LOCATION}/recognizers/_",
        config=speech.RecognitionConfig(
            auto_decoding_config=speech.AutoDetectDecodingConfig(),
            model=MODEL,
            language_codes=[STT_LANGS[lang]],
            features=speech.RecognitionFeatures(enable_automatic_punctuation=True),
        ),
        content=audio,
    )
    res = _client_().recognize(request=req, timeout=TIMEOUT_SEC)
    parts = [r.alternatives[0].transcript.strip() for r in res.results if r.alternatives]
    sep = "" if lang in ("ja", "zh-CN") else " "  # 日本語・中国語は区切りを空白にしない
    return sep.join(p for p in parts if p), time.time() - start
