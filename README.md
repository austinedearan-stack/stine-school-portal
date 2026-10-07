# Full-Stack University Student Portal

A production-grade, highly secure, full-stack university student information and services portal built with **Python / Django 5**, **Django REST Framework**, and **Tailwind CSS**.

---

## 🏛️ Features Overview

- **Role-Based Access Control (RBAC)**: Strict segregation between `STUDENT`, `STAFF`, `ADMIN`, and `SUPERADMIN`. All authorization enforced server-side.
- **Authentication & Security**:
  - Argon2 password hashing (`Argon2PasswordHasher`).
  - RFC 6238 TOTP Multi-Factor Authentication (MFA) for administrative roles.
  - Progressive rate limiting & account lockout on brute-force detection.
  - Session rotation on login to defeat session fixation.
  - Generic authentication errors (no account enumeration).
- **Student Dashboard & Profile**:
  - Live summary of units, timetable, hostel status, requests, and announcements.
  - Separation of institutional read-only data from student-editable data.
  - Secure profile photo upload pipeline with MIME & magic byte inspection.
- **Academic Units & Registration**:
  - Unit catalog browsing with prerequisite validation and credit load constraints.
  - Atomic transactions preventing over-enrollment and duplicate registration.
- **Timetable Management**:
  - Interactive weekly schedule grid.
  - Multi-dimensional clash detection (room conflict, lecturer conflict, cohort conflict).
- **Hostel Booking & Allocation**:
  - Hostel, building, floor, room, and bed hierarchy.
  - **Zero-race-condition bed booking** using atomic transactions and row-level locking (`select_for_update`).
- **Clubs & Societies**:
  - Club discovery, event management, and structured membership application workflow.
- **Unified Student Requests & Transfers**:
  - Generalized ticketing engine supporting academic queries, service requests, and official program/department transfers.
  - Multi-level administrative review, assignment, commenting, and attachment support.
- **Administrative Control Panel**:
  - Real-time KPIs (students, staff, active units, hostel occupancy, pending requests).
  - Triage workflows and user management.
- **Immutable Audit Logging**:
  - Comprehensive audit trail of every administrative and security-critical action.

---

## 🚀 Quickstart

### Prerequisites
- Python 3.12+ (or 3.14)
- Git

### Installation
1. Clone the repository and navigate into the project directory:
   ```bash
   git clone <repo-url>
   cd "school portal"
   ```
2. Create and activate a virtual environment:
   ```bash
   py -m venv .venv
   .venv\Scripts\activate   # Windows
   # or source .venv/bin/activate on Linux/macOS
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Configure environment:
   ```bash
   cp .env.example .env
   ```
5. Apply database migrations:
   ```bash
   python manage.py migrate
   ```
6. Populate development seed data:
   ```bash
   python manage.py runscript seed_data
   # or: python scripts/seed_data.py
   ```
7. Run the development server:
   ```bash
   python manage.py runserver
   ```
8. Access the portal at `http://127.0.0.1:8000/`.

---

## 🧪 Testing

Run the comprehensive automated test suite (including IDOR, concurrency, and security tests):
```bash
pytest
```
