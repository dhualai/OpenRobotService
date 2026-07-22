import uvicorn
from app.core.config import settings

if __name__ == "__main__":
    port = 8400
    print(f"服务启动...")
    print(f"服务地址: http://0.0.0.0:{port}")
    print(f"API文档: http://0.0.0.0:{port}/docs")
    print(f"默认管理员账号: admin / usp2026@EP")
    
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=True
    )