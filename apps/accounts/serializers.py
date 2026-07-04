from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from .models import User

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, validators=[validate_password])
    class Meta:
        model = User
        fields = ['id','username','email','full_name','phone','is_active','is_staff','is_mfa_enabled','last_login','date_joined','password']
        read_only_fields = ['id','last_login','date_joined']
        extra_kwargs = {'is_staff': {'read_only': True}}
    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user
    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for k,v in validated_data.items():
            setattr(instance,k,v)
        if password:
            instance.set_password(password)
        instance.save()
        return instance

class MeSerializer(UserSerializer):
    class Meta(UserSerializer.Meta):
        read_only_fields = tuple(UserSerializer.Meta.read_only_fields) + ('is_active', 'is_staff')
