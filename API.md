# REST API Documentation

## 1. Authentication & Headers
All API endpoints require active session authentication or standard Django CSRF headers for mutating methods (`X-CSRFToken`).
- **Content-Type**: `application/json`
- **Error Codes**:
  - `400 Bad Request`: Payload validation error.
  - `401 Unauthorized`: Unauthenticated session.
  - `403 Forbidden`: Authenticated user lacks permission (RBAC).
  - `404 Not Found`: Resource does not exist or user unauthorized to access it (IDOR protection).
  - `429 Too Many Requests`: Rate limit exceeded.

## 2. Core Endpoints

### 2.1 Academic Endpoints
- `GET /api/academics/units/`: List available units with prerequisites and capacity.
- `POST /api/academics/units/register/`:
  - Request: `{"unit_code": "CS101"}`
  - Response: `{"status": "success", "message": "Successfully registered for CS101"}`
- `POST /api/academics/units/drop/`:
  - Request: `{"unit_code": "CS101"}`
  - Response: `{"status": "success", "message": "Successfully dropped CS101"}`

### 2.2 Hostel Endpoints
- `GET /api/hostels/available-rooms/`: List hostels and rooms with vacancy.
- `POST /api/hostels/book-bed/`:
  - Request: `{"bed_id": "<uuid>"}`
  - Response: `{"status": "success", "allocation_id": "<uuid>"}`
  - Note: Employs database row locking to guarantee zero race conditions.

### 2.3 Student Requests & Ticketing
- `GET /api/requests/my-requests/`: Returns caller's ticket history.
- `POST /api/requests/create/`:
  - Request: `{"category_id": "<id>", "subject": "Grade issue", "description": "Details..."}`
- `POST /api/requests/<id>/messages/`:
  - Post comments or student responses.

### 2.4 Notifications & Announcements
- `GET /api/notifications/`: List unread/read in-app notifications.
- `POST /api/notifications/<id>/mark-read/`: Mark as acknowledged.
- `GET /api/announcements/`: List announcements scoped to caller's audience.
