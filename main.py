"""UNSAID の Cloud Run バックエンド（FastAPI）。

クライアントがメッセージを Firestore に作った直後に、その id をこの API に渡す。
サーバーは Firestore から本文と言語を読み直して Gemini を呼び、結果を同じメッセージに書き戻す。
画面は onSnapshot で変化を受け取るので、API のレスポンスを待たなくても表示は更新される。

  POST /analyze    患者の発言: analyzing → clarifying（確認質問あり）/ confirmed（質問なし、翻訳まで済む）
  POST /translate  患者が回答した後: clarifying → confirmed ／ 医師の発言: analyzing → sent
  POST /transcribe 音声 → 文字（Firestore には書かない。端末が確認してから発言を作る）

詳しい契約は docs/api.md。
"""
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import firebase_admin
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from firebase_admin import auth, firestore
from pydantic import BaseModel

import stt
import unsaid_ai

log = logging.getLogger("unsaid")
logging.basicConfig(level=logging.INFO)

MAX_AUDIO_BYTES = 2_000_000  # 16kHz モノラル WAV で約 60 秒分。Speech-to-Text の同期認識の上限に合わせる
MAX_CHARS = 1000  # 1発言の上限。長すぎる入力でトークンを使い切らないように
# 1つの発言を処理している間の「処理中」の印の有効期限。Gemini の再試行をすべて使い切っても収まる長さ。
# 画面は 25 秒待っても進まないと「もう一度」を出す（web/ui.js の STUCK_MS）。押されたときにまだ処理中なら 409 で何もしない。
# サーバーが途中で落ちて印が残っても、この時間が過ぎれば「もう一度」でやり直せる
LOCK_SEC = 60
# 確認質問を作る呼び出しが失敗したとき、確認なしの翻訳で先に進める。医師画面の注意書きに出す（doctorLang は ja）
NO_CHECK_NOTE = "自動の確認ができませんでした。あいまいな言葉がそのまま訳されている可能性があります。"
# 開発中は "*"。本番は Firebase App Hosting の URL をカンマ区切りで渡す（Cloud Run の環境変数）
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

@asynccontextmanager
async def lifespan(app):
    # 最初の Gemini 呼び出しが遅いので、起動直後に裏で1回済ませておく
    threading.Thread(target=unsaid_ai.warm_up, daemon=True).start()
    yield


app = FastAPI(title="UNSAID API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["Authorization", "Content-Type"],
)

_db = None


def db():
    global _db
    if _db is None:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(options={"projectId": unsaid_ai.PROJECT})
        _db = firestore.client()
    return _db


def current_uid(authorization: str = Header(default="")):
    """Firebase（匿名ログイン）の ID トークンを確認して uid を返す。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authorization: Bearer <Firebase ID token> が必要です")
    db()  # firebase_admin の初期化を済ませる
    try:
        return auth.verify_id_token(authorization.removeprefix("Bearer "))["uid"]
    except Exception:
        raise HTTPException(401, "ID トークンが無効です")


class MessageRef(BaseModel):
    sessionId: str
    messageId: str


def _session(session_id: str, uid: str):
    """セッションのメンバーか確認し、(セッションの参照, セッション) を返す。"""
    s_ref = db().collection("sessions").document(session_id)
    s = s_ref.get()
    if not s.exists:
        raise HTTPException(404, "session がありません")
    session = s.to_dict()
    if uid not in session.get("members", []):
        raise HTTPException(403, "この session のメンバーではありません")
    if not session.get("patientLang"):
        raise HTTPException(409, "患者がまだ参加していません（QR から参加すると patientLang が決まります）")
    return s_ref, session


def _load(ref: MessageRef, uid: str):
    """セッションのメンバーか確認し、(セッション, メッセージの参照, メッセージ) を返す。"""
    s_ref, session = _session(ref.sessionId, uid)
    m_ref = s_ref.collection("messages").document(ref.messageId)
    m = m_ref.get()
    if not m.exists:
        raise HTTPException(404, "message がありません")
    msg = m.to_dict()
    if not msg.get("original") or len(msg["original"]) > MAX_CHARS:
        raise HTTPException(400, f"original は 1〜{MAX_CHARS} 文字にしてください")
    return session, m_ref, msg


def _fail(m_ref, e):
    """Gemini が再試行しても失敗したとき。status を error にして画面で「もう一度」を出せるようにする。"""
    log.exception("gemini call failed")
    m_ref.update({"status": "error", "error": type(e).__name__, "lockedUntil": firestore.DELETE_FIELD})
    raise HTTPException(502, "AI の呼び出しに失敗しました。もう一度お試しください")


def _transact(m_ref, check):
    """メッセージを読み、check(msg) が返した変更を同じトランザクションで書く。check が例外を出したら何も書かない。"""
    @firestore.transactional
    def run(tx):
        msg = m_ref.get(transaction=tx).to_dict()
        tx.update(m_ref, check(msg))
        return msg
    return run(db().transaction())


def _claim(m_ref, statuses):
    """同じ発言を2つのリクエストが同時に処理しないように「処理中」の印（lockedUntil）を付ける。

    画面の「もう一度」や二重押しで同じ発言が2回届くと、Gemini を2回呼んで、
    患者が答えている最中に確認質問が別のものに書き換わることがある。それを防ぐ。
    """
    def check(msg):
        if msg.get("status") not in statuses:
            raise HTTPException(409, f"status が {msg.get('status')} なので処理済みです")
        locked = msg.get("lockedUntil")
        if locked and locked > datetime.now(timezone.utc):
            raise HTTPException(409, "この発言は処理中です")
        return {"lockedUntil": datetime.now(timezone.utc) + timedelta(seconds=LOCK_SEC)}
    return _transact(m_ref, check)


def _translation(out, lang):
    return {
        "lang": lang,
        "text": out["translation"],
        "back_translation": out["back_translation"],
        "note": out["note"],
        # 医師画面の「『しばらく』→ 約1週間」の1行。確認していない発言・医師の発言は ""
        "clarified_target": out.get("clarified_target", ""),
        "clarified_answer": out.get("clarified_answer", ""),
    }


def _patient_translate(session, msg, clarification=None):
    out, sec = unsaid_ai.translate(
        msg["original"], session["patientLang"], session["doctorLang"], "patient_to_doctor", clarification
    )
    log.info("translate patient_to_doctor %.1fs", sec)
    return _translation(out, session["doctorLang"])


@app.get("/health")  # Cloud Run は末尾が z のパス（/healthz など）を予約していて届かない
def healthz():
    return {"ok": True}


@app.post("/analyze")
def analyze(ref: MessageRef, uid: str = Depends(current_uid)):
    """患者の発言を解析する。確認質問が無ければそのまま翻訳まで進めて confirmed にする。

    患者の待ち時間を縮めるため、Gemini を3本並列に投げる（docs/ai-pipeline.md「待ち時間を縮める」）:
      clarify  確認質問だけ。出力が短く速い → 返ったらすぐ Firestore に書く（画面に質問が出る）
      facts    医師画面の補助表示。患者は待たないので、後から同じメッセージに書き足す
      translate 確認質問なしだった場合に備えた先回りの翻訳。質問ありなら結果は捨てる
    """
    t0 = time.time()
    session, m_ref, msg = _load(ref, uid)
    if msg.get("from") != "patient":
        raise HTTPException(400, "analyze は患者の発言だけです（医師の発言は /translate）")
    msg = _claim(m_ref, ("analyzing", "error"))

    args = (msg["original"], session["patientLang"], session["doctorLang"])
    f_tr = unsaid_ai.submit(_patient_translate, session, msg)
    if unsaid_ai.ANALYZE_MODE == "single":
        f_clar = f_facts = unsaid_ai.submit(unsaid_ai.analyze_single, *args)
    else:
        f_clar = unsaid_ai.submit(unsaid_ai.clarify, *args)
        f_facts = unsaid_ai.submit(unsaid_ai.facts, *args)
    if msg.get("status") == "error":  # 「もう一度」のときだけ。普段は書き込み1回分の往復を省く
        m_ref.update({"status": "analyzing", "error": firestore.DELETE_FIELD})

    # 1) 確認質問（患者が待っているもの）。作れなかったら確認なしの翻訳で先に進める（発言を止めない）
    try:
        out, _ = f_clar.result()
    except Exception:
        log.exception("clarify failed; continuing without clarification")
        out = None
    try:
        if out and out["clarifications"]:
            c = out["clarifications"][0]
            update = {
                "clarification": {
                    "target": c["target"],
                    "question": c["question"],
                    "ui_type": c["ui_type"],
                    "options": c["options"],
                    "fixed_options": c["fixed_options"],
                    "answer": None,
                },
                "translation": None,
                "status": "clarifying",
            }
        else:
            tr = f_tr.result()
            if out is None:
                tr["note"] = f"{tr['note']} {NO_CHECK_NOTE}".strip()
            update = {"clarification": None, "translation": tr, "status": "confirmed"}
    except Exception as e:
        _fail(m_ref, e)
    m_ref.update({**update, "lockedUntil": firestore.DELETE_FIELD})
    question_sec = time.time() - t0

    # 2) facts（医師画面の補助）。失敗しても発言は止めない
    try:
        update["facts"] = f_facts.result()[0]["facts"]
    except Exception:
        log.exception("facts failed")
        update["facts"] = []
    m_ref.update({"facts": update["facts"]})
    log.info("analyze mode=%s status=%s shown=%.1fs facts=%.1fs",
             unsaid_ai.ANALYZE_MODE, update["status"], question_sec, time.time() - t0)
    return update


@app.post("/translate")
def translate(ref: MessageRef, uid: str = Depends(current_uid)):
    """患者: 確認質問に答えた後に呼ぶ（答えは先に clarification.answer に書いておく）。
    医師: 発言を作った後に呼ぶ。確認なしで翻訳して sent にする。"""
    session, m_ref, msg = _load(ref, uid)
    status = msg.get("status")

    if msg.get("from") == "doctor":
        if status not in ("analyzing", "error"):
            raise HTTPException(409, f"status が {status} なので翻訳済みです")
        _claim(m_ref, ("analyzing", "error"))
        try:
            out, sec = unsaid_ai.translate(
                msg["original"], session["doctorLang"], session["patientLang"], "doctor_to_patient"
            )
            log.info("translate doctor_to_patient %.1fs", sec)
        except Exception as e:
            _fail(m_ref, e)
        update = {
            "translation": _translation(out, session["patientLang"]),
            "status": "sent",
            "sentAt": firestore.SERVER_TIMESTAMP,
            "error": firestore.DELETE_FIELD,
        }
        m_ref.update({**update, "lockedUntil": firestore.DELETE_FIELD})
        return {**update, "sentAt": None, "error": None}

    c = msg.get("clarification") or {}
    answer = c.get("answer")
    if status not in ("clarifying", "error") or not answer:
        raise HTTPException(409, "clarification.answer を書いてから呼んでください")
    if answer.get("type") not in ("option", "free_text", "unsure"):
        raise HTTPException(400, "answer.type は option / free_text / unsure のどれかです")
    _claim(m_ref, ("clarifying", "error"))
    try:
        translation = _patient_translate(session, msg, {
            "target": c["target"],
            "question": c["question"],
            "answer_type": answer["type"],
            "answer": answer.get("text", ""),
        })
    except Exception as e:
        _fail(m_ref, e)
    update = {"translation": translation, "status": "confirmed", "error": firestore.DELETE_FIELD}
    m_ref.update({**update, "lockedUntil": firestore.DELETE_FIELD})
    return {**update, "error": None}




@app.post("/transcribe")
async def transcribe(
    request: Request,
    sessionId: str = Query(...),
    speaker: str = Query(..., pattern="^(patient|doctor)$"),
    uid: str = Depends(current_uid),
):
    """録音した1発言を文字にして返す。ボディは音声そのもの（web/stt.js は 16kHz モノラル WAV を送る）。
    言語は session の patientLang / doctorLang から決める。音声も結果も保存しない。"""
    _, session = await run_in_threadpool(_session, sessionId, uid)  # メンバー以外にクレジットを使わせない
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "音声が空です")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, f"音声は {stt.MAX_SECONDS} 秒以内にしてください")
    lang = session["patientLang"] if speaker == "patient" else session["doctorLang"]
    if lang not in stt.STT_LANGS:
        raise HTTPException(400, f"{lang} は音声入力に未対応です（stt.STT_LANGS に追加）")
    try:
        text, sec = await run_in_threadpool(stt.transcribe, audio, lang)
    except Exception:
        log.exception("speech-to-text failed")
        raise HTTPException(502, "音声の聞き取りに失敗しました。もう一度話すか、文字で入力してください")
    log.info("transcribe %s %.1fs %dB chars=%d", lang, sec, len(audio), len(text))
    return {"text": text, "lang": lang}
