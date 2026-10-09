"""Test utilities for capturing HTTP request/response details in Allure reports."""
import json
import allure
from fastapi.testclient import TestClient


class LoggingTestClient:
    """Wraps TestClient and attaches HTTP request/response details to the current test."""
    def __init__(self, app, **kwargs):
        self._client = TestClient(app, **kwargs)

    def __enter__(self):
        self._client.__enter__()
        return self

    def __exit__(self, *args):
        return self._client.__exit__(*args)

    def request(self, method, url, **kwargs):
        self._attach_request(method, url, dict(kwargs))
        response = self._client.request(method, url, **kwargs)
        self._attach_response(response)
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def _attach_request(self, method, url, kwargs):
        details = {"method": method, "url": url}
        if kwargs.get("json"):
            details["body"] = kwargs["json"]
        if kwargs.get("params"):
            details["params"] = dict(kwargs["params"])
        if kwargs.get("headers"):
            details["headers"] = dict(kwargs["headers"])
        text = json.dumps(details, indent=2, ensure_ascii=False)
        allure.attach(text, name="Request: " + method + " " + url, attachment_type=allure.attachment_type.JSON)

    def _attach_response(self, response):
        try:
            body = response.json()
        except Exception:
            body = response.text[:2000]
        details = {"status_code": response.status_code, "body": body}
        text = json.dumps(details, indent=2, ensure_ascii=False)
        allure.attach(text, name="Response: " + str(response.status_code), attachment_type=allure.attachment_type.JSON)
