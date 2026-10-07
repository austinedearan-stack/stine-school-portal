# API

Status: **implemented (Phase 13)** — a small, read-mostly JSON API (ARCHITECTURE.md D2) used for integrations and
future front-ends. The HTML portal does not depend on it.

## Rules

* Base path `/api/v1/`. JSON only. **Session authentication + CSRF** (`X-CSRFToken` header) for unsafe methods;
  no API tokens (nothing to leak into URLs, logs or browser storage). The session policy (timeouts, MFA gate)
  applies exactly as for HTML pages.
* Every endpoint returns only the caller's own data; identity comes from the session, never from a parameter.
* Endpoints use the same selectors/services as the HTML views; serializers declare explicit read-only fields.
* Lists are paginated (`?page=`; 25 per page). Search input is truncated to 100 characters.
* Throttles: anonymous 60/hour, authenticated 2000/day.
* Errors: `{"detail": "..."}`; no stack traces, SQL or internal names. Unauthenticated: 403.

## Endpoints

| Method | Path | Who | Returns |
|---|---|---|---|
| GET | `/api/v1/me/` | any signed-in user | own account; students also get their (non-sensitive) record |
| GET | `/api/v1/me/registrations/` | students | current-semester registrations with offering details |
| GET | `/api/v1/me/timetable/` | students (registered classes), staff (teaching) | weekly entries for the current semester |
| GET | `/api/v1/me/notifications/` | any signed-in user | own notifications, newest first |
| POST | `/api/v1/me/notifications/{id}/read/` | any signed-in user | 204; 404 for someone else's notification |
| GET | `/api/v1/offerings/?q=` | any signed-in user | browsable offerings of the current semester |

There are deliberately no write endpoints for registrations, bookings or requests: those flows involve
multi-step validation, locking and audit, and are only available through the HTML views.
