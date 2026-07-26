@echo off
REM Start test dependencies with healthcheck wait
docker compose -f automation\docker\docker-compose.test.yml up -d --wait
echo All services ready

