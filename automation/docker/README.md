# docker/ — 测试环境容器编排

| 服务 | 镜像 | 端口 |
|------|------|------|
| mysql-test | mysql:8.0 | 3306 |
| redis-test | redis:7-alpine | 6379 |
| qdrant-test | qdrant/qdrant:v1.10 | 6333/6334 |

## 使用方式

docker compose -f automation/docker/docker-compose.test.yml up -d
