from __future__ import annotations

from datetime import timedelta

import pytest

from spy_market_agent.execution import PaperExecutionApprovalError
from spy_market_agent.execution.approvals import validate_matching_approval
from unit.phase8_helpers import BROKER_TIME, make_approval, make_instruction


def test_approval_is_valid_one_microsecond_before_instruction_expiration() -> None:
    instruction = make_instruction(expires_at=BROKER_TIME)
    approval = make_approval(instruction)

    validate_matching_approval(
        instruction,
        approval,
        execution_time_utc=BROKER_TIME - timedelta(microseconds=1),
    )


@pytest.mark.parametrize(
    "offset",
    [
        timedelta(),
        timedelta(microseconds=1),
    ],
)
def test_approval_is_invalid_at_and_after_instruction_expiration(
    offset: timedelta,
) -> None:
    instruction = make_instruction(expires_at=BROKER_TIME)
    approval = make_approval(instruction)

    with pytest.raises(PaperExecutionApprovalError) as exc_info:
        validate_matching_approval(
            instruction,
            approval,
            execution_time_utc=BROKER_TIME + offset,
        )

    assert exc_info.value.code == "approval_expired"
