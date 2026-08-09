"""Mock backend server using httpx MockTransport.

Provides an in-memory mock of the OpenRobotService backend for API tests.
"""

import json
import base64
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs
import httpx


def _make_token(username: str) -> str:
    payload = json.dumps({"sub": username, "exp": time.time() + 3600, "iat": time.time()}).encode()
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"mock-sig").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


def _decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


_DEFAULT_USERS = {
    "testadmin": {"id": "u001", "username": "testadmin", "password": "admin123",
                  "name": "test admin", "role": "admin", "permissions": ["admin"]},
    "engineer": {"id": "u002", "username": "engineer", "password": "eng123",
                 "name": "test engineer", "role": "engineer",
                 "permissions": ["task:read", "task:write"]},
    "customer": {"id": "u003", "username": "customer", "password": "cust123",
                 "name": "test customer", "role": "customer",
                 "permissions": ["task:read"]},
}


class MockBackend:
    def __init__(self):
        self._users: Dict[str, dict] = {}
        self._tasks: Dict[int, dict] = {}
        self._task_id_counter: int = 1
        self._comments: Dict[int, list] = {}
        self._comment_id_counter: int = 1
        self._wechat_menu: dict = {}
        self._conversations: Dict[int, dict] = {}
        self._wechat_tags: dict = {}
        self._wechat_tag_id_counter: int = 1
        self._admin_projects: dict = {}
        self._admin_project_id: int = 1
        self._admin_risks: dict = {}
        self._admin_risk_id: int = 1
        self._tickets: Dict[int, dict] = {}
        self._ticket_id_counter: int = 1
        self._admin_roles: dict = {}
        self._admin_role_id: int = 1
        self._integrations_sources: list = [
            {"name": "wecom", "display_name": "企业微信", "status": "enabled", "last_sync": None},
            {"name": "zentao", "display_name": "禅道", "status": "enabled", "last_sync": None},
        ]
        self._integrations_mappings: dict = {}
        self._integration_mapping_id: int = 1
        self._setup_defaults()
        self._seed_default_resources()

    def _setup_defaults(self):
        for uid, data in _DEFAULT_USERS.items():
            self._users[uid] = dict(data)

    def _get_user_from_token(self, request):
        auth = request.headers.get("authorization", "") or request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") and not auth.startswith("bearer "):
            return None
        token = auth.split(" ", 1)[1]
        payload = _decode_token(token)
        if payload is None:
            return None
        return self._users.get(payload.get("sub"))

    def _require_auth(self, request):
        u = self._get_user_from_token(request)
        if u is None:
            return httpx.Response(401, json={"detail": "Invalid authentication credentials"})
        return None

    def _get_task_or_404(self, tid):
        if tid not in self._tasks:
            return httpx.Response(404, json={"detail": "Task not found"})
        return None

    async def handle(self, request):
        path = request.url.path
        method = request.method.upper()
        qs = urlparse(str(request.url)).query
        params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(qs).items()} if qs else {}
        body = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except Exception:
                pass
        if path == "/health" and method == "GET":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/api/auth/login" and method == "POST":
            return self._handle_login(body)
        if path == "/api/auth/me" and method == "GET":
            return self._handle_me(request)
        if path.startswith("/api/tasks/sources"):
            return self._route_sources(path, method, body, request)
        if path.startswith("/api/tasks"):
            return self._route_tasks(path, method, body, params, request)
        if path.startswith("/api/wechat"):
            return self._route_wechat(path, method, body, request)
        if path.startswith("/api/admin"):
            return self._route_admin(path, method, body, request, params)
        if path.startswith("/api/call/conversations") or path.startswith("/api/call/qa") or path.startswith("/api/call/messages") or path.startswith("/api/call/my-tasks"):
            return self._route_call(path, method, body, request)
        if path.startswith("/api/ai"):
            return self._route_ai(path, method, body, request)
        return httpx.Response(404, json={"detail": "Not found"})

    def _require_api_key(self, request):
        key = request.headers.get("x-api-key", "") or request.headers.get("X-API-Key", "")
        if key != "test-api-key":
            return httpx.Response(401, json={"detail": "Invalid API key"})
        return None

    def _route_sources(self, path, method, body, request):
        rest = path[len("/api/tasks/sources"):] or ""
        if rest.startswith("/wecom/projects/sync") and method == "POST":
            e = self._require_auth(request)
            return e if e else httpx.Response(200, json={
                "code": 200, "message": "ok",
                "data": {"fetched": 0, "filtered": 0, "created": 0, "updated": 0,
                         "skipped": 0, "errors": 0},
            })
        e = self._require_api_key(request)
        if e:
            return e
        if not rest and method == "GET":
            return httpx.Response(200, json=self._integrations_sources)
        if rest.startswith("/") and method == "POST":
            import re as _re
            m = _re.match(r"^/([^/]+)/sync$", rest)
            if not m:
                return httpx.Response(404, json={"detail": "Not found"})
            source = m.group(1)
            if source not in {s["name"] for s in self._integrations_sources}:
                return httpx.Response(404, json={"detail": f"Source not registered: {source}"})
            return httpx.Response(200, json={"code": 200, "message": "ok",
                                             "data": {"source": source, "synced": 1, "upserted": 0}})
        return httpx.Response(404, json={"detail": "Not found"})

    def _handle_login(self, body):
        username = body.get("username", "")
        password = body.get("password", "")
        if not username or not password:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "username"], "msg": "field required"}]})
        user = self._users.get(username)
        if user is None or user["password"] != password:
            return httpx.Response(401, json={"detail": "Invalid credentials"})
        return httpx.Response(200, json={
            "access_token": _make_token(username), "token_type": "bearer",
            "refresh_token": _make_token(username + ":refresh"),
        })

    def _handle_me(self, request):
        user = self._get_user_from_token(request)
        if user is None:
            return httpx.Response(401, json={"detail": "Invalid authentication credentials"})
        return httpx.Response(200, json={
            "username": user["username"], "name": user["name"],
            "role": user["role"], "id": user["id"],
            "permissions": user.get("permissions", []),
        })

    def _route_tasks(self, path, method, body, params, request):
        rest = path[len("/api/tasks"):] or ""
        if rest == "/cuiban-notification" and method == "POST":
            e = self._require_auth(request)
            return e if e else self._handle_cuiban(body)
        if rest == "/assignable-users" and method == "GET":
            e = self._require_auth(request)
            return e if e else self._handle_assignable_users(params)
        if rest == "/stats/overview" and method == "GET":
            return self._handle_task_stats()
        if rest == "/filter" and method == "POST":
            e = self._require_auth(request)
            return e if e else self._handle_task_filter(body)
        if not rest and method == "GET":
            return self._handle_task_list(params)
        if not rest and method == "POST":
            e = self._require_auth(request)
            return e if e else self._handle_task_create(body, request)
        parts = rest.strip("/").split("/") if rest else []
        if len(parts) >= 1 and parts[0].isdigit():
            tid, sub = int(parts[0]), ("/" + "/".join(parts[1:])) if len(parts) > 1 else ""
            if sub == "/comments" and method == "POST":
                return self._handle_comment_create(tid, body, request)
            if sub == "/comments" and method == "GET":
                return self._handle_comment_list(tid)
            if sub == "/status" and method == "PATCH":
                return self._handle_task_status(tid, body)
            if sub == "/assign" and method == "PATCH":
                return self._handle_task_assign(tid, body, request)
            if sub == "/ai-assign" and method == "POST":
                return self._handle_ai_assign(tid)
            e = self._get_task_or_404(tid)
            if e:
                return e
            if method == "GET":
                return self._handle_task_detail(tid)
            if method == "PUT":
                return self._handle_task_update(tid, body)
            if method == "DELETE":
                return self._handle_task_delete(tid)
        return httpx.Response(404, json={"detail": "Not found"})

    def _handle_task_create(self, body, request):
        missing = [f for f in ["title", "description"] if not body.get(f)]
        if missing:
            return httpx.Response(422, json={"detail": [{"loc": ["body", f], "msg": "field required"} for f in missing]})
        ttype = body.get("ticket_type", "problem")
        allowed_types = {"bug", "requirement", "support", "problem"}
        if ttype not in allowed_types:
            return httpx.Response(400, json={"detail": f"Invalid ticket_type: {ttype}. Allowed: {allowed_types}"})
        tid = self._task_id_counter
        self._task_id_counter += 1
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        user = self._get_user_from_token(request)
        task = {"id": tid, "title": body["title"], "description": body.get("description", ""),
                "ticket_type": ttype, "priority": body.get("priority", "medium"),
                "status": "pending", "created_by": user["username"] if user else "system",
                "assigned_to": body.get("assigned_to"), "customer": body.get("customer"),
                "project_name": body.get("project_name"), "project_id": body.get("project_id"),
                "tags": body.get("tags", []), "metadata_info": body.get("metadata_info", {}),
                "attachments": body.get("attachments", []),
                "created_at": now, "updated_at": now, "resolved_at": None, "closed_at": None,
                "deadline_at": None, "reply_count": 0, "view_count": 0, "comments": []}
        self._tasks[tid] = task
        self._comments[tid] = []
        return httpx.Response(200, json=task)

    def _handle_task_list(self, params):
        page = int(params.get("page", 1)) if params else 1
        size = int(params.get("size", 10)) if params else 10
        all_tasks = sorted(self._tasks.values(), key=lambda t: t["id"], reverse=True)
        total = len(all_tasks)
        start = (page - 1) * size
        return httpx.Response(200, json={
            "items": [dict(t) for t in all_tasks[start:start + size]],
            "total": total, "page": page, "size": size,
            "pages": (total + size - 1) // size if total else 1,
        })

    def _handle_task_detail(self, tid):
        return httpx.Response(200, json=dict(self._tasks[tid]))

    def _handle_task_update(self, tid, body):
        task = self._tasks[tid]
        for k in ["title", "description", "priority", "assigned_to", "customer", "tags"]:
            if k in body:
                task[k] = body[k]
        task["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        return httpx.Response(200, json=dict(task))

    _ALLOWED = {"pending": ["in_progress", "cancelled"], "in_progress": ["resolved", "cancelled"],
                "resolved": ["closed"], "closed": [], "cancelled": []}

    def _handle_task_status(self, tid, body):
        task = self._tasks[tid]
        new_status = body.get("status", "")
        allowed = self._ALLOWED.get(task["status"], [])
        if new_status not in allowed:
            return httpx.Response(400, json={"detail": f"Invalid transition: {task['status']} -> {new_status}. Allowed: {allowed}"})
        task["status"] = new_status
        task["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        return httpx.Response(200, json=dict(task))

    def _handle_task_assign(self, tid, body, request):
        e = self._get_task_or_404(tid)
        if e:
            return e
        task = self._tasks[tid]
        if task["status"] in ("closed", "cancelled"):
            return httpx.Response(400, json={"detail": f"Cannot assign on terminal status: {task['status']}"})
        user = self._get_user_from_token(request)
        is_admin = user and user.get("role") == "admin"
        new_assignee = body.get("assigned_to", "")
        if task.get("assigned_to") and new_assignee and new_assignee != task["assigned_to"] and not is_admin:
            return httpx.Response(403, json={"detail": "Only admin can reassign tickets"})
        task["assigned_to"] = new_assignee or task.get("assigned_to")
        if task["status"] == "pending" and new_assignee:
            task["status"] = "in_progress"
        task["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        return httpx.Response(200, json=dict(task))

    def _handle_task_delete(self, tid):
        del self._tasks[tid]
        self._comments.pop(tid, None)
        return httpx.Response(204)

    def _handle_task_filter(self, body):
        rs = list(self._tasks.values())
        if body.get("status"):
            rs = [t for t in rs if t["status"] == body["status"]]
        if body.get("priority"):
            rs = [t for t in rs if t["priority"] == body["priority"]]
        if body.get("keyword"):
            kw = body["keyword"].lower()
            rs = [t for t in rs if kw in t["title"].lower() or kw in t["description"].lower()]
        return httpx.Response(200, json={"items": [dict(t) for t in rs], "total": len(rs)})

    def _handle_task_stats(self):
        ss, ps = {}, {}
        for t in self._tasks.values():
            ss[t["status"]] = ss.get(t["status"], 0) + 1
            ps[t["priority"]] = ps.get(t["priority"], 0) + 1
        return httpx.Response(200, json={"total": len(self._tasks), "by_status": ss, "by_priority": ps})

    def _handle_comment_create(self, tid, body, request):
        e = self._get_task_or_404(tid)
        if e:
            return e
        cid = self._comment_id_counter
        self._comment_id_counter += 1
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        user = self._get_user_from_token(request)
        c = {"id": cid, "task_id": tid, "content": body.get("content", ""),
             "author": user["username"] if user else "anonymous", "created_at": now}
        self._comments.setdefault(tid, []).append(c)
        self._tasks[tid]["reply_count"] = len(self._comments[tid])
        return httpx.Response(201, json=c)

    def _handle_comment_list(self, tid):
        e = self._get_task_or_404(tid)
        if e:
            return e
        return httpx.Response(200, json=self._comments.get(tid, []))

    def _handle_ai_assign(self, tid):
        e = self._get_task_or_404(tid)
        if e:
            return e
        return httpx.Response(200, json={"task_id": tid, "assigned_to": "engineer-01", "method": "auto", "confidence": 0.85})

    def _handle_qa_submit(self, body, request):
        e = self._require_auth(request)
        if e:
            return e
        conv_id = body.get("conversation_id")
        if not conv_id:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "conversation_id"], "msg": "field required"}]})
        conv = self._conversations.get(conv_id)
        if not conv:
            return httpx.Response(404, json={"detail": "Conversation not found"})
        tid = self._ticket_id_counter
        self._ticket_id_counter += 1
        user = self._get_user_from_token(request)
        ticket = {"ticket_id": tid, "conversation_id": conv_id, "status": "created",
                  "created_by": user["username"] if user else "unknown"}
        self._tickets[tid] = ticket
        return httpx.Response(200, json=ticket)

    def _handle_ticket_ack(self, body):
        tid = body.get("ticket_id")
        if not tid:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "ticket_id"], "msg": "field required"}]})
        ticket = self._tickets.get(tid)
        if not ticket:
            return httpx.Response(404, json={"detail": "Ticket not found"})
        if ticket["status"] != "created":
            return httpx.Response(400, json={"detail": f"Invalid state: {ticket['status']}, expected created"})
        ticket["status"] = "acknowledged"
        return httpx.Response(200, json={"ticket_id": tid, "status": "acknowledged"})

    def _handle_cuiban(self, body):
        task_id = body.get("task_id")
        note = body.get("note", "")
        if not task_id:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "task_id"], "msg": "field required"}]})
        if len(note) > 10000:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "note"], "msg": "string too long"}]})
        task = self._tasks.get(task_id)
        if not task:
            return httpx.Response(404, json={"detail": "Task not found"})
        if task["status"] in ("closed", "cancelled"):
            return httpx.Response(400, json={"detail": f"Cannot urge on terminal status: {task['status']}"})
        return httpx.Response(200, json={"success": True, "task_id": task_id, "message": "Urge sent"})

    def _handle_assignable_users(self, params):
        pid = params.get("project_id")
        if pid is not None:
            try:
                int(pid)
            except (ValueError, TypeError):
                return httpx.Response(400, json={"detail": "Invalid project_id"})
        return httpx.Response(200, json=[
            {"id": "eng-01", "name": "Engineer One", "role": "engineer"},
            {"id": "eng-02", "name": "Engineer Two", "role": "engineer"},
        ])

    def _handle_ai_analyze(self, body):
        tid = body.get("task_id")
        if not tid:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "task_id"], "msg": "field required"}]})
        if tid not in self._tasks:
            return httpx.Response(404, json={"detail": "Task not found"})
        return httpx.Response(200, json={"task_id": tid, "solution": "Mock: replace faulty sensor module", "confidence": 0.92, "steps": ["Disconnect power", "Replace sensor", "Test"]})

    def _handle_ai_task_submit(self, body):
        solution = body.get("solution", "")
        if not solution:
            return httpx.Response(400, json={"detail": "Solution is required"})
        return httpx.Response(200, json={"success": True, "task_id": body.get("task_id"), "message": "Solution submitted"})

    def _handle_ai_chat(self, body):
        message = body.get("message", "")
        if not message:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "message"], "msg": "field required"}]})
        if len(message) > 10000:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "message"], "msg": "string too long"}]})
        return httpx.Response(200, json={"reply": f"Mock: received '{message[:50]}...'", "confidence": 0.9})

    def _handle_ai_chat_stream(self, body):
        return httpx.Response(200, json={"event": "message", "data": {"content": "Mock stream reply"}, "done": True})

    def _handle_ai_task_list(self):
        return httpx.Response(200, json={"items": [], "total": 0})

    def _handle_ai_health(self):
        return httpx.Response(200, json={"status": "ok", "model": "mock-1.0"})

    def _route_wechat(self, path, method, body, request):
        rest = path[len("/api/wechat"):] or ""
        if rest == "/health" and method == "GET":
            return httpx.Response(200, json={"code": 200, "message": "服务运行正常"})
        if not rest:
            return self._handle_callback(path, method, body, request)
        if rest == "/login" and method == "POST":
            openid = body.get("openid", "")
            if not openid:
                return httpx.Response(400, json={"detail": "openid is required"})
            return httpx.Response(200, json={
                "token": _make_token(openid), "refresh_token": _make_token(openid + ":refresh"),
            })
        if rest == "/permissions" and method == "GET":
            openid = request.url.params.get("openid", "")
            if not openid:
                return httpx.Response(404, json={"detail": "user not found"})
            return httpx.Response(200, json={"openid": openid, "permissions": ["task:read"]})
        if rest == "/get_menu" and method == "GET":
            return httpx.Response(200, json={"code": 200, "message": "ok", "data": self._wechat_menu})
        if rest == "/create_menu" and method == "POST":
            return httpx.Response(200, json={"code": 0, "message": "ok"})
        if rest == "/delete_menu" and method == "DELETE":
            return httpx.Response(200, json={"code": 0, "message": "ok"})
        if rest == "/send_message" and method == "POST":
            if not body.get("open_id"):
                return httpx.Response(422, json={"detail": [{"loc": ["body", "open_id"], "msg": "field required"}]})
            return httpx.Response(200, json={"code": 200, "message": "ok"})
        if rest == "/broadcast_message" and method == "POST":
            if not body.get("content"):
                return httpx.Response(422, json={"detail": [{"loc": ["body", "content"], "msg": "field required"}]})
            return httpx.Response(200, json={"code": 200, "message": "ok"})
        if rest == "/send_link_message" and method == "POST":
            if not body.get("url"):
                return httpx.Response(422, json={"detail": [{"loc": ["body", "url"], "msg": "field required"}]})
            return httpx.Response(200, json={"code": 200, "message": "ok"})
        if rest == "/webnotify" and method == "POST":
            msg_type = body.get("msg_type", "")
            if msg_type == "link" and not body.get("link"):
                return httpx.Response(422, json={"detail": [{"loc": ["body", "link"], "msg": "field required"}]})
            at = body.get("at") or {}
            if at.get("is_all"):
                return httpx.Response(400, json={"detail": "@all is not allowed"})
            return httpx.Response(200, json={"code": 200, "message": "ok",
                                             "message_id": "mock-msg-1",
                                             "data": {"status": "success", "recipients": []},
                                             "timestamp": int(time.time())})
        if rest == "/import-data" and method == "POST":
            for field in ("project", "indicator", "content"):
                if field not in body:
                    return httpx.Response(400, json={"detail": f"{field} is required"})
            if not isinstance(body["content"], list):
                return httpx.Response(400, json={"detail": "content must be a list"})
            return httpx.Response(200, json={"success": True, "message": "ok", "content": body["content"],
                                             "api_status": "success", "api_response": {}})
        if rest == "/backend/notify/" and method == "POST":
            at = body.get("at") or {}
            if at.get("is_all"):
                return httpx.Response(400, json={"detail": "@all is not allowed"})
            return httpx.Response(200, json={"status": "success", "message": "ok"})
        if rest.startswith("/tag"):
            return self._route_wechat_tag(rest, method, body, request)
        return httpx.Response(404, json={"detail": "WeChat route not found"})

    def _handle_callback(self, path, method, body, request):
        """GET: signature check (local sha1, echostr roundtrip). POST: XML message callback."""
        token = "local_token"
        if method == "GET":
            signature = request.url.params.get("signature", "")
            timestamp = request.url.params.get("timestamp", "")
            nonce = request.url.params.get("nonce", "")
            echostr = request.url.params.get("echostr", "")
            if not all((signature, timestamp, nonce, echostr)):
                return httpx.Response(403, json={"detail": "invalid signature"})
            import hashlib
            raw = "".join(sorted([token, timestamp, nonce]))
            if hashlib.sha1(raw.encode("utf-8")).hexdigest() == signature:
                return httpx.Response(200, text=echostr)
            return httpx.Response(403, json={"detail": "invalid signature"})
        if method == "POST":
            return httpx.Response(200, text="<xml><MsgType>text</MsgType><Content>mock reply</Content></xml>")
        return httpx.Response(404, json={"detail": "WeChat route not found"})

    def _route_wechat_tag(self, rest, method, body, request):
        sub = rest[len("/tag"):] or ""
        if not sub and method == "GET":
            tags = [{"id": k, "name": v} for k, v in self._wechat_tags.items()]
            return httpx.Response(200, json={"code": 0, "message": "ok", "data": tags})
        if not sub and method == "POST":
            name = body.get("name", "")
            if not name:
                return httpx.Response(422, json={"detail": [{"loc": ["body", "name"], "msg": "field required"}]})
            tid = self._wechat_tag_id_counter
            self._wechat_tag_id_counter += 1
            self._wechat_tags[tid] = name
            return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"id": tid, "name": name}})
        if sub == "/batch-tagging" and method == "POST":
            openid_list = body.get("openid_list", [])
            if len(openid_list) > 100:
                return httpx.Response(400, json={"detail": "openid_list exceeds 100"})
            return httpx.Response(200, json={"code": 0, "message": "ok"})
        if sub == "/batch-untagging" and method == "POST":
            openid_list = body.get("openid_list", [])
            if len(openid_list) > 100:
                return httpx.Response(400, json={"detail": "openid_list exceeds 100"})
            return httpx.Response(200, json={"code": 0, "message": "ok"})
        parts = sub.strip("/").split("/") if sub else []
        if parts and parts[0].isdigit():
            tid = int(parts[0])
            if tid not in self._wechat_tags:
                return httpx.Response(404, json={"detail": "Tag not found"})
            if method == "PUT":
                if "name" in body and not body["name"]:
                    return httpx.Response(422, json={"detail": [{"loc": ["body", "name"], "msg": "field required"}]})
                if "name" in body:
                    self._wechat_tags[tid] = body["name"]
                return httpx.Response(200, json={"code": 0, "message": "ok"})
            if method == "DELETE":
                del self._wechat_tags[tid]
                return httpx.Response(200, json={"code": 0, "message": "ok"})
            if len(parts) >= 2 and parts[1] == "fans" and method == "GET":
                return httpx.Response(200, json={"code": 0, "message": "ok", "data": []})
        if sub.startswith("/user/") and method == "GET":
            openid = sub[len("/user/"):]
            return httpx.Response(200, json={"code": 0, "message": "ok",
                                             "data": {"openid": openid, "tagid_list": []}})
        return httpx.Response(404, json={"detail": "WeChat tag route not found"})

    def _route_call(self, path, method, body, request):
        # --- Conversations ---
        if path.startswith("/api/call/conversations"):
            rest = path[len("/api/call/conversations"):] or ""
            if not hasattr(self, "_conversations"):
                self._conversations = {}
            if not rest and method == "POST":
                cid = len(self._conversations) + 1
                now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                conv = {"id": cid, "title": body.get("title", ""), "created_at": now}
                self._conversations[cid] = conv
                return httpx.Response(200, json=conv)
            if not rest and method == "GET":
                return httpx.Response(200, json={"items": list(self._conversations.values()), "total": len(self._conversations)})
            parts = rest.strip("/").split("/") if rest else []
            if len(parts) >= 1 and parts[0].isdigit():
                cid = int(parts[0])
                if cid not in self._conversations:
                    return httpx.Response(404, json={"detail": "Conversation not found"})
                if method == "PUT":
                    if "title" in body and not body["title"]:
                        return httpx.Response(422, json={"detail": [{"loc": ["body", "title"], "msg": "field required"}]})
                    conv = self._conversations[cid]
                    if "title" in body:
                        conv["title"] = body["title"]
                    return httpx.Response(200, json=conv)
                if method == "DELETE":
                    del self._conversations[cid]
                    return httpx.Response(204)
                return httpx.Response(200, json=self._conversations[cid])
            return httpx.Response(404)
        # --- QA ---
        if path == "/api/call/qa/ask" and method == "POST":
            q = body.get("question", "")
            if not q:
                return httpx.Response(422, json={"detail": [{"loc": ["body", "question"], "msg": "field required"}]})
            return httpx.Response(200, json={"success": True, "question": q, "answer": "Mock: " + q, "conversation_id": 1, "action": "GENERAL_REPLY"})
        if path == "/api/call/qa/ask/stream" and method == "POST":
            q = body.get("question", "")
            return httpx.Response(200, json={"event": "message", "data": {"content": "Mock stream: " + q}, "done": True})
        # --- Messages ---
        if path.startswith("/api/call/messages"):
            rest = path[len("/api/call/messages"):] or ""
            if not hasattr(self, "_messages"):
                self._messages = {}
                self._msg_id = 0
            if not rest and method == "POST":
                self._msg_id += 1
                now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                msg = {"id": self._msg_id, "content": body.get("content", ""), "created_at": now}
                self._messages[self._msg_id] = msg
                return httpx.Response(200, json=msg)
            if not rest and method == "GET":
                if "conversation_id" not in str(request.url.query):
                    return httpx.Response(422, json={"detail": [{"loc": ["query", "conversation_id"], "msg": "field required"}]})
                return httpx.Response(200, json={"items": list(self._messages.values()), "total": len(self._messages)})
            parts = rest.strip("/").split("/") if rest else []
            if len(parts) >= 1 and parts[0].isdigit():
                mid = int(parts[0])
                if mid not in self._messages:
                    return httpx.Response(404, json={"detail": "Message not found"})
                if method == "PUT":
                    if "content" in body and not body["content"]:
                        return httpx.Response(422, json={"detail": [{"loc": ["body", "content"], "msg": "field required"}]})
                    msg = self._messages[mid]
                    if "content" in body:
                        msg["content"] = body["content"]
                    return httpx.Response(200, json=msg)
                if method == "DELETE":
                    del self._messages[mid]
                    return httpx.Response(204)
                return httpx.Response(200, json=self._messages[mid])
            return httpx.Response(404)
        # --- My Tasks ---
        if path.startswith("/api/call/my-tasks/"):
            rest = path[len("/api/call/my-tasks/"):] or ""
            if method == "GET" and rest:
                tid = int(rest) if rest.isdigit() else -1
                if tid in self._tasks:
                    return httpx.Response(200, json=self._tasks[tid])
                return httpx.Response(404, json={"detail": "Task not found"})
            if not rest and method == "GET":
                return httpx.Response(200, json={"items": [], "total": 0})
            if not rest and method == "POST":
                tid = len(self._tasks) + 1
                return httpx.Response(200, json={"id": tid, "title": body.get("title", "")})
        return httpx.Response(404)

    def _route_admin(self, path, method, body, request, params):
        e = self._require_auth(request)
        if e:
            return e
        user = self._get_user_from_token(request)
        if user and user.get("role") != "admin":
            return httpx.Response(403, json={"detail": "Admin access required"})
        rest = path[len("/api/admin"):] or ""
        if rest == "/tickets" and method == "GET":
            return self._handle_admin_tickets_list()
        if rest == "/tickets/stats" and method == "GET":
            return self._handle_admin_tickets_stats()
        if rest.startswith("/dashboard"):
            return self._handle_admin_dashboard()
        # ---- permissions ----
        if rest.startswith("/permissions"):
            return self._route_admin_permissions(rest, method, body)
        # ---- users ----
        if rest == "/users/usp-username" and method == "GET":
            return self._handle_admin_user_usp_username(params)
        if rest == "/users/options" and method == "GET":
            return httpx.Response(200, json={"companies": [], "departments": []})
        if rest in ("/users", "/users/") and method == "GET":
            return self._handle_admin_users_list()
        if rest == "/users" and method == "POST":
            return self._handle_admin_users_create(body)
        if rest.startswith("/users/") and len(rest) > 7:
            return self._route_admin_user_detail(rest, method, body, params)
        # ---- roles ----
        if rest == "/roles/auto-classify" and method == "POST":
            return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"count": 0}})
        if rest in ("/roles", "/roles/") and method == "GET":
            return self._handle_admin_roles_list()
        if rest == "/roles" and method == "POST":
            return self._handle_admin_roles_create(body)
        if rest.startswith("/roles/") and len(rest) > 7:
            return self._route_admin_role_detail(rest, method, body)
        # ---- projects ----
        if rest == "/projects/me" and method == "GET":
            return httpx.Response(200, json=[])
        if rest == "/projects/licenses" and method == "POST":
            for field in ("project_code", "apply_time", "expire_time", "license_code", "applicant", "applicant_id"):
                if field not in body:
                    return httpx.Response(400, json={"detail": f"{field} is required"})
            return httpx.Response(200, json={"code": 0, "message": "ok", "data": body})
        if rest.startswith("/projects/licenses/") and method == "GET":
            return self._handle_admin_project_licenses(rest[len("/projects/licenses/"):], params)
        if rest.startswith("/projects/risks") and method == "GET":
            return self._handle_admin_risks_list()
        if rest.startswith("/projects/risks/") and method in ("PUT", "DELETE"):
            return self._handle_admin_risks_detail(rest[len("/projects/risks/"):], method, body)
        if rest.startswith("/projects/risks") and method == "POST":
            return self._handle_admin_risks_create(body)
        if rest.startswith("/projects/") and len(rest) > 10:
            return self._handle_admin_projects_detail(rest, method, body)
        if rest == "/projects" and method == "GET":
            return self._handle_admin_projects_list()
        if rest == "/projects" and method == "POST":
            return self._handle_admin_projects_create(body)
        # ---- task-user-mappings ----
        if rest == "/task-user-mappings" and method == "GET":
            return self._handle_admin_mappings_list(params)
        if rest == "/task-user-mappings" and method == "POST":
            return self._handle_admin_mappings_create(body)
        if rest.startswith("/task-user-mappings/") and len(rest) > len("/task-user-mappings/"):
            try:
                mid = int(rest[len("/task-user-mappings/"):])
            except ValueError:
                return httpx.Response(404, json={"detail": "Mapping not found"})
            return self._handle_admin_mappings_detail(mid, method, body)
        # ---- daily-reports ----
        if rest.startswith("/daily-reports/by-date/"):
            return self._handle_admin_daily_report_by_date(rest[len("/daily-reports/by-date/"):])
        if rest.startswith("/daily-reports/search/"):
            return httpx.Response(200, json={"items": [], "total": 0})
        if rest in ("/daily-reports", "/daily-reports/") and method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        if rest in ("/daily-reports", "/daily-reports/") and method == "POST":
            return self._handle_daily_report(body)
        if rest.startswith("/daily-reports/"):
            return self._route_admin_daily_report(rest, method, body)
        # ---- export / resources ----
        if rest.startswith("/export/project/") and method == "POST":
            return self._handle_export(body)
        if rest.startswith("/resource-manager/resource-folders"):
            return self._route_resource_folders(path, method, body, request)
        if rest.startswith("/resource-manager/resources"):
            return self._handle_resources(path, method, body, request)
        if rest.startswith("/resource-manager/minio/presigned-url") and method == "GET":
            return self._handle_minio_presigned(path, method, body, request)
        return httpx.Response(404, json={"detail": "Admin route not found"})

    def _route_admin_permissions(self, rest, method, body):
        if not hasattr(self, "_permissions"):
            self._permissions = {}
            self._permission_id = 0
            self._seed_permissions()
        if rest in ("/permissions", "/permissions/") and method == "GET":
            return httpx.Response(200, json=list(self._permissions.values()))
        if rest in ("/permissions", "/permissions/") and method == "POST":
            for field in ("code", "name", "resource_type", "action"):
                if field not in body:
                    return httpx.Response(400, json={"detail": f"{field} is required"})
            if any(p["code"] == body["code"] for p in self._permissions.values()):
                return httpx.Response(400, json={"detail": "Permission code already exists"})
            self._permission_id += 1
            p = {"id": self._permission_id, "code": body["code"], "name": body["name"],
                 "resource_type": body["resource_type"], "action": body["action"]}
            self._permissions[self._permission_id] = p
            return httpx.Response(200, json=p)
        if rest.startswith("/permissions/"):
            try:
                pid = int(rest[len("/permissions/"):])
            except ValueError:
                return httpx.Response(404, json={"detail": "Permission not found"})
            if pid not in self._permissions:
                return httpx.Response(404, json={"detail": "Permission not found"})
            if method == "GET":
                return httpx.Response(200, json=self._permissions[pid])
            if method == "PUT":
                if "code" in body and not body["code"]:
                    return httpx.Response(400, json={"detail": "code is required"})
                p = self._permissions[pid]
                for k in ("code", "name", "resource_type", "action"):
                    if k in body:
                        p[k] = body[k]
                return httpx.Response(200, json=p)
            if method == "DELETE":
                del self._permissions[pid]
                return httpx.Response(200, json={"message": "删除成功"})
        return httpx.Response(404, json={"detail": "Admin route not found"})

    def _seed_permissions(self):
        for i, code in enumerate(("task:read", "task:write", "user:read"), start=1):
            self._permissions[i] = {"id": i, "code": code, "name": code, "resource_type": "base", "action": "read"}

    def _route_admin_user_detail(self, rest, method, body, params):
        sub = rest[len("/users/"):]
        if sub == "usp-username" or sub == "options":
            return httpx.Response(404, json={"detail": "Admin route not found"})
        if sub.endswith("/roles/remove") and method == "POST":
            username = sub[: -len("/roles/remove")]
            return self._handle_admin_user_roles(username, method="remove", body=body)
        if "/roles" in sub and method == "POST":
            username = sub[: sub.index("/roles")]
            return self._handle_admin_user_roles(username, method="assign", body=body)
        if sub.endswith("/reporters") and method == "GET":
            username = sub[: -len("/reporters")]
            return self._handle_admin_user_reporters(username, params)
        if sub.endswith("/uspinfo") and method == "POST":
            username = sub[: -len("/uspinfo")]
            return self._handle_admin_user_uspinfo(username, body)
        if sub.endswith("/detail") and method == "GET":
            username = sub[: -len("/detail")]
            return self._handle_admin_user_detail(username)
        return self._handle_admin_users_detail(rest, method, body)

    def _handle_admin_user_detail(self, username):
        user = self._users.get(username)
        if user is None:
            return httpx.Response(404, json={"detail": "User not found"})
        return httpx.Response(200, json=user)

    def _handle_admin_user_roles(self, username, method, body):
        if username not in self._users:
            return httpx.Response(404, json={"detail": "User not found"})
        if method == "assign":
            role_ids = body.get("role_ids") or []
            if not body.get("project_id"):
                return httpx.Response(400, json={"detail": "project_id is required"})
            if not role_ids:
                return httpx.Response(400, json={"detail": "role_ids is required"})
            return httpx.Response(200, json={"message": "ok"})
        role_ids = body.get("role_ids") or []
        if not body.get("project_id") or not role_ids:
            return httpx.Response(400, json={"detail": "project_id and role_ids are required"})
        return httpx.Response(200, json={"message": "ok"})

    def _handle_admin_user_reporters(self, username, params):
        if username not in self._users:
            return httpx.Response(404, json={"detail": "User not found"})
        if not params.get("project_id"):
            return httpx.Response(400, json={"detail": "project_id is required"})
        return httpx.Response(200, json=[])

    def _handle_admin_user_uspinfo(self, username, body):
        if username not in self._users:
            return httpx.Response(404, json={"detail": "User not found"})
        if not body.get("name"):
            return httpx.Response(400, json={"detail": "name is required"})
        return httpx.Response(200, json={"username": username, "name": body["name"], "usp_password": "mock"})

    def _handle_admin_user_usp_username(self, params):
        name = params.get("name", "")
        if not name:
            return httpx.Response(400, json={"detail": "name is required"})
        return httpx.Response(200, json={"usp_username": name})

    def _route_admin_role_detail(self, rest, method, body):
        if rest.startswith("/roles/auto-classify"):
            return httpx.Response(404, json={"detail": "Admin route not found"})
        if "/permissions" in rest or rest.endswith("/all-permissions"):
            return self._route_admin_role_permissions(rest, method, body)
        return self._handle_admin_roles_detail(rest, method, body)

    def _route_admin_role_permissions(self, rest, method, body):
        core = rest.split("/all-permissions")[0].split("/permissions")[0]
        try:
            rid = int(core[len("/roles/"):])
        except ValueError:
            return httpx.Response(404, json={"detail": "Role not found"})
        if rid not in self._admin_roles:
            return httpx.Response(404, json={"detail": "Role not found"})
        role = self._admin_roles[rid]
        if rest.endswith("/all-permissions") and method == "GET":
            return httpx.Response(200, json=role.get("permissions", []))
        if method == "GET":
            return httpx.Response(200, json=role.get("permissions", []))
        if method == "POST":
            permission_ids = body.get("permission_ids") or []
            if not permission_ids:
                return httpx.Response(400, json={"detail": "permission_ids is required"})
            existing = set(role.get("permissions", []))
            if any(pid in existing for pid in permission_ids):
                return httpx.Response(400, json={"detail": "permission already assigned"})
            role["permissions"] = list(existing | set(permission_ids))
            return httpx.Response(200, json={"message": "ok"})
        if method == "DELETE":
            permission_ids = body.get("permission_ids") or []
            if not permission_ids:
                return httpx.Response(400, json={"detail": "permission_ids is required"})
            role["permissions"] = [p for p in role.get("permissions", []) if p not in permission_ids]
            return httpx.Response(200, json={"message": "ok"})
        return httpx.Response(404, json={"detail": "Admin route not found"})

    def _route_admin_daily_report(self, rest, method, body):
        sub = rest[len("/daily-reports/"):]
        try:
            rid = int(sub)
        except ValueError:
            return httpx.Response(404, json={"detail": "Report not found"})
        if rid != 1:
            return httpx.Response(404, json={"detail": "Report not found"})
        if method == "GET":
            return httpx.Response(200, json={"id": rid, "project_code": "P001",
                                             "report_date": "2026-08-01", "report_content": "mock"})
        if method == "PUT":
            return httpx.Response(200, json={"id": rid, **body})
        if method == "DELETE":
            return httpx.Response(200, json={"message": "删除成功"})
        return httpx.Response(404, json={"detail": "Admin route not found"})

    def _handle_admin_daily_report_by_date(self, path):
        parts = path.strip("/").split("/")
        if len(parts) < 2:
            return httpx.Response(404, json={"detail": "Report not found"})
        return httpx.Response(200, json={"id": 1, "project_code": parts[0],
                                         "report_date": parts[1], "report_content": "mock"})

    def _handle_admin_project_licenses(self, code, params):
        ptype = params.get("type", "last")
        if ptype not in ("last", "all"):
            return httpx.Response(400, json={"detail": "invalid type"})
        return httpx.Response(200, json={"code": 0, "message": "ok",
                                         "data": {"project_code": code, "licenses": []}})

    def _handle_admin_tickets_list(self):
        items = list(self._tasks.values())
        return httpx.Response(200, json={"items": items, "total": len(items)})

    def _handle_admin_tickets_stats(self):
        ss = {}
        for t in self._tasks.values():
            s = t.get("status", "unknown")
            ss[s] = ss.get(s, 0) + 1
        return httpx.Response(200, json={"total": len(self._tasks), "by_status": ss})

    def _handle_admin_dashboard(self):
        ss = {}
        for t in self._tasks.values():
            s = t.get("status", "unknown")
            ss[s] = ss.get(s, 0) + 1
        return httpx.Response(200, json={"total_tickets": len(self._tasks), "by_status": ss})

    def _handle_admin_users_list(self):
        users = [{"id": uid, "username": u["username"], "name": u["name"], "role": u["role"]} for uid, u in self._users.items()]
        return httpx.Response(200, json=users)

    def _handle_admin_users_create(self, body):
        username = body.get("username", "")
        email = body.get("email", "")
        if not username:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "username"], "msg": "field required"}]})
        if username in self._users:
            return httpx.Response(409, json={"detail": "Username already exists"})
        if email and "@" not in email:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "email"], "msg": "invalid email format"}]})
        uid = f"u{len(self._users) + 1:03d}"
        self._users[uid] = {"id": uid, "username": username, "password": body.get("password", "default"),
                            "name": body.get("name", username), "role": body.get("role", "customer"),
                            "permissions": body.get("permissions", [])}
        return httpx.Response(201, json={"id": uid, "username": username, "name": body.get("name", username), "role": body.get("role", "customer")})

    def _handle_admin_users_detail(self, rest, method, body):
        uid = rest[7:]
        if uid not in self._users:
            return httpx.Response(404, json={"detail": "User not found"})
        if method == "PUT":
            if "username" in body and not body["username"]:
                return httpx.Response(422, json={"detail": [{"loc": ["body", "username"], "msg": "field required"}]})
            for k in ("name", "role", "email"):
                if k in body:
                    self._users[uid][k] = body[k]
            return httpx.Response(200, json={"id": uid, "username": self._users[uid]["username"],
                                              "name": self._users[uid]["name"], "role": self._users[uid]["role"]})
        if method == "DELETE":
            del self._users[uid]
            return httpx.Response(204)

    def _handle_admin_roles_list(self):
        roles = [{"id": rid, "name": r["name"]} for rid, r in self._admin_roles.items()]
        if not roles:
            roles = [{"id": 1, "name": "admin"}, {"id": 2, "name": "engineer"}]
        return httpx.Response(200, json=roles)

    def _handle_admin_roles_create(self, body):
        name = body.get("name", "")
        if not name:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "name"], "msg": "field required"}]})
        if any(r["name"] == name for r in self._admin_roles.values()):
            return httpx.Response(409, json={"detail": "Role name already exists"})
        rid = self._admin_role_id
        self._admin_role_id += 1
        self._admin_roles[rid] = {"id": rid, "name": name, "permissions": body.get("permissions", [])}
        return httpx.Response(201, json={"id": rid, "name": name})

    def _handle_admin_roles_detail(self, rest, method, body):
        try:
            rid = int(rest[7:])
        except ValueError:
            return httpx.Response(400, json={"detail": "Invalid role id"})
        if rid not in self._admin_roles:
            return httpx.Response(404, json={"detail": "Role not found"})
        if method == "PUT":
            if "name" in body:
                self._admin_roles[rid]["name"] = body["name"]
            if "permissions" in body:
                self._admin_roles[rid]["permissions"] = body["permissions"]
            return httpx.Response(200, json={"id": rid, "name": self._admin_roles[rid]["name"]})
        if method == "DELETE":
            if rid in (1, 2):
                return httpx.Response(400, json={"detail": "Role is assigned to users"})
            del self._admin_roles[rid]
            return httpx.Response(204)

    def _handle_admin_projects_list(self):
        return httpx.Response(200, json=list(self._admin_projects.values()))

    def _handle_admin_projects_create(self, body):
        if not body.get("name") or not body.get("project_code"):
            return httpx.Response(422, json={"detail": [{"loc": ["body", "name"], "msg": "field required"}]})
        pid = self._admin_project_id
        self._admin_project_id += 1
        proj = {"id": pid, "name": body.get("name", ""), "project_code": body.get("project_code", ""),
                "description": body.get("description", "")}
        self._admin_projects[pid] = proj
        return httpx.Response(200, json=proj)

    def _handle_admin_projects_detail(self, rest, method, body):
        try:
            pid = int(rest[10:])
        except ValueError:
            return httpx.Response(400, json={"detail": "Invalid project id"})
        if pid not in self._admin_projects:
            return httpx.Response(404, json={"detail": "Project not found"})
        if method == "GET":
            return httpx.Response(200, json=self._admin_projects[pid])
        if method == "PUT":
            if "name" in body and not body["name"]:
                return httpx.Response(422, json={"detail": [{"loc": ["body", "name"], "msg": "field required"}]})
            proj = self._admin_projects[pid]
            for k in ("name", "description"):
                if k in body:
                    proj[k] = body[k]
            return httpx.Response(200, json=proj)
        if method == "DELETE":
            del self._admin_projects[pid]
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "Admin route not found"})

    def _handle_admin_risks_list(self):
        return httpx.Response(200, json=list(self._admin_risks.values()))

    def _handle_admin_risks_create(self, body):
        name = body.get("name", "")
        if not name:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "name"], "msg": "field required"}]})
        rc = body.get("risk_code") or f"R{len(self._admin_risks) + 1}"
        risk = {"risk_code": rc, "name": name, "level": body.get("level", "medium")}
        self._admin_risks[rc] = risk
        return httpx.Response(200, json=risk)

    def _handle_admin_risks_detail(self, risk_code, method, body):
        if risk_code not in self._admin_risks:
            return httpx.Response(404, json={"detail": "Risk not found"})
        if method == "PUT":
            risk = self._admin_risks[risk_code]
            if "name" in body and not body["name"]:
                return httpx.Response(422, json={"detail": [{"loc": ["body", "name"], "msg": "field required"}]})
            for k in ("name", "level"):
                if k in body:
                    risk[k] = body[k]
            return httpx.Response(200, json=risk)
        if method == "DELETE":
            del self._admin_risks[risk_code]
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "Admin route not found"})

    def _handle_admin_mappings_list(self, params):
        return httpx.Response(200, json=list(self._integrations_mappings.values()))

    def _handle_admin_mappings_create(self, body):
        source = body.get("source", "")
        external_account = body.get("external_account", "")
        if not source or not external_account:
            return httpx.Response(422, json={"detail": [{"loc": ["body", "source"], "msg": "field required"}]})
        if any(m["source"] == source and m["external_account"] == external_account
               for m in self._integrations_mappings.values()):
            return httpx.Response(409, json={"detail": "Mapping already exists"})
        mid = self._integration_mapping_id
        self._integration_mapping_id += 1
        m = {"id": mid, "source": source, "external_account": external_account,
             "external_realname": body.get("external_realname", ""),
             "local_user_id": body.get("local_user_id", None)}
        self._integrations_mappings[mid] = m
        return httpx.Response(201, json=m)

    def _handle_admin_mappings_detail(self, mid, method, body):
        if mid not in self._integrations_mappings:
            return httpx.Response(404, json={"detail": "Mapping not found"})
        if method == "PUT":
            m = self._integrations_mappings[mid]
            for k in ("external_realname", "local_user_id"):
                if k in body:
                    m[k] = body[k]
            return httpx.Response(200, json=m)
        if method == "DELETE":
            del self._integrations_mappings[mid]
            return httpx.Response(200, json={"message": "删除成功"})
        return httpx.Response(404, json={"detail": "Not found"})

    def _handle_daily_report(self, body):
        rtype = body.get("type", "daily")
        return httpx.Response(200, json={"id": 1, "type": rtype, "status": "generated"})

    def _handle_export(self, body):
        return httpx.Response(200, json={"task_id": "exp-001", "status": "processing", "format": body.get("format", "xlsx")})

    def _handle_resources(self, path, method, body, request):
        rest = path[len("/api/admin/resource-manager/resources"):] or ""
        if rest in ("", "/"):
            if method == "GET":
                return httpx.Response(200, json={"items": [], "total": 0})
            if method == "POST":
                return httpx.Response(200, json={"id": 1, "name": body.get("name", ""), "type": body.get("type", "file")})
            return httpx.Response(404)
        parts = rest.strip("/").split("/") if rest else []
        if parts[0] == "recent" and method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        if parts[0] == "stats" and method == "GET":
            if len(parts) >= 2 and parts[1] == "summary":
                return httpx.Response(200, json={"total": 0, "by_type": {}, "storage_used": 0})
            if len(parts) >= 2 and parts[1] == "daily":
                return httpx.Response(200, json={"items": []})
            return httpx.Response(404)
        if parts[0] == "hash" and method == "GET":
            return httpx.Response(404, json={"detail": "Resource not found"})
        if parts[0] == "owner" and method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        if parts[0] == "type" and method == "GET":
            if parts[1] not in ("file", "image", "video", "audio", "document", "archive", "other"):
                return httpx.Response(422, json={"detail": "invalid resource_type"})
            return httpx.Response(200, json={"items": [], "total": 0})
        if parts[0] == "category" and method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        if parts[0] == "search" and method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        if parts[0] == "sync-build-deploy" and method == "POST":
            return httpx.Response(200, json={"code": 0, "message": "ok"})
        if parts[0] == "sync-oss" and method == "POST":
            return httpx.Response(200, json={"code": 0, "message": "ok"})
        if parts[0].isdigit():
            rid = int(parts[0])
            if method == "GET" and len(parts) == 1:
                return httpx.Response(200, json={"id": rid, "name": "test-resource", "type": "file",
                                                 "resource_status": "available"})
            if method in ("PUT", "PATCH") and len(parts) == 1:
                return httpx.Response(200, json={"id": rid, "name": body.get("name", "updated"), "type": "file"})
            if method == "DELETE" and len(parts) == 1:
                return httpx.Response(200, json={"message": "删除成功"})
            if method == "POST" and parts[1] == "like":
                return httpx.Response(200, json={"id": rid, "liked": True})
            if method == "GET" and parts[1] == "download":
                return httpx.Response(403, json={"detail": "resource unavailable"})
            if method == "GET" and parts[1] in ("download-url", "thumbnail-url", "preview-url"):
                return httpx.Response(404, json={"detail": "url not available"})
            if method == "POST" and parts[1] == "download-count":
                return httpx.Response(200, json={"id": rid, "download_count": 1})
        return httpx.Response(404)

    def _route_resource_folders(self, path, method, body, request):
        rest = path[len("/api/admin/resource-manager/resource-folders"):] or ""
        if rest in ("", "/") and method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        if rest in ("", "/") and method == "POST":
            if not body.get("folder_name"):
                return httpx.Response(400, json={"detail": "folder_name is required"})
            return httpx.Response(201, json={"id": 1, "folder_name": body["folder_name"]})
        if rest == "/root" and method == "GET":
            return httpx.Response(200, json=[])
        if rest == "/root/children" and method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        if rest.startswith("/") and rest[1:].isdigit():
            fid = int(rest[1:])
            if method == "GET":
                return httpx.Response(200, json={"id": fid, "folder_name": f"folder-{fid}"})
            if method == "PUT":
                return httpx.Response(200, json={"id": fid, **body})
            if method == "DELETE":
                return httpx.Response(200, json={"message": "删除成功"})
            if method == "GET" and rest.endswith("/children"):
                return httpx.Response(200, json={"items": [], "total": 0})
        if rest.endswith("/children") and method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        return httpx.Response(404, json={"detail": "Folder not found"})

    def _handle_minio_presigned(self, path, method, body, request):
        params = request.url.params
        if not params.get("bucket_name") or not params.get("object_name"):
            return httpx.Response(422, json={"detail": "bucket_name and object_name are required"})
        return httpx.Response(200, json={"url": f"http://mock-minio/{params['bucket_name']}/{params['object_name']}",
                                         "expires_minutes": params.get("expires_minutes", 5)})

    def _route_ai(self, path, method, body, request):
        rest = path[len("/api/ai"):] or ""
        if rest == "/qa/submit" and method == "POST":
            return self._handle_qa_submit(body, request)
        if rest == "/qa/ticket/ack" and method == "POST":
            return self._handle_ticket_ack(body)
        if rest == "/task/analyze" and method == "POST":
            return self._handle_ai_analyze(body)
        if rest == "/task/analyze/stream" and method == "POST":
            return httpx.Response(200, json={"event": "message", "data": {"content": "Mock analysis stream"}, "done": True})
        if rest == "/task/diagnose" and method == "POST":
            return httpx.Response(200, json={"diagnosis": "Mock: sensor fault detected", "confidence": 0.85})
        if rest == "/task/discuss" and method == "POST":
            return httpx.Response(200, json={"reply": "Mock: check wiring and reboot", "suggestions": ["check cable", "reboot controller"]})
        if rest == "/task/summarize" and method == "POST":
            return httpx.Response(200, json={"summary": "Mock: issue resolved by replacing sensor module"})
        if rest == "/task/submit" and method == "POST":
            return self._handle_ai_task_submit(body)
        if rest == "/task/chat" and method == "POST":
            return self._handle_ai_chat(body)
        if rest == "/task/chat/stream" and method == "POST":
            return self._handle_ai_chat_stream(body)
        if rest == "/task/list" and method == "POST":
            return self._handle_ai_task_list()
        if rest == "/task/health" and method == "GET":
            return self._handle_ai_health()
        return httpx.Response(404)

    def _seed_default_resources(self):
        """Seed default resources for data-driven parametrize tests."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        self._tasks[1] = {
            "id": 1, "title": "Default task", "description": "Seeded for tests",
            "ticket_type": "problem", "priority": "medium", "status": "pending",
            "created_by": "system", "assigned_to": None,
            "tags": [], "metadata_info": {}, "attachments": [],
            "created_at": now, "updated_at": now,
            "resolved_at": None, "closed_at": None,
            "reply_count": 0, "view_count": 0,
        }
        self._comments[1] = []
        self._conversations[1] = {
            "id": 1, "title": "Default conversation",
            "created_at": now,
        }
        self._messages = {}
        self._messages[1] = {"id": 1, "content": "Default message", "created_at": now}
        self._msg_id = 1
        self._tickets[1] = {
            "ticket_id": 1, "conversation_id": 1, "status": "created",
            "created_by": "system",
        }
        self._ticket_id_counter = 2
        self._admin_projects[1] = {
            "id": 1, "name": "Default Project", "description": "Seeded for tests",
        }
        self._admin_project_id = 2
        self._admin_roles[1] = {"id": 1, "name": "admin", "permissions": ["admin"]}
        self._admin_roles[2] = {"id": 2, "name": "engineer", "permissions": ["task:read", "task:write"]}
        self._admin_role_id = 3
        self._admin_risks["R1"] = {"risk_code": "R1", "name": "Seed Risk", "level": "high"}
        self._wechat_tags[1] = "Default tag"
        self._wechat_tag_id_counter = 2

def create_mock_transport():
    backend = MockBackend()
    return httpx.MockTransport(backend.handle)




