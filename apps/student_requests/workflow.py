"""The request state machine (ARCHITECTURE.md §5.4, D8). Statuses and transitions are code, not data.

Each transition names WHO may perform it; the service additionally requires the object policy
(own request, or a reviewer/approver whose scope includes the request).
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.student_requests.models import RequestStatus as S

OWNER = "owner"  # the submitting student
REVIEWER = "reviewer"  # review_requests holder in scope
APPROVER = "approver"  # holder of the category's approval capability (never the submitter)
SYSTEM = "system"  # automatic (owner reply, scheduled job)


@dataclass(frozen=True)
class Transition:
    source: str
    target: str
    actor: str
    note_required: bool = False
    needs_approval_category: bool | None = None  # True: only approval categories; False: only non-approval


TRANSITIONS = [
    Transition(S.SUBMITTED, S.CANCELLED, OWNER),
    Transition(S.NEEDS_INFORMATION, S.CANCELLED, OWNER),
    Transition(S.NEEDS_INFORMATION, S.UNDER_REVIEW, SYSTEM),
    Transition(S.SUBMITTED, S.UNDER_REVIEW, REVIEWER),
    Transition(S.UNDER_REVIEW, S.NEEDS_INFORMATION, REVIEWER, note_required=True),
    Transition(S.UNDER_REVIEW, S.RESOLVED, REVIEWER, note_required=True, needs_approval_category=False),
    Transition(S.UNDER_REVIEW, S.APPROVED, APPROVER, needs_approval_category=True),
    Transition(S.UNDER_REVIEW, S.REJECTED, APPROVER, note_required=True, needs_approval_category=True),
    Transition(S.RESOLVED, S.UNDER_REVIEW, REVIEWER),
    Transition(S.SUBMITTED, S.CLOSED, REVIEWER, note_required=True),
    Transition(S.UNDER_REVIEW, S.CLOSED, REVIEWER, note_required=True),
    Transition(S.NEEDS_INFORMATION, S.CLOSED, REVIEWER, note_required=True),
    Transition(S.RESOLVED, S.CLOSED, REVIEWER),
    Transition(S.APPROVED, S.CLOSED, REVIEWER),
    Transition(S.REJECTED, S.CLOSED, REVIEWER),
]

TERMINAL = {S.CLOSED, S.CANCELLED}


def find(source: str, target: str) -> Transition | None:
    for transition in TRANSITIONS:
        if transition.source == source and transition.target == target:
            return transition
    return None


def targets_from(source: str, actor_kind: str, *, requires_approval: bool) -> list[str]:
    result = []
    for t in TRANSITIONS:
        if t.source != source or t.actor != actor_kind:
            continue
        if t.needs_approval_category is not None and t.needs_approval_category != requires_approval:
            continue
        result.append(t.target)
    return result
