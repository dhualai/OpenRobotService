@echo off
docker compose -f automation\docker\docker-compose.test.yml up -d
echo Waiting for services...
timeout /t 10 /nobreak >nul
