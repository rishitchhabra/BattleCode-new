from django.contrib import admin
from .models import (
    Contest, Section, Question,
    TestCase, ContestParticipant, UserSectionProgress, SecurityEvent
)


class SectionInline(admin.TabularInline):
    model = Section
    extra = 0


class TestCaseInline(admin.TabularInline):
    model = TestCase
    extra = 0


@admin.register(Contest)
class ContestAdmin(admin.ModelAdmin):
    list_display = ['title', 'status', 'start_time', 'end_time', 'enable_security_features', 'created_by']
    list_filter = ['status']
    inlines = [SectionInline]


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ['name', 'contest', 'order', 'duration']


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ['title', 'section', 'marks', 'time_limit', 'order']
    inlines = [TestCaseInline]


@admin.register(TestCase)
class TestCaseAdmin(admin.ModelAdmin):
    list_display = ['question', 'is_hidden', 'order']
    list_filter = ['is_hidden']


@admin.register(ContestParticipant)
class ContestParticipantAdmin(admin.ModelAdmin):
    list_display  = ['user', 'contest', 'registered_at', 'ip_address', 'rejoin_code']
    list_filter   = ['contest']
    search_fields = ['user__username', 'ip_address', 'rejoin_code']
    readonly_fields = ['registered_at', 'ip_address']


@admin.register(SecurityEvent)
class SecurityEventAdmin(admin.ModelAdmin):
    list_display  = ['user', 'contest', 'event_type', 'ip_address', 'created_at']
    list_filter   = ['event_type', 'contest']
    search_fields = ['user__username']
    readonly_fields = ['user', 'contest', 'participant', 'event_type',
                       'screenshot', 'ip_address', 'created_at']


admin.site.register(UserSectionProgress)