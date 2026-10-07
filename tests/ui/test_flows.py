"""End-to-end UI flows through the real views, forms and templates (Phase 13)."""

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.academics.models import RegistrationStatus, UnitRegistration
from apps.clubs.models import Club, ClubEvent, ClubMembership, MembershipStatus
from apps.core.capabilities import Role
from apps.core.models import AuditLog
from apps.hostels.models import AllocationStatus, HostelAllocation, HostelApplication
from apps.notifications.models import Announcement
from apps.student_requests.models import RequestCategory, RequestStatus, StudentRequest
from apps.timetable.models import TimetableEntry, Venue
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def admin(*groups, role=Role.ADMIN):
    user = f.user(role, groups=groups)
    enrol(user)
    return user


def ok(response):
    assert response.status_code in (200, 302), response.status_code
    return response


def png(name="doc.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (9, 9, 9)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


# --- Academics ------------------------------------------------------------------------------------


def test_academics_pages_and_override(client):
    semester = f.semester(is_current=True)
    lecturer = f.staff(groups=["Lecturers"])
    offering = f.offering(semester=semester, lecturer=lecturer, unit=f.unit(title="Ethics"))
    student = f.student()
    login(client, student.user)
    ok(client.get(reverse("academics:catalog"), {"q": "eth", "level": "1", "page": "1"}))
    ok(client.get(reverse("academics:offering_detail", args=[offering.pk])))
    ok(client.post(reverse("academics:register", args=[offering.pk])))
    assert b"Ethics" in client.get(reverse("academics:my_units")).content
    registration = UnitRegistration.objects.get(student=student)
    ok(client.post(reverse("academics:drop", args=[registration.pk])))
    registration.refresh_from_db()
    assert registration.status == RegistrationStatus.DROPPED

    registrar = admin("Registrar")
    login(client, registrar)
    ok(client.post(reverse("academics:override", args=[offering.pk]),
                   {"student_number": student.student_number, "reason": "Late clearance", "action": "register"}))
    assert UnitRegistration.objects.filter(student=student, status=RegistrationStatus.REGISTERED).exists()
    ok(client.post(reverse("academics:override", args=[offering.pk]),
                   {"student_number": student.student_number, "reason": "Error", "action": "drop"}))
    ok(client.post(reverse("academics:override", args=[offering.pk]),
                   {"student_number": "NOPE", "reason": "x", "action": "register"}))

    login(client, lecturer.user)
    ok(client.get(reverse("academics:my_teaching")))
    services_reg = UnitRegistration.objects.create(student=f.student(), offering=offering, unit=offering.unit,
                                                   semester=semester)
    ok(client.get(reverse("academics:class_list", args=[offering.pk])))
    ok(client.post(reverse("academics:record_grade", args=[services_reg.pk]), {"grade": "A"}))
    services_reg.refresh_from_db()
    assert services_reg.grade == "A"


# --- Timetable ------------------------------------------------------------------------------------


def test_timetable_management_ui(client):
    semester = f.semester(is_current=True)
    offering = f.offering(semester=semester)
    login(client, f.staff(groups=["Timetabling"]).user)
    ok(client.post(reverse("timetable:venue_create"), {"code": "LAB9", "name": "Lab 9", "campus": "Main",
                                                       "building": "B", "capacity": 30, "venue_type": "LAB",
                                                       "is_active": "on"}))
    venue = Venue.objects.get(code="LAB9")
    ok(client.post(reverse("timetable:venue_edit", args=[venue.pk]), {"code": "LAB9", "name": "Lab Nine",
                                                                      "campus": "Main", "building": "B", "capacity": 30,
                                                                      "venue_type": "LAB", "is_active": "on"}))
    ok(client.post(reverse("timetable:entry_create"), {"offering": offering.pk, "venue": venue.pk, "day_of_week": 2,
                                                       "start_time": "09:00", "end_time": "10:00",
                                                       "class_type": "LAB"}))
    entry = TimetableEntry.objects.get()
    ok(client.get(reverse("timetable:entry_edit", args=[entry.pk])))
    ok(client.post(reverse("timetable:entry_edit", args=[entry.pk]), {"offering": offering.pk, "venue": venue.pk,
                                                                      "day_of_week": 3, "start_time": "09:00",
                                                                      "end_time": "11:00", "class_type": "LAB"}))
    entry.refresh_from_db()
    assert entry.day_of_week == 3
    ok(client.get(reverse("timetable:master"), {"day": "3", "venue": str(venue.pk)}))
    ok(client.get(reverse("timetable:manage")))


# --- Hostels --------------------------------------------------------------------------------------


def test_hostel_application_offer_and_office_ui(client):
    semester = f.semester(is_current=True)
    f.booking_window(semester, mode="APPLICATION")
    hostel = f.hostel(gender_policy="MIXED")
    bed, other_bed = f.bed(hostel), f.bed(hostel)
    student = f.student()
    login(client, student.user)
    ok(client.get(reverse("hostels:catalog")))
    ok(client.get(reverse("hostels:detail", args=[hostel.pk])))
    ok(client.post(reverse("hostels:apply"), {"first_choice": hostel.pk, "room_type": "", "special_needs": "None"}))
    application = HostelApplication.objects.get(student=student)

    office = admin("Accommodation Office")
    login(client, office)
    ok(client.get(reverse("hostels:manage"), {"status": "SUBMITTED"}))
    ok(client.post(reverse("hostels:application", args=[application.pk]),
                   {"form": "decision", "status": "UNDER_REVIEW", "note": "Looking"}))
    ok(client.post(reverse("hostels:application", args=[application.pk]), {"form": "offer", "bed": bed.pk}))
    allocation = HostelAllocation.objects.get(student=student)

    login(client, student.user)
    ok(client.get(reverse("hostels:my_hostel")))
    ok(client.post(reverse("hostels:respond", args=[allocation.pk]), {"decision": "accept"}))
    allocation.refresh_from_db()
    assert allocation.status == AllocationStatus.ACTIVE

    login(client, office)
    ok(client.post(reverse("hostels:transfer", args=[allocation.pk]), {"bed": other_bed.pk}))
    new = HostelAllocation.objects.get(student=student, status=AllocationStatus.ACTIVE)
    ok(client.post(reverse("hostels:vacate", args=[new.pk])))
    ok(client.post(reverse("hostels:space_status", args=["bed", bed.pk]), {"status": "MAINTENANCE"}))
    ok(client.post(reverse("hostels:hostel_create"), {"code": "NH", "name": "New Hall", "campus": "Main",
                                                      "gender_policy": "MIXED", "rules": "Be kind", "is_active": "on"}))
    from apps.hostels.models import Hostel

    new_hostel = Hostel.objects.get(code="NH")
    ok(client.post(reverse("hostels:hostel_edit", args=[new_hostel.pk]), {
        "form": "rooms", "building": "A", "floor_level": 1, "first_room_number": 101, "rooms": 2,
        "room_type": "DOUBLE", "beds_per_room": 2, "fee_per_semester": "100"}))
    assert new_hostel.buildings.get().floors.get().rooms.count() == 2
    ok(client.post(reverse("hostels:window_create"), {
        "mode": "DIRECT_BOOKING", "opens_at": (timezone.now()).strftime("%Y-%m-%dT%H:%M"),
        "closes_at": (timezone.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"), "acceptance_hours": 24}))
    ok(client.post(reverse("hostels:direct_offer"), {"student_number": f.student().student_number,
                                                     "bed": f.bed(hostel).pk}))


# --- Clubs ----------------------------------------------------------------------------------------


def test_club_management_ui(client):
    advisor = f.staff()
    office = admin("Student Affairs")
    login(client, office)
    ok(client.post(reverse("clubs:create"), {"kind": "CLUB", "code": "ROBO", "name": "Robotics", "category": "Tech",
                                             "description": "Bots", "advisor": advisor.pk, "meeting_info": "Fri",
                                             "contact_email": "robo@example.test", "requires_approval": "on",
                                             "is_active": "on"}))
    club = Club.objects.get(code="ROBO")
    ok(client.get(reverse("clubs:manage")))
    student = f.student()
    login(client, student.user)
    ok(client.get(reverse("clubs:directory"), {"q": "rob", "kind": "CLUB", "category": "Tech"}))
    ok(client.post(reverse("clubs:join", args=[club.pk])))
    ok(client.get(reverse("clubs:my_clubs")))
    membership = ClubMembership.objects.get(student=student)

    login(client, advisor.user)
    ok(client.get(reverse("clubs:members", args=[club.pk])))
    ok(client.post(reverse("clubs:decide", args=[membership.pk]), {"decision": "approve"}))
    ok(client.post(reverse("clubs:appoint", args=[membership.pk]), {"position": "SECRETARY",
                                                                   "can_manage_members": "on"}))
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.APPROVED and membership.can_manage_members
    start = timezone.now() + timedelta(days=2)
    ok(client.post(reverse("clubs:event_create", args=[club.pk]), {
        "title": "Build night", "description": "", "starts_at": start.strftime("%Y-%m-%dT%H:%M"),
        "ends_at": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"), "location": "Lab", "visibility": "PUBLIC"}))
    event = ClubEvent.objects.get()
    ok(client.get(reverse("clubs:event_edit", args=[club.pk, event.pk])))
    ok(client.post(reverse("clubs:edit", args=[club.pk]), {"kind": "CLUB", "code": "ROBO", "name": "Robotics Club",
                                                           "category": "Tech", "description": "Bots",
                                                           "meeting_info": "Fri", "contact_email": "",
                                                           "requires_approval": "on", "is_active": "on"}))
    ok(client.post(reverse("clubs:remove", args=[membership.pk])))
    login(client, student.user)
    ok(client.post(reverse("clubs:join", args=[club.pk])))
    ok(client.post(reverse("clubs:leave", args=[club.pk])))


# --- Requests -------------------------------------------------------------------------------------


def test_request_and_transfer_ui(client):
    student = f.student()
    login(client, student.user)
    ok(client.get(reverse("requests:create")))
    ok(client.post(reverse("requests:create"), {"category": RequestCategory.objects.get(code="ACADEMIC_QUERY").pk,
                                                "subject": "Exam date", "description": "When?",
                                                "attachments": [png()]}))
    req = StudentRequest.objects.get(student=student)
    ok(client.post(reverse("requests:message", args=[req.pk]), {"body": "Any update?"}))
    target = f.program()
    ok(client.post(reverse("requests:create_transfer"), {"transfer_type": "PROGRAM", "to_program": target.pk,
                                                         "subject": "Switch", "reason": "Interest"}))
    transfer_req = StudentRequest.objects.get(category__code="TRANSFER")
    ok(client.get(reverse("requests:my_requests")))

    reviewer = f.staff(department=student.program.department, groups=["Department Reviewers"])
    colleague = f.staff(department=student.program.department, groups=["Department Reviewers"])
    login(client, reviewer.user)
    ok(client.get(reverse("requests:queue"), {"status": "open", "q": "Exam", "mine": "on"}))
    ok(client.get(reverse("requests:detail", args=[req.pk])))
    ok(client.post(reverse("requests:transition", args=[req.pk]), {"target": "UNDER_REVIEW", "note": ""}))
    ok(client.post(reverse("requests:assign", args=[req.pk]), {"assignee": colleague.pk}))
    ok(client.post(reverse("requests:routing", args=[req.pk]), {"priority": "HIGH",
                                                                "department": student.program.department.pk}))
    ok(client.post(reverse("requests:message", args=[req.pk]), {"body": "Internal", "internal": "on"}))
    attachment = req.attachments.get()
    ok(client.get(reverse("requests:attachment", args=[attachment.pk])))
    ok(client.post(reverse("requests:transition", args=[transfer_req.pk]), {"target": "UNDER_REVIEW"}))

    board = admin("Academic Board")
    login(client, board)
    ok(client.post(reverse("requests:transition", args=[transfer_req.pk]), {"target": "APPROVED"}))
    registrar = admin("Registrar")
    login(client, registrar)
    ok(client.get(reverse("requests:detail", args=[transfer_req.pk])))
    ok(client.post(reverse("requests:execute_transfer", args=[transfer_req.pk])))
    student.refresh_from_db()
    assert student.program == target
    req.refresh_from_db()
    assert req.status == RequestStatus.UNDER_REVIEW and req.priority == "HIGH"

    settings_admin = admin("Student Services")
    login(client, settings_admin)
    ok(client.post(reverse("requests:category_create"), {"code": "PARKING", "name": "Parking permit",
                                                         "description": "", "default_priority": "LOW",
                                                         "approval_capability": "approve_requests",
                                                         "allows_attachments": "on", "max_attachments": 2,
                                                         "is_active": "on", "sort_order": 200}))
    category = RequestCategory.objects.get(code="PARKING")
    ok(client.get(reverse("requests:category_edit", args=[category.pk])))


# --- Admin panel ----------------------------------------------------------------------------------


def test_admin_panel_ui(client):
    sa = admin("Superadmin", role=Role.SUPERADMIN)
    login(client, sa)
    ok(client.get(reverse("administration:dashboard")))
    ok(client.get(reverse("administration:users"), {"q": "a", "role": "STUDENT", "status": "active"}))
    ok(client.post(reverse("administration:staff_create"), {
        "role": "STAFF", "username": "E77777", "email": "e77777@example.test", "first_name": "Ann", "last_name": "Lee",
        "staff_number": "E77777", "department": f.department().pk, "title": "Lecturer", "office": "", "phone": "",
        "is_lecturer": "on"}))
    from apps.accounts.models import StaffProfile

    staff = StaffProfile.objects.get(staff_number="E77777")
    ok(client.get(reverse("administration:user_detail", args=[staff.user_id])))
    ok(client.post(reverse("administration:staff_edit", args=[staff.pk]), {
        "first_name": "Ann", "last_name": "Lee", "email": "e77777@example.test", "staff_number": "E77777",
        "department": f.department().pk, "title": "Senior Lecturer", "office": "B2", "phone": "", "is_lecturer": "on"}))
    for action in ("deactivate", "activate", "reset-password", "enrollment-code"):
        ok(client.post(reverse("administration:user_action", args=[staff.user_id, action])))
    ok(client.get(reverse("administration:academics")))
    for kind in ("faculties", "departments", "programs", "years", "semesters", "units", "offerings"):
        ok(client.get(reverse("administration:setup_list", args=[kind])))
        ok(client.get(reverse("administration:setup_create", args=[kind])))
    unit, prereq = f.unit(), f.unit()
    ok(client.post(reverse("administration:prerequisite_add", args=[unit.pk]), {"prerequisite": prereq.pk}))
    link = unit.prerequisite_links.get()
    ok(client.get(reverse("administration:setup_edit", args=["units", unit.pk])))
    ok(client.post(reverse("administration:prerequisite_remove", args=[link.pk])))
    offering = f.offering()
    ok(client.get(reverse("administration:setup_edit", args=["offerings", offering.pk])))
    ok(client.post(reverse("administration:offering_lecturer", args=[offering.pk]), {"lecturer": staff.pk}))
    semester = f.semester()
    ok(client.get(reverse("administration:setup_edit", args=["semesters", semester.pk])))
    ok(client.post(reverse("administration:semester_current", args=[semester.pk])))
    ok(client.get(reverse("administration:audit_log"), {"actor": sa.username, "outcome": "SUCCESS",
                                                        "since": "2000-01-01", "until": "2100-01-01"}))
    ok(client.get(reverse("administration:security_events"), {"event_type": "LOGIN_FAILURE", "since": "2000-01-01"}))
    assert AuditLog.objects.filter(action="SETUP.CURRENT_SEMESTER_SET").exists()


# --- Announcements --------------------------------------------------------------------------------


def test_announcement_ui(client):
    lecturer = f.staff(groups=["Communications"])
    login(client, lecturer.user)
    ok(client.get(reverse("notifications:announcement_create")))
    ok(client.post(reverse("notifications:announcement_create"), {
        "title": "Lab moved", "body": "Room change", "audience_roles": "EVERYONE", "scope": "DEPARTMENT",
        "department": lecturer.department.pk, "publish_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
        "action": "publish", "attachments": [png("map.png")]}))
    announcement = Announcement.objects.get()
    ok(client.get(reverse("notifications:manage")))
    ok(client.get(reverse("notifications:announcement_edit", args=[announcement.pk])))
    attachment = announcement.attachments.get()
    colleague = f.staff(department=lecturer.department)
    login(client, colleague.user)
    ok(client.get(reverse("notifications:announcements")))
    ok(client.get(reverse("notifications:attachment", args=[attachment.pk])))
    ok(client.get(reverse("notifications:list")))
    login(client, lecturer.user)
    ok(client.post(reverse("notifications:withdraw", args=[announcement.pk])))
    assert Announcement.objects.get().status == "WITHDRAWN"
