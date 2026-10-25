from decimal import Decimal
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from app.core.config import settings
from app.services.airwallex.client import AirwallexClient, AirwallexError, safe_beneficiary_summary, safe_transfer_summary

def test_read_adapter_authenticates_and_validates_transfer_id(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/authentication/login"):
            assert request.headers["x-client-id"] == "client"
            assert request.headers["x-api-key"] == "secret"
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
            return httpx.Response(201, json={"token":"access", "expires_at":expiry})
        assert request.headers["authorization"] == "Bearer access"
        return httpx.Response(200, content=b'{"id":"tr_1","status":"FAILED","transfer_amount":4500.25}')
    transfer = AirwallexClient(transport=httpx.MockTransport(handler)).get_transfer("tr_1")
    assert transfer["transfer_amount"] == Decimal("4500.25")
    assert len(calls) == 2
    assert safe_transfer_summary(transfer)["transfer_amount"] == "4500.25"

def test_transfer_summary_omits_unbounded_failure_message_and_keeps_safe_code():
    summary = safe_transfer_summary({"id": "tr_1", "status": "CANCELLED", "failure": {
        "code": "90101", "message": "Rejected account 123456789; contact alice@example.com"}})
    assert summary["failure"] == {"code": "90101"}
    assert "123456789" not in str(summary)
    assert "alice@example.com" not in str(summary)

def test_transfer_summary_filters_unstructured_failure_fields():
    summary = safe_transfer_summary({"id": "tr_1", "status": "FAILED", "failure": {
        "code": "account 123456789", "details": {"reason_codes": ["insufficient_funds", "email alice@example.com"]}}})
    assert summary["failure"] == {"details": {"reason_codes": ["insufficient_funds"]}}
    assert "123456789" not in str(summary)
    assert "alice@example.com" not in str(summary)

def test_scoped_key_can_select_target_account_at_authentication(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    monkeypatch.setattr(settings, "airwallex_login_as", "account_123")
    def handler(request):
        if request.url.path.endswith("/authentication/login"):
            assert request.headers["x-login-as"] == "account_123"
            expiry=(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat()
            return httpx.Response(201,json={"token":"access","expires_at":expiry})
        return httpx.Response(200,json={"id":"ben_1", "transfer_methods":["LOCAL"]})
    result=AirwallexClient(transport=httpx.MockTransport(handler)).get_beneficiary("ben_1")
    assert result["id"] == "ben_1"

def test_beneficiary_retrieval_encodes_identifier_and_summary_omits_bank_pii(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    paths=[]
    def handler(request):
        if request.url.path.endswith("/authentication/login"):
            expiry=(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat()
            return httpx.Response(201,json={"token":"access","expires_at":expiry})
        paths.append(request.url.raw_path.decode())
        return httpx.Response(200,json={"id":"beneficiary/1","nickname":"Supplier",
            "transfer_methods":["LOCAL"],"beneficiary":{"entity_type":"COMPANY","type":"BANK_ACCOUNT",
                "bank_details":{"bank_country_code":"US","account_currency":"USD",
                    "account_name":"Secret Name","account_number":"123456789"}}})
    beneficiary=AirwallexClient(transport=httpx.MockTransport(handler)).get_beneficiary("beneficiary/1")
    summary=safe_beneficiary_summary(beneficiary)
    assert paths == ["/api/v1/beneficiaries/beneficiary%2F1"]
    assert summary["bank_country_code"] == "US"
    assert "account_name" not in summary and "account_number" not in summary

def test_read_adapter_reports_missing_credentials(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "")
    monkeypatch.setattr(settings, "airwallex_api_key", "")
    with pytest.raises(AirwallexError) as error:
        AirwallexClient().get_transfer("tr_1")
    assert error.value.category == "configuration"

def test_read_adapter_rejects_non_sandbox_base_url(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_base_url", "https://api.airwallex.com")
    with pytest.raises(AirwallexError, match="sandbox host") as error:
        AirwallexClient()
    assert error.value.category == "configuration"

def test_balance_adapter_validates_shape_and_preserves_decimal(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    def handler(request):
        if request.url.path.endswith("/authentication/login"):
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
            return httpx.Response(201, json={"token":"access", "expires_at":expiry})
        return httpx.Response(200, content=b'[{"currency":"USD","available_amount":12000.01,"account_type":"cash"}]')
    balances = AirwallexClient(transport=httpx.MockTransport(handler)).get_balances()
    assert balances[0]["available_amount"] == Decimal("12000.01")

def test_balance_adapter_rejects_nonnumeric_amount(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    def handler(request):
        if request.url.path.endswith("/authentication/login"):
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
            return httpx.Response(201, json={"token":"access", "expires_at":expiry})
        return httpx.Response(200, json=[{"currency":"USD", "available_amount":"12000.00"}])
    with pytest.raises(AirwallexError, match="numeric available amount"):
        AirwallexClient(transport=httpx.MockTransport(handler)).get_balances()

def test_authentication_rejects_expired_or_timezone_free_tokens(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    expiries = [
        (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(tzinfo=None).isoformat(),
    ]
    for expiry in expiries:
        def handler(request):
            return httpx.Response(201, json={"token": "expired", "expires_at": expiry})
        with pytest.raises(AirwallexError, match="expired or has no timezone") as error:
            AirwallexClient(transport=httpx.MockTransport(handler)).get_transfer("tr_1")
        assert error.value.category == "invalid_response"

def test_authentication_failure_is_classified_without_read_retry(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"message": "unauthorized"})
    with pytest.raises(AirwallexError) as error:
        AirwallexClient(transport=httpx.MockTransport(handler)).get_balances()
    assert error.value.category == "authentication"
    assert error.value.status_code == 401
    assert calls == 1

def test_read_retries_rate_limit_then_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    monkeypatch.setattr("app.services.airwallex.client.time.sleep", lambda _seconds: None)
    reads = 0
    def handler(request):
        nonlocal reads
        if request.url.path.endswith("/authentication/login"):
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
            return httpx.Response(201, json={"token": "access", "expires_at": expiry})
        reads += 1
        if reads == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=[])
    assert AirwallexClient(transport=httpx.MockTransport(handler)).get_balances() == []
    assert reads == 2

def test_read_rate_limit_exhaustion_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    monkeypatch.setattr("app.services.airwallex.client.time.sleep", lambda _seconds: None)
    reads = 0
    def handler(request):
        nonlocal reads
        if request.url.path.endswith("/authentication/login"):
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
            return httpx.Response(201, json={"token": "access", "expires_at": expiry})
        reads += 1
        return httpx.Response(429)
    with pytest.raises(AirwallexError) as error:
        AirwallexClient(transport=httpx.MockTransport(handler)).get_balances()
    assert error.value.category == "rate_limit"
    assert error.value.status_code == 429
    assert reads == 3

def test_invalid_retry_after_values_use_bounded_fallback(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    for retry_after in ("nan", "inf"):
        sleeps = []
        monkeypatch.setattr("app.services.airwallex.client.time.sleep", sleeps.append)
        reads = 0
        def handler(request):
            nonlocal reads
            if request.url.path.endswith("/authentication/login"):
                expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
                return httpx.Response(201, json={"token": "access", "expires_at": expiry})
            reads += 1
            if reads == 1:
                return httpx.Response(429, headers={"Retry-After": retry_after})
            return httpx.Response(200, json=[])
        assert AirwallexClient(transport=httpx.MockTransport(handler)).get_balances() == []
        assert reads == 2
        assert sleeps == [0.1]

def test_transfer_create_posts_once_and_validates_returned_transfer(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/authentication/login"):
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
            return httpx.Response(201, json={"token": "access", "expires_at": expiry})
        assert request.url.path == "/api/v1/transfers/create"
        assert request.headers["authorization"] == "Bearer access"
        assert request.read() == b'{"request_id":"recovery-123","reason":"supplier_payment","reference":"INV-42"}'
        return httpx.Response(201, json={"id": "tr_new", "status": "SCHEDULED"})
    response = AirwallexClient(transport=httpx.MockTransport(handler)).create_transfer({
        "request_id": "recovery-123", "reason": "supplier_payment", "reference": "INV-42"})
    assert response == {"id": "tr_new", "status": "SCHEDULED"}
    assert len(requests) == 2

def test_transfer_create_timeout_is_outcome_unknown_and_never_retried(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    creates = 0
    def handler(request):
        nonlocal creates
        if request.url.path.endswith("/authentication/login"):
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
            return httpx.Response(201, json={"token": "access", "expires_at": expiry})
        creates += 1
        raise httpx.ReadTimeout("response lost", request=request)
    with pytest.raises(AirwallexError) as error:
        AirwallexClient(transport=httpx.MockTransport(handler)).create_transfer({"request_id": "recovery-123"})
    assert error.value.category == "outcome_unknown"
    assert "reconciled" in str(error.value)
    assert creates == 1

def test_transfer_create_requires_stable_request_id(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    with pytest.raises(AirwallexError) as error:
        AirwallexClient().create_transfer({"reason": "supplier_payment"})
    assert error.value.category == "validation"

def test_transfer_create_server_error_is_outcome_unknown(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    def handler(request):
        if request.url.path.endswith("/authentication/login"):
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
            return httpx.Response(201, json={"token": "access", "expires_at": expiry})
        return httpx.Response(500, json={"message": "unknown"})
    with pytest.raises(AirwallexError) as error:
        AirwallexClient(transport=httpx.MockTransport(handler)).create_transfer({"request_id": "recovery-123"})
    assert error.value.category == "outcome_unknown"

def test_transfer_list_uses_documented_request_id_filter(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    def handler(request):
        if request.url.path.endswith("/authentication/login"):
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
            return httpx.Response(201, json={"token": "access", "expires_at": expiry})
        assert request.url.path == "/api/v1/transfers"
        assert request.url.params["request_id"] == "recovery-123"
        return httpx.Response(200, json={"items": [{"id": "tr_new", "status": "SCHEDULED", "request_id": "recovery-123"}]})
    items = AirwallexClient(transport=httpx.MockTransport(handler)).list_transfers_by_request_id("recovery-123")
    assert items[0]["id"] == "tr_new"
