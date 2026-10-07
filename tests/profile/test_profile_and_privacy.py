"""Profile editing allowlist, photo access control and student privacy (Phase 4; audit Z-3, T4, T5, T15)."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.academics.models import UnitRegistration
from apps.accounts.models import StudentProfile
from apps.core.capabilities import Role
from apps.core.models import AuditLog, SecurityEvent, SecurityEventType
from tests import factories as f
from tests.helpers import enrol, login

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def png(name="me.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (0, 128, 0)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def register(student, offering):
    return UnitRegistration.objects.create(student=student, offering=offering, unit=offering.unit,
                                           semester=offering.semester)


def admin_with(*groups):
    admin = f.user(Role.ADMIN, groups=groups)
    enrol(admin)
    return admin


# --- Editing --------------------------------------------------------------------------------------


def test_student_edits_contact_fields_and_change_is_audited(client):
    student = f.student()
    login(client, student.user)
    response = client.post(reverse("accounts:edit_profile"), {
        "phone": "+254700000001", "personal_email": "me@example.test", "emergency_contact_name": "Parent",
        "emergency_contact_phone": "+254700000002", "emergency_contact_relationship": "Mother"})
    assert response.status_code == 302
    student.refresh_from_db()
    assert student.phone == "+254700000001"
    entry = AuditLog.objects.get(action="PROFILE.CONTACTS_UPDATED", object_id=str(student.pk))
    assert entry.changes["after"]["phone"] == "+254700000001" and entry.changes["before"]["phone"] == ""


def test_institutional_fields_cannot_be_mass_assigned(client):
    student = f.student()
    other_program = f.program()
    login(client, student.user)
    client.post(reverse("accounts:edit_profile"), {
        "phone": "1", "program": str(other_program.pk), "student_number": "HACKED", "academic_status": "GRADUATED",
        "year_of_study": "8", "disciplinary_status": "CLEAR", "role": "ADMIN", "user": str(f.user().pk)})
    student.refresh_from_db()
    student.user.refresh_from_db()
    assert student.program != other_program and student.student_number != "HACKED"
    assert student.academic_status == "ACTIVE" and student.year_of_study == 1 and student.user.role == Role.STUDENT


def test_staff_edits_only_office_and_phone(client):
    staff = f.staff(title="Lecturer")
    login(client, staff.user)
    client.post(reverse("accounts:edit_profile"), {"office": "B12", "phone": "123", "title": "Vice Chancellor",
                                                   "department": str(f.department().pk)})
    staff.refresh_from_db()
    assert staff.office == "B12" and staff.title == "Lecturer"


def test_account_without_profile_cannot_edit(client):
    admin = admin_with()
    login(client, admin)
    assert client.get(reverse("accounts:edit_profile")).status_code == 403


# --- Photos ---------------------------------------------------------------------------------------


def test_photo_upload_and_owner_only_access(client):
    student = f.student()
    login(client, student.user)
    client.post(reverse("accounts:photo_upload"), {"photo": png()})
    student.refresh_from_db()
    assert student.photo_id
    url = reverse("accounts:photo", args=[student.photo_id])
    response = client.get(url)
    assert response.status_code == 200 and response["Content-Type"] == "image/png"

    other = f.student()
    login(client, other.user)
    assert client.get(url).status_code == 404  # out of scope looks like "does not exist"
    assert SecurityEvent.objects.filter(event_type=SecurityEventType.PERMISSION_DENIED, user=other.user).exists()

    registrar = admin_with("Registrar")
    login(client, registrar)
    assert client.get(url).status_code == 200


def test_rejected_upload_is_reported_and_recorded(client):
    student = f.student()
    login(client, student.user)
    bad = SimpleUploadedFile("evil.png", b"<?php echo 1; ?>", content_type="image/png")
    response = client.post(reverse("accounts:photo_upload"), {"photo": bad}, follow=True)
    assert b"not accepted" in response.content
    assert SecurityEvent.objects.filter(event_type=SecurityEventType.UPLOAD_REJECTED).exists()
    student.refresh_from_db()
    assert student.photo_id is None


def test_replacing_a_photo_deletes_the_old_file(client, tmp_path):
    student = f.student()
    login(client, student.user)
    client.post(reverse("accounts:photo_upload"), {"photo": png()})
    student.refresh_from_db()
    first = student.photo.storage_name
    client.post(reverse("accounts:photo_upload"), {"photo": png("second.png")})
    assert not (tmp_path / first).exists()


def test_anonymous_cannot_fetch_photos(client):
    student = f.student()
    login(client, student.user)
    client.post(reverse("accounts:photo_upload"), {"photo": png()})
    student.refresh_from_db()
    client.post(reverse("accounts:logout"))
    response = client.get(reverse("accounts:photo", args=[student.photo_id]))
    assert response.status_code == 302 and "/accounts/login/" in response.url


# --- Student detail (IDOR / privacy) -------------------------------------------------------------


def detail(client, student):
    return client.get(reverse("accounts:student_detail", args=[student.pk]))


def test_student_cannot_view_another_student(client):
    me, other = f.student(), f.student(phone="+254711111111")
    login(client, me.user)
    assert detail(client, me).status_code == 200
    assert detail(client, other).status_code == 404


def test_lecturer_sees_only_own_students_and_no_contacts(client):
    lecturer = f.staff()
    offering = f.offering(lecturer=lecturer)
    taught = f.student(phone="+254722222222", emergency_contact_phone="+254733333333")
    register(taught, offering)
    stranger = f.student()
    login(client, lecturer.user)
    response = detail(client, taught)
    assert response.status_code == 200
    assert taught.student_number.encode() in response.content
    assert b"+254722222222" not in response.content and b"+254733333333" not in response.content
    assert detail(client, stranger).status_code == 404


def test_registrar_sees_contacts_but_admin_without_capability_does_not_see_student(client):
    student = f.student(phone="+254744444444")
    login(client, admin_with("Registrar"))
    assert b"+254744444444" in detail(client, student).content
    login(client, admin_with("Timetabling"))
    assert detail(client, student).status_code == 404


def test_unknown_student_id_is_404(client):
    import uuid

    login(client, admin_with("Registrar"))
    assert client.get(reverse("accounts:student_detail", args=[uuid.uuid4()])).status_code == 404


def test_student_profiles_are_never_listed_to_students(client):
    student = f.student()
    login(client, student.user)
    assert StudentProfile.objects.count() >= 1
    assert client.get("/accounts/students/").status_code == 404
