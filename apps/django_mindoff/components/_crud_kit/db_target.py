"""
Resolve which database a CRUD operation targets.

A target is normally an alias configured in ``settings.DATABASES``. It may also be
a connection provisioned at runtime and registered only in ``django.db.connections``
— the multi-tenant case, where per-tenant credentials are resolved on demand and
never written into settings at all.

Both cases end at the same place, because ``settings.DATABASES[alias]`` and
``connection.settings_dict`` are the same mapping shape. Only the lookup differs,
so everything downstream (engine construction, dialect dispatch, write strategy)
is shared.
"""

from typing import Optional, Union

from django.conf import settings
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.utils import ConnectionDoesNotExist

# ----------------
# Constants
# ----------------
DEFAULT_DB_ALIAS = "default"


# ----------------
# Classes
# ----------------
class DbTarget:
    """The database one CRUD call reads and writes through.

    Holds the two things the kit needs, and keeps them consistent with each other:
    the settings mapping the SQLAlchemy engine is built from, and the live Django
    connection the SQLite engine binds to.

    ``is_explicit`` records whether the *caller* chose this target. That is what
    decides whether ORM queries are pinned with ``.using()``: a caller who said
    nothing must keep whatever routing the project already has, database routers
    included.
    """

    __slots__ = ("alias", "settings_dict", "is_explicit", "_connection")

    def __init__(
        self,
        alias: str,
        settings_dict: dict,
        *,
        is_explicit: bool,
        connection: Optional[BaseDatabaseWrapper] = None,
    ):
        self.alias = alias
        self.settings_dict = settings_dict
        self.is_explicit = is_explicit
        self._connection = connection

    @property
    def connection(self) -> BaseDatabaseWrapper:
        """The live Django connection, resolved on first use.

        Deliberately lazy. An unsupported backend is rejected from the settings
        mapping alone, and building a connection would import that backend's
        driver first — turning a clear "unsupported engine" message into an
        import error about a driver the caller never asked for.
        """
        if self._connection is None:
            self._connection = connections[self.alias]
        return self._connection

    def orm_alias(self, *, operation: str) -> Optional[str]:
        """Alias to pin ORM queries to, or ``None`` to leave routing untouched.

        Returns ``None`` whenever the caller did not name a target, which keeps
        the default path byte-identical to previous behavior. When a target *was*
        named, the alias must be reachable through ``django.db.connections`` or
        the ORM cannot honor it — so this fails loudly here rather than letting
        the query silently resolve somewhere else.
        """
        if not self.is_explicit:
            return None
        try:
            registered = connections[self.alias]
        except ConnectionDoesNotExist:
            registered = None
        is_same_connection = (
            self._connection is None or registered is self._connection
        )
        if registered is None or not is_same_connection:
            raise ValueError(
                f"{operation} runs an ORM query against '{self.alias}', but that "
                "connection is not registered in django.db.connections, so the "
                "query cannot be routed to it. Either register the connection "
                "under its alias, or skip the ORM-dependent steps with "
                'validation_level="columns_only" and skip_db_fill=True.'
            )
        return self.alias


# What a caller may hand to ``using``: an alias, a live Django connection, an
# already-resolved target, or ``None`` meaning "whatever this project is already
# configured to use". Declared after ``DbTarget`` so the union needs no forward
# reference for runtime type checking.
DbTargetLike = Union[str, BaseDatabaseWrapper, DbTarget, None]


# ----------------
# Functions
# ----------------
def resolve_db_target(using: DbTargetLike = None) -> DbTarget:
    """Normalize ``using`` into a :class:`DbTarget`.

    Accepts an alias, a live Django connection, or ``None`` for the default
    alias. A connection is taken at face value: it carries both its own alias and
    its own settings, which is exactly what a runtime-provisioned database has.

    Idempotent, so a caller that already resolved a target can pass it straight
    through and every stage of one operation shares a single resolution.
    """
    if isinstance(using, DbTarget):
        return using
    if isinstance(using, BaseDatabaseWrapper):
        return DbTarget(
            using.alias,
            using.settings_dict,
            is_explicit=True,
            connection=using,
        )
    alias = using if using is not None else DEFAULT_DB_ALIAS
    return DbTarget(
        alias,
        resolve_settings_dict(alias),
        is_explicit=using is not None,
    )


def resolve_settings_dict(alias: str) -> dict:
    """Return the settings mapping for ``alias``.

    ``settings.DATABASES`` is consulted first, so an ordinary configured alias
    resolves exactly as it always has — without building a connection, and
    without the two sources ever disagreeing. The fallback is what reaches a
    dynamically-provisioned database: its credentials live only on the connection
    object registered in ``django.db.connections``.
    """
    configured = settings.DATABASES.get(alias)
    if configured is not None:
        return configured
    try:
        return connections[alias].settings_dict
    except ConnectionDoesNotExist:
        raise ValueError(
            f"Database alias '{alias}' not found in settings.DATABASES "
            "or django.db.connections"
        ) from None


def find_settings_dict(alias: str) -> Optional[dict]:
    """Like :func:`resolve_settings_dict`, but ``None`` for an unknown alias.

    For callers that treat an unresolvable alias as "take the slower path"
    rather than an error.
    """
    try:
        return resolve_settings_dict(alias)
    except ValueError:
        return None
