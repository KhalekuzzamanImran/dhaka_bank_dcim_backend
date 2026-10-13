import pytest
from apps.organizations.models import Organization
@pytest.mark.django_db
def test_organization_str():
    org = Organization.objects.create(name='One Bank', code='ONE_BANK')
    assert str(org) == 'One Bank'
