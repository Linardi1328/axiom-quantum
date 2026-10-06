from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from spy_market_agent.api import create_app
from spy_market_agent.config import Settings
from spy_market_agent.execution import (
    PAPER_ATTEMPT_ACCEPTED,
    PAPER_ATTEMPT_BLOCKED,
    SQLitePaperExecutionRepository,
)
from spy_market_agent.persistence import initialize_database
from unit.phase8_helpers import CLIENT_ORDER_ID, make_approval, make_instruction, make_receipt


def test_paper_trading_status_route_is_read_only_and_uses_local_ledger(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    client = TestClient(create_app(database_path=str(database_path), settings=Settings()))

    response = client.get("/api/v1/paper-trading/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["kill_switch_engaged"] is True
    assert payload["configuration_kill_switch_engaged"] is True
    assert payload["durable_kill_switch_engaged"] is True
    assert payload["effective_kill_switch_engaged"] is True
    assert payload["paper_execution_enabled"] is False
    assert payload["dry_run"] is True
    assert payload["alpaca_api_key_present"] is False
    assert payload["alpaca_secret_key_present"] is False
    assert "not investment advice" in payload["limitation"].lower()


def test_paper_trading_status_effective_kill_switch_is_configuration_or_durable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)
    repository.set_paper_execution_kill_switch(
        engaged=False,
        reason="explicit_api_test",
        updated_at_utc=make_instruction().created_at_utc,
        confirmation="DISENGAGE_PAPER_EXECUTION_KILL_SWITCH",
    )
    client = TestClient(
        create_app(
            database_path=str(database_path),
            settings=Settings(paper_execution_kill_switch=True),
        )
    )

    payload = client.get("/api/v1/paper-trading/status").json()

    assert payload["configuration_kill_switch_engaged"] is True
    assert payload["durable_kill_switch_engaged"] is False
    assert payload["effective_kill_switch_engaged"] is True
    assert payload["kill_switch_engaged"] is True


def test_paper_order_list_and_detail_routes_return_persisted_attempts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    repository.record_receipt(
        make_receipt(instruction),
        status=PAPER_ATTEMPT_ACCEPTED,
        account_id_fingerprint="a" * 64,
        now_utc=instruction.created_at_utc,
        event_type="broker_order_accepted",
    )
    client = TestClient(create_app(database_path=str(database_path)))

    listing = client.get("/api/v1/paper-orders", params={"limit": 10, "offset": 0})
    detail = client.get(f"/api/v1/paper-orders/{CLIENT_ORDER_ID}")

    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["client_order_id"] == CLIENT_ORDER_ID
    assert detail.status_code == 200
    assert detail.json()["attempt_status"] == PAPER_ATTEMPT_ACCEPTED
    assert detail.json()["account_id_fingerprint"] == "a" * 64


def test_blocked_local_request_failure_is_visible_as_blocked_not_unknown(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    repository.mark_failure(
        client_order_id=instruction.client_order_id,
        signal_id=instruction.signal_id,
        status=PAPER_ATTEMPT_BLOCKED,
        failure_code="broker_request_construction_failed",
        now_utc=instruction.created_at_utc,
        event_type="broker_request_construction_failed",
    )
    client = TestClient(create_app(database_path=str(database_path)))

    detail = client.get(f"/api/v1/paper-orders/{CLIENT_ORDER_ID}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["attempt_status"] == PAPER_ATTEMPT_BLOCKED
    assert payload["attempt_status"] != "submission_unknown"
    assert payload["failure_code"] == "broker_request_construction_failed"
    assert "raw" not in detail.text.lower()


def test_paper_order_unknown_and_invalid_ids_return_safe_client_errors(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    client = TestClient(create_app(database_path=str(database_path)))

    missing = client.get("/api/v1/paper-orders/missing-order")
    invalid = client.get("/api/v1/paper-orders/bad%25percent")

    assert missing.status_code == 404
    assert missing.json()["code"] == "paper_order_not_found"
    assert invalid.status_code == 422
    assert invalid.status_code != 503


def test_corrupted_paper_execution_record_returns_sanitized_503(tmp_path: Path) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE paper_execution_attempts SET quantity = 'not-an-int' WHERE client_order_id = ?",
            (CLIENT_ORDER_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    response = TestClient(create_app(database_path=str(database_path))).get(
        f"/api/v1/paper-orders/{CLIENT_ORDER_ID}"
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "invalid_paper_execution_attempt",
        "message": "Persisted paper-execution data is unavailable or invalid.",
    }
    assert "not-an-int" not in response.text
    assert str(database_path) not in response.text


def test_api_factory_health_and_get_requests_do_not_create_database_or_broker_client(
    tmp_path: Path,
) -> None:
    sys.modules.pop("spy_market_agent.execution.alpaca_paper", None)
    database_path = tmp_path / "missing.sqlite3"
    app = create_app(database_path=str(database_path))
    client = TestClient(app)

    health = client.get("/health")
    unavailable = client.get("/api/v1/paper-trading/status")

    assert health.status_code == 200
    assert unavailable.status_code == 503
    assert not database_path.exists()
    assert "spy_market_agent.execution.alpaca_paper" not in sys.modules


def test_phase8_api_route_inventory_has_no_state_changing_application_routes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    app = create_app(database_path=str(database_path))

    state_changing = {"POST", "PUT", "PATCH", "DELETE"}
    for route in app.routes:
        methods: set[str] = set(getattr(route, "methods", set()) or set())
        assert not state_changing.intersection(methods)
