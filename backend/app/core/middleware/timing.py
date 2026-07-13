from fastapi import Request, Response, HTTPException
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime
import json
from app.services.logging import get_logger
from app.modules.admin.api.auth import get_current_active_user_from_token
from starlette.status import HTTP_403_FORBIDDEN, HTTP_401_UNAUTHORIZED
from app.core.security import decode_token
from jose import JWTError

logger = get_logger("AAS.middleware")
operation_logger = get_logger("AAS.operation")

class RequestResponseLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = datetime.now()
        
        client_ip = request.client.host if request.client else "unknown"
        
        path = request.url.path
        method = request.method
        query_params = dict(request.query_params)
        
        try:
            if method in ["POST", "PUT", "PATCH"]:
                request_body = await request.body()
                if request_body:
                    try:
                        request_body_json = await request.json()
                        request_body_str = json.dumps(request_body_json, ensure_ascii=False)
                    except json.JSONDecodeError:
                        request_body_str = request_body.decode("utf-8", errors="replace")[:1000]
                else:
                    request_body_str = ""
            else:
                request_body_str = ""
        except Exception as e:
            request_body_str = f"读取请求体失败: {str(e)}"
        
        response = await call_next(request)
        
        status_code = response.status_code
        
        response_body = b""
        async for chunk in response.body_iterator:
            response_body += chunk
        
        try:
            response_body_json = json.loads(response_body.decode("utf-8"))
            response_body_str = json.dumps(response_body_json, ensure_ascii=False)
        except (json.JSONDecodeError, UnicodeDecodeError):
            response_body_str = response_body.decode("utf-8", errors="replace")[:1000]
        
        processing_time = (datetime.now() - start_time).total_seconds() * 1000
        
        operator = "匿名用户"
        if 'authorization' in request.headers:
            try:
                token = request.headers['authorization'].replace('Bearer ', '')
                payload = decode_token(token)
                if payload:
                    operator = payload.get('sub', '匿名用户')
            except:
                pass
        else:
            if path == "/AAS/auth/login" and method == "POST":
                operator = request_body_json.get("username", "匿名用户")
        
        summary = ""
        try:
            if path in request.app.openapi_schema['paths']:
                route = request.app.openapi_schema['paths'][path]
                summary = route[method.lower()].get("summary", f"{method} {path}")
        except Exception:
            summary = f"{method} {path}"
        
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + 'Z',
            "level": "INFO",
            "message": "API请求处理完成",
            "client_ip": client_ip,
            "method": method,
            "path": path,
            "status_code": status_code,
            "processing_time": round(processing_time, 2),
            "response_body": response_body_str if status_code != 200 else "",
            "operator": operator,
            "summary": summary,
            "tag": "Operation"
        }
        
        operation_logger.info(json.dumps(log_data, ensure_ascii=False))
        
        log_message = (f"API请求处理完成 - IP: {client_ip} - {method} {path} - 状态码: {status_code} - 耗时: {round(processing_time, 2)}ms"
                       f" - 查询参数: {query_params}"
                       f" - 请求体: {request_body_str}"
                       f" - 响应体: {response_body_str}")
        logger.info(log_message)
        
        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type
        )


class PermissionMiddleware(BaseHTTPMiddleware):
    SKIP_ROUTES = {
        "exact": [
            "/AAS/health",
            "/docs",
            "/redoc",
            "/AAS/redoc",
            "/AAS/docs",
            "/api/openapi.json",
        ],
        "prefix": [
            "/AAS/auth/login",
            "/AAS/auth/register",
            "/AAS/auth/refresh",
            "/static/",
        ]
    }
    
    def is_skip_route(self, path: str) -> bool:
        if not path.startswith("/AAS"):
            return True
        
        if path in self.SKIP_ROUTES.get("exact", []):
            return True
        
        for prefix in self.SKIP_ROUTES.get("prefix", []):
            if path.startswith(prefix):
                return True
        
        return False
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method
        
        if method == "OPTIONS":
            return await call_next(request)
        
        if self.is_skip_route(path):
            return await call_next(request)
        
        try:
            token = request.headers.get("Authorization", "")
            if not token:
                return JSONResponse(
                    status_code=HTTP_401_UNAUTHORIZED,
                    content={"detail": "未提供认证令牌"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            if not token.startswith("Bearer "):
                return JSONResponse(
                    status_code=HTTP_401_UNAUTHORIZED,
                    content={"detail": "无效的令牌格式，应为Bearer token"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            token = token[7:]
            
            try:
                payload = decode_token(token)
                if payload is None:
                    return JSONResponse(
                        status_code=HTTP_401_UNAUTHORIZED,
                        content={"detail": "无效的令牌签名或令牌已过期"},
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                
                if payload.get("type") == "refresh":
                    return JSONResponse(
                        status_code=HTTP_401_UNAUTHORIZED,
                        content={"detail": "请使用访问令牌，而非刷新令牌"},
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                
                request.state.token_payload = payload
                
                username = payload.get("sub")
                if not username:
                    return JSONResponse(
                        status_code=HTTP_401_UNAUTHORIZED,
                        content={"detail": "无效的令牌内容"},
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                
            except JWTError:
                return JSONResponse(
                    status_code=HTTP_401_UNAUTHORIZED,
                    content={"detail": "无效的令牌签名或令牌已过期"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            return await call_next(request)
        except Exception as e:
            logger.error(f"JWT验证失败: {str(e)}")
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": "身份验证失败"},
                headers={"WWW-Authenticate": "Bearer"},
            )