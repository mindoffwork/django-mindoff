import pytest
from django_mindoff.components.tdd_kit import MindoffRouterTestCase


@pytest.mark.django_db
class SampleRouterTestClassName(MindoffRouterTestCase):
    """Tests for the Sample Api Human Name version router."""

    app_module = "apps.sample_app_name.views"
    router_function_name = "sample_router_function_name"
