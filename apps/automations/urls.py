from django.urls import path

from . import views

app_name = "automations"

urlpatterns = [
    path("", views.AutomationListView.as_view(), name="list"),
    path("new/", views.AutomationNewView.as_view(), name="new"),
    path("new/<slug:recipe>/", views.AutomationCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.AutomationUpdateView.as_view(), name="edit"),
    path("<int:pk>/status/", views.AutomationStatusView.as_view(), name="status"),
    path("<int:pk>/delete/", views.AutomationDeleteView.as_view(), name="delete"),
]
