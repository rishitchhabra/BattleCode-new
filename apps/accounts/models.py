from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    ROLE_CANDIDATE = 'candidate'
    ROLE_ADMIN = 'admin'
    ROLE_CHOICES = [
        (ROLE_CANDIDATE, 'Candidate'),
        (ROLE_ADMIN, 'Admin'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_CANDIDATE)
    email = models.EmailField(unique=True)

    @property
    def is_candidate(self):
        return self.role == self.ROLE_CANDIDATE

    @property
    def is_contest_admin(self):
        return self.role == self.ROLE_ADMIN or self.is_staff

    def __str__(self):
        return f"{self.username} ({self.role})"