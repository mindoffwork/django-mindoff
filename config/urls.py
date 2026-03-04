from django.views.generic.base import TemplateView
from django.contrib import admin
from django.urls import include, path
from apps.django_mindoff import urls as mindoff_urls

urlpatterns = [
    path("", TemplateView.as_view(template_name="index.html")),
    path("admin/", admin.site.urls),
    path("mindoff/", include(mindoff_urls)),
]
