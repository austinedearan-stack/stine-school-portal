import os
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from apps.requests.models import StudentRequest, RequestCategory, RequestMessage, RequestAttachment, TransferRequest
from apps.academics.models import Program
from apps.core.permissions import can_view_request, ROLE_STUDENT, ADMIN_ROLES
from apps.core.utils import (
    log_audit_event,
    get_client_ip,
    validate_file_security,
    ALLOWED_DOCUMENT_EXTENSIONS,
    ALLOWED_DOCUMENT_MIMES,
    MAX_ATTACHMENT_SIZE,
)

@login_required
def student_request_list_view(request):
    """
    Lists only the calling student's requests.
    """
    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        return redirect('administration:requests')

    requests_qs = StudentRequest.objects.filter(
        student=request.user.student_profile
    ).select_related('category').order_by('-updated_at')

    transfer_requests_qs = TransferRequest.objects.filter(
        student=request.user.student_profile
    ).select_related('current_program', 'requested_program').order_by('-created_at')

    return render(request, 'requests/student_list.html', {
        'requests': requests_qs,
        'transfer_requests': transfer_requests_qs,
    })


@login_required
def create_request_view(request):
    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        raise PermissionDenied("Only students can submit student requests.")

    categories = RequestCategory.objects.filter(is_active=True)

    if request.method == 'POST':
        category_id = request.POST.get('category_id')
        subject = request.POST.get('subject', '').strip()
        description = request.POST.get('description', '').strip()
        priority = request.POST.get('priority', 'NORMAL')
        uploaded_file = request.FILES.get('attachment')

        if not subject or not description or not category_id:
            messages.error(request, "Please fill in all required fields.")
            return render(request, 'requests/create_request.html', {'categories': categories})

        category = get_object_or_404(RequestCategory, id=category_id, is_active=True)

        # Validate file if provided
        if uploaded_file:
            try:
                validate_file_security(
                    uploaded_file=uploaded_file,
                    allowed_extensions=ALLOWED_DOCUMENT_EXTENSIONS,
                    allowed_mimes=ALLOWED_DOCUMENT_MIMES,
                    max_size_bytes=MAX_ATTACHMENT_SIZE
                )
            except ValidationError as e:
                messages.error(request, e.message)
                return render(request, 'requests/create_request.html', {'categories': categories})

        ticket = StudentRequest.objects.create(
            ticket_number=StudentRequest.generate_ticket_number(),
            student=request.user.student_profile,
            category=category,
            subject=subject,
            description=description,
            priority=priority,
            status='SUBMITTED'
        )

        if uploaded_file:
            RequestAttachment.objects.create(
                request=ticket,
                file=uploaded_file,
                original_filename=os.path.basename(uploaded_file.name),
                uploaded_by=request.user
            )

        log_audit_event(
            actor=request.user,
            action='CREATE_REQUEST',
            target_model='StudentRequest',
            target_id=str(ticket.id),
            changes={'ticket_number': ticket.ticket_number, 'category': category.name},
            ip_address=get_client_ip(request),
            details=f"Student created ticket {ticket.ticket_number}"
        )

        messages.success(request, f"Request {ticket.ticket_number} submitted successfully!")
        return redirect('requests:detail', ticket_number=ticket.ticket_number)

    return render(request, 'requests/create_request.html', {'categories': categories})


@login_required
def request_detail_view(request, ticket_number):
    """
    IDOR-safe detail view: strictly ensures student can only access their own tickets.
    """
    ticket = get_object_or_404(
        StudentRequest.objects.select_related('student__user', 'category', 'assigned_to__user'),
        ticket_number=ticket_number
    )

    # Server-side authorization check
    if not can_view_request(request.user, ticket):
        raise PermissionDenied("You are not authorized to view this ticket.")

    is_student_caller = (request.user.role == ROLE_STUDENT)
    
    # Hide internal staff notes from students
    messages_qs = ticket.messages.select_related('sender')
    if is_student_caller:
        messages_qs = messages_qs.filter(is_internal_note=False)

    attachments = ticket.attachments.select_related('uploaded_by')

    # Handle student or staff reply
    if request.method == 'POST':
        reply_text = request.POST.get('message', '').strip()
        uploaded_file = request.FILES.get('attachment')

        if reply_text:
            if uploaded_file:
                try:
                    validate_file_security(
                        uploaded_file=uploaded_file,
                        allowed_extensions=ALLOWED_DOCUMENT_EXTENSIONS,
                        allowed_mimes=ALLOWED_DOCUMENT_MIMES,
                        max_size_bytes=MAX_ATTACHMENT_SIZE
                    )
                except ValidationError as e:
                    messages.error(request, e.message)
                    return redirect('requests:detail', ticket_number=ticket.ticket_number)

            msg = RequestMessage.objects.create(
                request=ticket,
                sender=request.user,
                message=reply_text,
                is_internal_note=False
            )

            if uploaded_file:
                RequestAttachment.objects.create(
                    request=ticket,
                    message=msg,
                    file=uploaded_file,
                    original_filename=os.path.basename(uploaded_file.name),
                    uploaded_by=request.user
                )

            log_audit_event(
                actor=request.user,
                action='REPLY_REQUEST',
                target_model='StudentRequest',
                target_id=str(ticket.id),
                ip_address=get_client_ip(request)
            )

            messages.success(request, "Response posted successfully.")
            return redirect('requests:detail', ticket_number=ticket.ticket_number)

    return render(request, 'requests/request_detail.html', {
        'ticket': ticket,
        'messages_list': messages_qs,
        'attachments': attachments,
        'is_student_caller': is_student_caller,
    })


@login_required
def download_attachment_view(request, attachment_id):
    """
    IDOR-safe attachment download endpoint.
    Verifies that the caller owns the ticket or is authorized staff/admin.
    """
    attachment = get_object_or_404(RequestAttachment.objects.select_related('request__student'), id=attachment_id)

    if not can_view_request(request.user, attachment.request):
        raise PermissionDenied("Access to this attachment is forbidden.")

    if not attachment.file or not os.path.exists(attachment.file.path):
        raise Http404("File not found on server.")

    response = FileResponse(open(attachment.file.path, 'rb'))
    response['Content-Disposition'] = f'attachment; filename="{attachment.original_filename}"'
    return response


@login_required
def create_transfer_request_view(request):
    """
    Dedicated university transfer request workflow.
    """
    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        raise PermissionDenied("Only active students may submit program transfers.")

    student = request.user.student_profile
    programs = Program.objects.exclude(id=student.program_id)

    if request.method == 'POST':
        transfer_type = request.POST.get('transfer_type', 'PROGRAM')
        requested_program_id = request.POST.get('requested_program_id')
        reason = request.POST.get('reason', '').strip()

        if not requested_program_id or not reason:
            messages.error(request, "Please select target program and explain your reason for transfer.")
            return render(request, 'requests/create_transfer.html', {'student': student, 'programs': programs})

        requested_program = get_object_or_404(Program, id=requested_program_id)

        transfer = TransferRequest.objects.create(
            ticket_number=TransferRequest.generate_ticket_number(),
            student=student,
            transfer_type=transfer_type,
            current_program=student.program,
            requested_program=requested_program,
            reason=reason,
            status='SUBMITTED'
        )

        log_audit_event(
            actor=request.user,
            action='CREATE_TRANSFER_REQUEST',
            target_model='TransferRequest',
            target_id=str(transfer.id),
            changes={
                'ticket_number': transfer.ticket_number,
                'from': student.program.code,
                'to': requested_program.code
            },
            ip_address=get_client_ip(request)
        )

        messages.success(request, f"Transfer request {transfer.ticket_number} submitted for academic board evaluation.")
        return redirect('requests:my_requests')

    return render(request, 'requests/create_transfer.html', {
        'student': student,
        'programs': programs,
    })
