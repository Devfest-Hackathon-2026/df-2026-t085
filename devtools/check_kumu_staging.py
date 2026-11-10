"""独立ステージング（kumu-devfest-2026）の版がそろっているかを、外から確かめる（読み取りだけ）。

  .venv/bin/python devtools/check_kumu_staging.py [--commit <git の短い hash>]

- 画面: Hosting が配っている web/ のファイルが、そのコミットの web/ と 1 バイトも違わない（旧版のキャッシュ・別のコミットを検出）
- API: Cloud Run の /health が同じコミットを返す
- ルール: Firestore に公開中のルールが、そのコミットの firestore.rules と同じ（Firebase Rules API。ADC の認証を使う）
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

P = "kumu-devfest-2026"
HOST = f"https://{P}.web.app"
SKIP = {"stt-test.html"}  # firebase.kumu.json で配らない（チームのプロジェクトを向いた開発用のページ）


def git(*a):
    return subprocess.run(["git", *a], check=True, capture_output=True).stdout


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def api_url():
    src = git("show", "HEAD:web/firebase-config.js").decode()
    block = src[src.index("const KUMU_STAGING"):]
    return block.split('api: "', 1)[1].split('"', 1)[0]


def deployed_rules():
    import google.auth
    import google.auth.transport.requests as tr
    cred, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    cred.refresh(tr.Request())
    h = {"Authorization": f"Bearer {cred.token}", "x-goog-user-project": P}
    base = "https://firebaserules.googleapis.com/v1"
    s, rel = fetch(f"{base}/projects/{P}/releases/cloud.firestore", h)
    if s != 200:
        return None, f"release {s}"
    s, rs = fetch(f"{base}/{json.loads(rel)['rulesetName']}", h)
    files = json.loads(rs)["source"]["files"]
    return files[0]["content"], json.loads(rel)["rulesetName"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", default=git("rev-parse", "--short=12", "HEAD").decode().strip())
    a = ap.parse_args()
    ok = True

    def check(name, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"{'OK ' if good else 'NG '} {name}{'  ' + detail if detail else ''}")

    s, body = fetch(f"{HOST}/build.json")
    check("画面の版の印", s == 200 and json.loads(body).get("commit") == a.commit, body.decode(errors="replace").strip()[:120])
    for f in git("ls-tree", "-r", "--name-only", a.commit, "web/").decode().split():
        rel = f.removeprefix("web/")
        if rel in SKIP or rel.endswith(".test.mjs") or "/." in f:
            continue
        s, body = fetch(f"{HOST}/{rel}")
        check(f"画面 {rel}", s == 200 and body == git("show", f"{a.commit}:{f}"), f"HTTP {s}")
    s, body = fetch(f"{api_url()}/health")
    check("API の版（/health）", s == 200 and json.loads(body).get("commit") == a.commit, body.decode(errors="replace")[:120])
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.path.exists(os.path.expanduser("~/.config/gcloud/application_default_credentials.json")):
        content, info = deployed_rules()
        check("Firestore のルール", content == git("show", f"{a.commit}:firestore.rules").decode(), info)
    else:
        check("Firestore のルール", False, "認証が無いので確かめていない")
    print("すべて一致" if ok else "版のずれがある")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
