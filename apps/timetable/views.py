"""Timetables: personal (student/lecturer), master (everyone), management (manage_timetable)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.academics.selectors import current_semester
from apps.core.authz import get_in_scope_or_404
from apps.core.authz.decorators import capability_required
from apps.core.capabilities import Role
from apps.core.context import RequestContext
from apps.timetable import selectors, services
from apps.timetable.forms import EntryForm, MasterFilterForm, VenueForm
from apps.timetable.models import TimetableEntry, Venue


def _by_day(entries):
    days: dict[int, list] = {}
    for entry in entries:
        days.setdefault(entry.day_of_week, []).append(entry)
    return [(items[0].get_day_of_week_display(), items) for _, items in sorted(days.items())]


@login_required
def my_timetable_view(request):
    semester = current_semester()
    user = request.user
    if user.role == Role.STUDENT and hasattr(user, "student_profile"):
        entries = selectors.entries_for_student(user.student_profile, semester)
        heading = "My timetable"
    elif user.role == Role.STAFF and hasattr(user, "staff_profile"):
        entries = selectors.entries_for_lecturer(user.staff_profile, semester)
        heading = "My teaching timetable"
    else:
        return redirect("timetable:master")
    return render(request, "timetable/my_timetable.html",
                  {"semester": semester, "days": _by_day(entries), "heading": heading})


@login_required
def master_view(request):
    semester = current_semester()
    form = MasterFilterForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    entries = selectors.master(semester, department=filters.get("department"), venue=filters.get("venue"),
                               day=filters.get("day"), group=filters.get("group"))
    return render(request, "timetable/master.html", {"semester": semester, "form": form, "days": _by_day(entries)})


@capability_required("manage_timetable")
def manage_view(request):
    semester = current_semester()
    return render(request, "timetable/manage.html", {
        "semester": semester,
        "entries": selectors.master(semester),
        "venues": Venue.objects.order_by("code"),
    })


@capability_required("manage_timetable")
@require_http_methods(["GET", "POST"])
def entry_form_view(request, entry_id=None):
    ctx = RequestContext.from_request(request)
    entry = None
    if entry_id is not None:
        entry = get_in_scope_or_404(request.user, "can_manage_timetable", TimetableEntry.objects.all(), ctx=ctx,
                                    pk=entry_id)
    form = EntryForm(request.POST or None, instance=entry, semester=current_semester())
    if request.method == "POST" and form.is_valid():
        try:
            if entry is None:
                services.create_entry(request.user, form.cleaned_data, ctx)
            else:
                services.update_entry(request.user, entry, form.cleaned_data, ctx)
            messages.success(request, "Timetable saved. Registered students and the lecturer were notified.")
            return redirect("timetable:manage")
        except services.TimetableConflict as exc:
            for message in exc.messages:
                form.add_error(None, message)
    return render(request, "timetable/entry_form.html", {"form": form, "entry": entry})


@capability_required("manage_timetable")
@require_POST
def entry_delete_view(request, entry_id):
    ctx = RequestContext.from_request(request)
    entry = get_in_scope_or_404(request.user, "can_manage_timetable", TimetableEntry.objects.all(), ctx=ctx,
                                pk=entry_id)
    services.delete_entry(request.user, entry, ctx)
    messages.success(request, "Timetable entry removed.")
    return redirect("timetable:manage")


@capability_required("manage_timetable")
@require_http_methods(["GET", "POST"])
def venue_form_view(request, venue_id=None):
    ctx = RequestContext.from_request(request)
    venue = None
    if venue_id is not None:
        venue = get_in_scope_or_404(request.user, "can_manage_timetable", Venue.objects.all(), ctx=ctx, pk=venue_id)
    form = VenueForm(request.POST or None, instance=venue)
    if request.method == "POST" and form.is_valid():
        services.save_venue(request.user, form.save(commit=False), ctx)
        messages.success(request, "Venue saved.")
        return redirect("timetable:manage")
    return render(request, "timetable/venue_form.html", {"form": form, "venue": venue})
