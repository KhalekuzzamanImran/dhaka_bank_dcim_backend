from django.contrib import admin
from .models import DataCenter, Room, Row, Rack

@admin.register(DataCenter)
class DataCenterAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "organization", "city", "country", "status", "timezone")
    list_filter = ("organization", "status", "country")
    search_fields = ("name", "code", "organization__name", "organization__code", "city")
    ordering = ("organization__name", "name")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "data_center", "room_type", "floor_name")
    list_filter = ("data_center", "room_type")
    search_fields = ("name", "code", "data_center__name", "data_center__code")
    ordering = ("data_center__name", "name")


@admin.register(Row)
class RowAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "data_center", "room", "position_x", "position_y")
    list_filter = ("data_center", "room")
    search_fields = ("name", "code", "data_center__name", "room__name")
    ordering = ("data_center__name", "room__name", "name")


@admin.register(Rack)
class RackAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "data_center", "room", "row", "rack_u_height", "status")
    list_filter = ("data_center", "room", "status")
    search_fields = ("name", "code", "data_center__name", "room__name", "row__name")
    ordering = ("data_center__name", "name")
