## mocks/ — Mock 服务模块

本目录提供外部依赖的 Mock 服务，使测试不依赖真实环境。
- wechat_server.py：微信回调 Mock（XML 加解密/签名校验）
- llm_server.py：DeepSeek API Mock（三种模式）
- qdrant_server.py：内存向量库

这些 Mock 通过 conftest.py 的 Fixture 管理生命周期。
