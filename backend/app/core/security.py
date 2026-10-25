import hmac
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.core.config import settings

bearer = HTTPBearer(auto_error=False)


def require_operator(credentials: HTTPAuthorizationCredentials | None = Security(bearer)) -> str:
    if not settings.app_api_token and not settings.app_admin_token:
        raise HTTPException(status_code=503, detail="Operator authentication is not configured")
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if settings.app_api_token and hmac.compare_digest(credentials.credentials, settings.app_api_token):
        return "operator"
    if settings.app_admin_token and hmac.compare_digest(credentials.credentials, settings.app_admin_token):
        return "admin"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


def require_policy_admin(credentials: HTTPAuthorizationCredentials | None = Security(bearer)) -> str:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if settings.app_admin_token and hmac.compare_digest(credentials.credentials, settings.app_admin_token):
        return "admin"
    if (settings.app_env != "production" and settings.app_api_token
            and hmac.compare_digest(credentials.credentials, settings.app_api_token)):
        # Local single-operator development shortcut. Production always requires a distinct admin token.
        return "admin"
    if settings.app_api_token and hmac.compare_digest(credentials.credentials, settings.app_api_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Policy administrator authorization required")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
