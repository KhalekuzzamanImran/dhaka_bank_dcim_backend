import pytest
from apps.organizations.models import Organization
@pytest.mark.django_db
def test_organization_str():
    org = Organization.objects.create(name='Dhaka Bank', code='DBL')
    assert str(org) == 'Dhaka Bank'
