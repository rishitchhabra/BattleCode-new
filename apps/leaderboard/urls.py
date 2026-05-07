from django.urls import path
from . import views

urlpatterns = [
    path('<int:contest_id>/',      views.leaderboard_view, name='leaderboard'),
    path('<int:contest_id>/api/',  views.leaderboard_api,  name='leaderboard_api'),
]