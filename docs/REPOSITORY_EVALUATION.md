# Open-Source Foundation Evaluation

Evaluated 2026-10-07. Method: read-only shallow clones into an isolated scratch directory (no
code from any candidate was executed), plus `git log`/`shortlog` for activity and the GitHub
web pages for stars/issues. Feature coverage is partly based on keyword searches and should be
treated as approximate. **No code from any candidate has been copied into this project.**

## Comparison

| Repository | Stars | Last commit | License (SPDX) | Stack | Tests | Authorization quality | Verdict |
|---|---|---|---|---|---|---|---|
| [TareqMonwer/Django-School-Management](https://github.com/TareqMonwer/Django-School-Management) | 595 | 2026-09-25 | **none** | Django 4.2 (EOL Apr 2026), DRF, allauth, Celery | 9/10 test files are stubs | Central predicates, no object-level checks | Reference only — no license means no right to reuse |
| [SkyCascade/SkyLearn](https://github.com/SkyCascade/SkyLearn) (successor of adilmohak/django-lms) | 655 | 2025-04-21 | MIT | Django 4.0.8 (EOL 2023) | ~315 lines | Role decorators, IDOR (any lecturer deletes any program/file by GET) | Data-model reference only |
| [jobic10/student-management-using-django](https://github.com/jobic10/student-management-using-django) | 391 | 2023-08-07 | MIT | Django 3.1 (EOL 2021) | stub | 21 `@csrf_exempt` views, module-name middleware authz, committed secret | Rejected |
| [frappe/education](https://github.com/frappe/education) | 644 | 2026-09-30 | GPL-3.0 (+ERPNext GPL-3.0) | Frappe ≥17 / ERPNext | 68 files | Declarative role perms, but whitelisted APIs bypass them (e.g. `collect_fees`) | Domain reference only (GPL, non-Django) |
| [openeducat/openeducat_erp](https://github.com/openeducat/openeducat_erp) | 867 | 2026-09-07 | LGPL-3.0-only | Odoo 19 | 39 files | Odoo groups + record rules; one `auth='none'`+`sudo()` write endpoint | Domain reference only (Odoo platform) |
| [GibbonEdu/core](https://github.com/GibbonEdu/core) | 627 | 2026-10-07 | GPL-3.0 | PHP 8.1, Slim 4 | Codeception, 466 files | Central `isActionAccessible` role→action matrix; TOTP MFA; lockout. Weak: salted SHA-256 passwords | Best functional/UX reference (GPL, PHP) |
| [OS4ED/openSIS-Classic](https://github.com/OS4ED/openSIS-Classic) | 339 | 2026-10-05 | GPL-2.0-only | procedural PHP/MySQL | none | String-built SQL from `$_REQUEST`, unsalted MD5 passwords | Rejected |
| [projectfedena/fedena](https://github.com/projectfedena/fedena) | 548 | 2016-07-20 | Apache-2.0 | Rails 2.3.5 | ~125 files | `declarative_authorization`; SHA-1 passwords, hard-coded session secret | Rejected (abandoned) |

## Decision

**No candidate is a safe starting codebase for this project.**

* The Django candidates run end-of-life Django, have thin or empty test suites and contain the
  very vulnerability classes this project must avoid (IDOR, CSRF exemptions, stored XSS, hard-coded
  secrets). One has no license at all, so it cannot legally be reused.
* The mature, well-tested systems (Frappe Education, OpenEduCat, Gibbon) are GPL/LGPL and built on
  other platforms (Frappe, Odoo, PHP). Adopting one would mean adopting that platform, not Django,
  and inheriting copyleft obligations the institution has not agreed to.

The spec's preferred stack is Django. The project folder already contains a Django skeleton whose
structure (custom user, UUID keys, service layer, partial unique constraints) is closer to the
spec than any candidate. Therefore:

> **Foundation = the existing in-house Django skeleton, upgraded to Django 5.2 LTS and hardened**,
> with the candidates above used strictly as *architectural references*. Every existing
> authentication, authorization, database, upload and admin feature was audited
> (`docs/AUDIT_EXISTING_CODE.md`) and is treated as untrusted until re-tested.

This is a deliberate deviation from "use the selected repository as the starting codebase",
recorded here because no repository met the security and licensing bar.

## What is referenced (ideas only, re-implemented from scratch)

| Source | Idea adopted | Explicitly *not* adopted |
|---|---|---|
| Gibbon | Central role → action permission matrix checked on every action; MFA + lockout + admin alert login flow; Timetable/Activities/Messenger module boundaries as UX reference | SHA-256 password storage |
| OpenEduCat | Row-level scoping ("students see own records, lecturers see their offerings") implemented once as queryset scoping so every view inherits it | `auth='none'` + `sudo()` endpoints |
| Frappe Education | Program → Course → Enrollment → Registration schema; Academic Year/Term; leave application as a request type | Whitelisted methods that skip permission checks |
| SkyLearn (MIT) | Lecturer↔unit allocation concept (here: `UnitOffering.lecturer`) | Unvalidated registration, GET-based deletes |
| TareqMonwer | Single module of permission predicates (shape only — no code, no license) | Everything else |

## Third-party Django packages chosen instead

Only small, maintained, permissively licensed packages are used (see `requirements/`):
`argon2-cffi` (password hashing), `pyotp` (TOTP), `cryptography` (encrypting TOTP secrets at
rest), `djangorestframework` (limited API), `psycopg` (PostgreSQL), `redis`/`django-redis`-free
built-in Redis cache backend, `whitenoise` (static files), `gunicorn`, `Pillow` (image re-encoding).
Rate limiting, audit logging and authorization are implemented in-project because they are core
to the security model and must be fully covered by this project's tests.
