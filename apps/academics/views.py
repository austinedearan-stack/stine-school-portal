"""Unit catalogue, registration, teaching lists and grading (Phase 5)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.academics import selectors, services
from apps.academics.forms import CatalogueFilterForm, GradeForm, OverrideForm
from apps.academics.models import RegistrationStatus, UnitOffering, UnitRegistration
from apps.accounts.models import StudentProfile
from apps.core.authz import deny, get_in_scope_or_404, is_allowed
from apps.core.authz.decorators import role_required
from apps.core.capabilities import Role
from apps.core.context import RequestContext

PAGE_SIZE = 25


def _student(request):
    return getattr(request.user, "student_profile", None) if request.user.role == Role.STUDENT else None


@login_required
def catalogue_view(request):
    semester = selectors.current_semester()
    form = CatalogueFilterForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    offerings = selectors.catalogue(semester, query=(filters.get("q") or "").strip(),
                                    department=filters.get("department"), level=filters.get("level"))
    page = Paginator(offerings, PAGE_SIZE).get_page(request.GET.get("page"))
    student = _student(request)
    registered = set()
    if student is not None and semester is not None:
        registered = set(selectors.registrations_for_student(student, semester).values_list("offering_id", flat=True))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "academics/catalogue.html", {
        "semester": semester, "form": form, "page": page, "registered": registered, "query": query.urlencode(),
    })


@login_required
def offering_detail_view(request, offering_id):
    offering = get_in_scope_or_404(request.user, "can_browse_units", selectors.browsable_offerings(),
                                   pk=offering_id)
    student = _student(request)
    context = {
        "offering": offering,
        "unit": offering.unit,
        "prerequisites": [link.prerequisite for link in offering.unit.prerequisite_links.select_related("prerequisite")],
        "entries": offering.timetable_entries.select_related("venue").order_by("day_of_week", "start_time"),
        "taken": selectors.seats_taken(offering),
        "eligible_programs": list(offering.eligible_programs.all()),
        "can_override": is_allowed(request.user, "can_override_registration"),
        "override_form": OverrideForm(),
        "can_view_class_list": is_allowed(request.user, "can_view_class_list", offering),
    }
    if student is not None:
        context["registration"] = UnitRegistration.objects.filter(
            student=student, offering=offering, status=RegistrationStatus.REGISTERED).first()
        context["problems"] = services.eligibility_problems(student, offering)
    return render(request, "academics/offering_detail.html", context)


@role_required(Role.STUDENT)
@require_POST
def register_view(request, offering_id):
    student = _student(request)
    if student is None:
        raise Http404
    offering = get_in_scope_or_404(request.user, "can_browse_units", selectors.browsable_offerings(),
                                   pk=offering_id)
    try:
        services.register(request.user, student, offering, RequestContext.from_request(request))
        messages.success(request, f"You are registered for {offering.unit.code}.")
    except services.RegistrationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("academics:offering_detail", offering_id=offering.pk)


@role_required(Role.STUDENT)
def my_units_view(request):
    student = _student(request)
    if student is None:
        raise Http404
    semester = selectors.current_semester()
    current = list(selectors.registrations_for_student(student, semester)) if semester else []
    return render(request, "academics/my_units.html", {
        "semester": semester,
        "current": [r for r in current if r.status == RegistrationStatus.REGISTERED],
        "credits": sum(r.unit.credit_hours for r in current if r.status == RegistrationStatus.REGISTERED),
        "history": selectors.history_for_student(student).exclude(semester=semester) if semester else
        selectors.history_for_student(student),
        "program": student.program,
        "drop_open": semester is not None and timezone.now() <= semester.add_drop_deadline,
    })


@role_required(Role.STUDENT)
@require_POST
def drop_view(request, registration_id):
    student = _student(request)
    if student is None:
        raise Http404
    # Only the student's own registrations are even looked up (another student's id is a 404).
    registration = UnitRegistration.objects.filter(pk=registration_id, student=student).first()
    if registration is None:
        raise Http404
    try:
        services.drop(request.user, registration, RequestContext.from_request(request))
        messages.success(request, f"{registration.unit.code} was dropped.")
    except services.RegistrationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("academics:my_units")


@role_required(Role.STAFF)
def my_teaching_view(request):
    staff = getattr(request.user, "staff_profile", None)
    semester = selectors.current_semester()
    return render(request, "academics/my_teaching.html", {
        "semester": semester,
        "offerings": selectors.teaching_offerings(staff, semester),
        "past": selectors.teaching_offerings(staff).exclude(semester=semester) if semester else [],
    })


@login_required
def class_list_view(request, offering_id):
    ctx = RequestContext.from_request(request)
    offering = get_in_scope_or_404(request.user, "can_view_class_list",
                                   UnitOffering.objects.select_related("unit", "semester", "lecturer__user"),
                                   ctx=ctx, pk=offering_id)
    registrations = list(selectors.class_list(offering))
    can_grade = bool(registrations) and is_allowed(request.user, "can_record_grade", registrations[0])
    return render(request, "academics/class_list.html", {
        "offering": offering, "registrations": registrations, "can_grade": can_grade, "grade_form": GradeForm(),
    })


@login_required
@require_POST
def record_grade_view(request, registration_id):
    ctx = RequestContext.from_request(request)
    registration = get_in_scope_or_404(request.user, "can_record_grade",
                                       UnitRegistration.objects.select_related("offering__lecturer", "unit"),
                                       ctx=ctx, pk=registration_id)
    form = GradeForm(request.POST)
    if form.is_valid() and form.cleaned_data["grade"]:
        try:
            services.record_grade(request.user, registration, form.cleaned_data["grade"], ctx)
            messages.success(request, f"Grade recorded for {registration.student.student_number}.")
        except services.RegistrationError as exc:
            messages.error(request, exc.messages[0])
    else:
        messages.error(request, "Choose a grade.")
    return redirect("academics:class_list", offering_id=registration.offering_id)


@login_required
@require_http_methods(["POST"])
def override_view(request, offering_id):
    """Registrar registers or drops a student outside the window (other rules still apply; reason audited)."""
    ctx = RequestContext.from_request(request)
    if not is_allowed(request.user, "can_override_registration"):
        deny(request.user, "can_override_registration", ctx=ctx)
    offering = get_in_scope_or_404(request.user, "can_browse_units", selectors.browsable_offerings(),
                                   pk=offering_id)
    form = OverrideForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter the student number and a reason.")
        return redirect("academics:offering_detail", offering_id=offering.pk)
    student = StudentProfile.objects.filter(student_number__iexact=form.cleaned_data["student_number"].strip()).first()
    if student is None:
        messages.error(request, "No student with that number.")
        return redirect("academics:offering_detail", offering_id=offering.pk)
    reason = form.cleaned_data["reason"]
    try:
        if request.POST.get("action") == "drop":
            registration = UnitRegistration.objects.filter(student=student, offering=offering,
                                                           status=RegistrationStatus.REGISTERED).first()
            if registration is None:
                raise services.RegistrationError("That student is not registered for this offering.")
            services.drop(request.user, registration, ctx, override_reason=reason)
            messages.success(request, f"{student.student_number} was dropped from {offering.unit.code}.")
        else:
            services.register(request.user, student, offering, ctx, override_reason=reason)
            messages.success(request, f"{student.student_number} was registered for {offering.unit.code}.")
    except services.RegistrationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("academics:offering_detail", offering_id=offering.pk)
