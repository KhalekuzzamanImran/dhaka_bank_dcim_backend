from django.contrib import admin
from .models import ReportTemplate, ReportJob

admin.site.register(ReportTemplate)
admin.site.register(ReportJob)
