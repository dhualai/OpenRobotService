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
        self._integrations_sources: list = [{"name": "wecom", "status": "enabled", "last_sync": None}]
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
        if path == "/auth/login" and method == "POST":
            return self._handle_login(body)
        if path == "/auth/me" and method == "GET":
            return self._handle_me(request)
        if path.startswith("/api/tasks"):
            return self._route_tasks(path, method, body, params, request)
        if path.startswith("/api/wechat"):
            return self._route_wechat(path, method, body, request)
        if path.startswith("/api/admin"):
            return self._route_admin(path, method, body, request, params)
        if path.startswith("/api/conversations") or path.startswith("/api/qa") or path.startswith("/api/messages") or path.startswith("/api/my-tasks"):
            return self._route_call(path, method, body, request)
        if path.startswith("/api/ai"):
            return self._route_ai(path, method, body, request)
        if path == "/api/integrations" and method == "GET":
            return httpx.Response(200, json=self._integrations_sources)
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
                return self._handle_task_assign(tid, body)
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
        tid = self._task_id_counter
        self._task_id_counter += 1
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        user = self._get_user_from_token(request)
        task = {"id": tid, "title": body["title"], "description": body.get("description", ""),
                "ticket_type": body.get("ticket_type", "problem"), "priority": body.get("priority", "medium"),
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

    def _handle_task_assign(self, tid, body):
        task = self._tasks[tid]
        task["assigned_to"] = body.get("assigned_to", task.get("assigned_to"))
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



    def _route_wechat(self, path, method, body, request):
        rest = path[len("/api/wechat"):] or ""
        if rest == "/health" and method == "GET":
            return httpx.Response(200, json={"code": 200, "message": "服务运行正常"})
        if rest == "/get_menu" and method == "GET":
            return httpx.Response(200, json={"menu": self._wechat_menu})
        if rest == "/create_menu" and method == "POST":
            self._wechat_menu = body
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
        if rest == "/send_message" and method == "POST":
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
        if not rest and method == "GET":
            tags = [{"id": k, "name": v} for k, v in self._wechat_tags.items()]
            return httpx.Response(200, json={"tags": tags})
        if not rest and method == "POST":
            tid = self._wechat_tag_id_counter
            self._wechat_tag_id_counter += 1
            name = body.get("name", "")
            self._wechat_tags[tid] = name
            return httpx.Response(200, json={"tag": {"id": tid, "name": name}})
        return httpx.Response(404, json={"detail": "WeChat route not found"})

    def _route_call(self, path, method, body, request):
        # --- Conversations ---
        if path.startswith("/api/conversations"):
            rest = path[len("/api/conversations"):] or ""
            if not hasattr(self, "_conversations"):
                self._conversations = {}
            if not rest and method == "POST":
                cid = len(self._conversations) + 1
                now = __import__("time").strftime("%Y-%m-%dT%H:%M:%S", __import__("time").gmtime())
                conv = {"id": cid, "title": body.get("title", ""), "created_at": now}
                self._conversations[cid] = conv
                return httpx.Response(200, json=conv)
            if not rest and method == "GET":
                return httpx.Response(200, json={"items": list(self._conversations.values()), "total": len(self._conversations)})
            parts = rest.strip("/").split("/") if rest else []
            if len(parts) >= 1 and parts[0].isdigit():
                cid = int(parts[0])
                if cid in self._conversations:
                    return httpx.Response(200, json=self._conversations[cid])
                return httpx.Response(404, json={"detail": "Conversation not found"})
            return httpx.Response(404)
        # --- QA ---
        if path == "/api/qa/ask" and method == "POST":
            q = body.get("question", "")
            import time
            return httpx.Response(200, json={"success": True, "question": q, "answer": "Mock: " + q, "conversation_id": 1, "action": "GENERAL_REPLY"})
        if path == "/api/qa/ask/stream" and method == "POST":
            q = body.get("question", "")
            return httpx.Response(200, json={"event": "message", "data": {"content": "Mock stream: " + q}, "done": True})
        # --- Messages ---
        if path.startswith("/api/messages"):
            rest = path[len("/api/messages"):] or ""
            if not hasattr(self, "_messages"):
                self._messages = {}
                self._msg_id = 0
            if not rest and method == "POST":
                self._msg_id += 1
                now = __import__("time").strftime("%Y-%m-%dT%H:%M:%S", __import__("time").gmtime())
                msg = {"id": self._msg_id, "content": body.get("content", ""), "created_at": now}
                self._messages[self._msg_id] = msg
                return httpx.Response(200, json=msg)
            if not rest and method == "GET":
                return httpx.Response(200, json={"items": list(self._messages.values()), "total": len(self._messages)})
            return httpx.Response(404)
        # --- My Tasks ---
        if path == "/api/my-tasks/" and method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        if path == "/api/my-tasks/" and method == "POST":
            tid = len(self._tasks) + 1
            return httpx.Response(200, json={"id": tid, "title": body.get("title", "")})
        return httpx.Response(404)

    def _route_admin(self, path, method, body, request, params):
        rest = path[len("/api/admin"):] or ""
        if rest == "/tickets" and method == "GET":
            items = list(self._tasks.values())
            return httpx.Response(200, json={"items": items, "total": len(items)})
        if rest == "/tickets/stats" and method == "GET":
            ss = {}
            for t in self._tasks.values():
                s = t.get("status", "unknown")
                ss[s] = ss.get(s, 0) + 1
            return httpx.Response(200, json={"total": len(self._tasks), "by_status": ss})
        if rest == "/projects" and method == "GET":
            return httpx.Response(200, json=list(self._admin_projects.values()))
        if rest == "/projects" and method == "POST":
            pid = self._admin_project_id
            self._admin_project_id += 1
            proj = {"id": pid, "name": body.get("name", ""), "description": body.get("description", "")}
            self._admin_projects[pid] = proj
            return httpx.Response(200, json=proj)
        if rest == "/projects/risks" and method == "GET":
            return httpx.Response(200, json=list(self._admin_risks.values()))
        if rest.startswith("/dashboard"):
            ss = {}
            for t in self._tasks.values():
                s = t.get("status", "unknown")
                ss[s] = ss.get(s, 0) + 1
            return httpx.Response(200, json={"total_tickets": len(self._tasks), "by_status": ss})
        if rest in ("/users", "/users/") and method == "GET":
            users = [{k: u[k] for k in ["id", "username", "name", "role"]} for u in self._users.values()]
            return httpx.Response(200, json=users)
        if rest in ("/roles", "/roles/") and method == "GET":
            return httpx.Response(200, json=[{"id": 1, "name": "admin"}, {"id": 2, "name": "engineer"}])
        if rest == "/task-user-mappings" and method == "GET":
            return httpx.Response(200, json=list(self._integrations_mappings.values()))
        if rest == "/task-user-mappings" and method == "POST":
            mid = self._integration_mapping_id
            self._integration_mapping_id += 1
            m = {"id": mid, "source_task_id": body.get("source_task_id", ""), "local_task_id": body.get("local_task_id", None)}
            self._integrations_mappings[mid] = m
            return httpx.Response(200, json=m)
        # --- Admin extensions ---
        if rest == "/daily-reports" and method == "POST":
            return self._handle_daily_report(body)
        if rest == "/export" and method == "POST":
            return self._handle_export(body)
        if rest.startswith("/resources"):
            return self._handle_resources(path, method, body, request)
        return httpx.Response(404, json={"detail": "Admin route not found"})

    def _handle_daily_report(self, body):
        rtype = body.get("type", "daily")
        return httpx.Response(200, json={"id": 1, "type": rtype, "status": "generated"})

    def _handle_export(self, body):
        return httpx.Response(200, json={"task_id": "exp-001", "status": "processing", "format": body.get("format", "xlsx")})

    def _handle_resources(self, path, method, body, request):
        rest = path[len("/api/admin/resources"):] or ""
        parts = rest.strip("/").split("/") if rest else []
        if len(parts) >= 1 and parts[0].isdigit():
            rid = int(parts[0])
            if method == "GET":
                return httpx.Response(200, json={"id": rid, "name": "test-resource", "type": "file"})
            if method in ("PUT", "PATCH"):
                return httpx.Response(200, json={"id": rid, "name": body.get("name", "updated"), "type": "file"})
        if method == "POST":
            return httpx.Response(200, json={"id": 1, "name": body.get("name", ""), "type": body.get("type", "file")})
        if method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        return httpx.Response(404)

    def _route_ai(self, path, method, body, request):
        rest = path[len("/api/ai"):] or ""
        if rest == "/task/diagnose" and method == "POST":
            return httpx.Response(200, json={"diagnosis": "Mock: sensor fault detected", "confidence": 0.85})
        if rest == "/task/discuss" and method == "POST":
            return httpx.Response(200, json={"reply": "Mock: check wiring and reboot", "suggestions": ["check cable", "reboot controller"]})
        if rest == "/task/summarize" and method == "POST":
            return httpx.Response(200, json={"summary": "Mock: issue resolved by replacing sensor module"})
        return httpx.Response(404)

    def _seed_default_resources(self):
        """Seed default resources for data-driven parametrize tests."""
        import time
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

def create_mock_transport():
    backend = MockBackend()
    return httpx.MockTransport(backend.handle)




