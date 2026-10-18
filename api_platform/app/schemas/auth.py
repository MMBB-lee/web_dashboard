"""认证相关请求 / 响应模型（03-接口总览 §3）。"""

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    role: str  # viewer | admin


class LoginData(BaseModel):
    user: UserOut
    csrf_token: str
    expires_at: datetime


class LogoutData(BaseModel):
    logged_out: bool = True
