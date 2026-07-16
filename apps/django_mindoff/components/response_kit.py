"""
Mindoff Response Kit
1. mo_response_kit.json_response
2. mo_response_kit.file_response
3. mo_response_kit.text_response
4. mo_response_kit.html_response
"""

import copy
import csv
import io
import logging
import mimetypes
import os
import textwrap
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Union
from django.conf import settings
from django.http import FileResponse, HttpResponse
from rest_framework.response import Response
from typeguard import typechecked
from .helper_kit import mo_helper_kit
from .validation_kit import mo_validation_kit

# ----------------
# Constants
# ----------------
MINDOFF_RESPONSES = {}
RESPONSES_FILE_NAME = "responses.csv"
BASE_DIR = Path(__file__).resolve().parent.parent
REQUIRED_HEADERS = ["code", "title", "description", "http_status"]
DEFAULT_RESPONSES_CSV = (
    BASE_DIR / "components" / "managers" / "resources" / RESPONSES_FILE_NAME
)
logger = logging.getLogger(__name__)
default_json_response_status = "fail"
default_json_response_code = "UNEXPECTED_ERR"
default_json_response_title = "Uh Oh!"
default_json_response_description = "An Unexpected Error has occurred."
default_json_response = {
    "status": default_json_response_status,
    "message": {
        "code": default_json_response_code,
        "title": default_json_response_title,
        "description": default_json_response_description,
        "category": "danger",
    },
    "data": [],
}


# ----------------
# Classes
# ----------------
class MindoffResponseHandler:
    """Response helper facade for standardized API output contracts.

    Typical usage:

    ```python
    from django_mindoff import mo_response_kit
    return mo_response_kit.json_response(...)
    ```
    """

    @typechecked
    def json_response(
        self,
        *,
        code: str = "",
        category: Literal["danger", "warning", "info", "success"] = "danger",
        data: Union[Dict[str, Any], List[Dict[str, Any]]] = {},
        exception: Exception | None = None,
    ):
        """Return standardized JSON API envelope using response-code metadata.

        Usage:

        ```python
        from django_mindoff import mo_response_kit

        return mo_response_kit.json_response(
            code="SUCCESS",
            category="success",
            data={"id": "abc123"},
        )
        ```

        Parameters:

        - `code`: Response code defined in `config/responses.csv`.
        - `category`: UI/semantic category for clients. One of:
          `danger`, `warning`, `info`, `success`.
        - `data`: Response payload body.
        - `exception`: Optional exception object for debug/log context.

        Behavior:

        - HTTP status and message metadata are resolved from `responses.csv`.
        - Unknown/empty `code` falls back to `UNEXPECTED_ERR`.
        """
        json_response_msg = copy.deepcopy(default_json_response)
        if code not in MINDOFF_RESPONSES or code == "":
            e = ValueError(f"Unknown Response code: '{code}'")
            return MindoffResponseHandler().json_response(
                code="UNEXPECTED_ERR", category="danger", data=[], exception=e
            )
        response = MINDOFF_RESPONSES[code]
        description = response["description"]
        if exception is not None:
            exception_name = exception.__class__.__name__
            if exception is not None and exception.__traceback__ is not None:
                tb_text = "".join(
                    traceback.format_exception(
                        type(exception), exception, exception.__traceback__
                    )
                )
            else:
                tb_text = mo_helper_kit.get_exact_traceback()
            pretty_tb = textwrap.indent(tb_text, "    ")
            if settings.DEBUG:
                description = f"{description} | {exception_name}"
                print(
                    f"\n========== [MINDOFF EXCEPTION START] ==========\n"
                    f"Exception: {exception}\n"
                    f"Traceback:\n{pretty_tb}\n"
                    f"========== [MINDOFF EXCEPTION END] ==========\n"
                )
            else:
                logger.error(
                    "\n========== [MINDOFF EXCEPTION START: %s] ==========\n"
                    "Exception:\n%s\n"
                    "Traceback:\n%s"
                    "\n========== [MINDOFF EXCEPTION END: %s] ==========\n",
                    code,
                    exception,
                    tb_text,
                    code,
                )
        json_response_msg["status"] = _derive_status_from_http(response["http_status"])
        json_response_msg["message"]["code"] = code
        json_response_msg["message"]["title"] = response["title"]
        json_response_msg["message"]["description"] = description
        json_response_msg["message"]["category"] = category
        json_response_msg["data"] = data or []
        http_status = response["http_status"]

        return Response(
            json_response_msg,
            status=http_status,
        )

    def file_response(
        self,
        file_obj: Union[str, io.BytesIO],
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ):
        """Return downloadable file response from file path or in-memory bytes.

        Usage:

        ```python
        from django_mindoff import mo_response_kit
        import io

        return mo_response_kit.file_response("exports/orders.csv")

        # OR
        buffer = io.BytesIO(b"id,total\\n1,30\\n")
        return mo_response_kit.file_response(
            buffer,
            filename="orders.csv",
            content_type="text/csv",
        )
        ```

        Parameters:

        - `file_obj`: File path (`str`) or in-memory bytes (`io.BytesIO`).
        - `filename`: Optional download filename. Auto-generated for BytesIO if omitted.
        - `content_type`: Optional MIME type override.
        """
        try:
            if isinstance(file_obj, str):
                guessed_type, _ = mimetypes.guess_type(file_obj)
                response = FileResponse(
                    open(file_obj, "rb"), content_type=content_type or guessed_type
                )
                filename = filename or file_obj.split("/")[-1]
            elif isinstance(file_obj, io.BytesIO):
                file_obj.seek(0)
                guessed_type, _ = mimetypes.guess_type(filename or "")
                response = FileResponse(
                    file_obj, content_type=content_type or guessed_type
                )
                if not filename:
                    ext = (
                        mimetypes.guess_extension(
                            content_type or guessed_type or "application/octet-stream"
                        )
                        or ".bin"
                    )
                    filename = f"{uuid.uuid4().hex}{ext}"

            else:
                raise TypeError("file_obj must be a file path (str) or BytesIO")

            if filename:
                response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return MindoffResponseHandler().json_response(
                code="UNEXPECTED_ERR", category="danger", data=[], exception=e
            )

    def text_response(
        self,
        text: str,
        *,
        status_code: int = 200,
    ):
        """Return plain text HTTP response.

        Usage:

        ```python
        from django_mindoff import mo_response_kit
        return mo_response_kit.text_response("ok", status_code=200)
        ```
        """
        return HttpResponse(text, content_type="text/plain", status=status_code)

    def html_response(
        self,
        html: str,
        *,
        status_code: int = 200,
    ):
        """Return HTML HTTP response.

        Usage:

        ```python
        from django_mindoff import mo_response_kit
        return mo_response_kit.html_response("<h1>Ready</h1>", status_code=200)
        ```
        """
        return HttpResponse(html, content_type="text/html", status=status_code)


# ----------------------------------
# Functions
# ----------------------------------
def load_responses_csv(csv_location=None):
    """Load and validate response code metadata into in-memory cache.

    Source order:
    1. `config/responses.csv` in project root
    2. package fallback CSV in framework resources

    Required headers:
    - `code`
    - `title`
    - `description`
    - `http_status`
    """
    csv_path = csv_location or _get_csv_path()
    if not os.path.exists(csv_path):
        if DEFAULT_RESPONSES_CSV.exists():
            csv_path = str(DEFAULT_RESPONSES_CSV)
        else:
            raise FileNotFoundError(
                "no responses.csv found in config folder or django-mindoff resources folder. "
                "Is django mindoff installed properly globally?"
            )
    mo_validation_kit.ensure_path(path=csv_path, is_exception=True)
    with open(csv_path, newline="", encoding="utf-8") as csvfile:
        sample = csvfile.read(1024)
        csvfile.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(csvfile, dialect=dialect, quotechar='"')
        actual_headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = [h for h in REQUIRED_HEADERS if h not in actual_headers]
        mo_validation_kit.ensure_falsey(
            value=missing,
            msg=f"Missing required headers: {missing}. Perhaps a typo? Or too many additional columns?",
            is_exception=True,
        )
        header_map = {h.lower(): h for h in reader.fieldnames}
        responses = {}
        seen_codes = set()
        for line_num, row in enumerate(reader, start=2):
            row_data = {
                h: (row[header_map[h]].strip() if row[header_map[h]] else "")
                for h in REQUIRED_HEADERS
            }
            code = str(row_data["code"]).strip().upper()
            mo_validation_kit.ensure_truthy(
                value=code, msg=f"Empty 'code' at line {line_num}", is_exception=True
            )
            mo_validation_kit.ensure_not_in(
                code,
                seen_codes,
                msg=f"Duplicate code '{code}' at line {line_num}",
                is_exception=True,
            )
            seen_codes.add(code)
            row_data["code"] = code
            http_status = int(row_data["http_status"])
            mo_validation_kit.ensure(
                (100 <= http_status <= 599),
                msg=f"Unsupported HTTP status '{http_status}' at line {line_num} in responses.csv. "
                "Valid range is 100 to 599.",
                is_exception=True,
            )
            derived_status = _derive_status_from_http(http_status)
            for k, v in row_data.items():
                mo_validation_kit.ensure_truthy(
                    value=v, msg=f"Empty '{k}' at line {line_num}", is_exception=True
                )
            responses[code] = {
                "title": row_data["title"],
                "description": row_data["description"],
                "http_status": http_status,
                "status": derived_status,
            }
    defaults = _load_response_defaults(
        fallback_code=default_json_response_code,
        fallback_title=default_json_response_title,
        fallback_description=default_json_response_description,
    )
    for k, v in defaults.items():
        responses.setdefault(k, v)
    global MINDOFF_RESPONSES
    MINDOFF_RESPONSES.clear()
    MINDOFF_RESPONSES.update(responses)


# ----------------
# Helper Functions
# ----------------
def _get_csv_path():
    return Path(settings.BASE_DIR) / "config" / RESPONSES_FILE_NAME


def _derive_status_from_http(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "ok"
    if 400 <= status_code < 500:
        return "fail"
    if 500 <= status_code < 600:
        return "exception"
    raise ValueError(f"Unsupported HTTP status code: {status_code}")


def _load_response_defaults(
    *,
    fallback_code: str,
    fallback_title: str,
    fallback_description: str,
) -> Dict[str, dict]:
    defaults: Dict[str, dict] = {}
    with DEFAULT_RESPONSES_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            http_status = int(row["http_status"])
            defaults[row["code"]] = {
                "http_status": http_status,
                "status": _derive_status_from_http(http_status),
                "code": row["code"],
                "title": row["title"],
                "description": row["description"],
            }
    defaults[fallback_code] = {
        "http_status": 500,
        "status": _derive_status_from_http(500),
        "code": fallback_code,
        "title": fallback_title,
        "description": fallback_description,
    }
    return defaults


# ----------------
# Entry Point
# ----------------
mo_response_kit = MindoffResponseHandler()
