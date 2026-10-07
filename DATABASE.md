# Database Design & Integrity Specification

## 1. Overview
The database layer enforces relational consistency, referential integrity, and concurrency safety. All sensitive state changes are tracked in an immutable audit ledger.

## 2. Entity Relational Details & Constraints

### 2.1 Accounts & Identity
- **`accounts.User`**:
  - `id`: UUID (Primary Key)
  - `username`: CharField(unique=True, max_length=50)
  - `email`: EmailField(unique=True)
  - `role`: CharField(choices: `STUDENT`, `STAFF`, `ADMIN`, `SUPERADMIN`)
  - `is_mfa_enabled`: BooleanField(default=False)
  - `mfa_secret`: CharField(blank=True, max_length=64) - encrypted TOTP key
  - `failed_login_attempts`: PositiveIntegerField(default=0)
  - `locked_until`: DateTimeField(null=True, blank=True)
- **`accounts.StudentProfile`**:
  - `user`: OneToOneField(`User`, on_delete=CASCADE)
  - `student_id`: CharField(unique=True, db_index=True) - Institutional immutable
  - `program`: ForeignKey(`academics.Program`, on_delete=PROTECT)
  - `current_year`: PositiveSmallIntegerField(default=1)
  - `current_semester`: PositiveSmallIntegerField(default=1)
  - `academic_status`: CharField(choices: `ACTIVE`, `PROBATION`, `SUSPENDED`, `GRADUATED`)
  - `phone`: CharField(blank=True, max_length=20) - Student-editable
  - `profile_photo`: ImageField(upload_to=secure_profile_path)
  - `emergency_contact_name`: CharField(blank=True, max_length=100)
  - `emergency_contact_phone`: CharField(blank=True, max_length=20)
- **`accounts.StaffProfile`**:
  - `user`: OneToOneField(`User`, on_delete=CASCADE)
  - `staff_id`: CharField(unique=True, db_index=True)
  - `department`: ForeignKey(`academics.Department`, on_delete=PROTECT)
  - `title`: CharField(max_length=50)
  - `office_location`: CharField(blank=True, max_length=100)

### 2.2 Academics Catalog & Registration
- **`academics.Unit`**:
  - `code`: CharField(unique=True, max_length=20, db_index=True) (e.g. `CS101`)
  - `title`: CharField(max_length=150)
  - `credit_hours`: PositiveSmallIntegerField(default=3)
  - `department`: ForeignKey(`Department`, on_delete=CASCADE)
  - `max_capacity`: PositiveIntegerField(default=50)
  - `is_active`: BooleanField(default=True)
- **`academics.UnitPrerequisite`**:
  - `unit`: ForeignKey(`Unit`, related_name='prerequisites')
  - `prerequisite`: ForeignKey(`Unit`, related_name='required_for')
  - UniqueConstraint(`unit`, `prerequisite`)
- **`academics.UnitRegistration`**:
  - `student`: ForeignKey(`StudentProfile`, on_delete=CASCADE)
  - `unit`: ForeignKey(`Unit`, on_delete=PROTECT)
  - `semester`: ForeignKey(`Semester`, on_delete=PROTECT)
  - `status`: CharField(choices: `REGISTERED`, `DROPPED`, `COMPLETED`)
  - `registered_at`: DateTimeField(auto_now_add=True)
  - UniqueConstraint: `[student, unit, semester]` where status is `REGISTERED`.

### 2.3 Hostels & Residential Allocation
- **`hostels.Hostel`**:
  - `name`: CharField(unique=True)
  - `gender_policy`: CharField(choices: `MALE`, `FEMALE`, `MIXED`)
- **`hostels.Room`**:
  - `building`: ForeignKey(`HostelBuilding`, on_delete=CASCADE)
  - `room_number`: CharField(max_length=20)
  - `floor`: IntegerField()
  - `room_type`: CharField(choices: `SINGLE`, `DOUBLE`, `QUAD`)
  - `capacity`: PositiveSmallIntegerField()
  - UniqueConstraint: `[building, room_number]`
- **`hostels.Bed`**:
  - `room`: ForeignKey(`Room`, on_delete=CASCADE)
  - `bed_number`: CharField(max_length=10)
  - `is_occupied`: BooleanField(default=False)
  - `status`: CharField(choices: `AVAILABLE`, `OCCUPIED`, `MAINTENANCE`, `RESERVED`)
  - UniqueConstraint: `[room, bed_number]`
- **`hostels.HostelAllocation`**:
  - `student`: OneToOneField(`StudentProfile`, on_delete=PROTECT)
  - `bed`: OneToOneField(`Bed`, on_delete=PROTECT)
  - `semester`: ForeignKey(`Semester`, on_delete=PROTECT)
  - `status`: CharField(choices: `ACTIVE`, `CANCELLED`, `COMPLETED`)
  - `allocated_at`: DateTimeField(auto_now_add=True)

### 2.4 Requests & Ticketing
- **`requests.StudentRequest`**:
  - `ticket_number`: CharField(unique=True, db_index=True)
  - `student`: ForeignKey(`StudentProfile`, on_delete=CASCADE)
  - `category`: ForeignKey(`RequestCategory`, on_delete=PROTECT)
  - `subject`: CharField(max_length=200)
  - `description`: TextField()
  - `status`: CharField(choices: `SUBMITTED`, `UNDER_REVIEW`, `NEEDS_INFO`, `APPROVED`, `REJECTED`, `RESOLVED`, `CLOSED`)
  - `priority`: CharField(choices: `LOW`, `NORMAL`, `HIGH`, `URGENT`)
  - `assigned_to`: ForeignKey(`StaffProfile`, null=True, blank=True, on_delete=SET_NULL)
- **`requests.TransferRequest`**:
  - `student`: ForeignKey(`StudentProfile`, on_delete=CASCADE)
  - `current_program`: ForeignKey(`Program`, related_name='transfers_from')
  - `requested_program`: ForeignKey(`Program`, related_name='transfers_to')
  - `reason`: TextField()
  - `status`: CharField(choices: `PENDING`, `APPROVED`, `REJECTED`)
  - `review_notes`: TextField(blank=True)

### 2.5 Audit Logging
- **`core.AuditLog`**:
  - `id`: UUIDField(primary_key=True)
  - `timestamp`: DateTimeField(auto_now_add=True, db_index=True)
  - `actor`: ForeignKey(`User`, null=True, on_delete=SET_NULL)
  - `actor_username`: CharField(max_length=150)
  - `action`: CharField(max_length=100)
  - `target_model`: CharField(max_length=100)
  - `target_id`: CharField(max_length=100)
  - `ip_address`: GenericIPAddressField(null=True, blank=True)
  - `user_agent`: TextField(blank=True)
  - `changes`: JSONField(default=dict)
  - Read-only integrity: custom model override prevents modification or deletion of records once inserted.
