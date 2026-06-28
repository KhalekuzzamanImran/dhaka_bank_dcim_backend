from django.contrib import admin
from .models import DataCenter, Room, Row, Rack

admin.site.register(DataCenter)
admin.site.register(Room)
admin.site.register(Row)
admin.site.register(Rack)
