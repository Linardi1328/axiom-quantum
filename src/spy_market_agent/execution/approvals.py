from __future__ import annotations

from datetime import datetime

from spy_market_agent.execution.errors import PaperExecutionApprovalError
from spy_market_agent.execution.models import (
    PaperOrderApproval,
    PaperOrderInstruction,
    utc_datetime,
)


def validate_matching_approval(
    instruction: PaperOrderInstruction,
    approval: PaperOrderApproval,
    *,
    execution_time_utc: datetime,
) -> None:
    now = utc_datetime(execution_time_utc, field_name="execution_time_utc")
    if approval.signal_id != instruction.signal_id:
        raise PaperExecutionApprovalError(
            "approval_signal_mismatch", "approval does not match instruction."
        )
    if approval.client_order_id != instruction.client_order_id:
        raise PaperExecutionApprovalError(
            "approval_client_order_mismatch", "approval does not match instruction."
        )
    if approval.instruction_fingerprint != instruction.instruction_fingerprint:
        raise PaperExecutionApprovalError(
            "approval_fingerprint_mismatch", "approval does not match instruction."
        )
    if approval.approved_at_utc <= instruction.created_at_utc:
        raise PaperExecutionApprovalError(
            "approval_before_instruction", "approval must be after instruction creation."
        )
    if approval.approved_at_utc > now:
        raise PaperExecutionApprovalError(
            "approval_from_future", "approval timestamp is in the future."
        )
    if now >= instruction.expires_at_utc:
        raise PaperExecutionApprovalError(
            "approval_expired", "approval expired with the instruction."
        )


__all__ = ["validate_matching_approval"]
