from rest_framework import serializers


class RegisterSerializer(serializers.Serializer):
    num_accounts = serializers.IntegerField(min_value=1)
