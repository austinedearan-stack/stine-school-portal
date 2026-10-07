from django.urls import path

from apps.academics import views

app_name = "academics"

urlpatterns = [
    path("units/", views.catalogue_view, name="catalog"),
    path("offerings/<uuid:offering_id>/", views.offering_detail_view, name="offering_detail"),
    path("offerings/<uuid:offering_id>/register/", views.register_view, name="register"),
    path("offerings/<uuid:offering_id>/override/", views.override_view, name="override"),
    path("offerings/<uuid:offering_id>/class-list/", views.class_list_view, name="class_list"),
    path("my-units/", views.my_units_view, name="my_units"),
    path("registrations/<uuid:registration_id>/drop/", views.drop_view, name="drop"),
    path("registrations/<uuid:registration_id>/grade/", views.record_grade_view, name="record_grade"),
    path("my-teaching/", views.my_teaching_view, name="my_teaching"),
]
