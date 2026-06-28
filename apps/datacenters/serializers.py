from rest_framework import serializers
from .models import DataCenter, Room, Row, Rack


class DataCenterSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataCenter
        fields = "__all__"


class RoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Room
        fields = "__all__"


class RowSerializer(serializers.ModelSerializer):
    room_name = serializers.CharField(source="room.name", read_only=True)

    class Meta:
        model = Row
        fields = "__all__"


class RackSerializer(serializers.ModelSerializer):
    row_name = serializers.CharField(source="row.name", read_only=True)

    class Meta:
        model = Rack
        fields = "__all__"
