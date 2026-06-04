from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('api/colegios/', views.api_colegios, name='api_colegios'),
    path('api/ruta/', views.api_ruta, name='api_ruta'),
    path('api/actualizar-datos/', views.api_actualizar_datos, name='api_actualizar_datos'),
    path('api/planificar-grupos/', views.api_planificar_grupos, name='api_planificar_grupos'),
]
