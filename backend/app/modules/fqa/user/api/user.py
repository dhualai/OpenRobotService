from fastapi import APIRouter, HTTPException
from typing import Optional
from app.modules.fqa.user.schemas.user import UserLoginRequest, UserLoginResponse, UserInfoResponse, RefreshToken
from app.modules.fqa.user.services.user_service import UserService

router = APIRouter(prefix="/user", tags=["user"])


@router.post("/login", response_model=UserLoginResponse)
async def login(request: UserLoginRequest):
    try:
        result = await UserService.login(request.username, request.password)
        return UserLoginResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.post("/refresh", response_model=UserLoginResponse)
async def refresh(request: RefreshToken):
    try:
        result = await UserService.refresh_token(request.refresh_token)
        return UserLoginResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/me", response_model=UserInfoResponse)
async def get_current_user_info(authorization: Optional[str] = None):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    try:
        token = authorization.replace("Bearer ", "")
        result = await UserService.get_user_info(token)
        return UserInfoResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))