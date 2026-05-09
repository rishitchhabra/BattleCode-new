from django.urls import path
from . import views

urlpatterns = [
    # ── Public / Candidate ─────────────────────────────────────
    path('', views.contest_list, name='contest_list'),
    path('<int:contest_id>/', views.contest_detail, name='contest_detail'),
    path('<int:contest_id>/register/', views.contest_register, name='contest_register'),
    path('<int:contest_id>/section/<int:section_id>/', views.section_view, name='section_view'),
    path('section/<int:section_id>/timer/', views.section_timer_api, name='section_timer_api'),

    # ── Security event (tab-switch detection) ──────────────────
    path('security-event/', views.security_event, name='security_event'),
    path('kick/', views.kick_participant, name='kick_participant'),

    # ── Contest Admin (manage/ avoids clash with Django's admin/) ──
    path('manage/create/', views.admin_contest_create, name='admin_contest_create'),
    path('manage/<int:contest_id>/', views.admin_contest_detail, name='admin_contest_detail'),
    path('manage/<int:contest_id>/edit/', views.admin_contest_edit, name='admin_contest_edit'),
    path('manage/<int:contest_id>/status/', views.admin_contest_status, name='admin_contest_status'),
    path('manage/<int:contest_id>/toggle-security/', views.admin_contest_toggle_security, name='admin_contest_toggle_security'),
    path('manage/<int:contest_id>/section/create/', views.admin_section_create, name='admin_section_create'),
    path('manage/section/<int:section_id>/edit/', views.admin_section_edit, name='admin_section_edit'),
    path('manage/section/<int:section_id>/toggle-lock/', views.admin_section_toggle_lock, name='admin_section_toggle_lock'),
    path('manage/<int:contest_id>/participants/add/', views.admin_add_participant, name='admin_add_participant'),
    path('manage/<int:contest_id>/submissions/', views.admin_submissions_view, name='admin_submissions'),
    path('manage/section/<int:section_id>/question/create/', views.admin_question_create, name='admin_question_create'),
    path('manage/question/<int:question_id>/edit/', views.admin_question_edit, name='admin_question_edit'),
]