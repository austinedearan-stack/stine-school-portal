"""Create a small, obviously fake data set for local development and demos.

* Refuses to run unless DEBUG is on (or --allow-non-debug is passed for a disposable staging copy).
* Refuses to run twice (if any faculty exists).
* Every account uses an @example.test address and a random password printed ONCE to the console;
  nothing is written to disk.
"""

from __future__ import annotations

import secrets
from datetime import date, time, timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.academics.models import (
    AcademicYear,
    AwardLevel,
    Department,
    Faculty,
    OfferingStatus,
    Program,
    Semester,
    Unit,
    UnitOffering,
    UnitPrerequisite,
)
from apps.accounts.models import StaffProfile, StudentProfile, User
from apps.clubs.models import Club, ClubKind
from apps.core.capabilities import Role
from apps.hostels.models import (
    Bed,
    BookingMode,
    GenderPolicy,
    Hostel,
    HostelBookingWindow,
    HostelBuilding,
    HostelFloor,
    Room,
    RoomType,
)
from apps.timetable.models import ClassType, StudentGroup, TimetableEntry, Venue

FIRST_NAMES = ["Amina", "Brian", "Cynthia", "David", "Esther", "Felix", "Grace", "Hassan", "Irene", "James",
               "Joy", "Kevin", "Lilian", "Moses", "Nancy"]
LAST_NAMES = ["Achieng", "Barasa", "Chebet", "Dida", "Ekirapa", "Fundi", "Gitau", "Hamisi", "Ireri", "Juma",
              "Kamau", "Langat", "Mwangi", "Njeri", "Otieno"]


class Command(BaseCommand):
    help = "Seed fake demo data (development only). Prints generated passwords once."

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=12)
        parser.add_argument("--allow-non-debug", action="store_true", help="Allow on a disposable non-DEBUG copy.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["allow_non_debug"]:
            raise CommandError("Refusing to seed demo data with DEBUG off (pass --allow-non-debug on a disposable copy).")
        if Faculty.objects.exists():
            raise CommandError("Data already exists; demo seed only runs on an empty database.")
        credentials: list[tuple[str, str, str]] = []
        with transaction.atomic():
            self._seed(options["students"], credentials)
        self.stdout.write(self.style.SUCCESS("Demo data created. Credentials (shown once, not stored):"))
        for username, role, password in credentials:
            self.stdout.write(f"  {role:<11} {username:<14} {password}")
        self.stdout.write("Privileged accounts must enrol an authenticator app at first login.")

    # ------------------------------------------------------------------------------------------
    def _user(self, credentials, username, role, first, last, groups=()):
        password = secrets.token_urlsafe(12)
        user = User.objects.create_user(
            username=username, email=f"{username.lower()}@example.test", password=password, role=role,
            first_name=first, last_name=last,
        )
        for name in groups:
            user.groups.add(Group.objects.get(name=name))
        credentials.append((username, role, password))
        return user

    def _seed(self, student_count: int, credentials):
        now = timezone.now()
        today = now.date()

        sci = Faculty.objects.create(code="FSCI", name="Faculty of Science and Technology")
        bus = Faculty.objects.create(code="FBUS", name="Faculty of Business")
        cs = Department.objects.create(faculty=sci, code="CS", name="Computer Science")
        math = Department.objects.create(faculty=sci, code="MATH", name="Mathematics")
        acc = Department.objects.create(faculty=bus, code="ACC", name="Accounting and Finance")
        Department.objects.create(faculty=bus, code="MKT", name="Marketing")
        bsc_cs = Program.objects.create(department=cs, code="BSCCS", name="BSc Computer Science",
                                        award_level=AwardLevel.BACHELOR, max_credits_per_semester=21,
                                        min_credits_per_semester=6)
        bsc_math = Program.objects.create(department=math, code="BSCMATH", name="BSc Mathematics",
                                          max_credits_per_semester=21, min_credits_per_semester=6)
        bcom = Program.objects.create(department=acc, code="BCOM", name="Bachelor of Commerce",
                                      max_credits_per_semester=24, min_credits_per_semester=6)

        year = AcademicYear.objects.create(name=f"{today.year}/{today.year + 1}", start_date=date(today.year, 1, 1),
                                           end_date=date(today.year, 12, 31), is_current=True)
        semester = Semester.objects.create(
            academic_year=year, number=1, name="Semester 1", start_date=today - timedelta(days=14),
            end_date=today + timedelta(days=100), registration_opens_at=now - timedelta(days=7),
            registration_closes_at=now + timedelta(days=21), add_drop_deadline=now + timedelta(days=28),
            is_current=True,
        )

        # Staff
        superadmin = self._user(credentials, "admin", Role.SUPERADMIN, "Portal", "Superadmin", groups=["Superadmin"])
        StaffProfile.objects.create(user=superadmin, staff_number="E00001", department=cs, title="System owner")
        registrar = self._user(credentials, "registrar", Role.ADMIN, "Rita", "Registrar",
                               groups=["Registrar", "Academic Office", "Student Services", "Accommodation Office",
                                       "Student Affairs", "Academic Board", "Communications", "Auditor", "IT Support",
                                       "Examinations"])
        StaffProfile.objects.create(user=registrar, staff_number="E00002", department=cs, title="Registrar")
        lecturers = []
        for i, (dept, first, last) in enumerate([(cs, "Peter", "Kariuki"), (cs, "Mary", "Wanjiru"),
                                                 (math, "Samuel", "Odhiambo"), (acc, "Ruth", "Akinyi")], start=1):
            user = self._user(credentials, f"staff{i:03d}", Role.STAFF, first, last,
                              groups=["Lecturers", "Department Reviewers"] if i == 1 else ["Lecturers"])
            lecturers.append(StaffProfile.objects.create(user=user, staff_number=f"E1{i:04d}", department=dept,
                                                         title="Lecturer", is_lecturer=True))

        # Units and offerings
        catalogue = [
            (cs, "CSC101", "Introduction to Programming", 3, 1, None),
            (cs, "CSC102", "Discrete Structures", 3, 1, None),
            (cs, "CSC201", "Data Structures and Algorithms", 4, 2, "CSC101"),
            (cs, "CSC202", "Database Systems", 3, 2, "CSC101"),
            (cs, "CSC301", "Operating Systems", 3, 3, "CSC201"),
            (cs, "CSC305", "Secure Software Engineering", 3, 3, "CSC202"),
            (math, "MAT101", "Calculus I", 3, 1, None),
            (math, "MAT102", "Linear Algebra", 3, 1, None),
            (math, "MAT201", "Probability and Statistics", 3, 2, "MAT101"),
            (acc, "ACC101", "Principles of Accounting", 3, 1, None),
            (acc, "ACC201", "Financial Reporting", 3, 2, "ACC101"),
            (acc, "BUS110", "Business Communication", 2, 1, None),
        ]
        units = {}
        for dept, code, title, credits, level, prereq in catalogue:
            units[code] = Unit.objects.create(department=dept, code=code, title=title, credit_hours=credits,
                                              level=level, description=f"{title}: syllabus summary.")
            if prereq:
                UnitPrerequisite.objects.create(unit=units[code], prerequisite=units[prereq])
        lecturer_for = {cs: lecturers[0], math: lecturers[2], acc: lecturers[3]}
        offerings = {}
        for dept, code, *_ in catalogue:
            offerings[code] = UnitOffering.objects.create(unit=units[code], semester=semester, lecturer=lecturer_for[dept],
                                                          capacity=40, status=OfferingStatus.OPEN,
                                                          min_year=units[code].level)
        offerings["CSC102"].lecturer = lecturers[1]
        offerings["CSC102"].save()
        offerings["ACC101"].eligible_programs.add(bcom)

        venues = [Venue.objects.create(code=f"LH{i}", name=f"Lecture Hall {i}", capacity=120, building="Main block")
                  for i in range(1, 4)]
        venues.append(Venue.objects.create(code="LAB1", name="Computer Lab 1", capacity=40, venue_type="LAB"))
        for program in (bsc_cs, bsc_math, bcom):
            StudentGroup.objects.create(program=program, year_of_study=1, label="G1")
        slots = [(1, time(8), time(10)), (1, time(10), time(12)), (2, time(8), time(10)), (2, time(14), time(16)),
                 (3, time(9), time(11)), (3, time(11), time(13)), (4, time(8), time(10)), (4, time(10), time(12)),
                 (5, time(9), time(11)), (5, time(14), time(16)), (2, time(10), time(12)), (4, time(14), time(16))]
        for (code, offering), (day, start, end), venue in zip(
            offerings.items(), slots, venues * 3, strict=False
        ):
            TimetableEntry.objects.create(offering=offering, semester=semester, lecturer=offering.lecturer, venue=venue,
                                          day_of_week=day, start_time=start, end_time=end,
                                          class_type=ClassType.LAB if code == "CSC101" and day == 1 else ClassType.LECTURE)

        # Students
        programs = [bsc_cs, bsc_cs, bsc_math, bcom]
        for i in range(1, student_count + 1):
            user = self._user(credentials, f"student{i:03d}", Role.STUDENT, FIRST_NAMES[i % len(FIRST_NAMES)],
                              LAST_NAMES[(i * 7) % len(LAST_NAMES)])
            StudentProfile.objects.create(
                user=user, student_number=f"S{today.year % 100:02d}{i:04d}", program=programs[i % len(programs)],
                year_of_study=1 + (i % 3), admission_date=date(today.year - (i % 3), 9, 1),
                gender=["FEMALE", "MALE"][i % 2], campus="Main Campus",
            )

        # Hostels
        for code, name, policy in (("H1", "Kilimanjaro Hall", GenderPolicy.FEMALE),
                                   ("H2", "Kenya Hall", GenderPolicy.MALE), ("H3", "Unity Hall", GenderPolicy.MIXED)):
            hostel = Hostel.objects.create(code=code, name=name, gender_policy=policy,
                                           rules="Quiet hours 22:00-06:00. No visitors after 20:00.")
            building = HostelBuilding.objects.create(hostel=hostel, name="Block A")
            for level in (1, 2):
                floor = HostelFloor.objects.create(building=building, level=level)
                for r in range(1, 4):
                    room = Room.objects.create(floor=floor, number=f"{level}0{r}", room_type=RoomType.DOUBLE,
                                               capacity=2, fee_per_semester=15000)
                    for label in ("A", "B"):
                        Bed.objects.create(room=room, label=label)
        HostelBookingWindow.objects.create(semester=semester, mode=BookingMode.DIRECT_BOOKING,
                                           opens_at=now - timedelta(days=3), closes_at=now + timedelta(days=30))

        # Clubs
        for code, name, kind, category in (("CHESS", "Chess Club", ClubKind.CLUB, "Games"),
                                           ("DEBATE", "Debating Society", ClubKind.SOCIETY, "Academic"),
                                           ("CODE", "Programming Club", ClubKind.CLUB, "Technology"),
                                           ("DRAMA", "Drama Society", ClubKind.SOCIETY, "Arts")):
            Club.objects.create(code=code, name=name, kind=kind, category=category, advisor=lecturers[0],
                                description=f"The {name} meets weekly.", meeting_info="Wednesdays 16:00",
                                contact_email=f"{code.lower()}@example.test")
