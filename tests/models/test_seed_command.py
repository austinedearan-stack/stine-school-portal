import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from apps.academics.models import UnitOffering
from apps.accounts.models import StudentProfile, User
from apps.hostels.models import Bed
from apps.timetable.models import TimetableEntry

pytestmark = pytest.mark.django_db


@override_settings(DEBUG=True)
def test_seed_creates_fake_data_once(capsys):
    call_command("seed_demo_data", students=4)
    out = capsys.readouterr().out
    assert StudentProfile.objects.count() == 4
    assert UnitOffering.objects.exists() and TimetableEntry.objects.exists() and Bed.objects.exists()
    assert all(email.endswith("@example.test") for email in User.objects.values_list("email", flat=True))
    assert "student001" in out
    with pytest.raises(CommandError):
        call_command("seed_demo_data")


def test_seed_refuses_without_debug():
    with pytest.raises(CommandError):
        call_command("seed_demo_data")
