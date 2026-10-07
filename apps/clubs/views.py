"""Clubs and societies (Phase 8)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.clubs import selectors, services
from apps.clubs.forms import AppointForm, ClubForm, DirectoryFilterForm, EventForm
from apps.clubs.models import Club, ClubEvent, ClubMembership, MembershipStatus
from apps.core.authz import deny, get_in_scope_or_404, is_allowed
from apps.core.authz.decorators import capability_required, role_required
from apps.core.capabilities import Role
from apps.core.context import RequestContext


def _student(request):
    return getattr(request.user, "student_profile", None) if request.user.role == Role.STUDENT else None


def _club_or_404(club_id, *, active_only=True):
    qs = Club.objects.select_related("advisor__user")
    if active_only:
        qs = qs.filter(is_active=True)
    club = qs.filter(pk=club_id).first()
    if club is None:
        raise Http404
    return club


@login_required
def directory_view(request):
    form = DirectoryFilterForm(request.GET or None, categories=selectors.categories())
    filters = form.cleaned_data if form.is_valid() else {}
    clubs = selectors.directory(query=(filters.get("q") or "").strip(), kind=filters.get("kind", ""),
                                category=filters.get("category", ""))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "clubs/directory.html", {
        "form": form, "page": Paginator(clubs, 24).get_page(request.GET.get("page")), "query": query.urlencode()})


@login_required
def detail_view(request, club_id):
    club = _club_or_404(club_id, active_only=not is_allowed(request.user, "can_manage_clubs_admin"))
    membership = selectors.membership_of(_student(request), club)
    insider = (membership is not None and membership.status == MembershipStatus.APPROVED) or is_allowed(
        request.user, "can_view_club_members", club)
    return render(request, "clubs/detail.html", {
        "club": club, "membership": membership,
        "events": selectors.visible_events(club, include_members_only=insider),
        "member_count": club.memberships.filter(status=MembershipStatus.APPROVED).count(),
        "can_view_members": is_allowed(request.user, "can_view_club_members", club),
        "can_manage": is_allowed(request.user, "can_manage_club", club),
        "can_events": is_allowed(request.user, "can_manage_club_events", club),
    })


@role_required(Role.STUDENT)
@require_POST
def join_view(request, club_id):
    club = _club_or_404(club_id)
    student = _student(request)
    if student is None:
        raise Http404
    try:
        membership = services.request_membership(request.user, student, club, RequestContext.from_request(request))
        messages.success(request, "Request sent to the club." if membership.status == MembershipStatus.PENDING
                         else f"You joined {club.name}.")
    except services.ClubError as exc:
        messages.error(request, exc.messages[0])
    return redirect("clubs:detail", club_id=club.pk)


@role_required(Role.STUDENT)
@require_POST
def leave_view(request, club_id):
    club = _club_or_404(club_id, active_only=False)
    membership = selectors.membership_of(_student(request), club)
    if membership is None:
        raise Http404
    services.leave(request.user, membership, RequestContext.from_request(request))
    messages.success(request, f"You left {club.name}." if membership.status == MembershipStatus.APPROVED
                     else "Your request was withdrawn.")
    return redirect("clubs:detail", club_id=club.pk)


@role_required(Role.STUDENT)
def my_clubs_view(request):
    student = _student(request)
    if student is None:
        raise Http404
    return render(request, "clubs/my_clubs.html", {"memberships": selectors.my_memberships(student)})


@login_required
def members_view(request, club_id):
    ctx = RequestContext.from_request(request)
    club = get_in_scope_or_404(request.user, "can_view_club_members", Club.objects.select_related("advisor__user"),
                               ctx=ctx, pk=club_id)
    members = list(selectors.members(club))
    pending = list(selectors.pending(club))
    return render(request, "clubs/members.html", {
        "club": club,
        "pending": [(m, is_allowed(request.user, "can_review_membership", m)) for m in pending],
        "members": [(m, is_allowed(request.user, "can_review_membership", m)) for m in members],
        "can_appoint": is_allowed(request.user, "can_manage_club", club),
        "appoint_form": AppointForm(),
    })


def _membership_or_404(membership_id):
    membership = ClubMembership.objects.select_related("club__advisor", "student__user").filter(pk=membership_id).first()
    if membership is None:
        raise Http404
    return membership


@login_required
@require_POST
def decide_view(request, membership_id):
    membership = _membership_or_404(membership_id)
    ctx = RequestContext.from_request(request)
    if not is_allowed(request.user, "can_view_club_members", membership.club):
        raise Http404  # outsiders cannot probe membership ids
    try:
        services.decide(request.user, membership, request.POST.get("decision") == "approve", ctx)
        messages.success(request, "Decision saved.")
    except services.ClubError as exc:
        messages.error(request, exc.messages[0])
    return redirect("clubs:members", club_id=membership.club_id)


@login_required
@require_POST
def remove_view(request, membership_id):
    membership = _membership_or_404(membership_id)
    if not is_allowed(request.user, "can_view_club_members", membership.club):
        raise Http404
    try:
        services.remove(request.user, membership, RequestContext.from_request(request))
        messages.success(request, "Member removed.")
    except services.ClubError as exc:
        messages.error(request, exc.messages[0])
    return redirect("clubs:members", club_id=membership.club_id)


@login_required
@require_POST
def appoint_view(request, membership_id):
    membership = _membership_or_404(membership_id)
    ctx = RequestContext.from_request(request)
    if not is_allowed(request.user, "can_view_club_members", membership.club):
        raise Http404
    form = AppointForm(request.POST)
    if form.is_valid():
        try:
            services.appoint(request.user, membership, form.cleaned_data["position"],
                             form.cleaned_data["can_manage_members"], ctx)
            messages.success(request, "Position updated.")
        except services.ClubError as exc:
            messages.error(request, exc.messages[0])
    return redirect("clubs:members", club_id=membership.club_id)


@login_required
@require_http_methods(["GET", "POST"])
def club_form_view(request, club_id=None):
    ctx = RequestContext.from_request(request)
    if club_id is None:
        if not is_allowed(request.user, "can_manage_clubs_admin"):
            deny(request.user, "can_manage_clubs_admin", ctx=ctx)
        club = None
    else:
        club = get_in_scope_or_404(request.user, "can_manage_club", Club.objects.all(), ctx=ctx, pk=club_id)
    can_set_advisor = is_allowed(request.user, "can_manage_clubs_admin")
    form = ClubForm(request.POST or None, instance=club, can_set_advisor=can_set_advisor)
    if request.method == "POST" and form.is_valid():
        club = services.save_club(request.user, form.save(commit=False), ctx, changed_fields=form.changed_data)
        messages.success(request, "Club saved.")
        return redirect("clubs:detail", club_id=club.pk)
    return render(request, "clubs/club_form.html", {"form": form, "club": club})


@login_required
@require_http_methods(["GET", "POST"])
def event_form_view(request, club_id, event_id=None):
    ctx = RequestContext.from_request(request)
    club = get_in_scope_or_404(request.user, "can_manage_club_events", Club.objects.all(), ctx=ctx, pk=club_id)
    event = ClubEvent.objects.filter(club=club, pk=event_id).first() if event_id else None
    if event_id and event is None:
        raise Http404
    form = EventForm(request.POST or None, instance=event)
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        event.club = club
        services.save_event(request.user, event, ctx)
        messages.success(request, "Event saved.")
        return redirect("clubs:detail", club_id=club.pk)
    return render(request, "clubs/event_form.html", {"form": form, "club": club, "event": event})


@capability_required("manage_clubs")
def manage_view(request):
    return render(request, "clubs/manage.html", {"clubs": Club.objects.select_related("advisor__user").order_by("name")})
