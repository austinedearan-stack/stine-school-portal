from django.urls import path

from apps.hostels import views

app_name = "hostels"

urlpatterns = [
    path("", views.catalog_view, name="catalog"),
    path("h/<uuid:hostel_id>/", views.hostel_detail_view, name="detail"),
    path("beds/<uuid:bed_id>/book/", views.book_view, name="book"),
    path("my/", views.my_hostel_view, name="my_hostel"),
    path("apply/", views.apply_view, name="apply"),
    path("offers/<uuid:allocation_id>/respond/", views.respond_view, name="respond"),
    path("cancel/", views.cancel_view, name="cancel"),
    path("manage/", views.manage_view, name="manage"),
    path("manage/applications/<uuid:application_id>/", views.application_view, name="application"),
    path("manage/offer/", views.direct_offer_view, name="direct_offer"),
    path("manage/allocations/<uuid:allocation_id>/transfer/", views.transfer_view, name="transfer"),
    path("manage/allocations/<uuid:allocation_id>/vacate/", views.vacate_view, name="vacate"),
    path("manage/hostels/new/", views.hostel_form_view, name="hostel_create"),
    path("manage/hostels/<uuid:hostel_id>/", views.hostel_form_view, name="hostel_edit"),
    path("manage/<str:kind>/<uuid:object_id>/status/", views.space_status_view, name="space_status"),
    path("manage/windows/new/", views.window_form_view, name="window_create"),
]
