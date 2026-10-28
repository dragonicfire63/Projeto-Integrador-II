from django.contrib import admin

from .models import CalendarCategory, CalendarEvent, Folder, Note, PostIt, UserPreference

admin.site.register((Folder, Note, PostIt, CalendarCategory, CalendarEvent, UserPreference))