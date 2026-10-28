from django.conf import settings
from django.db import models


class Folder(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="folders")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80)
    tag = models.CharField(max_length=80, blank=True)
    color_start = models.CharField(max_length=7, default="#4F46E5")
    color_end = models.CharField(max_length=7, default="#7C3AED")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["user", "slug"], name="unique_folder_slug_per_user")]

    def __str__(self):
        return self.name


class Note(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notes")
    folder = models.CharField(max_length=80, blank=True, default="")
    tag = models.CharField(max_length=80, blank=True)
    title = models.CharField(max_length=180)
    content = models.TextField(blank=True)
    color_start = models.CharField(max_length=7, default="#4F46E5")
    color_end = models.CharField(max_length=7, default="#7C3AED")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title


class PostIt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="postits")
    title = models.CharField(max_length=80)
    body = models.CharField(max_length=400, blank=True)
    links = models.JSONField(default=list, blank=True)
    event_id = models.CharField(max_length=80, blank=True)
    source = models.CharField(max_length=20, default="manual")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class CalendarCategory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_categories")
    name = models.CharField(max_length=60)
    slug = models.SlugField(max_length=80)
    color = models.CharField(max_length=7, default="#0EA5E9")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["user", "slug"], name="unique_calendar_category_slug_per_user")]

    def __str__(self):
        return self.name


class CalendarEvent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_events")
    title = models.CharField(max_length=80)
    date = models.DateField()
    time = models.TimeField()
    category = models.CharField(max_length=80, blank=True, default="")
    description = models.TextField(blank=True)
    notification = models.JSONField(default=dict, blank=True)
    note = models.JSONField(null=True, blank=True)
    create_postit = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "time"]


class UserPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences")
    values = models.JSONField(default=dict, blank=True)
    profile_image = models.ImageField(upload_to="profile_images/", blank=True)


class GoogleDriveConnection(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="google_drive")
    encrypted_credentials = models.TextField()
    backup_file_id = models.CharField(max_length=200, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
