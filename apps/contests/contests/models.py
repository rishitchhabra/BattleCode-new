from django.db import models
from django.utils import timezone
from apps.accounts.models import User


class Contest(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_ACTIVE = 'active'
    STATUS_ENDED = 'ended'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_ENDED, 'Ended'),
    ]
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_contests')
    participants = models.ManyToManyField(User, through='ContestParticipant', related_name='contests')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    enable_security_features = models.BooleanField(
        default=True,
        help_text="If enabled, participants will be logged out for exiting fullscreen or switching tabs."
    )
    manual_control_mode = models.BooleanField(
        default=False,
        help_text="If enabled, timers are disabled and sections must be manually unlocked by the admin."
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def is_active(self):
        now = timezone.now()
        return self.status == self.STATUS_ACTIVE and self.start_time <= now <= self.end_time

    @property
    def has_started(self):
        return timezone.now() >= self.start_time

    @property
    def has_ended(self):
        return timezone.now() > self.end_time or self.status == self.STATUS_ENDED

    def total_marks(self):
        return Question.objects.filter(
            section__contest=self
        ).aggregate(total=models.Sum('marks'))['total'] or 0


class ContestParticipant(models.Model):
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    registered_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    rejoin_code = models.CharField(max_length=100, null=True, blank=True)
    kicked_at = models.DateTimeField(null=True, blank=True, help_text='Set when participant exits/is kicked. Blocks normal login until cleared by rejoin.')

    class Meta:
        unique_together = ('contest', 'user')

    def __str__(self):
        return f"{self.user.username} in {self.contest.title}"


class SecurityEvent(models.Model):
    EVENT_TAB_SWITCH = 'tab_switch'
    EVENT_CHOICES = [
        (EVENT_TAB_SWITCH, 'Tab Switch / Window Blur'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='security_events')
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE, related_name='security_events')
    participant = models.ForeignKey(
        ContestParticipant, on_delete=models.CASCADE,
        related_name='security_events', null=True, blank=True
    )
    event_type = models.CharField(max_length=30, choices=EVENT_CHOICES, default=EVENT_TAB_SWITCH)
    screenshot = models.ImageField(upload_to='security_screenshots/', blank=True, null=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    details = models.TextField(blank=True, help_text='JSON or plain text extra metadata')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.event_type}] {self.user.username} in {self.contest.title} @ {self.created_at}"


class Section(models.Model):
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE, related_name='sections')
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=1)
    duration = models.PositiveIntegerField(help_text='Duration in minutes')
    is_manual_unlocked = models.BooleanField(
        default=False,
        help_text="Only used if Contest is in Manual Control Mode."
    )

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.contest.title} - {self.name}"

    def total_marks(self):
        return self.questions.aggregate(
            total=models.Sum('marks')
        )['total'] or 0


class Question(models.Model):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='questions')
    title = models.CharField(max_length=255)
    problem_statement = models.TextField()
    input_format = models.TextField(blank=True)
    output_format = models.TextField(blank=True)
    constraints = models.TextField(blank=True)
    sample_input = models.TextField(blank=True)
    sample_output = models.TextField(blank=True)
    marks = models.PositiveIntegerField(default=10)
    time_limit = models.FloatField(default=2.0, help_text='Seconds per test case')
    order = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.section.name} - Q{self.order}: {self.title}"

    def hidden_test_count(self):
        return self.test_cases.filter(is_hidden=True).count()

    def sample_test_count(self):
        return self.test_cases.filter(is_hidden=False).count()


class TestCase(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='test_cases')
    input_data = models.TextField()
    expected_output = models.TextField()
    image = models.ImageField(upload_to='testcase_images/', blank=True, null=True)
    is_hidden = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['order']

    def __str__(self):
        visibility = 'Hidden' if self.is_hidden else 'Sample'
        return f"TC-{self.order} ({visibility}) for {self.question.title}"


class UserSectionProgress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    section = models.ForeignKey(Section, on_delete=models.CASCADE)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    is_locked = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'section')

    def __str__(self):
        return f"{self.user.username} - {self.section.name}"

    def seconds_remaining(self):
        if self.is_locked or self.started_at is None:
            return 0
        elapsed = (timezone.now() - self.started_at).total_seconds()
        total = self.section.duration * 60
        return max(0, int(total - elapsed))

    def lock_if_expired(self):
        if not self.is_locked and self.started_at and self.seconds_remaining() == 0:
            self.is_locked = True
            self.ended_at = timezone.now()
            self.save()
            return True
        return False