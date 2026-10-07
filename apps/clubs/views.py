from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from apps.clubs.models import Club, ClubMembership, ClubEvent
from apps.core.permissions import ROLE_STUDENT
from apps.core.utils import log_audit_event, get_client_ip

@login_required
def club_directory_view(request):
    category = request.GET.get('cat', '').strip()
    query = request.GET.get('q', '').strip()

    clubs = Club.objects.filter(is_active=True).prefetch_related('memberships')
    if category:
        clubs = clubs.filter(category=category)
    if query:
        clubs = clubs.filter(name__icontains=query)

    my_memberships = {}
    if request.user.role == ROLE_STUDENT and hasattr(request.user, 'student_profile'):
        memberships = ClubMembership.objects.filter(student=request.user.student_profile)
        for m in memberships:
            my_memberships[m.club_id] = m.status

    return render(request, 'clubs/directory.html', {
        'clubs': clubs,
        'selected_category': category,
        'categories': Club.CATEGORY_CHOICES,
        'query': query,
        'my_memberships': my_memberships,
    })


@login_required
def club_detail_view(request, club_code):
    club = get_object_or_404(Club, code=club_code, is_active=True)
    events = ClubEvent.objects.filter(club=club, event_date__gte=timezone.now()).order_by('event_date')[:5]

    membership = None
    if request.user.role == ROLE_STUDENT and hasattr(request.user, 'student_profile'):
        membership = ClubMembership.objects.filter(student=request.user.student_profile, club=club).first()

    return render(request, 'clubs/club_detail.html', {
        'club': club,
        'events': events,
        'membership': membership,
    })


@login_required
def join_club_view(request):
    if request.method != 'POST':
        return redirect('clubs:directory')

    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        raise PermissionDenied("Only students may join clubs.")

    club_id = request.POST.get('club_id')
    club = get_object_or_404(Club, id=club_id, is_active=True)

    membership, created = ClubMembership.objects.get_or_create(
        student=request.user.student_profile,
        club=club,
        defaults={'status': 'APPROVED'}  # Direct auto-join or pending
    )

    if not created and membership.status == 'LEFT':
        membership.status = 'APPROVED'
        membership.save(update_fields=['status'])
        messages.success(request, f"Re-joined {club.name} successfully!")
    elif created:
        messages.success(request, f"Joined {club.name} successfully!")
    else:
        messages.info(request, f"You are already a member of {club.name}.")

    log_audit_event(
        actor=request.user,
        action='JOIN_CLUB',
        target_model='ClubMembership',
        target_id=str(membership.id),
        changes={'club': club.name},
        ip_address=get_client_ip(request)
    )

    return redirect('clubs:detail', club_code=club.code)


@login_required
def leave_club_view(request):
    if request.method != 'POST':
        return redirect('clubs:directory')

    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        raise PermissionDenied("Only students can perform this action.")

    club_id = request.POST.get('club_id')
    membership = get_object_or_404(
        ClubMembership,
        student=request.user.student_profile,
        club_id=club_id,
        status='APPROVED'
    )

    membership.status = 'LEFT'
    membership.save(update_fields=['status'])

    log_audit_event(
        actor=request.user,
        action='LEAVE_CLUB',
        target_model='ClubMembership',
        target_id=str(membership.id),
        changes={'club': membership.club.name},
        ip_address=get_client_ip(request)
    )

    messages.info(request, f"You have left {membership.club.name}.")
    return redirect('clubs:my_clubs')


@login_required
def my_clubs_view(request):
    if request.user.role != ROLE_STUDENT or not hasattr(request.user, 'student_profile'):
        return redirect('clubs:directory')

    memberships = ClubMembership.objects.filter(
        student=request.user.student_profile,
        status='APPROVED'
    ).select_related('club')

    return render(request, 'clubs/my_clubs.html', {
        'memberships': memberships,
    })
