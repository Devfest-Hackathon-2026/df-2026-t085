"""UNSAID の Cloud Run バックエンド（FastAPI）。

クライアントがメッセージを Firestore に作った直後に、その id をこの API に渡す。
サーバーは Firestore から本文と言語を読み直して Gemini を呼び、結果を同じメッセージに書き戻す。
画面は onSnapshot で変化を受け取るので、API のレスポンスを待たなくても表示は更新される。

  POST /analyze    患者の発言: analyzing → confirmed（翻訳まで済む）。並列に Meaning Engine（情報の構造化・確認候補）
                   医師の確認質問・理解の確認への答え（replyTo）もここ。選択肢の答えは Gemini を呼ばずに sent まで進む
  POST /translate  医師の発言: analyzing → sent ／（CLARIFY_MODE=patient のとき）患者が回答した後: clarifying → confirmed
  POST /send       患者が逆翻訳を見て「送信」: confirmed → sent。確認質問・理解の確認への答えなら、元の情報に結びつける
  POST /clarify/send    医師が確認候補（または自分で書いた質問）を患者に送る
  POST /clarify/update  医師が確認候補を保留・無視・戻す
  POST /fact/update     医師が情報を「確認済み」「要修正」にする／取り消す
  POST /teachback/start   医師が自分の説明について「理解を確認」する
  POST /teachback/resolve 医師が理解の確認の結果を「確認済み」「再説明が必要」にする
  POST /meaning/retry   意味の解析に失敗した発言をやり直す
  POST /transcribe 音声 → 文字（Firestore には書かない。端末が確認してから発言を作る）
  POST /end        医師が「診察を終える」: その session の発言と session 自体を消す

状態の変わり方は clinical.py（AI のイベントで「医師確認済み」にはならない）。詳しい契約は docs/api.md、全体は ARCHITECTURE.md。
"""
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import firebase_admin
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from firebase_admin import auth, firestore
from pydantic import BaseModel

import clinical
import stt
import unsaid_ai
import ussa

log = logging.getLogger("unsaid")
logging.basicConfig(level=logging.INFO)

MAX_AUDIO_BYTES = 2_000_000  # 16kHz モノラル WAV で約 60 秒分。Speech-to-Text の同期認識の上限に合わせる
MAX_CHARS = 1000  # 1発言の上限。長すぎる入力でトークンを使い切らないように
# 1つの発言を処理している間の「処理中」の印の有効期限。Gemini の再試行をすべて使い切っても収まる長さ。
# 画面は 25 秒待っても進まないと「もう一度」を出す（web/ui.js の STUCK_MS）。押されたときにまだ処理中なら 409 で何もしない。
# サーバーが途中で落ちて印が残っても、この時間が過ぎれば「もう一度」でやり直せる
LOCK_SEC = 110  # 意図の解析・照合・訳し直し・再照合まで含めても収まる長さ（Cloud Run の --timeout 120 より短く）
# 意味の解析のやり直しの期限（lease）。Cloud Run の --timeout 120 より長くし、期限が切れた再試行のリクエストはもう
# 終わっている（またはサーバーが止まった）とみなして、ほかの人がやり直せるようにする。古い処理の書き戻しは所有者 ID で防ぐ。
RETRY_LEASE_SEC = 130
# 確認質問を作る呼び出しが失敗したとき、確認なしの翻訳で先に進める。医師画面の注意書きに出す（doctorLang は ja）
NO_CHECK_NOTE = "自動の確認ができませんでした。あいまいな言葉がそのまま訳されている可能性があります。"
# 問診の記録を残し続けないための期限。サーバーが書くたびに expireAt を「今から TTL_HOURS 後」にする。
# Firestore の TTL ポリシー（sessions と messages の expireAt）で、閉じ忘れた session も自動で消える（docs/deploy.md）。
# 普段は医師の「診察を終える」（/end）でその場で消す
TTL_HOURS = int(os.environ.get("TTL_HOURS", "24"))
# 1つの session で API を呼べる回数の上限（/analyze・/translate・/transcribe の合計）。
# 医師も匿名ログインなので、誰でも session を作れる。1つの session でクレジットを使い切られないようにする。
# 問診1回は発言 20〜30 回 × 約2回 ≒ 60 回の想定
MAX_CALLS_PER_SESSION = int(os.environ.get("MAX_CALLS_PER_SESSION", "200"))
# 開発中は "*"。本番は Firebase App Hosting の URL をカンマ区切りで渡す（Cloud Run の環境変数）
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
# 確認質問を誰が決めるか。
#   doctor : Gemini が確認候補を作り、医師が選んで送る（既定。医師の確認なしに患者へ質問を送らない）
#   patient: 従来どおり、送信前に患者へ自動で1問だけ聞く（docs/ai-pipeline.md）
CLARIFY_MODE = os.environ.get("CLARIFY_MODE", "doctor")
# Meaning Engine に渡す直前の会話の数（短い答え・「それ」を理解するため。事実は今の発言からだけ取る）
CONTEXT_TURNS = 6
# 問診を作ってからこの時間を過ぎたら、API は 410 で何もしない（TTL で消える前でも使えなくする）
SESSION_MAX_HOURS = int(os.environ.get("SESSION_MAX_HOURS", "12"))
# ステージングは同じプロジェクトの別のデータベースに分けられる（ARCHITECTURE.md「環境の分け方」）。空なら (default)
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE") or None
MAX_QUESTION_CHARS = 300
# Verification Engine で重要な不一致が出たときに訳し直す回数・検証の呼び出しが失敗したときにやり直す回数。
# 使い切ったら発言者による確認（NEEDS_REVIEW）に切り替える（無制限に再試行しない）
TRANSLATION_RETRIES = int(os.environ.get("TRANSLATION_RETRIES", "1"))
VERIFY_RETRIES = 1
# 同じ人が同じ文をこの秒数以内にもう一度送ったら、二重送信として取り消す
DUPLICATE_SEC = 10

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
# Firebase（Auth・Firestore）のプロジェクト。Gemini のプロジェクト（unsaid_ai.PROJECT、UNSAID_VERTEX_PROJECT）を
# 評価のために替えても、こちらは変わらない
FIREBASE_PROJECT = os.environ.get("FIREBASE_PROJECT", "unsaid-devfest")


def db():
    global _db
    if _db is None:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(options={"projectId": FIREBASE_PROJECT})
        _db = firestore.client(database_id=FIRESTORE_DATABASE) if FIRESTORE_DATABASE else firestore.client()
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


class SessionRef(BaseModel):
    sessionId: str


class ClarifySend(BaseModel):
    sessionId: str
    messageId: str | None = None       # 元の患者の発言（確認候補がある発言）
    candidateId: str | None = None     # 確認候補をそのまま／編集して送るとき
    factId: str | None = None          # 自分で書いた質問を、元の発言のどの情報についてかひもづけるとき
    questionDoctor: str | None = None  # 編集した質問・自分で書いた質問（医師の言語）
    issueId: str | None = None         # 未解決事項（Conversation Auditor）について聞くとき


class CandidateAction(BaseModel):
    sessionId: str
    messageId: str
    candidateId: str
    action: str  # hold / dismiss / restore


class FactAction(BaseModel):
    sessionId: str
    messageId: str
    factId: str
    action: str  # confirm / flag / reset
    note: str = ""


class TeachbackAction(BaseModel):
    sessionId: str
    messageId: str
    action: str = ""  # resolve: confirm / reexplain


class TeachbackStart(BaseModel):
    sessionId: str
    messageId: str                   # 医師の説明
    previousId: str | None = None    # 再説明のとき、前の理解の確認の id（確認済みにすると前の相違も解決になる）


class IntentChoice(BaseModel):
    sessionId: str
    messageId: str
    choice: str       # 選択肢の id（i1 …）/ "free_text" / "none"（どれでもない → 言い直す）
    text: str = ""    # free_text のときの説明（発言者の言語）


class ReviewAction(BaseModel):
    sessionId: str
    messageId: str
    action: str       # send_unverified（未検証として送る）/ rephrase（言い直す）


class IssueAction(BaseModel):
    sessionId: str
    issueId: str
    action: str       # hold / unhold / resolve / dismiss / reopen
    note: str = ""


def _expire_at():
    return datetime.now(timezone.utc) + timedelta(hours=TTL_HOURS)


def _member_session(session_id: str, uid: str):
    """セッションのメンバーか確認し、(セッションの参照, セッション) を返す。"""
    if not session_id or "/" in session_id:
        raise HTTPException(400, "sessionId が不正です")
    s_ref = db().collection("sessions").document(session_id)
    s = s_ref.get()
    if not s.exists:
        raise HTTPException(404, "session がありません")
    session = s.to_dict()
    if uid not in session.get("members", []):
        raise HTTPException(403, "この session のメンバーではありません")
    created = session.get("createdAt")
    if isinstance(created, datetime) and datetime.now(timezone.utc) - created > timedelta(hours=SESSION_MAX_HOURS):
        raise HTTPException(410, "この問診は期限が切れました。新しい問診を始めてください")
    return s_ref, session


def _role(session, uid):
    members = session.get("members", [])
    if members and members[0] == uid:
        return "doctor"
    if len(members) > 1 and members[1] == uid:
        return "patient"
    return None


def _doctor_session(session_id: str, uid: str):
    """医師（members[0]）だけが使える API の入口。"""
    s_ref, session = _member_session(session_id, uid)
    if _role(session, uid) != "doctor":
        raise HTTPException(403, "医師だけが使えます")
    return s_ref, session


def _session(session_id: str, uid: str):
    """Gemini / Speech-to-Text を使う API の入口。メンバーか・患者が参加済みか・呼び出し回数の上限を確かめ、
    呼んだ回数と消える期限（expireAt）を session に書く。"""
    s_ref, session = _member_session(session_id, uid)
    if not session.get("patientLang"):
        raise HTTPException(409, "患者がまだ参加していません（QR から参加すると patientLang が決まります）")
    if session.get("apiCalls", 0) >= MAX_CALLS_PER_SESSION:
        raise HTTPException(429, "この問診で使える回数を超えました。新しい問診を始めてください")
    s_ref.update({"apiCalls": firestore.Increment(1), "expireAt": _expire_at()})
    return s_ref, session


def _msg_ref(s_ref, message_id):
    if not message_id or "/" in message_id:
        raise HTTPException(400, "messageId が不正です")
    return s_ref.collection("messages").document(message_id)


def _get(ref, what="message"):
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(404, f"{what} がありません")
    return snap.to_dict()


def _load(ref: MessageRef, uid: str):
    """セッションのメンバーか確認し、(セッションの参照, セッション, メッセージの参照, メッセージ) を返す。"""
    s_ref, session = _session(ref.sessionId, uid)
    m_ref = _msg_ref(s_ref, ref.messageId)
    msg = _get(m_ref)
    if not msg.get("original") or len(msg["original"]) > MAX_CHARS:
        raise HTTPException(400, f"original は 1〜{MAX_CHARS} 文字にしてください")
    return s_ref, session, m_ref, msg


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


def _tx(refs, fn, create=None):
    """refs を同じトランザクションで読み、fn(読んだ中身のリスト) が返した変更（refs と同じ順、None は書かない）を書く。
    create=(参照, 中身) を渡すと、同じトランザクションで新しい発言も作る。fn が例外を出したら何も書かない。"""
    @firestore.transactional
    def run(tx):
        datas = []
        for r in refs:
            snap = r.get(transaction=tx)
            datas.append(snap.to_dict() if snap.exists else None)
        updates = fn(datas)
        for r, u in zip(refs, updates):
            if u:
                tx.update(r, u)
        if create:
            tx.create(*create)
        return datas
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
        return {"lockedUntil": datetime.now(timezone.utc) + timedelta(seconds=LOCK_SEC), "expireAt": _expire_at()}
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


def _patient_translate(session, msg, clarification=None, context=None):
    args = (msg["original"], session["patientLang"], session["doctorLang"], "patient_to_doctor", clarification)
    out, sec = unsaid_ai.translate(*args, context=context) if context else unsaid_ai.translate(*args)
    log.info("translate patient_to_doctor %.1fs", sec)
    return _translation(out, session["doctorLang"])


@app.get("/health")  # Cloud Run は末尾が z のパス（/healthz など）を予約していて届かない
def healthz():
    return {"ok": True}


@app.post("/analyze")
def analyze(ref: MessageRef, uid: str = Depends(current_uid)):
    """患者の発言を解析する。

    CLARIFY_MODE=doctor（既定）: 翻訳（患者が待つ）と Meaning Engine（医師が見る）を並列に投げる。
      確認質問は患者に自動では出さず、確認候補として医師に見せる（医師が選んで送る: /clarify/send）。
    replyTo がある発言は、医師の確認質問・理解の確認への答え。
    CLARIFY_MODE=patient: 従来どおり、送信前に患者へ自動で1問だけ聞く（_analyze_legacy）。
    """
    t0 = time.time()
    s_ref, session, m_ref, msg = _load(ref, uid)
    if msg.get("from") != "patient":
        raise HTTPException(400, "analyze は患者の発言だけです（医師の発言は /translate）")
    reply = _reply_target(s_ref, msg)
    if not reply and CLARIFY_MODE == "patient":
        msg = _claim(m_ref, ("analyzing", "error"))
        if msg.get("status") == "error":  # 「もう一度」のときだけ
            m_ref.update({"status": "analyzing", "error": firestore.DELETE_FIELD})
        return _analyze_legacy(t0, session, m_ref, msg)
    if _role(session, uid) != "patient":
        raise HTTPException(403, "患者の発言を送れるのは患者だけです")
    if reply and reply[1].get("kind") == "clarify" and (msg.get("answer") or {}).get("type") in ("option", "unsure", "none"):
        msg = _claim(m_ref, ("analyzing", "error"))
        return _answer_fixed(s_ref, session, ref.messageId, m_ref, msg, reply[1])
    return _gate_start(t0, s_ref, session, ref.messageId, m_ref, msg, "patient", reply[1] if reply else None)


def _analyze_legacy(t0, session, m_ref, msg):
    """従来の流れ（CLARIFY_MODE=patient）。患者の待ち時間を縮めるため、Gemini を3本並列に投げる
    （docs/ai-pipeline.md「待ち時間を縮める」）:
      clarify  確認質問だけ。出力が短く速い → 返ったらすぐ Firestore に書く（画面に質問が出る）
      facts    医師画面の補助表示。患者は待たないので、後から同じメッセージに書き足す
      translate 確認質問なしだった場合に備えた先回りの翻訳。質問ありなら結果は捨てる
    """

    args = (msg["original"], session["patientLang"], session["doctorLang"])
    f_tr = unsaid_ai.submit(_patient_translate, session, msg)
    if unsaid_ai.ANALYZE_MODE == "single":
        f_clar = f_facts = unsaid_ai.submit(unsaid_ai.analyze_single, *args)
    else:
        f_clar = unsaid_ai.submit(unsaid_ai.clarify, *args)
        f_facts = unsaid_ai.submit(unsaid_ai.facts, *args)

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
    s_ref, session, m_ref, msg = _load(ref, uid)
    status = msg.get("status")

    if msg.get("from") == "doctor" and CLARIFY_MODE == "doctor":
        if _role(session, uid) != "doctor":
            raise HTTPException(403, "医師の発言を送れるのは医師だけです")
        return _gate_start(time.time(), s_ref, session, ref.messageId, m_ref, msg, "doctor", None)

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


# ───────────────────────── 意味の構造化・確認候補・理解の確認 ─────────────────────────

SHOWN = ("sent", "received")


def _all_messages(s_ref):
    """この問診の発言を古い順に [(id, 中身)]。会話の文脈と「もう聞いた語」に使う（問診1回で数十件なので全部読む）。"""
    rows = [(d.id, d.to_dict()) for d in s_ref.collection("messages").stream()]
    far = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(rows, key=lambda r: r[1].get("createdAt") if isinstance(r[1].get("createdAt"), datetime) else far)


def _doctor_text(m):
    """会話の文脈として渡す、医師の言語の文。医師の発言は原文、患者の発言は訳。"""
    return m.get("original", "") if m.get("from") == "doctor" else (m.get("translation") or {}).get("text", "")


def _context(msgs, upto_id, n=CONTEXT_TURNS):
    out = []
    for mid, m in msgs:
        if mid == upto_id:
            break
        if m.get("status") in SHOWN and _doctor_text(m):
            out.append({"speaker": m.get("from"), "text": _doctor_text(m)[:300]})
    return out[-n:]


def _known_quotes(msgs, exclude_id):
    """この問診で、もう確認候補・確認質問になった原文の語（同じことを2回聞かない）。"""
    out = []
    for mid, m in msgs:
        if mid == exclude_id or m.get("status") not in SHOWN:
            continue
        out += [c["source_quote"] for c in (m.get("meaning") or {}).get("candidates", []) if c.get("source_quote")]
        if (m.get("clarification") or {}).get("target"):
            out.append(m["clarification"]["target"])  # CLARIFY_MODE=patient で患者に聞いた語
    return out


def _reply_target(s_ref, msg):
    """replyTo がある患者の発言: 答えている医師の質問を確かめて (参照, 中身) を返す。無ければ None。"""
    rid = msg.get("replyTo")
    if not rid:
        return None
    q_ref = _msg_ref(s_ref, rid)
    snap = q_ref.get()
    q = snap.to_dict() if snap.exists else None
    if not q or q.get("from") != "doctor" or q.get("kind") not in ("clarify", "teachback"):
        raise HTTPException(400, "replyTo は医師の確認質問か、理解の確認の発言にしてください")
    if q.get("status") not in SHOWN:
        raise HTTPException(400, "まだ届いていない質問には答えられません")
    a = msg.get("answer") or {"type": "free_text"}
    if a.get("type") not in ("option", "unsure", "none", "free_text"):
        raise HTTPException(400, "answer.type は option / unsure / none / free_text のどれかです")
    if q["kind"] == "teachback" and a["type"] != "free_text":
        raise HTTPException(400, "理解の確認には自分の言葉で答えます")
    if a["type"] == "option":
        opts = q["clarify"].get("options", [])
        i = a.get("index")
        if not isinstance(i, int) or not 0 <= i < len(opts) or msg.get("original") != opts[i]:
            raise HTTPException(400, "選んだ選択肢が質問と合いません")
    return q_ref, q


def _empty_meaning(status="ready", **kw):
    return {"status": status, "facts": [], "candidates": [], "rejected": [], "at": clinical.now_iso(),
            "resolution": {"applies": False, "resolves": "no", "meaning_doctor": "", "source_quote": "",
                           "evidence_status": "none"}, **kw}


def _meaning_result(future, msg, mid, session, known):
    """Meaning Engine の結果を検証して保存する形にする。失敗しても発言は止めない（原文と翻訳は残る）。"""
    try:
        raw, sec = future.result()
        raw.pop("_meta", None)
        out = clinical.build_meaning(raw, utterance_id=mid, source_text=msg["original"],
                                     source_lang=session["patientLang"], known_quotes=known,
                                     schema=unsaid_ai.schema("meaning"))
        log.info("meaning %.1fs facts=%d candidates=%d rejected=%d",
                 sec, len(out["facts"]), len(out["candidates"]), len(out["rejected"]))
        return out
    except Exception as e:
        # 患者の発言・AI の出力はログに出さない（例外の種類と、スキーマ違反なら場所だけ）
        log.warning("meaning failed: %s %s", type(e).__name__, e if isinstance(e, clinical.InvalidAIOutput) else "")
        return _empty_meaning("error", error=type(e).__name__)


# ───────────────────────── USSA: 送信操作から Safety Gate まで（ARCHITECTURE.md）─────────────────────────
class _Stop(Exception):
    """この解析の結果はもう使わない（取り消された・新しい解析が始まった・問診が終わった）。何も書かない。"""


class _Refused(Exception):
    """Safety Gate の最終チェックを通らなかった。送らずに FAILED にする（理由は gate.history に残す）。"""


def _langs(session, role):
    """(発言者の言語, 相手の言語)"""
    if role == "patient":
        return session["patientLang"], session["doctorLang"]
    return session["doctorLang"], session["patientLang"]


def _turns(msgs, upto_id, n=CONTEXT_TURNS):
    """Source Agent と翻訳に渡す直前の会話（送信済みの発言の原文と訳）。"""
    out = []
    for mid, m in msgs:
        if mid == upto_id:
            break
        if m.get("status") in SHOWN:
            out.append({"speaker": m.get("from"), "original": (m.get("original") or "")[:300],
                        "translation": ((m.get("translation") or {}).get("text") or "")[:300]})
    return out[-n:]


def _duplicate_of(msgs, mid, msg):
    """同じ人が同じ文を DUPLICATE_SEC 秒以内にもう送っていて、取り消されていなければ、その id（二重送信）。"""
    t = msg.get("createdAt")
    if not isinstance(t, datetime):
        return None
    for oid, o in msgs:
        ot = o.get("createdAt")
        if (oid != mid and o.get("from") == msg.get("from") and o.get("original") == msg.get("original")
                and o.get("replyTo") == msg.get("replyTo") and isinstance(ot, datetime) and ot <= t
                and (t - ot).total_seconds() < DUPLICATE_SEC and o.get("status") not in ("cancelled", "error")):
            return oid
    return None


def _new_gate():
    # 端末が発言を作った（＝発言者が送信操作をした）時点で原文が決まる。下書き（DRAFT）は端末の入力欄の中だけ
    return {"state": ussa.SOURCE_CONFIRMED, "source_confirmed_at": ussa.now_iso(), "history": [], "attempts": [],
            "timings": {}, "revealed": []}


def _gate_claim(m_ref, role, allowed, intent=None):
    """解析を始める印。run_id が変わるので、前の解析が後から終わっても結果は送られない（古い結果の混入を防ぐ）。"""
    holder = {}

    def check(msg):
        if msg.get("from") != role:
            raise HTTPException(403, "発言者だけが操作できます")
        now = datetime.now(timezone.utc)
        g = msg.get("gate")
        if g is None:
            if msg.get("status") not in ("analyzing", "error"):
                raise HTTPException(409, f"status が {msg.get('status')} なので処理済みです")
            g = _new_gate()
        locked = msg.get("lockedUntil")
        if locked and locked > now:
            raise HTTPException(409, "この発言は処理中です")
        if g["state"] in (ussa.ANALYZING, ussa.VERIFYING):  # 前の解析が途中で止まった（処理中の印が切れた）
            ussa.gate_move(g, ussa.FAILED, reason="stuck")
        if g["state"] not in allowed:
            raise HTTPException(409, f"送信の状態が {g['state']} なので解析できません")
        ussa.gate_move(g, ussa.ANALYZING, by=role)
        g["run_id"] = uuid.uuid4().hex[:12]
        g["source_hash"] = ussa.source_hash(msg.get("original"))
        g["run_started_at"] = ussa.now_iso()
        if intent is not None:
            g["intent"] = intent
        holder["gate"] = g
        return {"gate": g, "status": "analyzing", "lockedUntil": now + timedelta(seconds=LOCK_SEC),
                "expireAt": _expire_at(), "error": firestore.DELETE_FIELD}
    _transact(m_ref, check)
    return holder["gate"]


def _gate_update(m_ref, run_id, fn):
    """今の解析（run_id）のときだけ gate を書き換える。fn(m, g) は gate を直し、ほかに書く項目を返す。"""
    def tx(datas):
        m = datas[0]
        if not m:
            raise _Stop("deleted")
        g = m.get("gate") or {}
        if g.get("run_id") != run_id or g.get("state") in (ussa.CANCELLED, ussa.SENT):
            raise _Stop(g.get("state") or "stale")
        extra = fn(m, g) or {}
        return [{"gate": g, **extra}]
    _tx([m_ref], tx)


def _gate_fail(m_ref, run_id, stage, e):
    log.warning("gate %s failed: %s", stage, type(e).__name__)  # 患者の発言・AI の出力はログに出さない

    def f(m, g):
        ussa.gate_move(g, ussa.FAILED, by="gate", reason=f"{stage}:{type(e).__name__}")
        return {"status": "error", "error": type(e).__name__, "lockedUntil": firestore.DELETE_FIELD}
    try:
        _gate_update(m_ref, run_id, f)
    except _Stop:
        pass
    raise HTTPException(502, "AI の呼び出しに失敗しました。もう一度お試しください（発言は残っています）")


def _timed(fn, *a, **k):
    t = time.time()
    out, _ = fn(*a, **k)
    out.pop("_meta", None)
    return out, round((time.time() - t) * 1000)


def _draft(msg, src_lang, dst_lang, direction, q_ctx, turns, ci, fix):
    """Translation Agent: 原文から相手の言語へ直接訳す（英語を経由しない）。確認済みの意図だけを確定した意味として渡す。"""
    out, ms = _timed(unsaid_ai.translate, msg["original"], src_lang, dst_lang, direction, None, q_ctx,
                     turns=turns, confirmed_intent=ci, fix=fix)
    return {"translation": out["translation"], "back_translation": out["back_translation"],
            "note": out.get("note", "")}, ms


def _verify(source, src_lang, translation, dst_lang, role, intent, ci, timings, key):
    """Verification Engine: D（ルール）は今すぐ、B（訳文だけから枠）と C（直接の照合）は並列に。
    A（原文の枠）は Source Agent が作ったもの。B と C には翻訳した側の判断を渡さない（C には本人が確認した意図だけ）。"""
    t = time.time()
    rules = ussa.rule_check(source, src_lang, translation, dst_lang)
    frame_b = llm = None
    failures = []
    for _ in range(VERIFY_RETRIES + 1):
        f_b = unsaid_ai.submit(_timed, unsaid_ai.extract_frame, translation, dst_lang, role)
        f_c = unsaid_ai.submit(_timed, unsaid_ai.compare_meaning, source, src_lang, translation, dst_lang, role, ci)
        failures = []
        try:
            raw_b, _ = f_b.result()
            clinical.validate(raw_b, unsaid_ai.schema("frame_extract"))
            frame_b = ussa.build_frame(raw_b["frame"], translation, raw_b.get("evidence", []))
        except Exception as e:
            failures.append(f"frame:{type(e).__name__}")
        try:
            raw_c, _ = f_c.result()
            llm = ussa.build_llm_check(raw_c, source=source, translation=translation,
                                       schema=unsaid_ai.schema("verify_compare"))
        except Exception as e:
            failures.append(f"compare:{type(e).__name__}")
        if not failures:
            break
    frames = ussa.compare_frames(intent["frame"], frame_b) if frame_b else []
    v = ussa.combine(rules, frames, llm if not failures else None, failures)
    v["translation_frame"] = frame_b
    timings[key] = round((time.time() - t) * 1000)
    return v


def _confirmed_frame(intent):
    """本人が意図を確認したら、原文の枠の「発話行為」は確認した意味のものにし、字面の肯定・否定は使わない
    （「没事」を「続けてよい」と確認したのに、字面の否定で訳の照合が食い違わないように）。"""
    f = dict(intent["frame"])
    c = intent.get("confirmed") or {}
    f["speech_act"] = c.get("speech_act", "other") if c.get("kind") == "option" else "other"
    f["polarity"] = "none"
    return {**intent, "frame": f}


def _deliver(s_ref, m_ref, run_id, role, *, verification, draft, attempts, timings, session, override=None):
    """Safety Gate: 最新の発言をトランザクションで読み直し、ussa.send_checks をすべて満たすときだけ送る。
    送るのはこのコードで、LLM ではない。問診が終わっていたら送らない。"""
    def fn(datas):
        m, sess = datas
        if not m or not sess:
            raise _Stop("deleted")
        g = m.get("gate") or {}
        if g.get("run_id") != run_id or g.get("state") in (ussa.CANCELLED, ussa.SENT):
            raise _Stop(g.get("state") or "stale")
        g["verification"], g["draft"], g["attempts"] = verification, draft, attempts
        g.setdefault("timings", {}).update(timings)
        if override:
            g["override"] = override
        ussa.gate_move(g, ussa.READY, by="gate")
        fails = ussa.send_checks({**m, "gate": g}, run_id=run_id, author_role=role)
        if fails:
            log.warning("gate refused: %s", fails)
            raise _Refused(",".join(fails))
        ussa.gate_move(g, ussa.SENT, by="gate")
        g["timings"]["total_ms"] = round((time.time() - _iso_ts(g.get("run_started_at"))) * 1000)
        _, dst = _langs(session, role)
        return [{"gate": g, "status": "sent", "sentAt": firestore.SERVER_TIMESTAMP, "lockedUntil": firestore.DELETE_FIELD,
                 "translation": {"lang": dst, "text": draft["translation"], "back_translation": draft["back_translation"],
                                 "note": draft.get("note", ""), "clarified_target": "", "clarified_answer": ""}}, None]
    _tx([m_ref, s_ref], fn)


def _iso_ts(iso):
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return time.time()


def _gate_start(t0, s_ref, session, mid, m_ref, msg, role, question):
    """発言者の送信操作（発言を作った）の後に呼ばれる。二重送信なら取り消す。"""
    msgs = _all_messages(s_ref)
    dup = _duplicate_of(msgs, mid, msg)
    if dup:
        def check(m):
            g = m.get("gate") or _new_gate()
            ussa.gate_move(g, ussa.CANCELLED, by="gate", reason=f"duplicate_of:{dup}")
            return {"gate": g, "status": "cancelled"}
        _transact(m_ref, check)
        raise HTTPException(409, "同じ発言を続けて送ったので、2つ目は取り消しました")
    gate = _gate_claim(m_ref, role, (ussa.SOURCE_CONFIRMED, ussa.FAILED))
    return _gate_run(t0, s_ref, session, mid, m_ref, msg, role, question, msgs, gate)


def _lease_expired(until):
    """lease の期限（ISO 文字列）が過ぎたか。期限の無い古いデータは切れたとみなす（永久に止まらないように）。"""
    try:
        return datetime.fromisoformat(until) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


def _pending_from(question):
    """確認質問への答えを Meaning Engine で解釈するための、質問の文脈（初回と再試行で同じものを使う）。"""
    if not question or question.get("kind") != "clarify":
        return None
    c = question.get("clarify") or {}
    return {"source_quote": c.get("source_quote", ""), "question_doctor_lang": question["original"],
            "question_patient_lang": question["translation"]["text"], "unresolved": c.get("unresolved", "")}


def _meaning_context(msgs, mid, ci):
    """Meaning Engine に渡す直前の会話。本人が意図を確認していれば、その意味も足す（初回と再試行で同じ）。"""
    ctx = _context(msgs, mid)
    if ci:
        ctx = ctx + [{"speaker": "patient",
                      "text": f"（本人が確認した意味）{ci.get('meaning_listener') or ci.get('meaning_speaker')}"}]
    return ctx


def _gate_run(t0, s_ref, session, mid, m_ref, msg, role, question, msgs, gate, intent=None):
    """USSA の本体。
      1) Source Agent（意図と原文の枠）・Translation Agent（下書き）・Meaning Engine（患者の問診メモ）を並列に
      2) 意図が決まらなければ、発言者に確認（AWAITING_INTENT_CLARIFICATION）。下書きの訳は使わない
      3) Verification Engine。重要な不一致なら訳し直して TRANSLATION_RETRIES 回まで再検証
      4) VERIFIED / AMBIGUOUS → Safety Gate（_deliver）。それ以外 → 発言者の確認（NEEDS_REVIEW）。自動では送らない
      5) 送ったら: 問診メモ・答えの結びつけ・Conversation Auditor
    """
    run_id = gate["run_id"]
    src_lang, dst_lang = _langs(session, role)
    direction = "patient_to_doctor" if role == "patient" else "doctor_to_patient"
    turns = _turns(msgs, mid)
    timings = {}
    q_ctx = ({"question_as_shown": question["translation"]["text"], "question": question["original"]}
             if question and role == "patient" else None)
    confirmed = intent.get("confirmed") if intent and intent["state"] == ussa.USER_CONFIRMED else None
    ci = ({"meaning_speaker": confirmed["meaning_speaker"], "meaning_listener": confirmed.get("meaning_listener", "")}
          if confirmed else None)
    is_tb = bool(question and question.get("kind") == "teachback")
    f_src = None if intent else unsaid_ai.submit(_timed, unsaid_ai.source_agent, msg["original"], role, src_lang,
                                                 dst_lang, turns)
    f_tr = unsaid_ai.submit(_draft, msg, src_lang, dst_lang, direction, q_ctx, turns, ci, None)
    f_mean = f_tb = None
    known = _known_quotes(msgs, mid)
    if role == "patient" and not is_tb:
        f_mean = unsaid_ai.submit(unsaid_ai.meaning, msg["original"], src_lang, dst_lang,
                                  _meaning_context(msgs, mid, ci), known, _pending_from(question))
    exp = dict(msgs).get(question["teachback"]["explanation_id"]) if is_tb else None
    try:
        # 1) 発話意図（Source Agent）
        if intent is None:
            try:
                raw, timings["source_ms"] = f_src.result()
                intent = ussa.build_intent(raw, source_text=msg["original"], speaker_lang=src_lang,
                                           listener_lang=dst_lang, schema=unsaid_ai.schema("source"))
            except Exception as e:
                _gate_fail(m_ref, run_id, "source", e)
        if is_tb:
            f_tb = unsaid_ai.submit(unsaid_ai.compare_understanding, exp.get("original", ""),
                                    (exp.get("translation") or {}).get("text", ""), msg["original"], src_lang, dst_lang,
                                    ((exp.get("gate") or {}).get("intent") or {}).get("frame"))
        if intent["state"] not in ussa.INTENT_OK:
            def wait(m, g):
                ussa.gate_move(g, ussa.AWAITING_INTENT, by="gate", reason=intent["state"])
                g["intent"] = intent
                g["timings"].update(timings)
                return {"status": "awaiting_intent", "lockedUntil": firestore.DELETE_FIELD}
            _gate_update(m_ref, run_id, wait)
            log.info("gate awaiting intent (%s) %.1fs", intent["state"], time.time() - t0)
            return {"status": "awaiting_intent", "intent": intent}
        frame_intent = _confirmed_frame(intent) if confirmed else intent

        # 2) 下書きの訳（Translation Agent）
        try:
            draft, timings["translate_ms"] = f_tr.result()
        except Exception as e:
            _gate_fail(m_ref, run_id, "translate", e)

        def verifying(m, g):
            ussa.gate_move(g, ussa.VERIFYING, by="gate")
            g["intent"] = intent
            return {"status": "analyzing"}
        _gate_update(m_ref, run_id, verifying)

        # 3) 検証（Verification Engine）。重要な不一致なら訳し直す（回数に上限）
        attempts = []
        for attempt in range(TRANSLATION_RETRIES + 1):
            v = _verify(msg["original"], src_lang, draft["translation"], dst_lang, role, frame_intent, ci, timings,
                        f"verify_ms_{attempt + 1}")
            attempts.append({"translation": draft["translation"], "status": v["status"],
                             "issues": [{k: i.get(k, "") for k in ("type", "strength", "layer", "detail")} for i in v["issues"]][:8]})
            if v["status"] != ussa.MISMATCH or attempt == TRANSLATION_RETRIES:
                break
            fix = [{"type": i["type"], "detail": i["detail"]} for i in v["issues"] if i["strength"] == "strong"][:6]
            try:
                draft, timings[f"retranslate_ms_{attempt + 1}"] = _draft(msg, src_lang, dst_lang, direction, q_ctx,
                                                                          turns, ci, fix)
            except Exception as e:
                log.warning("retranslate failed: %s", type(e).__name__)
                break

        # 4) 送る（Safety Gate）か、発言者の確認へ
        meaning = _meaning_result(f_mean, msg, mid, session, known) if f_mean else None
        tb_result = _teachback_result(f_tb, msg) if f_tb else None
        extra = {}
        if meaning is not None:
            extra.update(meaning=meaning, facts=clinical.legacy_facts(meaning))
        if tb_result is not None:
            extra["teachback_result"] = tb_result
        if v["status"] in (ussa.VERIFIED, ussa.AMBIGUOUS):
            if extra:
                m_ref.update(extra)  # 問診メモ・理解の比較は送る前に書いておく（送った瞬間に医師の画面にそろう）
            t_gate = time.time()
            try:
                _deliver(s_ref, m_ref, run_id, role, verification=v, draft=draft, attempts=attempts,
                         timings={**timings, "gate_ms": None}, session=session)
            except _Refused as e:
                _gate_fail(m_ref, run_id, f"gate_refused[{e}]", e)
            timings["gate_ms"] = round((time.time() - t_gate) * 1000)
            _after_send(s_ref, session, mid, role, intent, question)
            log.info("gate sent role=%s verification=%s attempts=%d %.1fs", role, v["status"], len(attempts),
                     time.time() - t0)
            return {"status": "sent", "verification": v["status"], "attempts": len(attempts), "timings": timings}

        def review(m, g):
            ussa.gate_move(g, ussa.NEEDS_REVIEW, by="gate", reason=v["status"])
            g["verification"], g["draft"], g["attempts"] = v, draft, attempts
            g["timings"].update(timings)
            return {"status": "needs_review", "lockedUntil": firestore.DELETE_FIELD, **extra}
        _gate_update(m_ref, run_id, review)
        log.info("gate needs review role=%s verification=%s %.1fs", role, v["status"], time.time() - t0)
        return {"status": "needs_review", "verification": v["status"], "timings": timings}
    except _Stop as e:
        log.info("gate stopped: %s", e)
        return {"status": "stopped", "reason": str(e)}


def _teachback_result(future, msg):
    try:
        raw, sec = future.result()
        raw.pop("_meta", None)
        return clinical.build_teachback(raw, restatement=msg["original"], schema=unsaid_ai.schema("teachback"))
    except Exception as e:
        log.warning("teachback compare failed: %s", type(e).__name__)
        return {"overall": "error", "points": [], "suggested_reexplanation": "", "rejected": 0,
                "decided_by": "ai", "at": clinical.now_iso()}


def _after_send(s_ref, session, mid, role, intent, question):
    """送った後: 確認質問なら元の候補に結びつけ、答えなら元の情報に結びつけ、会話全体を見直す（Conversation Auditor）。
    失敗しても送った発言はそのまま。"""
    try:
        _link_question(s_ref, mid)
    except Exception as e:
        log.warning("link question failed: %s", type(e).__name__)
    try:
        _apply_reply(s_ref, mid)
    except Exception as e:
        log.warning("apply_reply failed: %s", type(e).__name__)
    try:
        _audit(s_ref, mid, intent, question)
    except Exception as e:
        log.warning("audit failed: %s", type(e).__name__)


def _answer_fixed(s_ref, session, mid, m_ref, msg, question):
    """確認質問に選択肢・「分からない」・「どれでもない」で答えた: 訳は医師が送った質問の選択肢にあるので Gemini を呼ばない。
    Safety Gate は通す（本人が選んだ＝意図は本人の確認、訳は用意された対訳＝PRETRANSLATED と記録する）。"""
    c, a = question["clarify"], msg["answer"]
    if a["type"] == "option":
        dr = c["options_doctor"][a["index"]]
        res = {"applies": True, "resolves": "yes", "meaning_doctor": dr, "source_quote": msg["original"],
               "evidence_status": "exact"}
    else:
        dr = unsaid_ai.ANSWER_FIXED_DOCTOR[a["type"]]
        res = {"applies": True, "resolves": "no", "meaning_doctor": "", "source_quote": "", "evidence_status": "none"}
    g = msg.get("gate") or _new_gate()
    run_id = g.get("run_id") or uuid.uuid4().hex[:12]
    if g["state"] == ussa.SOURCE_CONFIRMED:
        ussa.gate_move(g, ussa.ANALYZING, by="patient")
    g.update(run_id=run_id, source_hash=ussa.source_hash(msg["original"]), run_started_at=ussa.now_iso(),
             intent={"state": ussa.USER_CONFIRMED, "decided_by": "speaker", "speech_act": "answer",
                     "interpretations": [], "frame": ussa.build_frame({"speech_act": "answer"}, msg["original"]),
                     "confirmed": {"kind": "option", "meaning_speaker": msg["original"], "meaning_listener": dr}})
    ussa.gate_move(g, ussa.VERIFYING, by="gate")
    m_ref.update({"gate": g, "meaning": _empty_meaning(resolution=res), "facts": [], "clarification": None})
    _deliver(s_ref, m_ref, run_id, "patient",
             verification={"status": "PRETRANSLATED", "issues": [], "failures": [], "at": ussa.now_iso(),
                           "note": "医師が送った確認質問の選択肢の対訳"},
             draft={"translation": dr, "back_translation": msg["original"], "note": ""}, attempts=[], timings={},
             session=session)
    _after_send(s_ref, session, mid, "patient", None, question)
    return {"status": "sent"}


# ───────────────────────── Conversation Auditor ─────────────────────────
def _utterances(msgs):
    return {i: {"original": m.get("original", ""), "translation": (m.get("translation") or {}).get("text", ""),
                "speaker": m.get("from")} for i, m in msgs if m.get("status") in SHOWN}


def _audit_update(s_ref, fn):
    """session の audit（未解決事項と記憶）をトランザクションで書き換える。端末は書けない（ルールの対象外の項目）。"""
    def tx(datas):
        sd = datas[0]
        if not sd:
            raise _Stop("deleted")
        a = sd.get("audit") or ussa.empty_audit()
        fn(a)
        a["updated_at"] = ussa.now_iso()
        return [{"audit": a}]
    try:
        _tx([s_ref], tx)
    except _Stop:
        pass


def _audit(s_ref, mid, intent, question):
    """送った発言1つ分の差分更新。会話全体ではなく、最新の発言・記憶（重要な発言の要点と枠）・未解決事項だけを使う。
      ルール: 患者が説明を言い直した（confirm_understanding / 理解の確認への答え）ら、直前の医師の指示と数量・回数・時間帯を照合
      LLM: 矛盾・理解の相違・未回答・説明不足などを、証拠（実在する引用）つきで提案。医師が片付けた事項は繰り返さない
    """
    t = time.time()
    msgs = _all_messages(s_ref)
    utter = _utterances(msgs)
    m = dict(msgs).get(mid)
    if not m or mid not in utter:
        return
    frame = (intent or {}).get("frame") or ussa.build_frame({}, m.get("original", ""))
    item = ussa.memory_item(mid, m, frame)
    sess = s_ref.get().to_dict() or {}
    a0 = sess.get("audit") or ussa.empty_audit()
    restated = m.get("from") == "patient" and (frame.get("speech_act") == "confirm_understanding"
                                                or (question or {}).get("kind") == "teachback")
    raw = None
    if not (question and question.get("kind") == "clarify" and (m.get("answer") or {}).get("type") != "free_text"):
        try:
            raw, _ = unsaid_ai.audit(
                {"utterance_id": mid, "speaker": m.get("from"), "text": item["text_doctor_lang"],
                 "speech_act": frame.get("speech_act")},
                a0["memory"],
                [{k: i[k] for k in ("issue_id", "type", "description", "related_utterances")}
                 for i in a0["issues"] if i["status"] in ussa.ACTIVE_ISSUE],
                [{k: i[k] for k in ("type", "description", "related_utterances")}
                 for i in a0["issues"] if i["status"] not in ussa.ACTIVE_ISSUE])
            raw.pop("_meta", None)
            clinical.validate(raw, unsaid_ai.schema("audit"))
        except Exception as e:
            log.warning("auditor call failed: %s", type(e).__name__)
            raw = None

    def fn(a):
        if restated:
            instr = next((x for x in reversed(a["memory"]) if x["speaker"] == "doctor"
                          and x["speech_act"] in ("instruction", "request") and x["utterance_id"] in utter), None)
            if instr:
                diffs = ussa.cross_check({**ussa.build_frame({}, ""), **instr["frame"]}, frame)
                if diffs:
                    ussa.add_issue(a, itype="instruction_misunderstanding",
                                   description="説明と患者の理解が違う可能性: " + "／".join(diffs),
                                   related=[instr["utterance_id"], mid],
                                   evidence=[{"utterance_id": mid, "field": "original", "quote": m["original"]}],
                                   source="rule", severity="high")
        if raw:
            ussa.merge_audit(a, raw, latest_id=mid, utterances=utter)
        ussa.remember(a, item)
    _audit_update(s_ref, fn)
    log.info("audit %.1fs", time.time() - t)


def _issue_event(s_ref, issue_id, event, **kw):
    def fn(a):
        i = next((x for x in a["issues"] if x["issue_id"] == issue_id), None)
        if i:
            try:
                ussa.issue_event(i, event, **kw)
            except clinical.TransitionError:
                pass
    _audit_update(s_ref, fn)


def _apply_reply(s_ref, aid):
    """送信済みの答え（aid）を、答えた先に結びつける。何度呼んでも同じ結果になる（/send と解析の完了の両方から呼ぶ）。

      確認質問への答え → 元の発言の確認候補と情報（fact）の状態を変える（clinical.apply_answer）
      理解の確認への答え → 理解の確認の状態と比べた結果を書く
    """
    a_ref = _msg_ref(s_ref, aid)
    a = a_ref.get().to_dict() or {}
    if a.get("status") not in SHOWN or not a.get("replyTo"):
        return
    q_ref = _msg_ref(s_ref, a["replyTo"])
    q = q_ref.get().to_dict() or {}
    a_type = (a.get("answer") or {}).get("type", "free_text")

    if q.get("kind") == "clarify" and (q.get("clarify") or {}).get("issue_id"):
        _issue_event(s_ref, q["clarify"]["issue_id"], "answered", ref=aid, note="確認質問に患者が答えた")
    if q.get("kind") == "clarify":
        mean = a.get("meaning") or {}
        if mean.get("status") != "ready":
            # 自由回答の解析がまだ／失敗: 状態は変えない（医師は会話の原文と訳を見て自分で確認できる）
            _tx([q_ref], lambda d: [{"clarify.last_answer_id": aid}])
            return
        res = mean.get("resolution") or {}
        sent = a.get("sentAt") or a.get("createdAt")
        sent_at = sent.astimezone(timezone.utc).isoformat() if hasattr(sent, "astimezone") else str(sent or "")
        c = q["clarify"]
        refs = [q_ref]
        src_ref = _msg_ref(s_ref, c["source_message_id"]) if c.get("source_message_id") and c.get("candidate_id") else None
        if src_ref:
            refs.append(src_ref)

        def fn(datas):
            out = [{"clarify.last_answer_id": aid}]
            if src_ref:
                src = datas[1]
                if not src or not (src.get("meaning") or {}).get("candidates"):
                    return out + [None]
                meaning = src["meaning"]
                try:
                    clinical.apply_answer(meaning, c["candidate_id"], message_id=aid, answer_type=a_type,
                                          resolves=res.get("resolves", "no"), meaning_doctor=res.get("meaning_doctor", ""),
                                          source_quote=res.get("source_quote", ""),
                                          sent_at=sent_at)
                except (KeyError, clinical.TransitionError) as e:
                    log.warning("apply_answer skipped: %s", type(e).__name__)
                    return out + [None]
                out.append({"meaning": meaning})
            return out
        _tx(refs, fn)
        return

    if q.get("kind") == "teachback":
        result = a.get("teachback_result")

        def fn(datas):
            tb = datas[0]["teachback"]
            current = tb.get("answer_message_id") or ""
            try:
                if current != aid:
                    clinical.apply_teachback_event(tb, "answer_sent", by="patient", ref=aid)
                    tb["answer_message_id"] = aid
                    tb["result"] = None
                if result and result.get("overall") != "error" and not tb.get("result"):
                    clinical.apply_teachback_event(tb, "assessed", by="ai", ref=aid)
                    tb["result"] = result
            except clinical.TransitionError as e:
                log.warning("teachback event skipped: %s", e)
                return [None]
            return [{"teachback": tb}]
        _tx([q_ref], fn)
        # 理解の相違は Conversation Auditor の未解決事項にも載せる（医師が「理解を確認済み」にすると解決になる）
        if result and result.get("overall") in ("inconsistent", "partial", "unclear"):
            exp_id = q["teachback"]["explanation_id"]
            pts = [p for p in result["points"] if p["verdict"] != "match"]
            ev = [{"utterance_id": aid, "field": "original", "quote": p["restatement_quote"]}
                  for p in pts if p["restatement_quote"]] or [{"utterance_id": aid, "field": "original", "quote": a["original"]}]
            desc = "理解の確認: " + "／".join(p["explanation"] or p["doctor_point"] for p in pts)[:280]
            _audit_update(s_ref, lambda au: ussa.add_issue(
                au, itype="understanding_mismatch", description=desc, related=[exp_id, aid], evidence=ev,
                source="teachback", severity="high" if result["overall"] == "inconsistent" else "medium"))


@app.post("/send")
def send(ref: MessageRef, uid: str = Depends(current_uid)):
    """患者が逆翻訳を見て「送信」: confirmed → sent。答え（replyTo）なら、答えた先に結びつける。
    （以前の画面は Firestore に直接 status を書いて送る。ルールはそのまま許しているが、その場合は結びつけない）"""
    s_ref, session = _member_session(ref.sessionId, uid)
    if _role(session, uid) != "patient":
        raise HTTPException(403, "送信できるのは患者だけです")
    m_ref = _msg_ref(s_ref, ref.messageId)
    _get(m_ref)

    def fn(datas):
        m = datas[0]
        if m.get("from") != "patient":
            raise HTTPException(400, "患者の発言ではありません")
        if m.get("status") in SHOWN:
            return [None]  # もう送った（二重押し）
        if m.get("status") != "confirmed":
            raise HTTPException(409, f"status が {m.get('status')} なので送れません")
        return [{"status": "sent", "sentAt": firestore.SERVER_TIMESTAMP, "expireAt": _expire_at()}]
    _tx([m_ref], fn)
    _apply_reply(s_ref, ref.messageId)
    return {"status": "sent"}


def _question_doc(session, *, original, shown, kind, extra, source):
    """医師の画面から送る、サーバーが作る医師の発言（確認質問・理解の確認）で、訳がもうあるもの。status は sent。

    この経路に来るのは、訳を新しく作らないものだけ:
      source="candidate_pair" … Meaning Engine が確認候補と一緒に作った質問と選択肢の対訳を、医師がそのまま送る
      source="fixed_text"     … 理解の確認の依頼文など、言語ごとに固定した文
    訳の照合はしていないので、gate に PRETRANSLATED と記録する。医師が編集・自作した質問は _gate_question（照合を通す）。
    """
    return {
        "from": "doctor", "kind": kind, "original": original, "status": "sent",
        "createdAt": firestore.SERVER_TIMESTAMP, "sentAt": firestore.SERVER_TIMESTAMP, "expireAt": _expire_at(),
        "translation": {"lang": session["patientLang"], "text": shown, "back_translation": original, "note": "",
                        "clarified_target": "", "clarified_answer": ""},
        "gate": {"state": ussa.SENT, "source": source, "history": [{"at": ussa.now_iso(), "to": ussa.SENT, "by": "doctor",
                                                                   "reason": source}],
                 "verification": {"status": ussa.PRETRANSLATED, "issues": [], "failures": [], "at": ussa.now_iso(),
                                  "note": {"candidate_pair": "確認候補の対訳（照合していない）",
                                           "fixed_text": "固定の依頼文"}[source]}},
        kind: extra,
    }


def _clarify_extra(session, *, source_message_id, candidate, fact_id, options, options_doctor, edited, issue_id):
    return {
        "source_message_id": source_message_id or "",
        "candidate_id": candidate["candidate_id"] if candidate else "",
        "fact_id": fact_id or "",  # 自作の質問: 送ったときに、この情報にひもづく候補を足す
        "fact_ids": candidate["fact_ids"] if candidate else [],
        "source_quote": candidate["source_quote"] if candidate else "",
        "unresolved": candidate["unresolved"] if candidate else "",
        "options": options,
        "options_doctor": options_doctor,
        "fixed": unsaid_ai.ANSWER_FIXED.get(session["patientLang"], unsaid_ai.ANSWER_FIXED["en"]),
        "edited": edited,
        "last_answer_id": "",
        "issue_id": issue_id or "",
        "linked": False,  # 元の候補・未解決事項を「質問送信済み」にしたか（届いたときに1回だけ）
    }


def _link_question(s_ref, qid):
    """確認質問が患者に届いた（SENT）ときに1回だけ: 元の確認候補（自作なら候補を足す）と未解決事項を「質問送信済み」にする。
    届く前（照合中・発言者の確認待ち・取り消し）には何も変えない。"""
    q_ref = _msg_ref(s_ref, qid)
    q = q_ref.get().to_dict() or {}
    c = q.get("clarify") or {}
    if q.get("kind") != "clarify" or q.get("status") not in SHOWN or c.get("linked"):
        return
    src_ref = _msg_ref(s_ref, c["source_message_id"]) if c.get("source_message_id") else None
    holder = {}

    def fn(datas):
        qd = datas[0]
        qc = qd.get("clarify") or {}
        if qc.get("linked"):
            raise _Stop("linked")
        upd = {"clarify.linked": True}
        out = [upd]
        if src_ref is not None:
            src = datas[1] or {}
            meaning = src.get("meaning") or _empty_meaning()
            try:
                cand = None
                if qc.get("candidate_id"):
                    cand = clinical.find(meaning.get("candidates", []), "candidate_id", qc["candidate_id"])
                else:  # 医師が自分で書いた質問: 届いたこの時点で候補を足す
                    cand = clinical.custom_candidate(meaning, utterance_id=qc["source_message_id"],
                                                     source_text=src.get("original", ""), fact_id=qc.get("fact_id") or None,
                                                     question_doctor=qd["original"],
                                                     question_patient=qd["translation"]["text"])
                cand = clinical.mark_question_sent(meaning, cand["candidate_id"], qid, edited=bool(qc.get("edited")))
                upd.update({"clarify.candidate_id": cand["candidate_id"], "clarify.fact_ids": cand["fact_ids"],
                            "clarify.source_quote": cand["source_quote"]})
                out.append({"meaning": meaning})
            except (KeyError, TypeError, clinical.TransitionError) as e:
                # 届いた後に候補が「無視」されていた等。質問はもう届いているので、記録だけ残して候補は変えない
                log.warning("link question skipped: %s", type(e).__name__)
                out.append(None)
        holder["issue"] = qc.get("issue_id")
        return out
    try:
        _tx([q_ref] + ([src_ref] if src_ref else []), fn)
    except _Stop:
        return
    if holder.get("issue"):
        _issue_event(s_ref, holder["issue"], "question_sent", ref=qid)


def _pending_question_for(s_ref, msgs, candidate_id, source_message_id):
    """同じ確認候補について、まだ届いていない（照合中・確認待ち・失敗）質問があるか。二重に送らないため。"""
    for _, m in msgs:
        c = m.get("clarify") or {}
        if (m.get("kind") == "clarify" and c.get("candidate_id") == candidate_id
                and c.get("source_message_id") == source_message_id
                and m.get("status") in ("analyzing", "awaiting_intent", "needs_review", "error")):
            return True
    return False


@app.post("/clarify/send")
def clarify_send(body: ClarifySend, uid: str = Depends(current_uid)):
    """医師が確認質問を患者に送る。Gemini が作った候補をそのまま送るか、医師が編集・自分で書いた質問を送る。
    編集・自作の質問は医師の言語で受け取り、Gemini で患者の言語に訳す（選択肢は付けず、自由に答えてもらう）。"""
    s_ref, session = _doctor_session(body.sessionId, uid)
    if not session.get("patientLang"):
        raise HTTPException(409, "患者がまだ参加していません")
    q_text = (body.questionDoctor or "").strip()
    if len(q_text) > MAX_QUESTION_CHARS:
        raise HTTPException(400, f"質問は {MAX_QUESTION_CHARS} 文字以内にしてください")
    src_ref = src = cand = None
    if body.messageId:
        src_ref = _msg_ref(s_ref, body.messageId)
        src = _get(src_ref)
        if src.get("from") != "patient" or src.get("status") not in SHOWN:
            raise HTTPException(400, "確認できるのは、患者が送った発言だけです")
        if body.candidateId:
            cand = clinical.find((src.get("meaning") or {}).get("candidates", []), "candidate_id", body.candidateId)
            if cand is None:
                raise HTTPException(404, "確認候補がありません")
    if not cand and not q_text:
        raise HTTPException(400, "candidateId か questionDoctor が必要です")
    edited = bool(q_text) and (cand is None or q_text != cand["question_doctor"])

    if cand is not None and _pending_question_for(s_ref, _all_messages(s_ref), cand["candidate_id"], body.messageId):
        raise HTTPException(409, "この確認候補の質問は、まだ送る前の確認中です")

    if edited:
        # 医師が編集・自作した質問は、医師のほかの発言と同じく Safety Gate を通す（意図の解析・訳の照合・発言者の確認）。
        # 届くまで元の候補・未解決事項は変えない（届いたときに _link_question）
        t0 = time.time()
        _session(body.sessionId, uid)  # Gemini を使うので回数を数える
        q_ref = s_ref.collection("messages").document()
        q_ref.set({
            "from": "doctor", "kind": "clarify", "original": q_text, "status": "analyzing",
            "createdAt": firestore.SERVER_TIMESTAMP, "expireAt": _expire_at(),
            "clarify": _clarify_extra(session, source_message_id=body.messageId, candidate=cand, fact_id=body.factId,
                                      options=[], options_doctor=[], edited=True, issue_id=body.issueId),
        })
        res = _gate_start(t0, s_ref, session, q_ref.id, q_ref, _get(q_ref), "doctor", None)
        log.info("clarify edited status=%s linked=%s", res.get("status"), bool(body.messageId))
        # 届いたときに結びついた候補（自作なら届いたときに足した候補）。まだ届いていなければ ""
        linked = ((_get(q_ref).get("clarify") or {}).get("candidate_id")) or (cand["candidate_id"] if cand else "")
        return {**res, "messageId": q_ref.id, "candidateId": linked}

    q_ref = s_ref.collection("messages").document()
    doc = {}  # 作る質問の発言。候補が決まってから（トランザクションの中で）中身を入れる

    def fn(datas):
        c = cand
        meaning = datas[0].get("meaning") or _empty_meaning()
        try:
            c = clinical.mark_question_sent(meaning, c["candidate_id"], q_ref.id, edited=False)
        except (KeyError, clinical.TransitionError) as e:
            raise HTTPException(409, f"この確認候補は送れません（{e}）")
        doc.clear()
        extra = _clarify_extra(session, source_message_id=body.messageId, candidate=c, fact_id=None,
                               options=cand["options_patient"], options_doctor=cand["options_doctor"], edited=False,
                               issue_id=body.issueId)
        extra["linked"] = True
        doc.update(_question_doc(session, original=cand["question_doctor"], shown=cand["question_patient"],
                                 kind="clarify", extra=extra, source="candidate_pair"))
        return [{"meaning": meaning}]

    _tx([src_ref], fn, create=(q_ref, doc))
    if body.issueId:
        _issue_event(s_ref, body.issueId, "question_sent", ref=q_ref.id)
    log.info("clarify sent (candidate pair)")
    return {"status": "sent", "messageId": q_ref.id, "candidateId": doc["clarify"]["candidate_id"]}


@app.post("/clarify/update")
def clarify_update(body: CandidateAction, uid: str = Depends(current_uid)):
    """医師が確認候補を「保留」「無視」「戻す」。無視しても情報は確定しない（未確認・確認省略として残る）。"""
    s_ref, _ = _doctor_session(body.sessionId, uid)
    m_ref = _msg_ref(s_ref, body.messageId)
    _get(m_ref)

    def fn(datas):
        meaning = (datas[0] or {}).get("meaning")
        if not meaning:
            raise HTTPException(404, "確認候補がありません")
        try:
            clinical.doctor_candidate_action(meaning, body.candidateId, body.action)
        except KeyError:
            raise HTTPException(404, "確認候補がありません")
        except clinical.TransitionError as e:
            raise HTTPException(409, str(e))
        return [{"meaning": meaning}]
    _tx([m_ref], fn)
    return {"ok": True}


@app.post("/fact/update")
def fact_update(body: FactAction, uid: str = Depends(current_uid)):
    """医師が情報（fact）を「確認済み」「要修正」にする／取り消す。医師確認済みにできるのはここだけ。"""
    s_ref, _ = _doctor_session(body.sessionId, uid)
    if body.action not in ("confirm", "flag", "reset"):
        raise HTTPException(400, "action は confirm / flag / reset のどれかです")
    m_ref = _msg_ref(s_ref, body.messageId)
    _get(m_ref)

    def fn(datas):
        m = datas[0] or {}
        meaning = m.get("meaning")
        if m.get("status") not in SHOWN or not meaning:
            raise HTTPException(404, "情報がありません")
        try:
            clinical.doctor_fact_action(meaning, body.factId, body.action, note=body.note[:200])
        except KeyError:
            raise HTTPException(404, "情報（fact）がありません")
        except clinical.TransitionError as e:
            raise HTTPException(409, str(e))
        return [{"meaning": meaning, "facts": clinical.legacy_facts(meaning)}]
    _tx([m_ref], fn)
    return {"ok": True}


@app.post("/teachback/start")
def teachback_start(ref: TeachbackStart, uid: str = Depends(current_uid)):
    """医師が自分の説明（messageId）について「理解を確認」: 患者に、理解した内容を自分の言葉で話してもらう依頼を送る。
    依頼文は固定（unsaid_ai.TEACHBACK_PROMPT）なので Gemini は呼ばない。"""
    s_ref, session = _doctor_session(ref.sessionId, uid)
    if not session.get("patientLang"):
        raise HTTPException(409, "患者がまだ参加していません")
    exp = _get(_msg_ref(s_ref, ref.messageId))
    if exp.get("from") != "doctor" or exp.get("kind") or exp.get("status") not in SHOWN:
        raise HTTPException(400, "理解を確認できるのは、送信済みの医師の説明だけです")
    lang = session["patientLang"]
    q_ref = s_ref.collection("messages").document()
    now = clinical.now_iso()
    doc = _question_doc(session, original=unsaid_ai.TEACHBACK_PROMPT[session["doctorLang"]],
                        shown=unsaid_ai.TEACHBACK_PROMPT.get(lang, unsaid_ai.TEACHBACK_PROMPT["en"]),
                        kind="teachback", source="fixed_text", extra={
                            "explanation_id": ref.messageId, "status": clinical.T_ASKED, "answer_message_id": "",
                            "previous_id": ref.previousId or "",
                            "result": None, "history": [{"at": now, "by": "doctor", "event": "requested"}]})
    q_ref.set(doc)
    return {"messageId": q_ref.id}


@app.post("/teachback/resolve")
def teachback_resolve(body: TeachbackAction, uid: str = Depends(current_uid)):
    """医師が理解の確認を「確認済み」（confirm）か「再説明が必要」（reexplain）にする。AI の判定だけでは確定しない。"""
    s_ref, _ = _doctor_session(body.sessionId, uid)
    event = {"confirm": "doctor_confirm", "reexplain": "doctor_reexplain"}.get(body.action)
    if not event:
        raise HTTPException(400, "action は confirm / reexplain のどれかです")
    m_ref = _msg_ref(s_ref, body.messageId)
    _get(m_ref)

    holder = {}

    def fn(datas):
        tb = (datas[0] or {}).get("teachback")
        if not tb:
            raise HTTPException(404, "理解の確認がありません")
        try:
            clinical.apply_teachback_event(tb, event, by="doctor")
        except clinical.TransitionError as e:
            raise HTTPException(409, str(e))
        holder["tb"] = tb
        return [{"teachback": tb}]
    _tx([m_ref], fn)
    # 未解決事項にも反映: 理解を確認済み → この説明（と再説明の前の説明）の理解の相違を解決に／再説明 → 確認を依頼中に
    tb = holder["tb"]
    ids = {tb.get("explanation_id"), tb.get("answer_message_id")} - {"", None}
    prev = tb.get("previous_id")
    if prev:
        pt = (_msg_ref(s_ref, prev).get().to_dict() or {}).get("teachback") or {}
        ids |= {pt.get("explanation_id"), pt.get("answer_message_id")} - {"", None}

    def upd(a):
        for i in a["issues"]:
            if i["status"] in ussa.ACTIVE_ISSUE and i["type"] in ("understanding_mismatch", "instruction_misunderstanding") \
                    and ids & set(i["related_utterances"]):
                try:
                    if body.action == "confirm":
                        ussa.issue_event(i, "resolve", ref=body.messageId, note="医師が理解を確認")
                        i["resolution"] = "医師が理解を確認"
                    else:
                        ussa.issue_event(i, "question_sent", ref=body.messageId, note="医師が再説明")
                except clinical.TransitionError:
                    pass
    _audit_update(s_ref, upd)
    return {"ok": True}


@app.post("/meaning/retry")
def meaning_retry(ref: MessageRef, uid: str = Depends(current_uid)):
    """意味の解析だけが失敗した発言（meaning.status = error）をやり直す。原文と翻訳はそのまま。

    初回と同じ文脈（直前の会話・本人が確認した意図・答えている確認質問）で解析し、成功したら、送信済みの答えを
    元の質問・確認候補・情報に結びつける（_apply_reply。同じ答えは2回入らない）。
    医師が答えを見て確認済み・要修正にした情報は変えない／判断の後に届いた答えなら「再確認が必要」にする
    （clinical.apply_answer。送った時刻と医師の判断の時刻で決める）。

    同時に2本は走らせない: 所有者 ID（retry_run）と期限（retry_until、RETRY_LEASE_SEC）を付けて始める。
    サーバーが止まって「やり直し中」のまま残っても、期限が切れたら別のリクエストが引き継げる。
    引き継がれた古い処理は、保存のトランザクションで所有者が違うと分かるので書き戻さない（409）。"""
    s_ref, session, m_ref, msg = _load(ref, uid)
    if msg.get("from") != "patient":
        raise HTTPException(409, "やり直す必要はありません")

    run = uuid.uuid4().hex  # この再試行の所有者 ID

    def claim(m):  # 同時に2本やり直さない。期限（lease）が切れた「やり直し中」は、止まったものとして引き継ぐ
        cur = m.get("meaning") or {}
        if cur.get("status") == "retrying" and not _lease_expired(cur.get("retry_until")):
            raise HTTPException(409, "いま整理し直しています")
        if cur.get("status") not in ("error", "retrying"):
            raise HTTPException(409, "やり直す必要はありません")
        until = datetime.now(timezone.utc) + timedelta(seconds=RETRY_LEASE_SEC)
        return {"meaning.status": "retrying", "meaning.retry_run": run, "meaning.retry_until": until.isoformat(),
                "meaning.retry_taken_over": cur.get("retry_run") if cur.get("status") == "retrying" else None}
    _transact(m_ref, claim)

    def mine(datas):
        cur = (datas[0] or {}).get("meaning") or {}
        return cur.get("status") == "retrying" and cur.get("retry_run") == run

    try:
        msgs = _all_messages(s_ref)
        known = _known_quotes(msgs, ref.messageId)
        question = dict(msgs).get(msg.get("replyTo")) if msg.get("replyTo") else None
        intent = (msg.get("gate") or {}).get("intent") or {}
        c = intent.get("confirmed") if intent.get("state") == ussa.USER_CONFIRMED else None
        f = unsaid_ai.submit(unsaid_ai.meaning, msg["original"], session["patientLang"], session["doctorLang"],
                             _meaning_context(msgs, ref.messageId, c), known, _pending_from(question))
        meaning = _meaning_result(f, msg, ref.messageId, session, known)
    except Exception as e:
        # 解析の外で失敗した: まだ自分が持ち主なら「失敗」に戻して、すぐやり直せるようにする（期限を待たせない）
        log.warning("meaning retry failed: %s", type(e).__name__)
        _tx([m_ref], lambda d: [{"meaning.status": "error"} if mine(d) else None])
        raise

    def save(datas):  # AI の後に最新を読み直す: この再試行がまだ持ち主のときだけ書く（古い meaning 全体で上書きしない）
        if not mine(datas):
            raise HTTPException(409, "この解析のやり直しは、ほかの処理に引き継がれました")
        return [{"meaning": meaning, "facts": clinical.legacy_facts(meaning)}]
    _tx([m_ref], save)
    # 元の候補・fact への反映は _apply_reply のトランザクションの中で最新を読み直して行う（医師の確認・要修正、
    # 患者が後から直した答えは変えない。同じ答えは2回入らない）
    if meaning["status"] == "ready" and question:
        _apply_reply(s_ref, ref.messageId)
    return {"status": meaning["status"]}


# ───────────────────────── 発言者の確認（意図・検証で食い違ったとき）・取り消し・未検証の共有・未解決事項 ─────────────────────────
def _author(s_ref, session, uid, message_id):
    role = _role(session, uid)
    m_ref = _msg_ref(s_ref, message_id)
    msg = _get(m_ref)
    if not role or msg.get("from") != role:
        raise HTTPException(403, "発言者だけが操作できます")
    return role, m_ref, msg


def _cancel(m_ref, by, reason, allowed):
    def check(m):
        g = m.get("gate") or _new_gate()
        if g["state"] not in allowed:
            raise HTTPException(409, f"送信の状態が {g['state']} なので取り消せません")
        ussa.gate_move(g, ussa.CANCELLED, by=by, reason=reason)
        return {"gate": g, "status": "cancelled", "lockedUntil": firestore.DELETE_FIELD}
    _transact(m_ref, check)


@app.post("/intent")
def intent_choose(body: IntentChoice, uid: str = Depends(current_uid)):
    """発言者が自分の発言の意味を選んだ・説明した（Translation Intent Clarification）。USER_CONFIRMED として訳し直し、検証して送る。
    「どれでもない」は取り消し（端末は原文を入力欄に戻し、言い直してもらう）。"""
    t0 = time.time()
    s_ref, session = _session(body.sessionId, uid)
    role, m_ref, msg = _author(s_ref, session, uid, body.messageId)
    g = msg.get("gate") or {}
    if g.get("state") != ussa.AWAITING_INTENT:
        raise HTTPException(409, "意図の確認を待っている発言ではありません")
    if body.choice == "none":
        _cancel(m_ref, role, "rephrase", {ussa.AWAITING_INTENT})
        return {"status": "cancelled", "original": msg["original"]}
    try:
        intent = ussa.confirm_intent(g["intent"], body.choice, body.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    gate = _gate_claim(m_ref, role, (ussa.AWAITING_INTENT,), intent=intent)
    msgs = _all_messages(s_ref)
    question = dict(msgs).get(msg.get("replyTo")) if msg.get("replyTo") else None
    return _gate_run(t0, s_ref, session, body.messageId, m_ref, msg, role, question, msgs, gate, intent=intent)


@app.post("/review")
def review(body: ReviewAction, uid: str = Depends(current_uid)):
    """検証で食い違いが残った発言（NEEDS_REVIEW）を、発言者が「未検証として送る」か「言い直す」。
    未検証として送った訳は、相手の画面に「未検証」と出る（確定した翻訳としては扱わない）。"""
    s_ref, session = _member_session(body.sessionId, uid)
    role, m_ref, msg = _author(s_ref, session, uid, body.messageId)
    g = msg.get("gate") or {}
    if g.get("state") != ussa.NEEDS_REVIEW:
        raise HTTPException(409, "確認を待っている発言ではありません")
    if body.action == "rephrase":
        _cancel(m_ref, role, "rephrase", {ussa.NEEDS_REVIEW})
        return {"status": "cancelled", "original": msg["original"]}
    if body.action != "send_unverified":
        raise HTTPException(400, "action は send_unverified / rephrase のどちらかです")
    try:
        _deliver(s_ref, m_ref, g["run_id"], role, verification=g["verification"], draft=g["draft"],
                 attempts=g.get("attempts", []), timings={}, session=session, override="speaker_ack")
    except (_Stop, _Refused) as e:
        raise HTTPException(409, f"送れません（{e}）") from None
    msgs = _all_messages(s_ref)
    question = dict(msgs).get(msg.get("replyTo")) if msg.get("replyTo") else None
    _after_send(s_ref, session, body.messageId, role, g.get("intent"), question)
    return {"status": "sent", "override": "speaker_ack"}


@app.post("/cancel")
def cancel(ref: MessageRef, uid: str = Depends(current_uid)):
    """発言者が送信を取り消す（解析中・意図の確認中・検証の確認中・失敗）。解析が後から終わっても送られない（run_id が合わない）。"""
    s_ref, session = _member_session(ref.sessionId, uid)
    role, m_ref, msg = _author(s_ref, session, uid, ref.messageId)
    _cancel(m_ref, role, "speaker",
            {ussa.SOURCE_CONFIRMED, ussa.ANALYZING, ussa.VERIFYING, ussa.AWAITING_INTENT, ussa.NEEDS_REVIEW, ussa.FAILED})
    return {"status": "cancelled", "original": msg["original"]}


@app.post("/reveal")
def reveal(ref: MessageRef, uid: str = Depends(current_uid)):
    """医師が、まだ送られていない（確認中・検証中・失敗した）患者の発言を、未検証と分かる形で見る。見たことを記録する。
    AI の確認が終わるまで重要な訴えを医師から完全に隠さないため。推測の訳は「未検証」として返し、問診メモには入れない。"""
    s_ref, session = _doctor_session(ref.sessionId, uid)
    m_ref = _msg_ref(s_ref, ref.messageId)
    msg = _get(m_ref)
    if msg.get("from") != "patient" or msg.get("status") in SHOWN:
        raise HTTPException(400, "まだ届いていない患者の発言だけです")

    def check(m):
        g = m.get("gate") or _new_gate()
        g.setdefault("revealed", []).append({"at": ussa.now_iso(), "by": "doctor", "state": g.get("state")})
        return {"gate": g}
    _transact(m_ref, check)
    g = msg.get("gate") or {}
    return {"original": msg["original"], "lang": session["patientLang"], "state": g.get("state", ussa.SOURCE_CONFIRMED),
            "verified": False, "draft": (g.get("draft") or {}).get("translation", ""),
            "verification": (g.get("verification") or {}).get("status", "")}


@app.post("/issue/update")
def issue_update(body: IssueAction, uid: str = Depends(current_uid)):
    """医師が未解決事項を保留・解決済み・無視・戻す。AI の提案は強制しない（無視してよい）。"""
    s_ref, _ = _doctor_session(body.sessionId, uid)
    if body.action not in ("hold", "unhold", "resolve", "dismiss", "reopen"):
        raise HTTPException(400, "action は hold / unhold / resolve / dismiss / reopen のどれかです")
    holder = {}

    def tx(datas):
        a = (datas[0] or {}).get("audit") or ussa.empty_audit()
        i = next((x for x in a["issues"] if x["issue_id"] == body.issueId), None)
        if i is None:
            raise HTTPException(404, "未解決事項がありません")
        if body.action in ("hold", "unhold"):
            if i["status"] not in ussa.ACTIVE_ISSUE:
                raise HTTPException(409, "閉じた事項は保留にできません")
            i["held"] = body.action == "hold"
            i.setdefault("history", []).append({"at": ussa.now_iso(), "by": "doctor", "event": body.action})
        else:
            try:
                ussa.issue_event(i, body.action, note=body.note)
            except clinical.TransitionError as e:
                raise HTTPException(409, str(e)) from None
            if body.action == "resolve":
                i["resolution"] = body.note[:200] or "医師が解決済みにした"
        holder["issue"] = i
        return [{"audit": a}]
    _tx([s_ref], tx)
    return {"ok": True, "status": holder["issue"]["status"]}


@app.post("/end")
def end(ref: SessionRef, uid: str = Depends(current_uid)):
    """医師が「診察を終える」: この問診の発言と session をすべて消す。消せるのは医師（members[0]）だけ。

    問診の内容は医療情報なので、使い終わったら残さない。カルテに必要な分は、医師が先に「カルテにコピー」で写す。
    閉じ忘れた session は Firestore の TTL（expireAt）が消す。ルールでは端末からの削除を禁止しているので、消すのはサーバーだけ。
    """
    s_ref, session = _member_session(ref.sessionId, uid)
    if session["members"][0] != uid:
        raise HTTPException(403, "診察を終えられるのは医師だけです")
    n = 0
    for m_ref in s_ref.collection("messages").list_documents():
        m_ref.delete()
        n += 1
    s_ref.delete()
    log.info("end session messages=%d", n)
    return {"deleted": n}


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
