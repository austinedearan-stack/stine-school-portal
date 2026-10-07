from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.db.models import Count, Q
from apps.accounts.models import User, StudentProfile, StaffProfile
from apps.academics.models import Unit, Program, Department, Faculty
from apps.hostels.models import Bed, Room, Hostel
from apps.clubs.models import Club, ClubMembership
from apps.requests.models import StudentRequest, RequestCategory, RequestMessage, TransferRequest
from apps.core.models import AuditLog, SecurityEventLog
from apps.notifications.services import send_in_app_notification
from apps.core.permissions import (
    role_required,
    can_view_audit_logs,
    can_manage_users,
    can_approve_transfers,
    ROLE_ADMIN,
    ROLE_SUPERADMIN,
    ROLE_STAFF,
)
from apps.core.utils import log_audit_event, get_client_ip

@login_required
@role_required(ROLE_ADMIN, ROLE_SUPERADMIN)
def admin_dashboard_view(request):
    """
    Administrative Cockpit providing key institutional metrics and activity streams.
    """
    total_students = StudentProfile.objects.count()
    active_students = StudentProfile.objects.filter(academic_status='ACTIVE').count()
    total_staff = StaffProfile.objects.count()
    total_units = Unit.objects.filter(is_active=True).count()

    pending_requests_count = StudentRequest.objects.exclude(status__in=['RESOLVED', 'CLOSED', 'CANCELLED']).count()
    pending_transfers_count = TransferRequest.objects.filter(status='SUBMITTED').count()

    total_beds = Bed.objects.count()
    occupied_beds = Bed.objects.filter(is_occupied=True).count()
    available_beds = Bed.objects.filter(status='AVAILABLE', is_occupied=False).count()
    occupancy_pct = round((occupied_beds / total_beds * 100), 1) if total_beds > 0 else 0

    total_clubs = Club.objects.filter(is_active=True).count()
    total_club_members = ClubMembership.objects.filter(status='APPROVED').count()

    recent_audit_logs = AuditLog.objects.all().order_by('-timestamp')[:8]
    recent_security_events = SecurityEventLog.objects.all().order_by('-timestamp')[:8]

    return render(request, 'administration/dashboard.html', {
        'total_students': total_students,
        'active_students': active_students,
        'total_staff': total_staff,
        'total_units': total_units,
        'pending_requests_count': pending_requests_count,
        'pending_transfers_count': pending_transfers_count,
        'total_beds': total_beds,
        'occupied_beds': occupied_beds,
        'available_beds': available_beds,
        'occupancy_pct': occupancy_pct,
        'total_clubs': total_clubs,
        'total_club_members': total_club_members,
        'recent_audit_logs': recent_audit_logs,
        'recent_security_events': recent_security_events,
    })


@login_required
def staff_dashboard_view(request):
    if request.user.role != ROLE_STAFF or not hasattr(request.user, 'staff_profile'):
        return redirect('core:dashboard')

    staff = request.user.staff_profile
    assigned_units = staff.assigned_units.filter(is_active=True)
    assigned_requests = staff.assigned_requests.exclude(status__in=['RESOLVED', 'CLOSED'])

    return render(request, 'administration/staff_dashboard.html', {
        'staff': staff,
        'assigned_units': assigned_units,
        'assigned_requests': assigned_requests,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_SUPERADMIN)
def admin_requests_panel_view(request):
    """
    Administrative triage panel for student support tickets and inquiries.
    """
    category_id = request.GET.get('cat', '').strip()
    status_filter = request.GET.get('status', '').strip()
    priority_filter = request.GET.get('priority', '').strip()
    query = request.GET.get('q', '').strip()

    tickets = StudentRequest.objects.all().select_related('student__user', 'category', 'assigned_to__user')

    if category_id:
        tickets = tickets.filter(category_id=category_id)
    if status_filter:
        tickets = tickets.filter(status=status_filter)
    if priority_filter:
        tickets = tickets.filter(priority=priority_filter)
    if query:
        tickets = tickets.filter(
            Q(ticket_number__icontains=query) |
            Q(student__student_id__icontains=query) |
            Q(subject__icontains=query)
        )

    categories = RequestCategory.objects.all()

    return render(request, 'administration/requests_panel.html', {
        'tickets': tickets.order_by('-updated_at')[:100],
        'categories': categories,
        'status_choices': StudentRequest.STATUS_CHOICES,
        'priority_choices': StudentRequest.PRIORITY_CHOICES,
        'selected_cat': category_id,
        'selected_status': status_filter,
        'selected_priority': priority_filter,
        'query': query,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_SUPERADMIN)
def admin_request_detail_view(request, ticket_number):
    ticket = get_object_or_404(
        StudentRequest.objects.select_related('student__user', 'category', 'assigned_to__user'),
        ticket_number=ticket_number
    )
    staff_members = StaffProfile.objects.select_related('user', 'department')
    messages_qs = ticket.messages.select_related('sender').order_by('created_at')
    attachments = ticket.attachments.select_related('uploaded_by')

    if request.method == 'POST':
        action_type = request.POST.get('action_type')

        if action_type == 'update_status':
            new_status = request.POST.get('status')
            new_assigned_to_id = request.POST.get('assigned_to')
            internal_note = request.POST.get('internal_note', '').strip()

            prev_status = ticket.status
            ticket.status = new_status
            if new_assigned_to_id:
                ticket.assigned_to = StaffProfile.objects.filter(id=new_assigned_to_id).first()
            elif new_assigned_to_id == '':
                ticket.assigned_to = None

            if new_status in ['RESOLVED', 'CLOSED']:
                ticket.resolved_at = timezone.now()

            ticket.save()

            if internal_note:
                RequestMessage.objects.create(
                    request=ticket,
                    sender=request.user,
                    message=internal_note,
                    is_internal_note=True
                )

            # Audit logging
            log_audit_event(
                actor=request.user,
                action='ADMIN_UPDATE_REQUEST',
                target_model='StudentRequest',
                target_id=str(ticket.id),
                changes={'status': [prev_status, new_status]},
                ip_address=get_client_ip(request),
                details=f"Admin updated ticket {ticket.ticket_number} status to {new_status}"
            )

            # In-app notification to student
            send_in_app_notification(
                recipient=ticket.student.user,
                title=f"Update on Request {ticket.ticket_number}",
                message=f"Your request '{ticket.subject}' status has been updated to: {ticket.get_status_display()}.",
                notification_type='REQUEST_STATUS',
                link=f"/requests/ticket/{ticket.ticket_number}/"
            )

            messages.success(request, f"Ticket {ticket.ticket_number} updated.")
            return redirect('administration:request_detail', ticket_number=ticket.ticket_number)

        elif action_type == 'send_reply':
            reply_text = request.POST.get('reply_text', '').strip()
            if reply_text:
                RequestMessage.objects.create(
                    request=ticket,
                    sender=request.user,
                    message=reply_text,
                    is_internal_note=False
                )
                send_in_app_notification(
                    recipient=ticket.student.user,
                    title=f"Response to Request {ticket.ticket_number}",
                    message=f"An administrator replied: '{reply_text[:60]}...'",
                    notification_type='REQUEST_RESPONSE',
                    link=f"/requests/ticket/{ticket.ticket_number}/"
                )
                messages.success(request, "Official response sent to student.")
                return redirect('administration:request_detail', ticket_number=ticket.ticket_number)

    return render(request, 'administration/request_detail.html', {
        'ticket': ticket,
        'staff_members': staff_members,
        'messages_list': messages_qs,
        'attachments': attachments,
        'status_choices': StudentRequest.STATUS_CHOICES,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_SUPERADMIN)
def admin_transfers_panel_view(request):
    transfers = TransferRequest.objects.all().select_related('student__user', 'current_program', 'requested_program')
    return render(request, 'administration/transfers_panel.html', {
        'transfers': transfers,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_SUPERADMIN)
def admin_review_transfer_view(request, ticket_number):
    transfer = get_object_or_404(
        TransferRequest.objects.select_related('student__user', 'current_program', 'requested_program'),
        ticket_number=ticket_number
    )

    if request.method == 'POST':
        decision = request.POST.get('decision')
        notes = request.POST.get('review_notes', '').strip()

        if decision in ['APPROVED', 'REJECTED']:
            transfer.status = decision
            transfer.reviewer = request.user
            transfer.review_notes = notes
            transfer.reviewed_at = timezone.now()
            transfer.save()

            log_audit_event(
                actor=request.user,
                action=f'TRANSFER_{decision}',
                target_model='TransferRequest',
                target_id=str(transfer.id),
                changes={'status': decision, 'notes': notes},
                ip_address=get_client_ip(request)
            )

            send_in_app_notification(
                recipient=transfer.student.user,
                title=f"Transfer Request {transfer.ticket_number} Decision",
                message=f"Your transfer request to {transfer.requested_program.code} was {transfer.get_status_display()}.",
                notification_type='REQUEST_STATUS'
            )
            messages.success(request, f"Transfer request {transfer.ticket_number} marked as {decision}.")
            return redirect('administration:transfers')

    return render(request, 'administration/review_transfer.html', {'transfer': transfer})


@login_required
@role_required(ROLE_ADMIN, ROLE_SUPERADMIN)
def admin_execute_transfer_view(request, ticket_number):
    """
    Explicit authorized administrative operation:
    Actually alters the student's institutional academic record to the new program.
    """
    if request.method != 'POST':
        return redirect('administration:transfers')

    transfer = get_object_or_404(
        TransferRequest.objects.select_related('student__user', 'requested_program'),
        ticket_number=ticket_number,
        status='APPROVED'
    )

    old_program = transfer.student.program
    transfer.student.program = transfer.requested_program
    transfer.student.save(update_fields=['program'])

    log_audit_event(
        actor=request.user,
        action='EXECUTE_PROGRAM_TRANSFER',
        target_model='StudentProfile',
        target_id=str(transfer.student.id),
        changes={'old_program': old_program.code, 'new_program': transfer.requested_program.code},
        ip_address=get_client_ip(request),
        details=f"Academic record officially updated from {old_program.code} to {transfer.requested_program.code} per Transfer {transfer.ticket_number}."
    )

    messages.success(
        request,
        f"Student {transfer.student.student_id}'s academic record updated to {transfer.requested_program.name}."
    )
    return redirect('administration:transfers')


@login_required
@role_required(ROLE_ADMIN, ROLE_SUPERADMIN)
def admin_user_management_view(request):
    role_filter = request.GET.get('role', '').strip()
    query = request.GET.get('q', '').strip()

    users = User.objects.all().select_related('student_profile', 'staff_profile')
    if role_filter:
        users = users.filter(role=role_filter)
    if query:
        users = users.filter(Q(username__icontains=query) | Q(email__icontains=query))

    return render(request, 'administration/users_list.html', {
        'users': users.order_by('role', 'username')[:100],
        'role_choices': User.ROLE_CHOICES,
        'selected_role': role_filter,
        'query': query,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_SUPERADMIN)
def admin_audit_logs_view(request):
    action_filter = request.GET.get('action', '').strip()
    query = request.GET.get('q', '').strip()

    logs = AuditLog.objects.all().select_related('actor')
    if action_filter:
        logs = logs.filter(action=action_filter)
    if query:
        logs = logs.filter(
            Q(actor_identifier__icontains=query) |
            Q(target_model__icontains=query) |
            Q(target_id__icontains=query)
        )

    return render(request, 'administration/audit_logs.html', {
        'logs': logs.order_by('-timestamp')[:150],
        'action_filter': action_filter,
        'query': query,
    })
