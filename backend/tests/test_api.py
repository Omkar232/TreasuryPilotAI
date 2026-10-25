import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import httpx
import json
from app.core.config import settings
settings.app_api_token = "test-secret"
settings.app_admin_token = "test-admin-secret"
from app.main import app
from app.api import routes
from app.db.database import SessionLocal
from app.models import Approval, PaymentIncident

async def api_scenario(callback):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await callback(client)

def test_health_public_and_financial_routes_protected():
    async def scenario(client):
        assert (await client.get("/api/health")).status_code == 200
        readiness = await client.get("/api/health/ready", headers={"X-Request-ID":"test.trace-1"})
        assert readiness.status_code == 200
        assert readiness.headers["X-Request-ID"] == "test.trace-1"
        assert (await client.get("/api/incidents")).status_code == 401
        assert (await client.get("/api/incidents", headers={"Authorization":"Bearer test-secret"})).status_code == 200
    asyncio.run(api_scenario(scenario))

def test_approval_center_lists_only_persisted_action_binding_without_secrets():
    async def scenario(client):
        assert (await client.get("/api/approvals")).status_code == 401
        headers = {"Authorization":"Bearer test-secret"}
        created = await client.post("/api/incidents", headers=headers, json={
            "supplier":"Approval Supplier", "original_transfer_id":"tr_approval_view",
            "amount":"125.50", "currency":"USD", "supplier_claim":"Payment is late"})
        assert created.status_code == 201
        incident_id = created.json()["id"]
        with SessionLocal() as db:
            db.add(Approval(id="approval-view-test", incident_id=incident_id,
                operation_id=None, action_hash="a" * 64, counterparty="Approval Supplier",
                amount=Decimal("125.50"), currency="USD", evidence_version="b" * 64,
                status="PENDING", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                requested_by="operator", policy_version=3))
            db.commit()
        response = await client.get("/api/approvals", headers=headers)
        assert response.status_code == 200, response.text
        item = next(row for row in response.json() if row["id"] == "approval-view-test")
        assert item["action"] == "REPLACE_TRANSFER"
        assert item["incident_id"] == incident_id
        assert item["original_transfer_id"] == "tr_approval_view"
        assert item["amount"] == "125.5000"
        assert item["currency"] == "USD"
        assert item["status"] == "PENDING"
        assert item["requested_by"] == "operator"
        assert item["policy_version"] == 3
        assert "action_hash" not in item and "evidence_version" not in item
        assert (await client.get("/api/approvals?limit=0", headers=headers)).status_code == 422
    asyncio.run(api_scenario(scenario))

def test_request_body_over_one_megabyte_is_rejected_before_route_handling():
    async def scenario(client):
        response = await client.post("/api/incidents", content=b"x" * (1_048_576 + 1),
            headers={"Authorization": "Bearer test-secret", "Content-Type": "application/json"})
        assert response.status_code == 413
        assert response.json() == {"detail": "Request body exceeds the 1 MiB limit"}
    asyncio.run(api_scenario(scenario))

def test_incident_recommendation_has_deterministic_fallback_and_no_authority():
    async def scenario(client):
        headers={"Authorization":"Bearer test-secret"}
        body={"supplier":"Demo supplier", "original_transfer_id":"tr_demo",
            "amount":"25.00", "currency":"USD", "supplier_claim":"Payment has not arrived"}
        created=await client.post("/api/incidents",headers=headers,json=body)
        assert created.status_code == 201
        incident_id=created.json()["id"]
        response=await client.post(f"/api/incidents/{incident_id}/recommendation",headers=headers)
        assert response.status_code == 200, response.text
        recommendation=response.json()
        assert recommendation["ai_interpretation"] is None
        assert recommendation["ai_status"] == "deterministic_fallback"
        assert recommendation["proposed_action"] == "INVESTIGATE"
        assert recommendation["financial_action_authorized"] is False
        assert recommendation["unverified_claims"] == ["Payment has not arrived"]
        audit=await client.get("/api/audit",headers=headers)
        assert any(event["event_type"] == "INCIDENT_RECOMMENDATION_GENERATED" for event in audit.json())
    asyncio.run(api_scenario(scenario))

def test_refresh_beneficiary_uses_verified_transfer_and_omits_sensitive_details(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    class FakeAirwallex:
        def get_transfer(self, transfer_id):
            return {"id": transfer_id, "status": "FAILED", "transfer_amount": "25.00",
                "transfer_currency": "USD", "source_currency": "USD", "transfer_method": "LOCAL",
                "beneficiary_id": "ben_demo", "failure": {"code":"rejected"}}
        def get_beneficiary(self, beneficiary_id):
            return {"id": beneficiary_id, "nickname":"Demo Supplier", "transfer_methods":["LOCAL"],
                "beneficiary":{"entity_type":"COMPANY", "type":"BANK_ACCOUNT",
                    "bank_details":{"bank_country_code":"US", "account_currency":"USD",
                        "account_name":"Sensitive Name", "account_number":"000000"}}}
    monkeypatch.setattr(routes, "AirwallexClient", FakeAirwallex)
    async def scenario(client):
        headers={"Authorization":"Bearer test-secret"}
        created=await client.post("/api/incidents",headers=headers,json={"supplier":"Demo",
            "original_transfer_id":"tr_beneficiary", "amount":"25.00", "currency":"USD",
            "supplier_claim":"Payment failed"})
        incident_id=created.json()["id"]
        before=await client.post(f"/api/incidents/{incident_id}/refresh-beneficiary",headers=headers)
        assert before.status_code == 409
        investigated=await client.post(f"/api/incidents/{incident_id}/investigate",headers=headers)
        assert investigated.status_code == 200
        refreshed=await client.post(f"/api/incidents/{incident_id}/refresh-beneficiary",headers=headers)
        assert refreshed.status_code == 200, refreshed.text
        data=refreshed.json()
        assert data["beneficiary"]["bank_country_code"] == "US"
        assert "account_number" not in data["beneficiary"]
        audit=await client.get("/api/audit",headers=headers)
        assert any(event["event_type"] == "BENEFICIARY_RETRIEVED" for event in audit.json())
    asyncio.run(api_scenario(scenario))

def test_synthetic_plan_is_protected_and_explicitly_labeled():
    async def scenario(client):
        assert (await client.get("/api/demo/treasury-plan")).status_code == 401
        response = await client.get("/api/demo/treasury-plan", headers={"Authorization":"Bearer test-secret"})
        assert response.status_code == 200
        plan = response.json()
        assert plan["simulated"] is True
        assert plan["source"] == "synthetic_demo_fixture"
        assert plan["available"] == "12000.00"
        assert [(entry["priority"], entry["action"]) for entry in plan["entries"]] == [
            ("CRITICAL", "PAY"), ("HIGH", "PAY"), ("LOW", "DEFER")]
        assert plan["remaining_balance"] == "4500.00"
        assert "synthetic" in plan["fee_assumption"]
        assert all("synthetic" in assumption for assumption in plan["assumptions"] if "amounts" in assumption or "fee value" in assumption)
    asyncio.run(api_scenario(scenario))

def test_synthetic_recovery_walkthrough_is_complete_and_never_calls_airwallex():
    async def scenario(client):
        assert (await client.get("/api/demo/recovery-scenario")).status_code == 401
        response = await client.get("/api/demo/recovery-scenario", headers={"Authorization":"Bearer test-secret"})
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["simulated"] is True
        assert run["source"] == "synthetic_demo_fixture"
        assert run["external_api_called"] is False
        assert run["persisted_to_incidents"] is False
        assert "No Airwallex API was called" in run["disclosure"]
        steps = {step["name"]: step for step in run["steps"]}
        assert steps["investigate_original"]["status"] == "PROCESSING"
        assert steps["investigate_original"]["decision"] == "BLOCK_REPLACEMENT"
        assert steps["confirm_failure"]["source"] == "synthetic_demo_fixture"
        assert steps["evaluate_treasury"]["status"] == "ELIGIBLE_IN_SIMULATION"
        assert [(item["priority"], item["action"]) for item in steps["evaluate_treasury"]["plan"]] == [
            ("CRITICAL", "PAY"), ("HIGH", "PAY"), ("LOW", "DEFER")]
        approval = steps["approve_exact_action"]
        execution = steps["create_replacement"]
        assert approval["status"] == "SIMULATED_APPROVED"
        assert approval["bound_action"]["amount"] == "4500.00"
        serialized_action = json.dumps(approval["bound_action"], sort_keys=True, separators=(",", ":"))
        assert approval["action_hash"] == sha256(serialized_action.encode()).hexdigest()
        changed_action = {**approval["bound_action"], "amount": "4500.01"}
        changed_serialized_action = json.dumps(changed_action, sort_keys=True, separators=(",", ":"))
        assert approval["action_hash"] != sha256(changed_serialized_action.encode()).hexdigest()
        assert approval["action_hash"] == execution["action_hash"]
        assert execution["status"] == "SIMULATED_CREATED"
        assert execution["external_api_called"] is False
        assert steps["verify_replacement"]["status"] == "SIMULATED_VERIFIED"
        assert steps["verify_replacement"]["external_api_called"] is False
    asyncio.run(api_scenario(scenario))

def test_obligations_are_persisted_as_operator_entered_and_audited():
    async def scenario(client):
        headers = {"Authorization":"Bearer test-secret"}
        assert (await client.get("/api/treasury/obligations")).status_code == 401
        created = await client.post("/api/treasury/obligations", headers=headers, json={
            "supplier":"Freight Partner", "amount":"1500.25", "currency":"USD",
            "due_at":"2026-11-05T12:00:00Z", "priority":"HIGH"})
        assert created.status_code == 201, created.text
        record = created.json()
        assert record["source"] == "operator_entered"
        assert record["simulated"] is False
        assert record["amount"] == "1500.2500"
        listed = await client.get("/api/treasury/obligations", headers=headers)
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == record["id"]
        assert listed.json()[0]["due_at"].endswith(("+00:00", "Z"))
        audit = await client.get("/api/audit", headers=headers)
        assert any(event["event_type"] == "OBLIGATION_RECORDED" for event in audit.json())
        invalid = await client.post("/api/treasury/obligations", headers=headers, json={
            "supplier":"Bad Currency", "amount":"10", "currency":"usd", "priority":"URGENT"})
        assert invalid.status_code == 422
    asyncio.run(api_scenario(scenario))

def test_live_treasury_plan_uses_balance_policy_and_holds_when_fee_unknown(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    from decimal import Decimal
    balance_items=[{"currency":"USD", "available_amount":Decimal("12000.00"), "account_type":"cash"}]
    class FakeAirwallex:
        def get_balances(self):
            return balance_items
    monkeypatch.setattr(routes, "AirwallexClient", FakeAirwallex)
    async def scenario(client):
        # App lifespan creates the SQLite test schema in development; exercise
        # the stricter production role checks for requests after startup.
        monkeypatch.setattr(settings, "app_env", "production")
        headers={"Authorization":"Bearer test-secret"}
        admin_headers={"Authorization":"Bearer test-admin-secret"}
        assert (await client.get("/api/treasury/policies")).status_code == 401
        assert (await client.put("/api/treasury/policies/USD",json={"reserve_floor":"0","approval_threshold":"0"})).status_code == 401
        assert (await client.post("/api/treasury/plans")).status_code == 401
        denied=await client.put("/api/treasury/policies/USD",headers=headers,json={
            "reserve_floor":"0", "approval_threshold":"0"})
        assert denied.status_code == 403
        invalid_policy=await client.put("/api/treasury/policies/USD",headers=headers,json={
            "reserve_floor":"0", "approval_threshold":"0", "priority_order":["CRITICAL","LOW"]})
        assert invalid_policy.status_code == 403
        invalid_admin_policy=await client.put("/api/treasury/policies/USD",headers=admin_headers,json={
            "reserve_floor":"0", "approval_threshold":"0", "priority_order":["CRITICAL","LOW"]})
        assert invalid_admin_policy.status_code == 422
        policy=await client.put("/api/treasury/policies/USD",headers=admin_headers,json={
            "reserve_floor":"3000.00", "approval_threshold":"1000.00",
            "priority_order":["CRITICAL","HIGH","NORMAL","LOW"]})
        assert policy.status_code == 200, policy.text
        assert policy.json()["version"] == 1
        obligation=await client.post("/api/treasury/obligations",headers=headers,json={
            "supplier":"Critical supplier", "amount":"4500.00", "currency":"USD", "priority":"CRITICAL"})
        assert obligation.status_code == 201
        euro=await client.post("/api/treasury/obligations",headers=headers,json={
            "supplier":"Euro supplier", "amount":"100.00", "currency":"EUR", "priority":"NORMAL"})
        assert euro.status_code == 201
        planned=await client.post("/api/treasury/plans",headers=headers)
        assert planned.status_code == 200, planned.text
        body=planned.json()
        assert body["source"] == "airwallex"
        assert body["status"] == "BLOCKED"
        usd_plan=next(item for item in body["currency_plans"] if item["currency"]=="USD")
        eur_plan=next(item for item in body["currency_plans"] if item["currency"]=="EUR")
        entry=usd_plan["entries"][0]
        assert entry["action"] == "HOLD"
        assert entry["projected_balance_after_payment"] is None
        assert entry["approval_required"] is True
        assert "Transfer fee is unknown" in entry["reasons"]
        assert eur_plan["status"] == "NO_BALANCE"
        assert eur_plan["available_amount"] is None
        loaded=await client.get(f"/api/treasury/plans/{body['id']}",headers=headers)
        assert loaded.status_code == 200
        assert loaded.json()["currency_plans"] == body["currency_plans"]
        changed=await client.put("/api/treasury/policies/USD",headers=admin_headers,json={
            "reserve_floor":"3500.00", "approval_threshold":"2000.00",
            "priority_order":["LOW","NORMAL","HIGH","CRITICAL"]})
        assert changed.status_code == 200
        assert changed.json()["version"] == 2
        admin_incident=await client.post("/api/incidents",headers=admin_headers,json={
            "supplier":"Admin-created incident", "original_transfer_id":"tr_admin_1",
            "amount":"10.00", "currency":"USD", "supplier_claim":"Payment status unknown"})
        assert admin_incident.status_code == 201
        assert next(item for item in loaded.json()["currency_plans"] if item["currency"]=="USD")["policy_version"] == 1
        balance_items.append({"currency":"USD", "available_amount":Decimal("800.00"), "account_type":"credit"})
        ambiguous=(await client.post("/api/treasury/plans",headers=headers)).json()
        ambiguous_usd=next(item for item in ambiguous["currency_plans"] if item["currency"]=="USD")
        assert ambiguous_usd["status"] == "AMBIGUOUS_ACCOUNT"
        assert ambiguous_usd["entries"] == []
        audit=await client.get("/api/audit",headers=headers)
        assert sum(event["event_type"] == "TREASURY_POLICY_UPDATED" for event in audit.json()) == 2
        assert all(event["actor"] == "admin" for event in audit.json() if event["event_type"] == "TREASURY_POLICY_UPDATED")
        assert any(event["event_type"] == "INCIDENT_CREATED" and event["actor"] == "admin"
            and event["incident_id"] == admin_incident.json()["id"] for event in audit.json())
        assert sum(event["event_type"] == "TREASURY_PLAN_CALCULATED" for event in audit.json()) == 2
    asyncio.run(api_scenario(scenario))

def test_live_plan_stays_unavailable_without_sandbox_credentials(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "")
    monkeypatch.setattr(settings, "airwallex_api_key", "")
    async def scenario(client):
        response=await client.post("/api/treasury/plans",headers={"Authorization":"Bearer test-secret"})
        assert response.status_code == 503
        assert "credentials" in response.json()["detail"]
    asyncio.run(api_scenario(scenario))

def test_incident_claim_remains_unverified_and_recovery_blocked():
    async def scenario(client):
        headers = {"Authorization":"Bearer test-secret"}
        created = await client.post("/api/incidents", headers=headers, json={
            "supplier":"Example Supplier", "original_transfer_id":"tr_demo_001",
            "amount":"4500.00", "currency":"USD", "supplier_claim":"We did not receive this payment"
        })
        assert created.status_code == 201
        incident_id = created.json()["id"]
        investigation = await client.post(f"/api/incidents/{incident_id}/investigate", headers=headers)
        assert investigation.json()["state"] == "OUTCOME_UNKNOWN"
        evaluation = await client.post(f"/api/incidents/{incident_id}/evaluate-recovery", headers=headers)
        assert evaluation.status_code == 200
        assert evaluation.json()["eligible"] is False
        assert evaluation.json()["proposed_action"] == "INVESTIGATE"
        audit = await client.get("/api/audit", headers=headers)
        recovery_event = next(event for event in audit.json() if event["event_type"] == "RECOVERY_EVALUATED")
        assert recovery_event["actor"] == "operator"
        assert recovery_event["incident_id"] == incident_id
    asyncio.run(api_scenario(scenario))

def test_investigation_uses_and_preserves_verified_airwallex_status(monkeypatch):
    monkeypatch.setattr(settings, "airwallex_client_id", "client")
    monkeypatch.setattr(settings, "airwallex_api_key", "secret")
    class FakeAirwallex:
        def get_transfer(self, transfer_id):
            return {"id":transfer_id, "status":"PROCESSING", "transfer_amount":"4500.00", "transfer_currency":"USD"}
    monkeypatch.setattr(routes, "AirwallexClient", FakeAirwallex)
    async def scenario(client):
        headers = {"Authorization":"Bearer test-secret"}
        created = await client.post("/api/incidents", headers=headers, json={
            "supplier":"Verified Supplier", "original_transfer_id":"tr_verified_1",
            "amount":"4500.00", "currency":"USD", "supplier_claim":"Payment not received"
        })
        incident_id = created.json()["id"]
        result = await client.post(f"/api/incidents/{incident_id}/investigate", headers=headers)
        assert result.json()["state"] == "PROCESSING"
        assert result.json()["verified_facts"][0]["status"] == "PROCESSING"
        record = (await client.get(f"/api/incidents/{incident_id}", headers=headers)).json()
        assert record["source"] == "airwallex"
        assert record["external_status"] == "PROCESSING"
        assert record["evidence"][0]["source"] == "airwallex"
        evaluated = await client.post(f"/api/incidents/{incident_id}/evaluate-recovery", headers=headers)
        assert evaluated.json()["eligible"] is False
    asyncio.run(api_scenario(scenario))
