path = r"D:\WorkCode\OpenRobotService\automation\mocks\backend_mock.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

# 1. In _route_admin, add daily-reports/export/resources before the 404 return
old_admin_404 = '        return httpx.Response(404, json={"detail": "Admin route not found"})'
new_admin_routes = (
    '        # --- Admin extensions ---\n'
    '        if rest == "/daily-reports" and method == "POST":\n'
    '            rtype = body.get("type", "daily")\n'
    '            return httpx.Response(200, json={"id": 1, "type": rtype, "status": "generated"})\n'
    '        if rest == "/export" and method == "POST":\n'
    '            return httpx.Response(200, json={"task_id": "exp-001", "status": "processing"})\n'
    '        if rest.startswith("/resources"):\n'
    '            parts = rest.strip("/").split("/")\n'
    '            if len(parts) >= 2 and parts[1].isdigit():\n'
    '                rid = int(parts[1])\n'
    '                if method == "GET":\n'
    '                    return httpx.Response(200, json={"id": rid, "name": "test-resource", "type": "file"})\n'
    '                if method in ("PUT", "PATCH"):\n'
    '                    return httpx.Response(200, json={"id": rid, "name": body.get("name", "updated"), "type": "file"})\n'
    '            if len(parts) == 1 and method == "POST":\n'
    '                nid = len(self._tasks) + 1\n'
    '                return httpx.Response(200, json={"id": nid, "name": body.get("name", ""), "type": body.get("type", "file")})\n'
    '            if len(parts) == 1 and method == "GET":\n'
    '                return httpx.Response(200, json={"items": [], "total": 0})\n'
    '        ' + old_admin_404
)
content = content.replace(old_admin_404, new_admin_routes)

# 2. In handle, add AI routing before the main 404
old_handle_404 = '        return httpx.Response(404, json={"detail": "Not found"})'
new_handle = (
    '        if path.startswith("/api/ai"):\n'
    '            return self._route_ai(path, method, body, request)\n'
    '        ' + old_handle_404
)
content = content.replace(old_handle_404, new_handle, 1)  # Only replace first occurrence

# 3. Add _route_ai method before create_mock_transport
old_mock_transport = 'def create_mock_transport():'
new_ai_method = (
    '    def _route_ai(self, path, method, body, request):\n'
    '        rest = path[len("/api/ai"):] or ""\n'
    '        if rest == "/task/diagnose" and method == "POST":\n'
    '            return httpx.Response(200, json={"diagnosis": "Mock diagnosis: sensor fault detected", "confidence": 0.85})\n'
    '        if rest == "/task/discuss" and method == "POST":\n'
    '            return httpx.Response(200, json={"reply": "Mock discussion: check sensor wiring and reboot", "suggestions": ["check cable", "reboot"]})\n'
    '        if rest == "/task/summarize" and method == "POST":\n'
    '            return httpx.Response(200, json={"summary": "Mock summary: issue resolved by replacing sensor"})\n'
    '        return httpx.Response(404)\n'
    '\n'
    '\n'
    + old_mock_transport
)
content = content.replace(old_mock_transport, new_ai_method)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Mock updated OK")
