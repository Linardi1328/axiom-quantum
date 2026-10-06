from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from spy_market_agent.execution import (
    PaperExecutionApprovalError,
    PaperExecutionInputError,
    PaperOrderApproval,
    PaperOrderReceipt,
    compute_instruction_fingerprint,
    require_execution_id,
    validate_matching_approval,
)
from spy_market_agent.execution.models import (
    BrokerAccountSnapshot,
    BrokerOpenOrderSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from unit.phase8_helpers import (
    APPROVED_AT,
    CREATED_AT,
    EXECUTION_SESSION,
    EXPIRES_AT,
    SIGNAL_SESSION,
    make_approval,
    make_costs,
    make_instruction,
    make_proposed_order,
    make_risk_decision,
)


@pytest.mark.parametrize(
    "value",
    ["A1", "run.01_test-02", "a" * 128],
)
def test_execution_identifier_contract_accepts_url_safe_ids(value: str) -> None:
    assert require_execution_id(value, field_name="signal_id") == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        " leading",
        "trailing ",
        "internal space",
        "bad/slash",
        r"bad\slash",
        "bad%percent",
        "bad?query",
        "bad#hash",
        "a" * 129,
    ],
)
def test_execution_identifier_contract_rejects_unsafe_ids(value: str) -> None:
    with pytest.raises(PaperExecutionInputError):
        require_execution_id(value, field_name="signal_id")


def test_instruction_fingerprint_is_deterministic_and_safety_sensitive() -> None:
    instruction = make_instruction()
    same = make_instruction()
    changed_order = make_proposed_order(quantity=11)
    changed = make_instruction(order=changed_order)

    assert same.instruction_fingerprint == instruction.instruction_fingerprint
    assert changed.instruction_fingerprint != instruction.instruction_fingerprint
    assert (
        compute_instruction_fingerprint(
            schema_version=instruction.schema_version,
            signal_id=instruction.signal_id,
            client_order_id=instruction.client_order_id,
            proposed_order=instruction.proposed_order,
            original_risk_decision=instruction.original_risk_decision,
            cost_assumptions=make_costs(),
            created_at_utc=instruction.created_at_utc,
            expires_at_utc=instruction.expires_at_utc,
        )
        == instruction.instruction_fingerprint
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "side",
        "quantity",
        "symbol",
        "signal_session",
        "execution_session",
        "reference_open",
    ],
)
def test_instruction_fingerprint_changes_for_safety_relevant_order_fields(
    field_name: str,
) -> None:
    original = make_instruction()
    base_order = original.proposed_order
    if field_name == "side":
        changed_order = replace(
            base_order,
            side="sell",
            target_position=0,
            estimated_cash_change=Decimal("1000"),
            current_shares=base_order.quantity,
        )
    elif field_name == "quantity":
        changed_order = replace(
            base_order,
            quantity=11,
            estimated_cash_change=Decimal("-1100"),
        )
    elif field_name == "symbol":
        changed_order = replace(base_order, symbol="QQQ")
    elif field_name == "signal_session":
        changed_order = replace(base_order, signal_session=SIGNAL_SESSION - timedelta(days=1))
    elif field_name == "execution_session":
        changed_order = replace(
            base_order,
            execution_session=EXECUTION_SESSION + timedelta(days=1),
        )
    elif field_name == "reference_open":
        changed_order = replace(
            base_order,
            reference_open=Decimal("101"),
            estimated_execution_price=Decimal("101"),
            estimated_cash_change=Decimal("-1010"),
        )
    else:
        raise AssertionError(f"unexpected field {field_name}")

    changed_fingerprint = compute_instruction_fingerprint(
        schema_version=original.schema_version,
        signal_id=original.signal_id,
        client_order_id=original.client_order_id,
        proposed_order=changed_order,
        original_risk_decision=make_risk_decision(changed_order),
        cost_assumptions=make_costs(),
        created_at_utc=original.created_at_utc,
        expires_at_utc=original.expires_at_utc,
    )

    assert changed_fingerprint != original.instruction_fingerprint


def test_approval_must_match_instruction_and_execution_time() -> None:
    instruction = make_instruction()
    approval = make_approval(instruction)

    validate_matching_approval(
        instruction,
        approval,
        execution_time_utc=APPROVED_AT + timedelta(minutes=1),
    )

    mismatched = PaperOrderApproval(
        approval_id="approval-other",
        signal_id=instruction.signal_id,
        client_order_id=instruction.client_order_id,
        instruction_fingerprint="b" * 64,
        approved=True,
        approved_at_utc=APPROVED_AT,
        approved_by="human-review",
        approval_reason="explicit phase 8 paper approval",
    )
    with pytest.raises(PaperExecutionApprovalError):
        validate_matching_approval(
            instruction,
            mismatched,
            execution_time_utc=APPROVED_AT + timedelta(minutes=1),
        )
    with pytest.raises(PaperExecutionApprovalError):
        validate_matching_approval(
            instruction,
            approval,
            execution_time_utc=CREATED_AT + timedelta(minutes=1),
        )
    with pytest.raises(PaperExecutionApprovalError):
        validate_matching_approval(
            instruction,
            approval,
            execution_time_utc=EXPIRES_AT + timedelta(seconds=1),
        )


def test_approval_requires_true_and_noncredential_reason() -> None:
    instruction = make_instruction()

    with pytest.raises(PaperExecutionInputError):
        make_approval(instruction, approved=False)
    with pytest.raises(PaperExecutionInputError):
        PaperOrderApproval(
            approval_id="approval-secret",
            signal_id=instruction.signal_id,
            client_order_id=instruction.client_order_id,
            instruction_fingerprint=instruction.instruction_fingerprint,
            approved=True,
            approved_at_utc=APPROVED_AT,
            approved_by="human-review",
            approval_reason="api_key=unsafe",
        )


def test_execution_snapshots_reject_nonfinite_and_fractional_quantities() -> None:
    with pytest.raises(PaperExecutionInputError):
        BrokerAccountSnapshot(
            status="active",
            currency="USD",
            cash=Decimal("Infinity"),
            equity=Decimal("100"),
            buying_power=Decimal("100"),
            trading_blocked=False,
            account_blocked=False,
            trade_suspended_by_user=False,
            account_id_fingerprint="a" * 64,
            retrieved_at_utc=datetime(2025, 1, 3, tzinfo=UTC),
        )
    with pytest.raises(PaperExecutionInputError):
        BrokerPositionSnapshot(
            symbol="SPY",
            side="long",
            quantity=Decimal("1.5"),
            available_quantity=Decimal("1.5"),
        )


def test_position_available_quantity_relationship_is_validated() -> None:
    full = BrokerPositionSnapshot(
        symbol="SPY",
        side="long",
        quantity=Decimal("10"),
        available_quantity=Decimal("10"),
    )
    partial = BrokerPositionSnapshot(
        symbol="SPY",
        side="long",
        quantity=Decimal("10"),
        available_quantity=Decimal("4"),
    )
    zero = BrokerPositionSnapshot(
        symbol="SPY",
        side="long",
        quantity=Decimal("0"),
        available_quantity=Decimal("0"),
    )

    assert full.available_quantity == Decimal("10")
    assert partial.available_quantity == Decimal("4")
    assert zero.quantity == Decimal("0")
    with pytest.raises(PaperExecutionInputError):
        BrokerPositionSnapshot(
            symbol="SPY",
            side="long",
            quantity=Decimal("10"),
            available_quantity=Decimal("11"),
        )
    with pytest.raises(PaperExecutionInputError):
        BrokerPositionSnapshot(
            symbol="SPY",
            side="long",
            quantity=Decimal("0"),
            available_quantity=Decimal("1"),
        )


def test_open_order_filled_quantity_relationship_is_validated() -> None:
    for filled_quantity in (Decimal("0"), Decimal("4"), Decimal("10")):
        order = BrokerOpenOrderSnapshot(
            broker_order_id="broker-open-1",
            client_order_id="client-order-1",
            symbol="SPY",
            side="buy",
            quantity=Decimal("10"),
            filled_quantity=filled_quantity,
            status="accepted",
            submitted_at_utc=datetime(2025, 1, 3, 15, 30, tzinfo=UTC),
        )
        assert order.filled_quantity == filled_quantity

    with pytest.raises(PaperExecutionInputError):
        BrokerOpenOrderSnapshot(
            broker_order_id="broker-open-1",
            client_order_id="client-order-1",
            symbol="SPY",
            side="buy",
            quantity=Decimal("10"),
            filled_quantity=Decimal("11"),
            status="accepted",
            submitted_at_utc=datetime(2025, 1, 3, 15, 30, tzinfo=UTC),
        )


def test_broker_order_snapshot_quantity_relationship_is_validated() -> None:
    instruction = make_instruction()
    snapshot = BrokerOrderSnapshot(
        broker_order_id="broker-1",
        client_order_id=instruction.client_order_id,
        broker_order_status="accepted",
        symbol="SPY",
        side="buy",
        submitted_quantity=10,
        filled_quantity=10,
        order_type="market",
        time_in_force="day",
        extended_hours=False,
        submitted_at_utc=datetime(2025, 1, 3, 15, 30, tzinfo=UTC),
        broker_response_at_utc=None,
        sanitized_request_id=None,
        execution_environment="alpaca_paper",
    )

    assert snapshot.filled_quantity == 10
    with pytest.raises(PaperExecutionInputError):
        replace(snapshot, filled_quantity=11)


def test_receipt_preserves_supported_market_day_order_contract() -> None:
    instruction = make_instruction()
    receipt = PaperOrderReceipt(
        signal_id=instruction.signal_id,
        client_order_id=instruction.client_order_id,
        instruction_fingerprint=instruction.instruction_fingerprint,
        broker_order_id="broker-1",
        broker_order_status="accepted",
        symbol="SPY",
        side="buy",
        submitted_quantity=10,
        filled_quantity=0,
        order_type="market",
        time_in_force="day",
        extended_hours=False,
        submitted_at_utc=datetime(2025, 1, 3, 15, 30, tzinfo=UTC),
        broker_response_at_utc=None,
        sanitized_request_id=None,
        execution_environment="alpaca_paper",
        reconciliation_status="broker_verified",
    )

    assert receipt.symbol == "SPY"
    with pytest.raises(PaperExecutionInputError):
        replace(receipt, extended_hours=True)
    with pytest.raises(PaperExecutionInputError):
        replace(receipt, filled_quantity=11)
