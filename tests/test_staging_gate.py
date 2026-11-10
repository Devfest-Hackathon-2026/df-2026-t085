"""ステージングの参加者の制限と、プロジェクト全体の 1 日の上限。

- 参加コードの SHA-256 を設定したときだけ、医師がコードで解錠するまで Gemini を使う API は 403
- 患者・第三者は解錠できない。間違いは問診ごとに 5 回まで
- 本番（設定が空）では今までどおり
"""
import hashlib

import pytest

import main

CODE = "test-only-staging-code"


@pytest.fixture
def gated(api, monkeypatch):
    monkeypatch.setattr(main, "STAGING_ACCESS_SHA256", hashlib.sha256(CODE.encode()).hexdigest())
    return api


def analyze(api, text="头疼。"):
    mid = api.as_("pat").say("patient", text)
    return api.post("/analyze", messageId=mid)


def test_production_is_unchanged(api):
    assert main.STAGING_ACCESS_SHA256 == ""
    assert analyze(api).status_code == 200
    r = api.as_("doc").post("/staging/unlock", code="")
    assert r.status_code == 200 and r.json() == {"unlocked": True, "required": False}


def test_locked_session_cannot_call_gemini(gated):
    r = analyze(gated)
    assert r.status_code == 403 and "staging_locked" in r.text
    mid = gated.as_("doc").say("doctor", "いつからですか？")
    assert gated.post("/translate", messageId=mid).status_code == 403
    assert gated.session().get("apiCalls", 0) == 0  # 数えてもいない


def test_doctor_unlocks_with_code_then_gemini_works(gated):
    r = gated.as_("doc").post("/staging/unlock", code=CODE)
    assert r.status_code == 200 and r.json()["required"] is True
    assert gated.session()["stagingAccess"] is True
    assert analyze(gated).status_code == 200


def test_wrong_code_and_attempt_limit(gated):
    for _ in range(main.STAGING_UNLOCK_ATTEMPTS):
        assert gated.as_("doc").post("/staging/unlock", code="wrong").status_code == 403
    # 上限の後は、正しいコードでも開かない（総当たりの防止）
    r = gated.as_("doc").post("/staging/unlock", code=CODE)
    assert r.status_code == 429
    assert gated.session().get("stagingAccess") is not True
    assert analyze(gated).status_code == 403


def test_patient_and_stranger_cannot_unlock(gated):
    assert gated.as_("pat").post("/staging/unlock", code=CODE).status_code == 403
    assert gated.as_("stranger").post("/staging/unlock", code=CODE).status_code == 403
    assert gated.session().get("stagingAccess") is not True


def test_daily_limit_across_sessions(gated, monkeypatch):
    monkeypatch.setattr(main, "DAILY_CALL_LIMIT", 2)
    gated.as_("doc").post("/staging/unlock", code=CODE)
    assert analyze(gated, "头疼。").status_code == 200
    assert analyze(gated, "发烧。").status_code == 200
    r = analyze(gated, "咳嗽。")
    assert r.status_code == 429 and "今日" in r.text
