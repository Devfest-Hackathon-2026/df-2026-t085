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
    msg = _claim(m_ref, ("analyzing", "error"))
    if msg.get("status") == "error":  # 「もう一度」のときだけ
        m_ref.update({"status": "analyzing", "error": firestore.DELETE_FIELD})
    if reply and reply[1].get("kind") == "clarify" and (msg.get("answer") or {}).get("type") in ("option", "unsure", "none"):
        return _answer_fixed(s_ref, session, ref.messageId, m_ref, msg, reply[1])
    if not reply and CLARIFY_MODE == "patient":
        return _analyze_legacy(t0, session, m_ref, msg)
    msgs = _all_messages(s_ref)
    if reply and reply[1].get("kind") == "teachback":
        return _analyze_teachback_answer(t0, s_ref, session, ref.messageId, m_ref, msg, reply[1], msgs)
    return _analyze_patient(t0, s_ref, session, ref.messageId, m_ref, msg, msgs, reply[1] if reply else None)


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
    _, session, m_ref, msg = _load(ref, uid)
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


def _analyze_patient(t0, s_ref, session, mid, m_ref, msg, msgs, question=None):
    """患者の発言（確認質問への自由回答を含む）: 翻訳と Meaning Engine を並列に投げる。患者が待つのは翻訳だけ。"""
    tr_ctx = pending = None
    if question:
        c = question.get("clarify") or {}
        tr_ctx = {"question_as_shown": question["translation"]["text"], "question": question["original"]}
        pending = {"source_quote": c.get("source_quote", ""), "question_doctor_lang": question["original"],
                   "question_patient_lang": question["translation"]["text"], "unresolved": c.get("unresolved", "")}
    known = _known_quotes(msgs, mid)
    f_tr = unsaid_ai.submit(_patient_translate, session, msg, None, tr_ctx)
    f_mean = unsaid_ai.submit(unsaid_ai.meaning, msg["original"], session["patientLang"], session["doctorLang"],
                              _context(msgs, mid), known, pending)
    try:
        tr = f_tr.result()
    except Exception as e:
        _fail(m_ref, e)
    update = {"clarification": None, "translation": tr, "status": "confirmed"}
    m_ref.update({**update, "lockedUntil": firestore.DELETE_FIELD})
    shown = time.time() - t0

    meaning = _meaning_result(f_mean, msg, mid, session, known)
    facts = clinical.legacy_facts(meaning)
    m_ref.update({"meaning": meaning, "facts": facts})
    if question:
        _apply_reply(s_ref, mid)  # 解析より先に患者が送信していたら、ここで元の情報に結びつける
    log.info("analyze mode=doctor shown=%.1fs meaning=%.1fs", shown, time.time() - t0)
    return {**update, "meaning": meaning, "facts": facts}


def _answer_fixed(s_ref, session, mid, m_ref, msg, question):
    """確認質問に選択肢・「分からない」・「どれでもない」で答えた: 訳はすでにあるので Gemini を呼ばず、そのまま送る。"""
    c, a = question["clarify"], msg["answer"]
    if a["type"] == "option":
        dr = c["options_doctor"][a["index"]]
        res = {"applies": True, "resolves": "yes", "meaning_doctor": dr, "source_quote": msg["original"],
               "evidence_status": "exact"}
    else:
        dr = unsaid_ai.ANSWER_FIXED_DOCTOR[a["type"]]
        res = {"applies": True, "resolves": "no", "meaning_doctor": "", "source_quote": "", "evidence_status": "none"}
    update = {
        "translation": {"lang": session["doctorLang"], "text": dr, "back_translation": msg["original"], "note": "",
                        "clarified_target": "", "clarified_answer": ""},
        "meaning": _empty_meaning(resolution=res),
        "facts": [],
        "clarification": None,
        "status": "sent",  # 患者が自分で選んだ答え（訳は確認質問と一緒に医師が見ている）なので、逆翻訳の確認を省く
        "sentAt": firestore.SERVER_TIMESTAMP,
    }
    m_ref.update({**update, "lockedUntil": firestore.DELETE_FIELD, "error": firestore.DELETE_FIELD})
    _apply_reply(s_ref, mid)
    return {**update, "sentAt": None}


def _analyze_teachback_answer(t0, s_ref, session, mid, m_ref, msg, question, msgs):
    """理解の確認への答え: 翻訳と「医師の説明との比べ」を並列に投げる。"""
    exp = dict(msgs).get(question["teachback"]["explanation_id"]) or {}
    tr_ctx = {"question_as_shown": question["translation"]["text"], "question": question["original"]}
    f_tr = unsaid_ai.submit(_patient_translate, session, msg, None, tr_ctx)
    f_cmp = unsaid_ai.submit(unsaid_ai.compare_understanding, exp.get("original", ""),
                             (exp.get("translation") or {}).get("text", ""), msg["original"],
                             session["patientLang"], session["doctorLang"])
    try:
        tr = f_tr.result()
    except Exception as e:
        _fail(m_ref, e)
    update = {"clarification": None, "translation": tr, "status": "confirmed"}
    m_ref.update({**update, "lockedUntil": firestore.DELETE_FIELD})
    try:
        raw, sec = f_cmp.result()
        raw.pop("_meta", None)
        result = clinical.build_teachback(raw, restatement=msg["original"], schema=unsaid_ai.schema("teachback"))
        log.info("teachback %.1fs overall=%s points=%d", sec, result["overall"], len(result["points"]))
    except Exception as e:
        log.warning("teachback compare failed: %s", type(e).__name__)
        result = {"overall": "error", "points": [], "suggested_reexplanation": "", "rejected": 0,
                  "decided_by": "ai", "at": clinical.now_iso()}
    m_ref.update({"teachback_result": result})
    _apply_reply(s_ref, mid)
    log.info("analyze teachback-answer total=%.1fs", time.time() - t0)
    return {**update, "teachback_result": result}


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

    if q.get("kind") == "clarify":
        mean = a.get("meaning") or {}
        if mean.get("status") != "ready":
            # 自由回答の解析がまだ／失敗: 状態は変えない（医師は会話の原文と訳を見て自分で確認できる）
            _tx([q_ref], lambda d: [{"clarify.last_answer_id": aid}])
            return
        res = mean.get("resolution") or {}
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
                                          source_quote=res.get("source_quote", ""))
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


def _question_doc(session, *, original, shown, kind, extra):
    """医師の画面から送る、サーバーが作る医師の発言（確認質問・理解の確認）。訳はもうあるので status は sent。"""
    return {
        "from": "doctor", "kind": kind, "original": original, "status": "sent",
        "createdAt": firestore.SERVER_TIMESTAMP, "sentAt": firestore.SERVER_TIMESTAMP, "expireAt": _expire_at(),
        "translation": {"lang": session["patientLang"], "text": shown, "back_translation": original, "note": "",
                        "clarified_target": "", "clarified_answer": ""},
        kind: extra,
    }


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

    if edited:
        _session(body.sessionId, uid)  # Gemini を使うので回数を数える
        try:
            out, _ = unsaid_ai.translate(q_text, session["doctorLang"], session["patientLang"], "doctor_to_patient")
        except Exception:
            log.warning("question translate failed")
            raise HTTPException(502, "質問の翻訳に失敗しました。もう一度お試しください")
        q_dr, q_pt, o_pt, o_dr = q_text, out["translation"], [], []
    else:
        q_dr, q_pt, o_pt, o_dr = cand["question_doctor"], cand["question_patient"], cand["options_patient"], cand["options_doctor"]

    q_ref = s_ref.collection("messages").document()
    doc = {}  # 作る質問の発言。候補が決まってから（トランザクションの中で）中身を入れる

    def fn(datas):
        c = cand
        updates = []
        if src_ref is not None:
            meaning = datas[0].get("meaning") or _empty_meaning()
            if c is None:  # 医師が自分で書いた質問（元の発言の情報にひもづける）
                try:
                    c = clinical.custom_candidate(meaning, utterance_id=body.messageId, source_text=datas[0]["original"],
                                                  fact_id=body.factId, question_doctor=q_dr, question_patient=q_pt)
                except KeyError:
                    raise HTTPException(404, "情報（fact）がありません")
            try:
                c = clinical.mark_question_sent(meaning, c["candidate_id"], q_ref.id, edited=edited)
            except (KeyError, clinical.TransitionError) as e:
                raise HTTPException(409, f"この確認候補は送れません（{e}）")
            updates.append({"meaning": meaning})
        doc.clear()
        doc.update(_question_doc(session, original=q_dr, shown=q_pt, kind="clarify", extra={
            "source_message_id": body.messageId or "",
            "candidate_id": c["candidate_id"] if c else "",
            "fact_ids": c["fact_ids"] if c else [],
            "source_quote": c["source_quote"] if c else "",
            "unresolved": c["unresolved"] if c else "",
            "options": o_pt,
            "options_doctor": o_dr,
            "fixed": unsaid_ai.ANSWER_FIXED.get(session["patientLang"], unsaid_ai.ANSWER_FIXED["en"]),
            "edited": edited,
            "last_answer_id": "",
        }))
        return updates

    _tx([src_ref] if src_ref else [], fn, create=(q_ref, doc))
    log.info("clarify sent edited=%s linked=%s", edited, bool(src_ref))
    return {"messageId": q_ref.id, "candidateId": doc["clarify"]["candidate_id"]}


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
def teachback_start(ref: MessageRef, uid: str = Depends(current_uid)):
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
                        kind="teachback", extra={
                            "explanation_id": ref.messageId, "status": clinical.T_ASKED, "answer_message_id": "",
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

    def fn(datas):
        tb = (datas[0] or {}).get("teachback")
        if not tb:
            raise HTTPException(404, "理解の確認がありません")
        try:
            clinical.apply_teachback_event(tb, event, by="doctor")
        except clinical.TransitionError as e:
            raise HTTPException(409, str(e))
        return [{"teachback": tb}]
    _tx([m_ref], fn)
    return {"ok": True}


@app.post("/meaning/retry")
def meaning_retry(ref: MessageRef, uid: str = Depends(current_uid)):
    """意味の解析だけが失敗した発言（meaning.status = error）をやり直す。原文と翻訳はそのまま。"""
    s_ref, session, m_ref, msg = _load(ref, uid)
    if msg.get("from") != "patient" or (msg.get("meaning") or {}).get("status") != "error":
        raise HTTPException(409, "やり直す必要はありません")
    msgs = _all_messages(s_ref)
    known = _known_quotes(msgs, ref.messageId)
    f = unsaid_ai.submit(unsaid_ai.meaning, msg["original"], session["patientLang"], session["doctorLang"],
                         _context(msgs, ref.messageId), known, None)
    meaning = _meaning_result(f, msg, ref.messageId, session, known)
    m_ref.update({"meaning": meaning, "facts": clinical.legacy_facts(meaning)})
    return {"status": meaning["status"]}


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
