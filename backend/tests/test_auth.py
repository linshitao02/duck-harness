from fastapi.testclient import TestClient

from app.access import reset_actor, set_actor
from app.config import UPLOAD_DIR
from app.db import open_session
from app.main import app
from app.models import Conversation, User
from app.services.runtime import deepseek_credentials


def test_health_stays_open_and_other_routes_require_login():
    with TestClient(app) as anon:
        assert anon.get("/api/health").status_code == 200
        assert anon.get("/api/auth/session").json()["authenticated"] is False
        assert anon.get("/api/conversations").status_code == 401
        assert anon.get("/api/settings").status_code == 401


def test_login_remember_and_reject_bad_password():
    with TestClient(app) as guest:
        bad = guest.post("/api/auth/login", json={"username": "tester", "password": "wrong-pass"})
        assert bad.status_code == 401
        short = guest.post("/api/auth/register", json={"username": "a", "password": "123456"})
        assert short.status_code == 400
        remembered = guest.post(
            "/api/auth/login",
            json={"username": "tester", "password": "tester-pass", "remember": True},
        )
        assert remembered.status_code == 200
        assert remembered.json()["remember"] is True
        assert "Max-Age=" in remembered.headers.get("set-cookie", "")
        assert guest.get("/api/auth/session").json()["remember"] is True
        plain = guest.post("/api/auth/login", json={"username": "tester", "password": "tester-pass"})
        assert plain.json()["remember"] is False
        assert "Max-Age=" not in plain.headers.get("set-cookie", "")
        guest.post("/api/auth/logout")
        assert guest.get("/api/conversations").status_code == 401


def test_bearer_token_opens_the_api_without_a_cookie():
    with TestClient(app) as guest:
        logged = guest.post("/api/auth/login", json={"username": "tester", "password": "tester-pass"})
        assert logged.status_code == 200
        token = logged.json()["token"]
        assert token
    with TestClient(app) as phone:
        assert phone.get("/api/conversations").status_code == 401
        opened = phone.get("/api/conversations", headers={"Authorization": f"Bearer {token}"})
        assert opened.status_code == 200
        assert phone.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"}).json()["authenticated"] is True


def test_first_register_claims_existing_chats(db):
    user = db.get(User, 1)
    user.username = None
    user.password_hash = ""
    db.commit()
    row = Conversation(title="升级前的会话", provider="deepseek", model="deepseek-chat", user_id=1)
    db.add(row)
    db.commit()
    row.user_id = None
    db.commit()
    with TestClient(app) as guest:
        created = guest.post(
            "/api/auth/register",
            json={"username": "owner", "password": "secret1", "name": "主人"},
        )
        assert created.status_code == 200
        assert created.json()["user"]["id"] == 1
        titles = [item["title"] for item in guest.get("/api/conversations").json()["items"]]
        assert "升级前的会话" in titles


def test_users_and_files_stay_apart(db):
    with TestClient(app) as ada, TestClient(app) as bob:
        ada_user = ada.post(
            "/api/auth/register",
            json={"username": "ada", "password": "secret1", "name": "Ada", "remember": True},
        ).json()["user"]
        created = ada.post("/api/conversations", json={"title": "ada 的会话"}).json()
        ada.put("/api/settings", json={"deepseek_api_key": "sk-ada-only", "user_name": "Ada"})
        uploaded = ada.post(
            f"/api/conversations/{created['id']}/attachments",
            files={"file": ("note.txt", b"ada-secret", "text/plain")},
        )
        assert uploaded.status_code == 200
        attachment_id = uploaded.json()["id"]
        ada.post("/api/tasks", json={"title": "ada 的任务"})
        ada.post("/api/skills", json={"name": "ada-skill", "description": "只属于 ada", "body": "ada"})
        ada.post("/api/mcp", json={"name": "ada-mcp", "command": "python", "args": []})
        ada.post("/api/memories", json={"title": "ada", "content": "ada 的记忆"})
        folder = UPLOAD_DIR / f"u{ada_user['id']}" / str(created["id"])
        assert (folder / "note.txt").read_bytes() == b"ada-secret"

        bob_user = bob.post(
            "/api/auth/register",
            json={"username": "ada", "password": "secret2", "name": "Bob"},
        )
        assert bob_user.status_code == 400
        bob_user = bob.post(
            "/api/auth/register",
            json={"username": "bob", "password": "secret2", "name": "Bob"},
        ).json()["user"]
        assert all(item["id"] != created["id"] for item in bob.get("/api/conversations").json()["items"])
        assert bob.get(f"/api/conversations/{created['id']}/messages").status_code == 404
        assert bob.patch(f"/api/conversations/{created['id']}", json={"title": "偷改"}).status_code == 404
        assert bob.delete(f"/api/conversations/{created['id']}").status_code == 404
        assert bob.get(f"/api/attachments/{attachment_id}").status_code == 404
        assert all(item["title"] != "ada 的任务" for item in bob.get("/api/tasks").json()["items"])
        assert all(item["name"] != "ada-skill" for item in bob.get("/api/skills").json()["items"])
        assert all(item["name"] != "ada-mcp" for item in bob.get("/api/mcp").json()["items"])
        assert all("ada 的记忆" not in item["content"] for item in bob.get("/api/memories").json()["items"])
        bob_chat = bob.post("/api/conversations", json={"title": "bob 的会话"}).json()
        assert bob_chat["workspace"] != created["workspace"]
        blocked = bob.patch(f"/api/conversations/{bob_chat['id']}", json={"workspace": created["workspace"]})
        assert blocked.status_code == 400
        bob_settings = bob.get("/api/settings").json()
        assert "sk-ada-only" not in str(bob_settings)
        assert bob_settings["user"]["name"] == "Bob"
        tokens = set_actor(bob_user["id"], ())
        try:
            _base, key, _source = deepseek_credentials(db)
        finally:
            reset_actor(tokens)
        assert key != "sk-ada-only"
        assert ada.get("/api/auth/session").json()["remember"] is True


def test_profile_updates_display_name_and_password():
    with TestClient(app) as guest:
        guest.post("/api/auth/register", json={"username": "nina", "password": "secret1", "name": "妮娜"})
        renamed = guest.post("/api/auth/profile", json={"name": " 小妮娜 "})
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "小妮娜"
        assert guest.get("/api/settings").json()["user"]["name"] == "小妮娜"
        wrong = guest.post("/api/auth/profile", json={"current_password": "nope", "new_password": "secret2"})
        assert wrong.status_code == 400
        changed = guest.post(
            "/api/auth/profile",
            json={"current_password": "secret1", "new_password": "secret2"},
        )
        assert changed.status_code == 200
        guest.post("/api/auth/logout")
        assert guest.post("/api/auth/login", json={"username": "nina", "password": "secret1"}).status_code == 401
        assert guest.post("/api/auth/login", json={"username": "nina", "password": "secret2"}).status_code == 200
