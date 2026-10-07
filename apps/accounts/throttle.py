"""Login, MFA and password-reset throttling (ARCHITECTURE.md §4.1, D7, D17).

The throttle *subject* is an HMAC of the normalised identifier, never the account: unknown
identifiers follow exactly the same rules, so throttling is not an existence or linking oracle.
Failures only are counted. All checks fail closed (``LimiterUnavailable`` -> HTTP 503).

Rules for login:
  ip            : 100 failures / 15 min -> refused for that IP, except requests with a valid device cookie
  subject + ip  : 5 failures -> back-off 1, 2, 4 ... 30 min for that pair only
  subject (all) : 30 failures / hour -> devices WITHOUT a valid cookie get one attempt per 60 s
  device cookie : attempts with a valid cookie use their own bucket (5 failures -> back-off for that cookie)
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.core import ratelimit
from apps.core.crypto import keyed_digest

IP_LIMIT, IP_WINDOW = 100, 15 * 60
PAIR_THRESHOLD, PAIR_WINDOW = 5, 24 * 60 * 60
SUBJECT_LIMIT, SUBJECT_WINDOW, SLOW_INTERVAL = 30, 60 * 60, 60
DEVICE_THRESHOLD = 5


def subject_for(identifier: str) -> str:
    return keyed_digest((identifier or "").strip().lower(), purpose="throttle-subject")[:32]


@dataclass
class Decision:
    allowed: bool
    retry_after: int = 0


class LoginThrottle:
    def __init__(self, identifier: str, ip: str | None, device_id: str | None):
        self.subject = subject_for(identifier)
        self.ip = ip or "unknown"
        self.device_id = device_id  # trusted-device pk when a valid cookie for this account is presented

    def check(self) -> Decision:
        if self.device_id:
            wait = ratelimit.locked_for(f"login:dev:{self.device_id}")
            return Decision(wait == 0, wait)
        if ratelimit.count(f"login:ip:{self.ip}") >= IP_LIMIT:
            return Decision(False, IP_WINDOW)
        wait = ratelimit.locked_for(f"login:pair:{self.subject}:{self.ip}")
        if wait:
            return Decision(False, wait)
        if ratelimit.count(f"login:subj:{self.subject}") >= SUBJECT_LIMIT:
            if not ratelimit.take_slot(f"login:slow:{self.subject}", SLOW_INTERVAL):
                return Decision(False, SLOW_INTERVAL)
        return Decision(True)

    def failure(self) -> None:
        ratelimit.increment(f"login:subj:{self.subject}", SUBJECT_WINDOW)
        if self.device_id:
            failures = ratelimit.increment(f"login:devfail:{self.device_id}", PAIR_WINDOW)
            wait = ratelimit.backoff_seconds(failures, threshold=DEVICE_THRESHOLD)
            if wait:
                ratelimit.lock(f"login:dev:{self.device_id}", wait)
            return
        ratelimit.increment(f"login:ip:{self.ip}", IP_WINDOW)
        failures = ratelimit.increment(f"login:pairfail:{self.subject}:{self.ip}", PAIR_WINDOW)
        wait = ratelimit.backoff_seconds(failures, threshold=PAIR_THRESHOLD)
        if wait:
            ratelimit.lock(f"login:pair:{self.subject}:{self.ip}", wait)

    def success(self) -> None:
        ratelimit.reset(f"login:pairfail:{self.subject}:{self.ip}")
        if self.device_id:
            ratelimit.reset(f"login:devfail:{self.device_id}")


# --- MFA ------------------------------------------------------------------------------------------
MFA_SESSION_LIMIT = 5  # failures per pre-auth session -> pre-auth discarded
MFA_USER_LIMIT, MFA_USER_WINDOW = 10, 15 * 60


def mfa_user_blocked(user_id: str) -> bool:
    return ratelimit.count(f"mfa:user:{user_id}") >= MFA_USER_LIMIT


def mfa_failure(user_id: str) -> None:
    ratelimit.increment(f"mfa:user:{user_id}", MFA_USER_WINDOW)


# --- Password reset -------------------------------------------------------------------------------
RESET_REQUEST_IP_LIMIT, RESET_REQUEST_SUBJECT_LIMIT, RESET_WINDOW = 10, 3, 60 * 60
RESET_CONFIRM_IP_LIMIT = 30


def reset_request_allowed(identifier: str, ip: str | None) -> bool:
    ip_count = ratelimit.increment(f"reset:req:ip:{ip or 'unknown'}", RESET_WINDOW)
    subject_count = ratelimit.increment(f"reset:req:subj:{subject_for(identifier)}", RESET_WINDOW)
    return ip_count <= RESET_REQUEST_IP_LIMIT and subject_count <= RESET_REQUEST_SUBJECT_LIMIT


def reset_confirm_allowed(ip: str | None) -> bool:
    return ratelimit.count(f"reset:confirm:ip:{ip or 'unknown'}") < RESET_CONFIRM_IP_LIMIT


def reset_confirm_failure(ip: str | None) -> None:
    ratelimit.increment(f"reset:confirm:ip:{ip or 'unknown'}", RESET_WINDOW)
