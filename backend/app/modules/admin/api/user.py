from fastapi import APIRouter, Header, HTTPException
from typing import Optional
from app.modules.call.schemas_user.user import UserLoginRequest, UserLoginResponse, UserInfoResponse, RefreshToken
from app.modules.call.services_user.user_service import AASClientError, UserService

router = APIRouter(prefix="/user", tags=["user"])


@router.post("/login", response_model=UserLoginResponse)
async def login(request: UserLoginRequest):
    try:
        result = await UserService.login(request.username, request.password)
        return UserLoginResponse(**result)
    except AASClientError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/refresh", response_model=UserLoginResponse)
async def refresh(request: RefreshToken):
    try:
        result = await UserService.refresh_token(request.refresh_token)
        return UserLoginResponse(**result)
    except AASClientError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/me", response_model=UserInfoResponse)
async def get_current_user_info(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    try:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Invalid authorization header")

        result = await UserService.get_user_info(token)
        return UserInfoResponse(**result)
    except AASClientError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
