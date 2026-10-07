from django.urls import path

from apps.academics import views

app_name = 'academics'

urlpatterns = [
    path('catalog/', views.unit_catalog_view, name='catalog'),
    path('units/<str:unit_code>/', views.unit_detail_view, name='unit_detail'),
    path('register/', views.register_unit_view, name='register_unit'),
    path('drop/', views.drop_unit_view, name='drop_unit'),
    path('my-units/', views.my_units_view, name='my_units'),
]
