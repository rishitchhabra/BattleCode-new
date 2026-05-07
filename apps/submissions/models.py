from django.db import models
from apps.accounts.models import User
from apps.contests.models import Question, TestCase


class Submission(models.Model):
    STATUS_PENDING  = 'pending'
    STATUS_RUNNING  = 'running'
    STATUS_ACCEPTED = 'accepted'
    STATUS_PARTIAL  = 'partial'
    STATUS_WRONG    = 'wrong_answer'
    STATUS_TLE      = 'time_limit_exceeded'
    STATUS_MLE      = 'memory_limit_exceeded'
    STATUS_RE       = 'runtime_error'
    STATUS_CE       = 'compile_error'
    STATUS_FAILED   = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING,  'Pending'),
        (STATUS_RUNNING,  'Running'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_PARTIAL,  'Partial'),
        (STATUS_WRONG,    'Wrong Answer'),
        (STATUS_TLE,      'Time Limit Exceeded'),
        (STATUS_MLE,      'Memory Limit Exceeded'),
        (STATUS_RE,       'Runtime Error'),
        (STATUS_CE,       'Compile Error'),
        (STATUS_FAILED,   'Failed'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='submissions')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='submissions')
    code = models.TextField()
    language = models.CharField(max_length=20, default='python')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    score = models.FloatField(default=0)
    error_message = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    execution_time = models.FloatField(null=True, blank=True)
    task_id = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"Sub #{self.id} by {self.user.username} on {self.question.title}"

    def get_status_class(self):
        mapping = {
            self.STATUS_ACCEPTED: 'status-accepted',
            self.STATUS_PARTIAL:  'status-partial',
            self.STATUS_WRONG:    'status-wrong',
            self.STATUS_TLE:      'status-tle',
            self.STATUS_RE:       'status-re',
            self.STATUS_CE:       'status-ce',
            self.STATUS_PENDING:  'status-pending',
            self.STATUS_RUNNING:  'status-running',
            self.STATUS_FAILED:   'status-wrong',
        }
        return mapping.get(self.status, 'status-default')


class SubmissionTestResult(models.Model):
    STATUS_PASS = 'pass'
    STATUS_FAIL = 'fail'
    STATUS_TLE  = 'tle'
    STATUS_RE   = 'runtime_error'
    STATUS_MLE  = 'mle'

    STATUS_CHOICES = [
        (STATUS_PASS, 'Pass'),
        (STATUS_FAIL, 'Fail'),
        (STATUS_TLE,  'Time Limit Exceeded'),
        (STATUS_RE,   'Runtime Error'),
        (STATUS_MLE,  'Memory Limit Exceeded'),
    ]

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name='test_results')
    test_case = models.ForeignKey(TestCase, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    actual_output = models.TextField(blank=True)
    execution_time = models.FloatField(null=True, blank=True)
    memory_used = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"TC-{self.test_case.order} → {self.status}"


class QuestionDraft(models.Model):
    """Auto-saved code draft per (user, question). Upserted on every switch or auto-save."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='drafts')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='drafts')
    code = models.TextField()
    saved_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'question')

    def __str__(self):
        return f"Draft by {self.user.username} for Q{self.question.id}"