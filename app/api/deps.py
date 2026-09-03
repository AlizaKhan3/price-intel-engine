from __future__ import annotations

"""Auth dependencies: tenant API key for customers, admin key for onboarding."""
import secrets

from fastapi import Depends, Header, HTTPException, status

from app.config import get_settings
from app.services.tenants import find_tenant_by_api_key

settings = get_settings()


async def get_current_tenant(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif x_api_key:
        token = x_api_key.strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Send `Authorization: Bearer <key>` or `X-API-Key`.",
        )

    tenant = await find_tenant_by_api_key(token)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
    return tenant


async def require_admin(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> None:
    expected = settings.ADMIN_API_KEY
    if not x_admin_key or not expected or not secrets.compare_digest(x_admin_key, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin key.",
        )


TenantDep = Depends(get_current_tenant)
AdminDep = Depends(require_admin)
