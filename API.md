# API

Status: **not implemented yet** (planned with the feature phases; design in ARCHITECTURE.md D2).

The inherited code configured Django REST Framework but exposed no endpoints; no API is reachable today.

## Design (binding for implementation)

* Base path `/api/v1/`. JSON only. Session authentication + CSRF header for unsafe methods; **no tokens**
  (nothing to leak into URLs, logs or browser storage).
* Every endpoint calls the same selectors/services as the HTML views, so authorization, validation and audit are identical.
* Serializers declare explicit `fields` allowlists; unknown fields are rejected (400), never silently assigned.
* Object lookups go through actor-scoped querysets: out-of-scope objects return **404**, in-scope but forbidden actions **403**.
* Pagination on every list (`page_size` ≤ 100); query parameters validated (length, enum, UUID).
* Throttles: anonymous 60/h, authenticated 2000/day, plus per-endpoint scopes for search and writes.
* Errors: `{"detail": "..."}` with no stack traces, SQL or internal class names.

## Planned endpoints

| Method | Path | Who | Phase |
|---|---|---|---|
| GET | `/api/v1/me/` | any authenticated user (own data only) | 4 |
| GET | `/api/v1/me/registrations/` | student | 5 |
| GET | `/api/v1/me/timetable/` | student, lecturer | 6 |
| GET | `/api/v1/me/notifications/` · POST `.../{id}/read/` | any (own) | 11 |
| GET | `/api/v1/offerings/?q=&department=` | any authenticated | 5 |
