from django.contrib import admin
from .models import Submission, SubmissionTestResult


class ResultInline(admin.TabularInline):
    model = SubmissionTestResult
    extra = 0
    readonly_fields = ['test_case', 'status', 'actual_output', 'execution_time']
    can_delete = False


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'question', 'status', 'score', 'submitted_at']
    list_filter  = ['status', 'language']
    readonly_fields = [
        'user', 'question', 'code', 'status',
        'score', 'submitted_at', 'execution_time'
    ]
    inlines = [ResultInline]