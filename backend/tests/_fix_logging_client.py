import pathlib
p = pathlib.Path(r"D:\WorkCode\OpenRobotService\backend\tests\tasks\test_standard_task_status_transition_api.py")
text = p.read_text("utf-8")
# Add import
text = text.replace(
    "from fastapi.testclient import TestClient",
    "from fastapi.testclient import TestClient\nfrom tests.test_utils import LoggingTestClient",
)
# Change client fixture
text = text.replace(
    "with TestClient(app) as test_client:",
    "with LoggingTestClient(app) as test_client:",
)
p.write_text(text, "utf-8")
print("Updated OK")
