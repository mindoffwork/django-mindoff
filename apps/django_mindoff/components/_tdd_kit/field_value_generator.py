import datetime
import random
import string
import uuid
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from model_bakery import baker


# ----------------
# Classes
# ----------------
class FieldValueGenerator:
    """
    Generate model-field test values that satisfy Django constraints.

    This helper powers TDD data generation by creating deterministic-enough random
    values across common Django field types while honoring defaults, uniqueness,
    validators, and UUID output mode.
    """
    def __init__(self, field, used_uniques, partial_kwargs, is_uuid_hex):
        self.field = field
        self.used_uniques = used_uniques
        self.partial_kwargs = partial_kwargs
        self.is_uuid_hex = is_uuid_hex

    def run(self):
        """Main entry point."""
        if self.field.has_default():
            value = self.field.get_default()
            if (
                self.field.get_internal_type() == "UUIDField"
                and isinstance(value, uuid.UUID)
                and self.is_uuid_hex
            ):
                value = value.hex
            return value

        field_type = self.field.get_internal_type()
        method_name = f"_gen_{field_type.lower()}"
        method = getattr(self, method_name, None)

        if method:
            return method()

        return self._gen_fallback()

    # ---------- Helpers ----------

    def _choose_null_blank_outcome(self):
        """Decide whether to return null, blank, or a value."""
        allow_null = getattr(self.field, "null", False)
        allow_blank = getattr(self.field, "blank", False)
        r = random.random()
        if allow_null and allow_blank:
            if r < 0.01:
                outcome = "null"
            elif r < 0.02:
                outcome = "blank"
            else:
                outcome = "value"
            return outcome
        if allow_null:
            return "null" if r < 0.6 else "value"
        if allow_blank:
            return "blank" if r < 0.2 else "value"
        return "value"

    def _register_unique(self, value, key=None):
        key = key or self.field.name
        if value in self.used_uniques.setdefault(key, set()):
            return False
        self.used_uniques[key].add(value)
        return True

    # ---------- Generators ----------

    def _gen_charfield(self):
        return self._gen_text()

    def _gen_textfield(self):
        return self._gen_text()

    def _gen_text(self):
        max_len = getattr(self.field, "max_length", 20) or 20
        while True:
            value = "".join(random.choices(string.ascii_letters, k=min(max_len, 10)))

            if getattr(self.field, "unique", False):
                if not self._register_unique(value):
                    continue

            if not self._check_unique_for(value):
                continue
            return value

    def _check_unique_for(self, value):
        """Handle unique_for_date/month/year."""
        for attr in ["unique_for_date", "unique_for_month", "unique_for_year"]:
            related = getattr(self.field, attr, None)
            if not related:
                continue

            dt_val = self.partial_kwargs.get(related)
            if not dt_val:
                continue

            if attr == "unique_for_date":
                key = f"{self.field.name}:date"
                unique_key = (value, dt_val.date())
            elif attr == "unique_for_month":
                key = f"{self.field.name}:month"
                unique_key = (value, dt_val.year, dt_val.month)
            else:  # unique_for_year
                key = f"{self.field.name}:year"
                unique_key = (value, dt_val.year)

            if not self._register_unique(unique_key, key):
                return False
        return True

    def _gen_integerfield(self):
        return self._gen_int()

    def _gen_smallintegerfield(self):
        return self._gen_int()

    def _gen_bigintegerfield(self):
        return self._gen_int()

    def _gen_int(self):
        min_value, max_value = -1000, 1000
        for v in getattr(self.field, "validators", []):
            if isinstance(v, MinValueValidator):
                min_value = max(min_value, v.limit_value)
            if isinstance(v, MaxValueValidator):
                max_value = min(max_value, v.limit_value)

        while True:
            value = random.randint(min_value, max_value)
            if getattr(self.field, "unique", False):
                if not self._register_unique(value):
                    continue
            return value

    def _gen_decimalfield(self):
        max_digits = getattr(self.field, "max_digits", 5) or 5
        decimal_places = getattr(self.field, "decimal_places", 2) or 2
        max_value = Decimal(10) ** (max_digits - decimal_places)

        while True:
            value = Decimal(random.uniform(0, float(max_value)))
            value = value.quantize(Decimal(10) ** -decimal_places)
            if getattr(self.field, "unique", False):
                if not self._register_unique(value):
                    continue
            return value

    def _gen_floatfield(self):
        return random.uniform(0, 1000)

    def _gen_booleanfield(self):
        return random.choice([True, False])

    def _gen_datefield(self):
        return datetime.date.today()

    def _gen_datetimefield(self):
        return datetime.datetime.now()

    def _gen_uuidfield(self):
        while True:
            raw_uuid = uuid.uuid4()
            if self.is_uuid_hex:
                value = raw_uuid.hex
            else:
                value = raw_uuid

            if getattr(self.field, "unique", False):
                if not self._register_unique(value):
                    continue
            return value

    def _gen_slugfield(self):
        while True:
            value = "".join(random.choices(string.ascii_lowercase, k=8))
            if getattr(self.field, "unique", False):
                if not self._register_unique(value):
                    continue
            return value

    def _gen_fallback(self):
        if getattr(self, "field", None) and getattr(self.field, "related_model", None):
            return baker.prepare(self.field.related_model)
        return None


# ----------------
# Functions
# ----------------
def generate_field_value(field, used_uniques, partial_kwargs, is_uuid_hex):
    """Return a generated test value for a Django field."""
    return FieldValueGenerator(
        field=field,
        used_uniques=used_uniques,
        partial_kwargs=partial_kwargs,
        is_uuid_hex=is_uuid_hex,
    ).run()
