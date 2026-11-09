"""firestore.rules と同じ条件を Python で書き写したもの（devserver の「Firestore もどき」と tests/test_rules_emulation.py が使う）。

本物のルールエンジン（Firebase エミュレーター）での確認の代わりにはならない。ルールを変えたらここも同じように変える。
値の印: "__TS__" はサーバーの時刻（serverTimestamp）、("__AU__", [...]) は arrayUnion。
"""
from datetime import datetime, timedelta, timezone

PATIENT_LANGS = ["en", "zh-CN", "vi"]


def role_of(sess, uid):
    m = (sess or {}).get("members", [])
    return "doctor" if m and m[0] == uid else "patient" if len(m) == 2 and m[1] == uid else None


def can_read(path, uid, session_of):
    parts = path.split("/")
    sess = session_of(parts[1])
    return bool(sess) and uid in sess.get("members", [])


def check_create(col, data, uid, session_of):
    parts = col.split("/")
    if parts == ["sessions"]:
        ok = (set(data) <= {"patientLang", "doctorLang", "members", "createdAt"} and data.get("members") == [uid]
              and data.get("patientLang") is None and data.get("doctorLang") == "ja" and data.get("createdAt") == "__TS__")
        return ok
    if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "messages":
        sess = session_of(parts[1])
        role = role_of(sess, uid)
        if not role or not set(data) <= {"from", "original", "status", "createdAt", "replyTo", "answer"}:
            return False
        if data.get("from") != role or data.get("status") != "analyzing" or data.get("createdAt") != "__TS__":
            return False
        if not isinstance(data.get("original"), str) or not 0 < len(data["original"]) <= 1000:
            return False
        if "replyTo" in data and (role != "patient" or not isinstance(data["replyTo"], str) or not 0 < len(data["replyTo"]) <= 64):
            return False
        if "answer" in data:
            a = data["answer"]
            if role != "patient" or "replyTo" not in data or not isinstance(a, dict) or not set(a) <= {"type", "index"}:
                return False
            if a.get("type") not in ("option", "unsure", "none", "free_text"):
                return False
            if "index" in a and (not isinstance(a["index"], int) or not 0 <= a["index"] < 4):
                return False
        return True
    return False


def check_update(path, old, data, uid, session_of):
    parts = path.split("/")
    keys = {k.split(".")[0] for k in data}
    if len(parts) == 2:  # 患者が参加する
        m = old.get("members", [])
        return (keys <= {"members", "patientLang"} and len(m) == 1 and data.get("members") == ("__AU__", [uid])
                and old.get("patientLang") is None and data.get("patientLang") in PATIENT_LANGS
                and datetime.now(timezone.utc) < old["createdAt"] + timedelta(minutes=30))
    role = role_of(session_of(parts[1]), uid)
    if role == "patient" and old.get("from") == "patient" and old.get("status") in ("clarifying", "error") \
            and set(data) == {"clarification.answer"}:
        return data["clarification.answer"].get("type") in ("option", "free_text", "unsure")
    # 端末が status を sent にする書き込みは許さない（Safety Gate を飛ばせない。firestore.rules と同じ）
    if role == "doctor" and old.get("from") == "patient" and old.get("status") == "sent" \
            and data.get("status") == "received" and keys <= {"status", "receivedAt"}:
        return True
    return False
