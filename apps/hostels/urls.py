from django.urls import path

from apps.hostels import views

app_name = 'hostels'

urlpatterns = [
    path('catalog/', views.hostel_catalog_view, name='catalog'),
    path('catalog/<str:hostel_code>/', views.hostel_rooms_view, name='rooms'),
    path('book/', views.book_bed_view, name='book_bed'),
    path('my-hostel/', views.my_hostel_view, name='my_hostel'),
    path('cancel/', views.cancel_allocation_view, name='cancel_allocation'),
]
