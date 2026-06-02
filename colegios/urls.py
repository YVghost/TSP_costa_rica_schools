from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('api/colegios/', views.api_colegios, name='api_colegios'),
    path('api/ruta/', views.api_ruta, name='api_ruta'),
]
