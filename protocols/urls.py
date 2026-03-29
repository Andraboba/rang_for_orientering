# -*- coding: utf-8 -*-
from django.urls import path
from . import views

app_name = 'protocols'

urlpatterns = [
    path('', views.home, name='home'),
    path('participants/', views.known_participants, name='known_participants'),
    path('protocol/<int:pk>/', views.protocol_detail, name='protocol_detail'),
    path('protocol/<int:pk>/export/', views.export_results, name='export_results'),
    path('protocol/<int:pk>/delete/', views.delete_protocol, name='delete_protocol'),
    path('protocol/<int:pk>/set-title/', views.set_protocol_title, name='set_protocol_title'),
    path('protocol/<int:pk>/set-event-date/', views.set_protocol_event_date, name='set_protocol_event_date'),
    path('season-export/', views.season_export_page, name='season_export_page'),
    path('season-export-excel/', views.export_season_excel_view, name='export_season_excel'),
]
