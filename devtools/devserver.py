"""ローカルで画面ごと動かす開発サーバー（Google Cloud も Firebase も使わない）。本番・ステージングには載せない。

  .venv/bin/python -m devtools.devserver            → http://localhost:8765/doctor.html
  （患者は QR の URL を別のウィンドウ／シークレットウィンドウで開く。タブごとに別の人としてログインする）

中身:
  - API は main.py をそのまま使う（/api/...）。Firestore はメモリ上の偽物（devtools/fakestore.py）、
    Gemini は決まった答えを返す偽物（devtools/fake_ai.py。DEMO.md のシナリオの発言だけ本物らしい答え）。
  - 画面の HTML / CSS / JS は web/ をそのまま配る。ただし web/unsaid.js（Firebase につなぐ部品）の代わりに
    devtools/web/unsaid-local.js を配る。画面のコードは同じものが動く。
  - 端末からの Firestore の読み書きは /_dev/... で受け、firestore.rules と同じ条件で確かめる（_allowed）。
    本物のルールエンジンではないので、ルールそのものの確認は Firebase エミュレーターで行う（SECURITY.md）。
"""
import argparse
import copy
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import main  # noqa: E402
import stt  # noqa: E402
import unsaid_ai  # noqa: E402
from devtools import fake_ai, fakestore, rules  # noqa: E402

DB = fakestore.FakeDB()
fakestore.install(main, DB)
fake_ai.install(unsaid_ai)
fake_ai.DELAY_SEC = float(os.environ.get("FAKE_AI_DELAY", "0.8"))
stt.transcribe = lambda audio, lang: ("", 0.0)  # 音声認識は使わない（文字で入力する）
PATIENT_LANGS = ["en", "zh-CN", "vi"]


def uid_of(authorization):
    if not authorization.startswith("Bearer local:"):
        raise HTTPException(401, "ローカルのログインが必要です")
    return authorization.removeprefix("Bearer local:")


main.app.dependency_overrides[main.current_uid] = lambda authorization=Header(default=""): uid_of(authorization)

app = FastAPI(title="UNSAID local dev")
app.mount("/api", main.app)


def _json(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json(x) for x in v]
    return v


def _decode(v):
    """画面が送る特別な値: {"__serverTimestamp": true} → サーバーの時刻、{"__arrayUnion": [...]} → ArrayUnion。"""
    if isinstance(v, dict):
        if v.get("__serverTimestamp"):
            return "__TS__"
        if "__arrayUnion" in v:
            return ("__AU__", v["__arrayUnion"])
        return {k: _decode(x) for k, x in v.items()}
    return v


def _materialize(v, old=None):
    if v == "__TS__":
        return fakestore.server_now()
    if isinstance(v, tuple) and v[0] == "__AU__":
        base = list(old or [])
        return base + [x for x in v[1] if x not in base]
    if isinstance(v, dict):
        return {k: _materialize(x) for k, x in v.items()}
    return v


def _doc(path):
    parts = path.strip("/").split("/")
    if len(parts) % 2 or any(not p for p in parts):
        raise HTTPException(400, "path が不正です")
    return "/".join(parts)


def _session(sid):
    return DB.store.get(f"sessions/{sid}")


def _role(sess, uid):
    m = (sess or {}).get("members", [])
    return "doctor" if m and m[0] == uid else "patient" if len(m) == 2 and m[1] == uid else None


# ── firestore.rules と同じ条件（devtools/rules.py。本物のルールエンジンではない）──
def _can_read(path, uid):
    return rules.can_read(path, uid, _session)


def _check_create(col, data, uid):
    return rules.check_create(col, data, uid, _session)


def _check_update(path, old, data, uid):
    return rules.check_update(path, old, data, uid, _session)


@app.post("/_dev/login")
def login():
    return {"uid": "u-" + uuid.uuid4().hex[:10]}


@app.post("/_dev/add")
async def add(req: Request, authorization: str = Header(default="")):
    uid = uid_of(authorization)
    body = await req.json()
    col, data = body["path"].strip("/"), _decode(body["data"])
    if not _check_create(col, data, uid):
        raise HTTPException(403, "permission-denied")
    ref = DB.collection(col).document()
    with fakestore.LOCK:
        DB.store[ref.path] = _materialize(data)
        DB.touch()
    return {"id": ref.id}


@app.post("/_dev/update")
async def update(req: Request, authorization: str = Header(default="")):
    uid = uid_of(authorization)
    body = await req.json()
    path, data = _doc(body["path"]), _decode(body["data"])
    with fakestore.LOCK:
        old = DB.store.get(path)
        if old is None or not _check_update(path, old, data, uid):
            raise HTTPException(403, "permission-denied")
        doc = DB.store[path]
        for k, v in data.items():
            *parents, leaf = k.split(".")
            target = doc
            for p in parents:
                target = target.setdefault(p, {})
            target[leaf] = _materialize(v, target.get(leaf))
        DB.touch()
    return {"ok": True}


def _read(path, uid, kind):
    if not _can_read(path, uid):
        raise HTTPException(403, "permission-denied")
    if kind == "doc":
        d = DB.store.get(path)
        return None if d is None else {"id": path.rsplit("/", 1)[-1], **_json(copy.deepcopy(d))}
    rows = [{"id": s.id, **_json(s.to_dict())} for s in DB.collection(path).stream()]
    return sorted(rows, key=lambda r: r.get("createdAt") or "")


@app.get("/_dev/get")
def get(path: str, authorization: str = Header(default="")):
    uid = uid_of(authorization)
    path = _doc(path)
    if path not in DB.store:
        return {"data": None}
    return {"data": _read(path, uid, "doc")}


@app.get("/_dev/watch")
async def watch(path: str, kind: str, since: int = -1, authorization: str = Header(default="")):
    """onSnapshot の代わり（ロングポーリング）。since より新しい変更があればすぐ、無ければ最大 20 秒待って返す。"""
    from fastapi.concurrency import run_in_threadpool
    uid = uid_of(authorization)
    path = path.strip("/")
    if since >= DB.version:
        await run_in_threadpool(DB.wait, since, 20)
    try:
        data = _read(path, uid, kind)
    except HTTPException:
        return JSONResponse({"error": "permission-denied"}, status_code=403)
    return {"version": DB.version, "data": data}


WEB = ROOT / "web"


@app.get("/unsaid.js")
def unsaid_js():
    return FileResponse(Path(__file__).parent / "web" / "unsaid-local.js", media_type="text/javascript")


@app.get("/")
def index():
    return FileResponse(WEB / "doctor.html")


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"UNSAID local dev: http://localhost:{a.port}/doctor.html  （偽の AI・メモリ上のデータ。本番には触れません）")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
