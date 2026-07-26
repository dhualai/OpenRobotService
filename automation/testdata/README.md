# fixtures/ - 全局共享 Fixture 和数据
职责：提供全局 pytest Fixture 和静态测试数据。
conftest.py 定义 db_session/redis_client/test_user/auth_token 等 Fixture。
data/ 存 YAML/JSON 静态数据，factories/ 存数据工厂（Factory Boy）。
