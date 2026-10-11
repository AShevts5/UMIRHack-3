from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.common import TwoFactorMethod

# в коде из письма ровно 6 цифр
Code = Annotated[str, Field(pattern=r"^\d{6}$", examples=["123456"])]

Password = Annotated[str, Field(min_length=8, max_length=128, examples=["StrongPass123"])]



class RegisterRequest(BaseModel):
    email: EmailStr = Field(examples=["student@example.com"])
    password: Password
    full_name: str | None = Field(default=None, max_length=200, examples=["Иванова Анна"])


class VerifyEmailRequest(BaseModel):

    email: EmailStr
    code: Code


class LoginRequest(BaseModel):
    email: EmailStr = Field(examples=["student@example.com"])
    password: str = Field(examples=["StrongPass123"])


class TokenResponse(BaseModel):

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(examples=[900], description="Через сколько секунд истекает access-токен")


class LoginResponse(BaseModel):

    requires_2fa: bool
    two_factor_method: TwoFactorMethod | None = None
    pre_auth_token: str | None = Field(default=None, description="Короткоживущий токен только для шага 2FA")
    tokens: TokenResponse | None = None


# Двухфакторка


class TwoFactorVerifyRequest(BaseModel):

    pre_auth_token: str
    code: Code


class EmailCodeResendRequest(BaseModel):
    # повторная отправка кода

    pre_auth_token: str


class TotpSetupResponse(BaseModel):

    secret: str = Field(description="Секрет для ручного ввода в приложение")
    otpauth_url: str = Field(examples=["otpauth://totp/Онлайн-деканат:student@example.com?secret=..."])
    qr_png_base64: str = Field(description="QR-код в виде PNG, закодированного в base64")


class TotpEnableRequest(BaseModel):
    # Включение TOTP: пользователь вводит первый код из приложения

    code: Code


# сброс пароля


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    code: Code
    new_password: Password


# данные пользователя


class UserOut(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str | None = None
    is_email_verified: bool
    two_factor_enabled: bool
    two_factor_method: TwoFactorMethod | None = None
    university_id: str | None = Field(default=None, examples=["rinh"])
    created_at: datetime