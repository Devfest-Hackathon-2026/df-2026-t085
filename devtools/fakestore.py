"""テストとローカル開発用の、メモリ上の Firestore もどき（main.py が使う操作だけ）。本番では使わない。

  - main.db() の代わり（main._db = FakeDB()）。main._transact / main._tx も install() で差し替える
  - devserver.py は同じものを画面の「Firestore」としても使う（変更があると version が増え、wait() が起きる）
"""
import copy
import itertools
import threading
import uuid
from datetime import datetime, timedelta, timezone

from firebase_admin import firestore

LOCK = threading.RLock()
_CHANGED = threading.Condition(LOCK)
_tick = itertools.count()


def server_now():
    # 同じマイクロ秒に2回書いても順番が逆にならないよう、少しずつずらす
    return datetime.now(timezone.utc) + timedelta(microseconds=next(_tick) % 1000)


def _resolve(v, old=None):
    if v is firestore.SERVER_TIMESTAMP:
        return server_now()
    if isinstance(v, firestore.Increment):
        return (old or 0) + v.value
    if isinstance(v, firestore.ArrayUnion):
        base = list(old or [])
        return base + [x for x in v.values if x not in base]
    if isinstance(v, dict):
        return {k: _resolve(x) for k, x in v.items()}
    return copy.deepcopy(v)


class FakeDB:
    def __init__(self, store=None):
        self.store = {} if store is None else store
        self.version = 0

    def collection(self, name):
        return FakeCol(self, name)

    def touch(self):
        self.version += 1
        _CHANGED.notify_all()

    def wait(self, since, timeout):
        """version が since より大きくなるまで待つ（devserver の onSnapshot もどき）。"""
        with _CHANGED:
            _CHANGED.wait_for(lambda: self.version > since, timeout=timeout)
            return self.version


class FakeCol:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def document(self, id=None):
        return FakeDoc(self.db, f"{self.path}/{id or uuid.uuid4().hex[:20]}")

    def _ids(self):
        pre = self.path + "/"
        return [k[len(pre):] for k in list(self.db.store) if k.startswith(pre) and "/" not in k[len(pre):]]

    def list_documents(self):
        with LOCK:
            return [self.document(i) for i in self._ids()]

    def stream(self):
        with LOCK:
            return [FakeSnap(i, copy.deepcopy(self.db.store[f"{self.path}/{i}"])) for i in self._ids()]


class FakeDoc:
    def __init__(self, db, path):
        self.db, self.path = db, path
        self.id = path.rsplit("/", 1)[-1]

    def collection(self, name):
        return FakeCol(self.db, f"{self.path}/{name}")

    def get(self, transaction=None):
        with LOCK:
            data = self.db.store.get(self.path)
            return FakeSnap(self.id, copy.deepcopy(data) if data is not None else None)

    def set(self, data):
        with LOCK:
            self.db.store[self.path] = {k: _resolve(v) for k, v in data.items()}
            self.db.touch()

    def create(self, data):
        with LOCK:
            if self.path in self.db.store:
                raise ValueError("already exists")
            self.set(data)

    def update(self, data):
        with LOCK:
            if self.path not in self.db.store:
                raise KeyError(f"no document {self.path}")
            doc = self.db.store[self.path]
            for k, v in data.items():
                *parents, leaf = k.split(".")  # "clarify.last_answer_id" のような入れ子の更新
                target = doc
                for p in parents:
                    target = target.setdefault(p, {})
                if v is firestore.DELETE_FIELD:
                    target.pop(leaf, None)
                else:
                    target[leaf] = _resolve(v, target.get(leaf))
            self.db.touch()

    def delete(self):
        with LOCK:
            self.db.store.pop(self.path, None)
            self.db.touch()


class FakeSnap:
    def __init__(self, id, data):
        self.id, self.exists, self._data = id, data is not None, data

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


def fake_transact(m_ref, check):
    with LOCK:
        msg = m_ref.get().to_dict()
        m_ref.update(check(msg))
        return msg


def fake_tx(refs, fn, create=None):
    with LOCK:
        datas = [r.get().to_dict() for r in refs]
        updates = fn(copy.deepcopy(datas))
        for r, u in zip(refs, updates):
            if u:
                r.update(u)
        if create:
            create[0].create(create[1])
        return datas


def install(main, db):
    """main.py の Firestore を偽物にする（Gemini はそのまま。差し替えは呼ぶ側で）。"""
    main._db = db
    main._transact = fake_transact
    main._tx = fake_tx
