## mocks/ — Mock 服务模块

本目录提供外部依赖的 Mock 服务，使测试不依赖真实环境。
- wechat_server.py：微信回调 Mock（XML 加解密/签名校验）
- llm_server.py：DeepSeek API Mock（三种模式）
- qdrant_server.py：内存向量库

当前实际可用的是 `backend_mock.py`（httpx.MockTransport，供 API Mock 用例使用）。
`wechat_server.py` / `llm_server.py` / `qdrant_server.py` 目前仅为占位，待建设。
