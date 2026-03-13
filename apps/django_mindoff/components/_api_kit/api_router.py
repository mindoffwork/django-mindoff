from django.conf import settings
from ..response_kit import mo_response_kit


class APIVersionRouter:
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
