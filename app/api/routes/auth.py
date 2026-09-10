"""Account registration and cookie-session endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.dependencies import get_auth_service, get_current_account
from app.api.schemas import AccountLoginRequest, AccountRegisterRequest, AccountResponse
from app.auth.models import Account
from app.auth.service import AuthService
from app.config import get_boolean

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
Auth = Annotated[AuthService, Depends(get_auth_service)]
CurrentAccount = Annotated[Account, Depends(get_current_account)]
COOKIE_NAME = "minialpha_session"


def _response(account: Account) -> AccountResponse:
    return AccountResponse(
        user_id=account.user_id,
        email=account.email,
        display_name=account.display_name,
        created_at=account.created_at,
    )


def _set_session(response: Response, token: str, service: AuthService) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=service.session_ttl_seconds,
        httponly=True,
        secure=get_boolean("AUTH_COOKIE_SECURE", False),
        samesite="lax",
        path="/",
    )


@router.post(
    "/register",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    request: AccountRegisterRequest,
    response: Response,
    service: Auth,
) -> AccountResponse:
    """Create an account and start its first session."""
    account, token = await service.register(
        email=request.email,
        display_name=request.display_name,
        password=request.password,
    )
    _set_session(response, token, service)
    return _response(account)


@router.post("/login", response_model=AccountResponse)
async def login(
    request: AccountLoginRequest,
    response: Response,
    service: Auth,
) -> AccountResponse:
    """Verify credentials and issue a fresh opaque session."""
    account, token = await service.login(email=request.email, password=request.password)
    _set_session(response, token, service)
    return _response(account)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, service: Auth) -> Response:
    """Revoke the current session and clear its browser cookie."""
    await service.logout(request.cookies.get(COOKIE_NAME))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(COOKIE_NAME, path="/", samesite="lax")
    return response


@router.get("/me", response_model=AccountResponse)
async def current_account(account: CurrentAccount) -> AccountResponse:
    """Return the identity associated with the current session."""
    return _response(account)
