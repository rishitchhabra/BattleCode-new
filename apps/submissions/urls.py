from django.urls import path
from . import views

urlpatterns = [
    path('question/<int:question_id>/editor/', views.question_editor, name='question_editor'),
    path('question/<int:question_id>/data/', views.question_data_api, name='question_data_api'),
    path('question/<int:question_id>/submit/', views.submit_code, name='submit_code'),
    path('question/<int:question_id>/draft/', views.save_draft, name='save_draft'),
    path('question/<int:question_id>/compile/', views.compile_run, name='compile_run'),
    path('section/<int:section_id>/lock/', views.lock_section, name='lock_section'),
    path('<int:submission_id>/', views.submission_detail, name='submission_status'),
    path('<int:submission_id>/api/', views.submission_status_api, name='submission_status_api'),
]