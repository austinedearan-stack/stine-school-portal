"""Read-mostly JSON API (ARCHITECTURE.md D2): session authentication + CSRF, same selectors and rules as the UI.

Every endpoint returns only the caller's own data (identity from the session, never from a parameter).
"""

from django.http import Http404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics import selectors as academics
from apps.api import serializers
from apps.core.authz.drf import IsActiveUser, role_permission
from apps.core.capabilities import Role
from apps.notifications import selectors as notification_selectors
from apps.notifications import services as notification_services
from apps.timetable import selectors as timetable


class MeView(APIView):
    permission_classes = [IsActiveUser]

    def get(self, request):
        return Response(serializers.MeSerializer(request.user).data)


class MyUnitsView(generics.ListAPIView):
    permission_classes = [role_permission(Role.STUDENT)]
    serializer_class = serializers.RegistrationSerializer

    def get_queryset(self):
        student = getattr(self.request.user, "student_profile", None)
        return academics.current_registrations(student) if student else academics.UnitRegistration.objects.none()


class MyTimetableView(generics.ListAPIView):
    permission_classes = [IsActiveUser]
    serializer_class = serializers.TimetableEntrySerializer
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        semester = academics.current_semester()
        if user.role == Role.STUDENT and hasattr(user, "student_profile"):
            return timetable.entries_for_student(user.student_profile, semester)
        return timetable.entries_for_lecturer(getattr(user, "staff_profile", None), semester)


class CatalogueView(generics.ListAPIView):
    permission_classes = [IsActiveUser]
    serializer_class = serializers.OfferingSerializer

    def get_queryset(self):
        query = (self.request.query_params.get("q") or "")[:100]
        return academics.catalogue(academics.current_semester(), query=query.strip())


class NotificationsView(generics.ListAPIView):
    permission_classes = [IsActiveUser]
    serializer_class = serializers.NotificationSerializer

    def get_queryset(self):
        return notification_selectors.notifications_for(self.request.user)


class NotificationReadView(APIView):
    permission_classes = [IsActiveUser]

    def post(self, request, notification_id):
        if notification_services.mark_read(request.user, notification_id) is None:
            raise Http404
        return Response(status=status.HTTP_204_NO_CONTENT)
