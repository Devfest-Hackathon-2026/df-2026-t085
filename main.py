"""UNSAID の Cloud Run バックエンド（FastAPI）。

クライアントがメッセージを Firestore に作った直後に、その id をこの API に渡す。
サーバーは Firestore から本文と言語を読み直して Gemini を呼び、結果を同じメッセージに書き戻す。
画面は onSnapshot で変化を受け取るので、API のレスポンスを待たなくても表示は更新される。

  POST /analyze    患者の発言: analyzing → clarifying（確認質問あり）/ confirmed（質問なし、翻訳まで済む）
  POST /translate  患者が回答した後: clarifying → confirmed ／ 医師の発言: analyzing → sent

詳しい契約は docs/api.md。
"""
import logging
import os
import threading
from contextlib import asynccontextmanager

import firebase_admin
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from firebase_admin import auth, firestore
from pydantic import BaseModel

import unsaid_ai

log = logging.getLogger("unsaid")
logging.basicConfig(level=logging.INFO)

MAX_CHARS = 1000  # 1発言の上限。長すぎる入力でトークンを使い切らないように
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


def _load(ref: MessageRef, uid: str):
    """セッションのメンバーか確認し、(セッション, メッセージの参照, メッセージ) を返す。"""
    s_ref = db().collection("sessions").document(ref.sessionId)
    s = s_ref.get()
    if not s.exists:
        raise HTTPException(404, "session がありません")
    session = s.to_dict()
    if uid not in session.get("members", []):
        raise HTTPException(403, "この session のメンバーではありません")
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
    m_ref.update({"status": "error", "error": type(e).__name__})
    raise HTTPException(502, "AI の呼び出しに失敗しました。もう一度お試しください")


def _translation(out, lang):
    return {
        "lang": lang,
        "text": out["translation"],
        "back_translation": out["back_translation"],
        "note": out["note"],
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
    """患者の発言を解析する。確認質問が無ければそのまま翻訳まで進めて confirmed にする。"""
    session, m_ref, msg = _load(ref, uid)
    if msg.get("from") != "patient":
        raise HTTPException(400, "analyze は患者の発言だけです（医師の発言は /translate）")
    if msg.get("status") not in ("analyzing", "error"):
        raise HTTPException(409, f"status が {msg.get('status')} なので解析済みです")
    m_ref.update({"status": "analyzing", "error": firestore.DELETE_FIELD})

    try:
        out, sec = unsaid_ai.analyze(msg["original"], session["patientLang"], session["doctorLang"])
        log.info("analyze %.1fs needs_clarification=%s", sec, out["needs_clarification"])
        update = {"facts": out["facts"], "clarification": None, "translation": None}
        if out["clarifications"]:
            c = out["clarifications"][0]
            update["clarification"] = {
                "target": c["target"],
                "question": c["question"],
                "ui_type": c["ui_type"],
                "options": c["options"],
                "fixed_options": c["fixed_options"],
                "answer": None,
            }
            update["status"] = "clarifying"
        else:
            update["translation"] = _patient_translate(session, msg)
            update["status"] = "confirmed"
    except Exception as e:
        _fail(m_ref, e)

    m_ref.update(update)
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
        m_ref.update(update)
        return {**update, "sentAt": None, "error": None}

    c = msg.get("clarification") or {}
    answer = c.get("answer")
    if status not in ("clarifying", "error") or not answer:
        raise HTTPException(409, "clarification.answer を書いてから呼んでください")
    if answer.get("type") not in ("option", "free_text", "unsure"):
        raise HTTPException(400, "answer.type は option / free_text / unsure のどれかです")
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
    m_ref.update(update)
    return {**update, "error": None}


