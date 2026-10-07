"""Hostels: browsing (everyone), booking/applications (students), accommodation office (manage_hostels)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.academics.selectors import current_semester
from apps.accounts.models import StudentProfile
from apps.core.authz import get_in_scope_or_404
from apps.core.authz.decorators import capability_required, role_required
from apps.core.capabilities import Role
from apps.core.context import RequestContext
from apps.hostels import selectors, services
from apps.hostels.forms import (
    ApplicationForm,
    BedChoiceForm,
    DecisionForm,
    HostelForm,
    OfferForm,
    RoomBatchForm,
    SpaceStatusForm,
    WindowForm,
)
from apps.hostels.models import (
    ApplicationStatus,
    Bed,
    BookingMode,
    Hostel,
    HostelAllocation,
    HostelApplication,
    HostelBookingWindow,
    Room,
)


def _student(request):
    return getattr(request.user, "student_profile", None) if request.user.role == Role.STUDENT else None


# --- Browsing -------------------------------------------------------------------------------------


@login_required
def catalog_view(request):
    semester = current_semester()
    return render(request, "hostels/catalog.html", {
        "semester": semester,
        "hostels": selectors.hostel_catalogue(semester),
        "direct_window": services.open_window(semester, BookingMode.DIRECT_BOOKING),
        "application_window": services.open_window(semester, BookingMode.APPLICATION),
    })


@login_required
def hostel_detail_view(request, hostel_id):
    hostel = Hostel.objects.filter(pk=hostel_id, is_active=True).first()
    if hostel is None:
        raise Http404
    semester = current_semester()
    student = _student(request)
    can_book = (
        student is not None and services.open_window(semester, BookingMode.DIRECT_BOOKING) is not None
        and services.gender_allowed(student, hostel) and selectors.current_allocation(student, semester) is None
    )
    beds = selectors.free_beds(semester, hostel) if semester else Bed.objects.none()
    return render(request, "hostels/hostel_detail.html", {
        "hostel": hostel, "semester": semester, "beds": beds, "can_book": can_book,
        "gender_ok": student is None or services.gender_allowed(student, hostel),
    })


# --- Student --------------------------------------------------------------------------------------


@role_required(Role.STUDENT)
def my_hostel_view(request):
    student = _student(request)
    if student is None:
        raise Http404
    semester = current_semester()
    services.expire_stale_offers(student=student)
    return render(request, "hostels/my_hostel.html", {
        "semester": semester,
        "allocation": selectors.current_allocation(student, semester),
        "application": selectors.open_application(student, semester),
        "history": selectors.history(student)[:10],
        "application_window": services.open_window(semester, BookingMode.APPLICATION),
    })


@role_required(Role.STUDENT)
@require_POST
def book_view(request, bed_id):
    student = _student(request)
    bed = Bed.objects.filter(pk=bed_id).select_related("room__floor__building__hostel").first()
    if student is None or bed is None:
        raise Http404
    try:
        services.book_bed(request.user, student, bed, current_semester(), RequestContext.from_request(request))
        messages.success(request, f"You booked {bed}.")
        return redirect("hostels:my_hostel")
    except services.HostelError as exc:
        messages.error(request, exc.messages[0])
        return redirect("hostels:detail", hostel_id=bed.room.floor.building.hostel_id)


@role_required(Role.STUDENT)
@require_http_methods(["GET", "POST"])
def apply_view(request):
    student = _student(request)
    if student is None:
        raise Http404
    form = ApplicationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.apply(request.user, student, current_semester(),
                           [data["first_choice"], data.get("second_choice"), data.get("third_choice")],
                           data.get("room_type", ""), data.get("special_needs", ""), RequestContext.from_request(request))
            messages.success(request, "Your hostel application was submitted.")
            return redirect("hostels:my_hostel")
        except services.HostelError as exc:
            form.add_error(None, exc.messages[0])
    return render(request, "hostels/apply.html", {"form": form})


@role_required(Role.STUDENT)
@require_POST
def respond_view(request, allocation_id):
    student = _student(request)
    allocation = HostelAllocation.objects.filter(pk=allocation_id, student=student).first() if student else None
    if allocation is None:
        raise Http404
    accept = request.POST.get("decision") == "accept"
    try:
        services.respond_to_offer(request.user, allocation, accept, RequestContext.from_request(request))
        messages.success(request, "Offer accepted. Welcome!" if accept else "Offer declined.")
    except services.HostelError as exc:
        messages.error(request, exc.messages[0])
    return redirect("hostels:my_hostel")


@role_required(Role.STUDENT)
@require_POST
def cancel_view(request):
    student = _student(request)
    if student is None:
        raise Http404
    try:
        services.cancel(request.user, student, current_semester(), RequestContext.from_request(request))
        messages.success(request, "Your hostel booking/application was cancelled.")
    except services.HostelError as exc:
        messages.error(request, exc.messages[0])
    return redirect("hostels:my_hostel")


# --- Accommodation office -------------------------------------------------------------------------


@capability_required("manage_hostels")
def manage_view(request):
    semester = current_semester()
    status = request.GET.get("status") or ""
    return render(request, "hostels/manage.html", {
        "semester": semester,
        "applications": selectors.applications_for_review(semester, status if status in ApplicationStatus.values else None)
        if semester else [],
        "allocations": selectors.allocations_for_semester(semester) if semester else [],
        "windows": HostelBookingWindow.objects.filter(semester=semester) if semester else [],
        "hostels": Hostel.objects.order_by("name"),
        "statuses": ApplicationStatus.choices, "status": status,
    })


@capability_required("manage_hostels")
@require_http_methods(["GET", "POST"])
def application_view(request, application_id):
    ctx = RequestContext.from_request(request)
    application = get_in_scope_or_404(
        request.user, "can_view_hostel_application",
        HostelApplication.objects.select_related("student__user", "student__program").prefetch_related(
            "preferences__hostel"), ctx=ctx, pk=application_id)
    semester = application.semester
    preferred = [p.hostel for p in application.preferences.all()]
    beds = selectors.free_beds(semester).filter(room__floor__building__hostel__in=preferred)
    decision_form = DecisionForm(request.POST if request.POST.get("form") == "decision" else None)
    offer_form = OfferForm(request.POST if request.POST.get("form") == "offer" else None, beds=beds)
    if request.method == "POST":
        try:
            if decision_form.is_bound and decision_form.is_valid():
                services.decide_application(request.user, application, decision_form.cleaned_data["status"],
                                            decision_form.cleaned_data["note"], ctx)
                messages.success(request, "Decision saved.")
                return redirect("hostels:application", application_id=application.pk)
            if offer_form.is_bound and offer_form.is_valid():
                services.offer_bed(request.user, application.student, offer_form.cleaned_data["bed"], semester, ctx,
                                   application=application)
                messages.success(request, "Offer sent to the student.")
                return redirect("hostels:manage")
        except services.HostelError as exc:
            messages.error(request, exc.messages[0])
    return render(request, "hostels/application.html", {
        "application": application, "preferred": preferred, "decision_form": decision_form,
        "offer_form": offer_form, "free_count": beds.count(),
    })


@capability_required("manage_hostels")
@require_http_methods(["GET", "POST"])
def direct_offer_view(request):
    """Offer a bed to a student who did not apply (e.g. special arrangements)."""
    semester = current_semester()
    form = OfferForm(request.POST or None, beds=selectors.free_beds(semester) if semester else Bed.objects.none())
    if request.method == "POST" and form.is_valid():
        student = StudentProfile.objects.filter(
            student_number__iexact=(form.cleaned_data.get("student_number") or "").strip()).first()
        if student is None:
            form.add_error("student_number", "No student with that number.")
        else:
            try:
                services.offer_bed(request.user, student, form.cleaned_data["bed"], semester,
                                   RequestContext.from_request(request))
                messages.success(request, "Offer sent to the student.")
                return redirect("hostels:manage")
            except services.HostelError as exc:
                form.add_error(None, exc.messages[0])
    return render(request, "hostels/simple_form.html", {"form": form, "title": "Offer a bed",
                                                        "intro": "The student must accept within the window's acceptance time."})


@capability_required("manage_hostels")
@require_http_methods(["GET", "POST"])
def transfer_view(request, allocation_id):
    ctx = RequestContext.from_request(request)
    allocation = get_in_scope_or_404(request.user, "can_manage_hostels", HostelAllocation.objects.select_related(
        "student__user", "bed"), ctx=ctx, pk=allocation_id)
    form = BedChoiceForm(request.POST or None, beds=selectors.free_beds(allocation.semester))
    if request.method == "POST" and form.is_valid():
        try:
            services.transfer(request.user, allocation, form.cleaned_data["bed"], ctx)
            messages.success(request, "Student transferred.")
            return redirect("hostels:manage")
        except services.HostelError as exc:
            form.add_error(None, exc.messages[0])
    return render(request, "hostels/simple_form.html", {
        "form": form, "title": f"Transfer {allocation.student.user.full_name}", "intro": f"Currently in {allocation.bed}."})


@capability_required("manage_hostels")
@require_POST
def vacate_view(request, allocation_id):
    ctx = RequestContext.from_request(request)
    allocation = get_in_scope_or_404(request.user, "can_manage_hostels", HostelAllocation.objects.all(), ctx=ctx,
                                     pk=allocation_id)
    try:
        services.vacate(request.user, allocation, ctx)
        messages.success(request, "Allocation ended.")
    except services.HostelError as exc:
        messages.error(request, exc.messages[0])
    return redirect("hostels:manage")


@capability_required("manage_hostels")
@require_http_methods(["GET", "POST"])
def hostel_form_view(request, hostel_id=None):
    ctx = RequestContext.from_request(request)
    hostel = get_in_scope_or_404(request.user, "can_manage_hostels", Hostel.objects.all(), ctx=ctx,
                                 pk=hostel_id) if hostel_id else None
    form = HostelForm(request.POST or None, instance=hostel)
    rooms_form = RoomBatchForm(request.POST if request.POST.get("form") == "rooms" else None)
    if request.method == "POST":
        try:
            if request.POST.get("form") == "rooms" and hostel is not None and rooms_form.is_valid():
                count = services.add_rooms(request.user, hostel, **rooms_form.cleaned_data, ctx=ctx)
                messages.success(request, f"Added {count} rooms.")
                return redirect("hostels:hostel_edit", hostel_id=hostel.pk)
            if request.POST.get("form") != "rooms" and form.is_valid():
                hostel = services.save_hostel(request.user, form.save(commit=False), ctx)
                messages.success(request, "Hostel saved.")
                return redirect("hostels:hostel_edit", hostel_id=hostel.pk)
        except services.HostelError as exc:
            messages.error(request, exc.messages[0])
    rooms = Room.objects.filter(floor__building__hostel=hostel).select_related("floor__building").prefetch_related(
        "beds") if hostel else []
    return render(request, "hostels/hostel_form.html", {"form": form, "rooms_form": rooms_form, "hostel": hostel,
                                                        "rooms": rooms, "status_form": SpaceStatusForm()})


@capability_required("manage_hostels")
@require_POST
def space_status_view(request, kind, object_id):
    ctx = RequestContext.from_request(request)
    model = {"room": Room, "bed": Bed}.get(kind)
    if model is None:
        raise Http404
    obj = get_in_scope_or_404(request.user, "can_manage_hostels", model.objects.all(), ctx=ctx, pk=object_id)
    form = SpaceStatusForm(request.POST)
    if form.is_valid():
        services.set_space_status(request.user, obj, form.cleaned_data["status"], ctx)
        messages.success(request, "Status updated.")
    hostel_id = (obj.floor if kind == "room" else obj.room.floor).building.hostel_id
    return redirect("hostels:hostel_edit", hostel_id=hostel_id)


@capability_required("manage_hostels")
@require_http_methods(["GET", "POST"])
def window_form_view(request):
    semester = current_semester()
    if semester is None:
        messages.error(request, "There is no current semester.")
        return redirect("hostels:manage")
    form = WindowForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        window = form.save(commit=False)
        window.semester = semester
        services.save_window(request.user, window, RequestContext.from_request(request))
        messages.success(request, "Booking window saved.")
        return redirect("hostels:manage")
    return render(request, "hostels/simple_form.html", {"form": form, "title": "New booking window",
                                                        "intro": f"For {semester}."})
