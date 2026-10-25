from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import re
import time
from typing import Any
from urllib.parse import quote
import httpx
from app.core.config import settings


class AirwallexError(Exception):
    def __init__(self, category: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.category = category
        self.status_code = status_code


class AirwallexClient:
    """Sandbox-only Airwallex client; financial writes are never retried."""
    def __init__(self, transport: httpx.BaseTransport | None = None):
        if not settings.airwallex_sandbox_url_valid:
            raise AirwallexError("configuration", "Only the documented Airwallex sandbox host is allowed")
        self.base_url = settings.airwallex_base_url.rstrip("/")
        self.client_id = settings.airwallex_client_id
        self.api_key = settings.airwallex_api_key
        self.transport = transport

    def _client(self):
        return httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(8.0, connect=3.0), transport=self.transport)

    def _token(self, client: httpx.Client) -> str:
        if not self.client_id or not self.api_key:
            raise AirwallexError("configuration", "Airwallex sandbox credentials are not configured")
        try:
            headers = {"x-client-id": self.client_id, "x-api-key": self.api_key}
            if settings.airwallex_login_as:
                headers["x-login-as"] = settings.airwallex_login_as
            response = client.post("/api/v1/authentication/login", headers=headers)
        except httpx.TimeoutException as exc:
            raise AirwallexError("timeout", "Airwallex authentication timed out") from exc
        except httpx.RequestError as exc:
            raise AirwallexError("network", "Airwallex authentication could not be reached") from exc
        if response.status_code != 201:
            raise self._error(response)
        try:
            payload = json.loads(response.content, parse_float=Decimal)
        except ValueError as exc:
            raise AirwallexError("invalid_response", "Airwallex authentication response was not valid JSON", response.status_code) from exc
        token = payload.get("token") if isinstance(payload, dict) else None
        expiry = payload.get("expires_at") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token or not isinstance(expiry, str):
            raise AirwallexError("invalid_response", "Airwallex authentication response is missing token or expiry", response.status_code)
        try:
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AirwallexError("invalid_response", "Airwallex authentication expiry is invalid", response.status_code) from exc
        if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
            raise AirwallexError("invalid_response", "Airwallex authentication token is expired or has no timezone", response.status_code)
        return token

    @staticmethod
    def _error(response: httpx.Response) -> AirwallexError:
        if response.status_code == 401: category = "authentication"
        elif response.status_code == 403: category = "permission"
        elif response.status_code == 429: category = "rate_limit"
        elif response.status_code >= 500: category = "external_service"
        else: category = "external_api"
        return AirwallexError(category, f"Airwallex returned HTTP {response.status_code}", response.status_code)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with self._client() as client:
            token = self._token(client)
            response = None
            for attempt in range(3):
                try:
                    response = client.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
                except httpx.TimeoutException as exc:
                    if attempt == 2:
                        raise AirwallexError("timeout", "Airwallex read timed out") from exc
                    time.sleep(0.1 * (attempt + 1))
                    continue
                except httpx.RequestError as exc:
                    if attempt == 2:
                        raise AirwallexError("network", "Airwallex read could not be reached") from exc
                    time.sleep(0.1 * (attempt + 1))
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        retry_after = response.headers.get("Retry-After", "")
                        try:
                            requested_delay = float(retry_after)
                            if not math.isfinite(requested_delay):
                                raise ValueError("Retry-After must be finite")
                            delay = min(max(requested_delay, 0.0), 2.0)
                        except ValueError:
                            delay = 0.1 * (attempt + 1)
                        time.sleep(delay)
                        continue
                break
        if response.status_code != 200:
            raise self._error(response)
        try:
            return json.loads(response.content, parse_float=Decimal)
        except ValueError as exc:
            raise AirwallexError("invalid_response", "Airwallex returned invalid JSON", response.status_code) from exc

    def get_transfer(self, transfer_id: str) -> dict:
        payload = self._get(f"/api/v1/transfers/{quote(transfer_id, safe='')}")
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str) or not isinstance(payload.get("status"), str):
            raise AirwallexError("invalid_response", "Transfer response is missing its identifier or status")
        return payload

    def get_beneficiary(self, beneficiary_id: str) -> dict:
        payload = self._get(f"/api/v1/beneficiaries/{quote(beneficiary_id, safe='')}")
        if not isinstance(payload, dict) or payload.get("id") != beneficiary_id:
            raise AirwallexError("invalid_response", "Beneficiary response does not match the requested identifier")
        return payload

    def list_transfers_by_request_id(self, request_id: str) -> list[dict]:
        # request_id is the documented reconciliation key for an ambiguous create.
        payload = self._get("/api/v1/transfers", {"request_id": request_id, "page_size": 100})
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise AirwallexError("invalid_response", "Transfer list response is missing items")
        if any(not isinstance(item, dict) for item in payload["items"]):
            raise AirwallexError("invalid_response", "Transfer list contains an invalid item")
        return payload["items"]

    @staticmethod
    def _decode_write_response(response: httpx.Response) -> dict:
        if response.status_code not in (200, 201):
            # A 409 may mean the same request is still being processed. A timeout,
            # rate limit, or server error also needs reconciliation before retry.
            if response.status_code in (408, 409, 429) or response.status_code >= 500:
                raise AirwallexError("outcome_unknown", "Airwallex write outcome must be reconciled", response.status_code)
            raise AirwallexClient._error(response)
        try:
            payload = json.loads(response.content, parse_float=Decimal)
        except ValueError as exc:
            raise AirwallexError("outcome_unknown", "Airwallex write returned an unreadable response", response.status_code) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str) or not isinstance(payload.get("status"), str):
            raise AirwallexError("outcome_unknown", "Airwallex write response is missing transfer identity or status", response.status_code)
        return payload

    def create_transfer(self, request: dict[str, Any]) -> dict:
        """Submit exactly one transfer create request; callers must persist first."""
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise AirwallexError("validation", "Transfer creation requires a durable request_id")
        with self._client() as client:
            token = self._token(client)
            try:
                response = client.post("/api/v1/transfers/create", json=request,
                    headers={"Authorization": f"Bearer {token}"})
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                # The request may have reached Airwallex. Never retry this call.
                raise AirwallexError("outcome_unknown", "Transfer create outcome must be reconciled by request_id") from exc
        return self._decode_write_response(response)

    def validate_transfer(self, request: dict[str, Any]) -> dict | str:
        """Call Airwallex's non-mutating transfer validation endpoint once."""
        with self._client() as client:
            token = self._token(client)
            try:
                response = client.post("/api/v1/transfers/validate", json=request,
                    headers={"Authorization": f"Bearer {token}"})
            except httpx.TimeoutException as exc:
                raise AirwallexError("timeout", "Airwallex transfer validation timed out") from exc
            except httpx.RequestError as exc:
                raise AirwallexError("network", "Airwallex transfer validation could not be reached") from exc
        if response.status_code != 200:
            raise self._error(response)
        try:
            payload = json.loads(response.content, parse_float=Decimal)
        except ValueError as exc:
            raise AirwallexError("invalid_response", "Airwallex validation response was not valid JSON", response.status_code) from exc
        if not isinstance(payload, (dict, str)):
            raise AirwallexError("invalid_response", "Airwallex validation response has an unsupported shape", response.status_code)
        return payload

    def get_balances(self) -> list[dict]:
        payload = self._get("/api/v1/balances/current")
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise AirwallexError("invalid_response", "Balance response must be an array")
        for item in payload:
            amount = item.get("available_amount")
            if not isinstance(item.get("currency"), str) or not isinstance(amount, (Decimal, int)) or isinstance(amount, bool):
                raise AirwallexError("invalid_response", "Balance response is missing a valid currency or numeric available amount")
            if not Decimal(amount).is_finite():
                raise AirwallexError("invalid_response", "Balance response has a non-finite available amount")
        return payload


def safe_transfer_summary(payload: dict) -> dict:
    """Retain decision evidence while omitting free-text failure messages and beneficiary PII."""
    allowed = ("id", "status", "transfer_amount", "transfer_currency", "source_currency",
               "transfer_method", "beneficiary_id", "amount_payer_pays", "fee_amount",
               "fee_currency", "created_at", "updated_at", "request_id")
    def json_safe(value):
        if isinstance(value, Decimal): return str(value)
        if isinstance(value, dict): return {str(k): json_safe(v) for k, v in value.items()}
        if isinstance(value, list): return [json_safe(v) for v in value]
        return value
    result = {key: json_safe(payload[key]) for key in allowed if key in payload}
    failure = payload.get("failure")
    if isinstance(failure, dict):
        safe_failure = {}
        code = failure.get("code")
        if isinstance(code, (str, int)) and not isinstance(code, bool):
            code = str(code)
            if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", code):
                safe_failure["code"] = code
        details = failure.get("details")
        if isinstance(details, dict):
            reason_codes = details.get("reason_codes")
            if isinstance(reason_codes, list):
                safe_codes = []
                for value in reason_codes[:16]:
                    if isinstance(value, (str, int)) and not isinstance(value, bool):
                        value = str(value)
                        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value):
                            safe_codes.append(value)
                if safe_codes:
                    safe_failure["details"] = {"reason_codes": safe_codes}
        if safe_failure: result["failure"] = json_safe(safe_failure)
    return result


def safe_beneficiary_summary(payload: dict) -> dict:
    """Expose only beneficiary identity and payment capability metadata, never bank PII."""
    if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
        raise AirwallexError("invalid_response", "Beneficiary response is missing its identifier")
    beneficiary = payload.get("beneficiary")
    beneficiary = beneficiary if isinstance(beneficiary, dict) else {}
    bank_details = beneficiary.get("bank_details")
    bank_details = bank_details if isinstance(bank_details, dict) else {}
    methods = payload.get("transfer_methods")
    if not isinstance(methods, list) or any(not isinstance(method, str) for method in methods):
        raise AirwallexError("invalid_response", "Beneficiary response is missing valid transfer methods")
    return {key: payload[key] for key in ("id", "nickname", "payer_entity_type") if isinstance(payload.get(key), str)} | {
        "entity_type": beneficiary.get("entity_type") if isinstance(beneficiary.get("entity_type"), str) else None,
        "beneficiary_type": beneficiary.get("type") if isinstance(beneficiary.get("type"), str) else None,
        "bank_country_code": bank_details.get("bank_country_code") if isinstance(bank_details.get("bank_country_code"), str) else None,
        "account_currency": bank_details.get("account_currency") if isinstance(bank_details.get("account_currency"), str) else None,
        "transfer_methods": methods,
    }


def balance_snapshot(payload: list[dict]) -> tuple[datetime, list[dict]]:
    captured = datetime.now(timezone.utc)
    return captured, [{"account_type": item.get("account_type"), "currency": item["currency"],
                       "available_amount": str(item["available_amount"]), "captured_at": captured.isoformat()}
                      for item in payload]
