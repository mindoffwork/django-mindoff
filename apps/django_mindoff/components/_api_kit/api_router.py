from django.conf import settings
from ..response_kit import mo_response_kit


class APIVersionRouter:
    """
    API Router that maps API version to API view class.

    This router is used to map URLs to API view classes based on the API version.
    It is responsible for resolving the correct view class based on the API version and
    dispatching the request to the resolved view class.

    The router is initialized with a VERSION_MAP which is a dictionary that maps API versions
    to API view classes. The VERSION_MAP is used to resolve the correct view class based on the
    API version.

    The router is also responsible for caching the resolved view classes. The caching is controlled
    by the MINDOFF_USE_VIEW_CACHE setting. If the setting is True, the resolved view classes are
    cached and reused. If the setting is False, the resolved view classes are not cached and are
    created on each request.

    The router returns a JSON response with code "INVALID_API_VERSION" if the requested API version
    is not found in the VERSION_MAP.

    Args:
        request: The request object.
        *args: The positional arguments.
        **kwargs: The keyword arguments.

    Returns:
        A JSON response with code "INVALID_API_VERSION" if the requested API version is not found in
        the VERSION_MAP. Otherwise, it returns the response from the resolved view class.
    """

    VERSION_MAP: dict = {}

    def __call__(self, request, *args, **kwargs):
        version = kwargs.get("version") or 1
        view_class = self.VERSION_MAP.get(version)

        if view_class is None:
            return mo_response_kit.json_response(
                code="INVALID_API_VERSION",
                category="danger",
                data={"available_versions": list(self.VERSION_MAP.keys())},
            )

        use_cache = getattr(settings, "MINDOFF_USE_VIEW_CACHE", False)
        if not use_cache:
            return view_class.as_view()(request, *args, **kwargs)
        if not hasattr(self, "_view_cache"):
            self._view_cache = {}
        if version not in self._view_cache:
            self._view_cache[version] = view_class.as_view()
        return self._view_cache[version](request, *args, **kwargs)
