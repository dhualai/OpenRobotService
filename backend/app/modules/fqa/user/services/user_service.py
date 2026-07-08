import httpx
from typing import Optional, Dict, Any
from app.core.config import settings


class UserService:
    @classmethod
    def _get_aas_base_url(cls) -> str:
        return settings.USER_CENTER_BASE_URL
    
    @classmethod
    async def login(cls, username: str, password: str) -> Dict[str, Any]:
        aas_url = cls._get_aas_base_url()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{aas_url}/auth/login",
                json={"username": username, "password": password},
                headers={"accept": "application/json"},
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
    
    @classmethod
    async def refresh_token(cls, refresh_token: str) -> Dict[str, Any]:
        aas_url = cls._get_aas_base_url()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{aas_url}/auth/refresh",
                json={"refresh_token": refresh_token},
                headers={"accept": "application/json"},
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
    
    @classmethod
    async def get_user_info(cls, access_token: str) -> dict:
        aas_url = cls._get_aas_base_url()
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{aas_url}/auth/me",
                headers={
                    "accept": "application/json",
                    "Authorization": f"Bearer {access_token}"
                },
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()