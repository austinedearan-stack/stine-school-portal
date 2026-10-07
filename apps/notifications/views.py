"""Notification inbox and announcements (Phase 11)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.authz import get_in_scope_or_404, has_capability, is_allowed
from apps.core.authz.decorators import capability_required
from apps.core.capabilities import Role
from apps.core.context import RequestContext
from apps.core.files import protected_file_response
from apps.notifications import selectors, services
from apps.notifications.forms import AnnouncementForm
from apps.notifications.models import Announcement, AnnouncementAttachment, AnnouncementStatus


@login_required
def inbox_view(request):
    page = Paginator(selectors.notifications_for(request.user), 25).get_page(request.GET.get("page"))
    items = [(n, services.link_for(n)) for n in page.object_list]
    return render(request, "notifications/inbox.html", {"page": page, "items": items})


@login_required
@require_POST
def open_view(request, notification_id):
    """Mark read (state change, so POST - audit Z-10) and go to the notification's internal link."""
    notification = services.mark_read(request.user, notification_id)
    if notification is None:
        raise Http404
    return redirect(services.link_for(notification) or "notifications:list")


@login_required
@require_POST
def mark_all_read_view(request):
    count = services.mark_all_read(request.user)
    messages.success(request, f"Marked {count} notification{'s' if count != 1 else ''} as read.")
    return redirect("notifications:list")


# --- Announcements --------------------------------------------------------------------------------


@login_required
def announcements_view(request):
    page = Paginator(selectors.visible_announcements(request.user), 20).get_page(request.GET.get("page"))
    return render(request, "notifications/announcements.html", {
        "page": page, "can_publish": has_capability(request.user, "publish_announcements")})


@login_required
def announcement_view(request, announcement_id):
    announcement = get_in_scope_or_404(request.user, "can_view_announcement",
                                       Announcement.objects.select_related("author"),
                                       ctx=RequestContext.from_request(request), pk=announcement_id)
    return render(request, "notifications/announcement.html", {
        "announcement": announcement, "attachments": announcement.attachments.select_related("file"),
        "can_manage": is_allowed(request.user, "can_manage_announcement", announcement)})


@login_required
def announcement_attachment_view(request, attachment_id):
    attachment = AnnouncementAttachment.objects.select_related("announcement", "file").filter(pk=attachment_id).first()
    if attachment is None or not is_allowed(request.user, "can_view_announcement", attachment.announcement):
        raise Http404
    return protected_file_response(attachment.file, as_attachment=True)


@capability_required("publish_announcements")
@require_http_methods(["GET", "POST"])
def announcement_form_view(request, announcement_id=None):
    ctx = RequestContext.from_request(request)
    announcement = get_in_scope_or_404(request.user, "can_manage_announcement", Announcement.objects.all(), ctx=ctx,
                                       pk=announcement_id) if announcement_id else None
    if announcement is not None and announcement.status == AnnouncementStatus.WITHDRAWN:
        messages.error(request, "Withdrawn announcements cannot be edited.")
        return redirect("notifications:announcement", announcement_id=announcement.pk)
    form = AnnouncementForm(request.POST or None, request.FILES or None, instance=announcement, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            saved = services.save_announcement(request.user, form.save(commit=False), ctx, recipients=form.recipients,
                                               uploads=form.cleaned_data["attachments"],
                                               publish=request.POST.get("action") == "publish")
            messages.success(request, "Announcement published." if saved.status == AnnouncementStatus.PUBLISHED
                             else "Draft saved.")
            return redirect("notifications:announcement", announcement_id=saved.pk)
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
    return render(request, "notifications/announcement_form.html", {"form": form, "announcement": announcement})


@capability_required("publish_announcements")
@require_POST
def announcement_withdraw_view(request, announcement_id):
    ctx = RequestContext.from_request(request)
    announcement = get_in_scope_or_404(request.user, "can_manage_announcement", Announcement.objects.all(), ctx=ctx,
                                       pk=announcement_id)
    services.withdraw(request.user, announcement, ctx)
    messages.success(request, "Announcement withdrawn.")
    return redirect("notifications:manage")


@capability_required("publish_announcements")
def manage_view(request):
    qs = Announcement.objects.select_related("author").order_by("-created_at")
    if request.user.role not in Role.ADMINS:
        qs = qs.filter(author=request.user)
    return render(request, "notifications/manage.html", {"page": Paginator(qs, 25).get_page(request.GET.get("page"))})
