"""デプロイした API を、画面なしで端から端まで通すテスト（本物の Firebase / Firestore / Gemini を使う）。

画面と同じく「匿名ログインした端末」として Firestore に書くので、firestore.rules も一緒に確かめられる。

使い方（1行で）:
  python smoke.py --api https://unsaid-api-xxxx.asia-northeast1.run.app --key <firebaseConfig の apiKey>
  python smoke.py --api ... --key ... --lang vi --text "Dạo này tôi hay bị đau đầu."

流れ: 患者・医師の2人で匿名ログイン → 医師が session を作る → 患者が言語を選んで参加 → 患者が発言 → /analyze
     → （従来の流れで確認質問があれば）最初の選択肢で回答 → /translate → 患者が送信 → 医師が受け取る
     → （医師が選ぶ流れなら）問診メモと確認候補を表示 → 医師が確認候補を送る → 患者が選択肢で答える → 情報の状態を確認
     → 医師が発言 → /translate → 理解の確認（teach-back）→ 患者が言い直す → 比べた結果

ステージング（別の Firestore データベース）を確かめるとき: --database staging --api <ステージングの URL>（docs/staging.md）
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

PROJECT = "unsaid-devfest"
DATABASE = "(default)"
FS = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/{DATABASE}/documents"


def use_database(name):
    global DATABASE, FS
    DATABASE = name
    FS = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/{DATABASE}/documents"

sys.stdout.reconfigure(encoding="utf-8")  # Windows でベトナム語・中国語を出しても落ちないように


def http(method, url, body=None, token=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, {"raw": text[:300]}


# --- Firestore REST の値の形 <-> Python ---
def enc(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, list):
        return {"arrayValue": {"values": [enc(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: enc(x) for k, x in v.items()}}}
    raise TypeError(type(v))


def dec(v):
    (kind, x), = v.items()
    if kind == "mapValue":
        return {k: dec(y) for k, y in x.get("fields", {}).items()}
    if kind == "arrayValue":
        return [dec(y) for y in x.get("values", [])]
    if kind == "integerValue":
        return int(x)
    return x  # string / boolean / null / double / timestamp


def fields(d):
    return {"fields": {k: enc(v) for k, v in d.items()}}


def doc_get(path, token):
    code, body = http("GET", f"{FS}/{path}", token=token)
    must(code == 200, f"Firestore 読み込み {path}", code, body)
    return {k: dec(v) for k, v in body.get("fields", {}).items()}


def doc_create(collection, doc_id, data, token, step=None, expect=200):
    """createdAt はサーバーの時刻で入れる（画面の serverTimestamp() と同じ。firestore.rules が求める）。"""
    name = f"projects/{PROJECT}/databases/{DATABASE}/documents/{collection}/{doc_id}"
    write = {"update": {"name": name, **fields(data)}, "currentDocument": {"exists": False},
             "updateTransforms": [{"fieldPath": "createdAt", "setToServerValue": "REQUEST_TIME"}]}
    code, body = http("POST", f"{FS}:commit", {"writes": [write]}, token)
    ok = code == 200 if expect == 200 else code == expect
    must(ok, step or f"Firestore 作成 {collection}/{doc_id}", code, body)


def doc_patch(path, data, mask, token, step):
    q = "&".join(f"updateMask.fieldPaths={m}" for m in mask)
    code, body = http("PATCH", f"{FS}/{path}?{q}&currentDocument.exists=true", fields(data), token)
    must(code == 200, step, code, body)


def must(ok, step, code=None, body=None):
    if ok:
        print(f"  OK   {step}")
        return
    print(f"  FAIL {step}  (HTTP {code})")
    print("       " + json.dumps(body, ensure_ascii=False)[:500])
    if code == 403 and "firestore" in json.dumps(body).lower():
        print("       → Firestore のルールで弾かれています。firestore.rules を公開したか確認してください")
    sys.exit(1)


def sign_in(key, who):
    code, body = http("POST", f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={key}", {"returnSecureToken": True})
    must(code == 200, f"{who}: 匿名ログイン", code, body)
    return body["idToken"], body["localId"]


def call(api, path, sid, mid, token, step, **extra):
    t = time.time()
    body = {"sessionId": sid, **({"messageId": mid} if mid else {}), **extra}
    code, out = http("POST", api.rstrip("/") + path, body, token)
    must(code == 200, f"{step}（{time.time() - t:.1f}秒）", code, out)
    return out


def doctor_mode_flow(a, sid, mid, pt, dt):
    """医師が選ぶ流れ: 送信 → 問診メモ・確認候補 → 医師が確認質問を送る → 患者が選択肢で答える → 情報の状態。"""
    call(a.api, "/send", sid, mid, pt, "患者が送信（/send）")
    for _ in range(20):  # 意味の解析は翻訳の後に届く
        m = doc_get(f"sessions/{sid}/messages/{mid}", dt)
        if m.get("meaning"):
            break
        time.sleep(1)
    mean = m.get("meaning") or {}
    must(mean.get("status") == "ready", f"問診メモへの整理（meaning.status={mean.get('status')}）")
    for f in mean["facts"]:
        print(f"       情報: [{f['category']}] {f['normalized_meaning']}  ←「{f['source_quote']}」 {f['status']}")
        must(f["source_quote"] in a.text, f"根拠「{f['source_quote']}」が原文にある")
    print(f"       採用しなかった AI の出力: {len(mean['rejected'])} 件")
    if not mean["candidates"]:
        print("       確認候補なし（この発言は曖昧でないと判断された）")
        return
    c = mean["candidates"][0]
    print(f"       確認候補:「{c['source_quote']}」{c['reason']}")
    print(f"       質問: {c['question_doctor']} / {c['question_patient']}  選択肢: {c['options_patient']}")
    q = call(a.api, "/clarify/send", sid, None, dt, "医師が確認質問を送る", messageId=mid, candidateId=c["candidate_id"])
    qm = doc_get(f"sessions/{sid}/messages/{q['messageId']}", pt)
    must(qm["kind"] == "clarify", "患者が確認質問を読める")
    aid = "a-" + uuid.uuid4().hex[:8]
    if qm["clarify"]["options"]:
        ans, text = {"type": "option", "index": 0}, qm["clarify"]["options"][0]
    else:
        ans, text = {"type": "unsure"}, qm["clarify"]["fixed"]["unsure"]
    doc_create(f"sessions/{sid}/messages", aid, {"from": "patient", "original": text, "status": "analyzing",
                                                 "replyTo": q["messageId"], "answer": ans}, pt, f"患者が答える: {text}")
    call(a.api, "/analyze", sid, aid, pt, "/analyze（答え）")
    src = doc_get(f"sessions/{sid}/messages/{mid}", dt)["meaning"]
    cand = next(x for x in src["candidates"] if x["candidate_id"] == c["candidate_id"])
    print(f"       確認候補の状態: {cand['status']}")
    for fid in cand["fact_ids"]:
        f = next(x for x in src["facts"] if x["fact_id"] == fid)
        print(f"       情報の状態: {f['normalized_meaning']} → {f['status']} {f['resolved_meaning']}")
        must(f["status"] != "doctor_confirmed", "患者が答えただけでは医師確認済みにならない")


def teachback_flow(a, sid, did, pt, dt):
    print("3b. 理解の確認（teach-back）")
    q = call(a.api, "/teachback/start", sid, did, dt, "医師が「理解を確認」")
    aid = "t-" + uuid.uuid4().hex[:8]
    doc_create(f"sessions/{sid}/messages", aid, {"from": "patient", "original": a.restatement, "status": "analyzing",
                                                 "replyTo": q["messageId"], "answer": {"type": "free_text"}}, pt,
               f"患者が言い直す: {a.restatement}")
    call(a.api, "/analyze", sid, aid, pt, "/analyze（言い直し）")
    call(a.api, "/send", sid, aid, pt, "患者が送信")
    tb = doc_get(f"sessions/{sid}/messages/{q['messageId']}", dt)["teachback"]
    r = tb.get("result") or {}
    print(f"       判定（AI）: {r.get('overall')}  状態: {tb['status']}")
    for p in r.get("points", []):
        print(f"       - {p['verdict']}: {p['doctor_point']} / {p['patient_understanding']}")
    must(tb["status"] in ("assessed", "answered"), "AI の判定だけでは医師確認済みにならない")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api", required=True, help="Cloud Run の Service URL")
    p.add_argument("--key", required=True, help="firebaseConfig の apiKey")
    p.add_argument("--lang", default="en", help="患者の言語（en / zh-CN / vi）")
    p.add_argument("--text", default="I've had a headache for a while.", help="患者の発言")
    p.add_argument("--doctor", default="この薬は1日3回、食後に飲んでください。", help="医師の発言")
    p.add_argument("--restatement", default="Three times a day, after meals.", help="理解の確認で患者が言い直す内容")
    p.add_argument("--database", default="(default)", help="Firestore のデータベース（ステージングは staging）")
    a = p.parse_args()
    use_database(a.database)

    print("1. ログインと session")
    pt, puid = sign_in(a.key, "患者")
    dt, duid = sign_in(a.key, "医師")
    sid = "smoke-" + uuid.uuid4().hex[:8]
    doc_create("sessions", sid, {"patientLang": None, "doctorLang": "ja", "members": [duid]}, dt, "医師が session を作る")
    doc_patch(f"sessions/{sid}", {"members": [duid, puid], "patientLang": a.lang}, ["members", "patientLang"], pt,
              "患者が言語を選んで参加")

    print("2. 患者の発言")
    mid = "p-" + uuid.uuid4().hex[:8]
    doc_create(f"sessions/{sid}/messages", mid, {"from": "patient", "original": a.text, "status": "analyzing"}, pt)
    res = call(a.api, "/analyze", sid, mid, pt, "/analyze")
    doctor_mode = "meaning" in res  # 医師が選ぶ流れ（CLARIFY_MODE=doctor）の API は meaning を返す
    m = doc_get(f"sessions/{sid}/messages/{mid}", pt)
    print(f"       status={m['status']}  facts={[f['doctor_gloss'] for f in m.get('facts', [])]}")

    if m["status"] == "clarifying":
        c = m["clarification"]
        print(f"       確認質問: {c['question']}  （対象: {c['target']}）")
        print(f"       選択肢: {c['options']} + {list(c['fixed_options'].values())}")
        ans = {"type": "option", "text": c["options"][0]} if c["options"] else {"type": "unsure", "text": ""}
        doc_patch(f"sessions/{sid}/messages/{mid}", {"clarification": {"answer": ans}},
                  ["clarification.answer"], pt, f"患者が回答: {ans['text'] or '説明できない'}")
        call(a.api, "/translate", sid, mid, pt, "/translate")
        m = doc_get(f"sessions/{sid}/messages/{mid}", pt)

    must(m["status"] == "confirmed", f"status が confirmed（実際: {m['status']}）")
    tr = m["translation"]
    print(f"       医師への訳: {tr['text']}")
    print(f"       逆翻訳:     {tr['back_translation']}")
    if tr.get("note"):
        print(f"       注意:       {tr['note']}")
    if doctor_mode:
        doctor_mode_flow(a, sid, mid, pt, dt)
    else:
        doc_patch(f"sessions/{sid}/messages/{mid}", {"status": "sent"}, ["status"], pt, "患者が送信")
    doc_patch(f"sessions/{sid}/messages/{mid}", {"status": "received"}, ["status"], dt, "医師が受け取り")

    print("3. 医師の発言")
    mid = "d-" + uuid.uuid4().hex[:8]
    doc_create(f"sessions/{sid}/messages", mid, {"from": "doctor", "original": a.doctor, "status": "analyzing"}, dt)
    call(a.api, "/translate", sid, mid, dt, "/translate")
    m = doc_get(f"sessions/{sid}/messages/{mid}", pt)
    must(m["status"] == "sent", f"status が sent（実際: {m['status']}）")
    print(f"       患者への訳: {m['translation']['text']}")
    if doctor_mode:
        code, _ = http("POST", a.api.rstrip("/") + "/teachback/start", {"sessionId": sid, "messageId": mid}, pt)
        must(code == 403, "患者は医師の操作（理解を確認）を呼べない", code)
        teachback_flow(a, sid, mid, pt, dt)

    print("4. 守られているか")
    doc_create(f"sessions/{sid}/messages", "x1", {"from": "patient", "original": "hi", "status": "confirmed"}, pt,
               "端末は analyzing 以外の状態で発言を作れない", expect=403)
    doc_create(f"sessions/{sid}/messages", "x2", {"from": "doctor", "original": "hi", "status": "analyzing"}, pt,
               "患者は医師になりすませない", expect=403)
    doc_create(f"sessions/{sid}/messages", "x3", {"from": "patient", "original": "hi", "status": "analyzing",
                                                  "kind": "clarify"}, pt, "患者は確認質問（kind）を作れない", expect=403)
    doc_create(f"sessions/{sid}/messages", "x4", {"from": "doctor", "original": "hi", "status": "analyzing",
                                                  "replyTo": "x"}, dt, "医師は患者の答え（replyTo）を作れない", expect=403)
    st, _ = sign_in(a.key, "部外者")
    code, _ = http("GET", f"{FS}/sessions/{sid}", token=st)
    must(code == 403, "メンバー以外は session を読めない", code)
    code, _ = http("POST", a.api.rstrip("/") + "/analyze", {"sessionId": sid, "messageId": mid}, st)
    must(code == 403, "メンバー以外は API を呼べない", code)

    print(f"\nすべて OK（session: {sid}）")


if __name__ == "__main__":
    main()
