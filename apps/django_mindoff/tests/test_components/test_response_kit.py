import csv
import io
import logging
import uuid
from copy import deepcopy
import pytest
from django.http import FileResponse, HttpResponse
from typeguard import TypeCheckError
from ...components.response_kit import (
    DEFAULT_RESPONSES_CSV,
    MINDOFF_RESPONSES,
    REQUIRED_HEADERS,
    load_responses_csv,
    mo_response_kit,
)
from ...components.api_kit import mo_api_kit
from ...components.tdd_kit import MindoffTestCase


CSV_HEADERS_VALID = ["code", "title", "description", "http_status"]
CSV_DATA_VALID = [
    ["UNEXPECTED_ERR", "Different Title", "An Unexpected Error has occurred.", "500"],
    [
        "VALIDATION_ERR",
        "Validation Failed",
        "Submitted data failed validation.",
        "400",
    ],
    [
        "PAYLOAD_SIZE_ERR",
        "Payload Size Exceeded Limit",
        "Payload cannot exceed the allowed size limit of 5 MB.",
        "400",
    ],
    [
        "PAYLOAD_TYPE_ERR",
        "Payload Schema Mismatch",
        "The Provided payload does not match the expected type.",
        "400",
    ],
    ["SUCCESS", "Success", "Operation completed successfully.", "200"],
    ["SUCCESS001", "User Created", "The user has been created.", "200"],
    ["SUCCESS002", "Data Saved", "Data saved without any issues.", "200"],
    ["SUCCESS003", "Operation Completed", "The request completed.", "200"],
    ["SUCCESS004", "Email Sent", "Email has been sent successfully.", "200"],
    ["SUCCESS005", "File Uploaded", "File uploaded successfully.", "200"],
    ["ERR001", "Invalid Input", "The provided input is invalid.", "400"],
    ["ERR002", "Not Found", "The requested resource was not found.", "400"],
    ["ERR003", "Permission Denied", "You do not have permission.", "400"],
    ["ERR004", "Server Error", "An unexpected server error occurred.", "500"],
    ["ERR005", "Database Error", "A database error occurred.", "500"],
]
default_data = [{"a": 1}, {"b": 2}]


class TestJsonResponse(MindoffTestCase):
    def setup_method(self, method):
        self._original_responses = deepcopy(MINDOFF_RESPONSES)

    def teardown_method(self, method):
        MINDOFF_RESPONSES.clear()
        MINDOFF_RESPONSES.update(self._original_responses)

    @pytest.mark.parametrize("is_debug", [True, False])
    @pytest.mark.parametrize(
        "exception",
        [
            (None),
            (ValueError("Test Exception")),
        ],
    )
    @pytest.mark.parametrize(
        "category",
        [
            ("danger"),
            ("warning"),
            ("info"),
            ("success"),
        ],
    )
    @pytest.mark.parametrize(
        "code, expected_title, expected_description, expected_status, expected_http_status",
        [
            ("SUCCESS", "Success", "Operation completed successfully.", "ok", 200),
            (
                "VALIDATION_ERR",
                "Validation Failed",
                "Submitted data failed validation.",
                "fail",
                400,
            ),
            (
                "UNEXPECTED_ERR",
                "Different Title",
                "An Unexpected Error has occurred.",
                "exception",
                500,
            ),
        ],
    )
    def test_json_response_valid(
        self,
        tmp_path,
        settings,
        capsys,
        caplog,
        is_debug,
        code,
        expected_title,
        expected_description,
        expected_status,
        expected_http_status,
        category,
        exception,
    ):
        """ACCEPTANCE: Returns expected JSON payload for valid code/category combinations."""
        csv_path = self._write_csv(tmp_path, CSV_HEADERS_VALID, CSV_DATA_VALID)
        load_responses_csv(csv_path)
        assert len(MINDOFF_RESPONSES) > 0
        settings.DEBUG = is_debug
        result = getattr(mo_response_kit, "json_response")(
            code=code, category=category, data=default_data, exception=exception
        )
        assert result.status_code == expected_http_status
        result = result.data
        assert result["status"] == expected_status
        assert result["message"]["code"] == code
        assert result["message"]["title"] == expected_title
        assert expected_description in result["message"]["description"]
        assert len(result["data"]) == len(default_data)
        if exception and expected_status != "ok":
            description = result["message"]["description"]
            exc_name = exception.__class__.__name__
            if is_debug:
                assert exc_name in description
                captured = capsys.readouterr()
                assert "Traceback" in captured.out
                assert "Test Exception" in captured.out
            else:
                assert exc_name not in description
                with caplog.at_level(logging.ERROR):
                    assert any("Traceback" in rec.message for rec in caplog.records)
                    assert any(
                        "Test Exception" in rec.message for rec in caplog.records
                    )

    @pytest.mark.parametrize("is_debug", [True, False])
    @pytest.mark.parametrize(
        "code, expected_error_code",
        [("", "UNEXPECTED_ERR"), ("INVALID_CODE", "UNEXPECTED_ERR")],
    )
    @pytest.mark.parametrize(
        "category",
        [
            ("danger"),
            ("Danger"),
            (""),
            (None),
            ("invalid_category"),
        ],
    )
    def test_json_response_invalid(
        self, settings, capsys, caplog, is_debug, code, expected_error_code, category
    ):
        """REJECTION: Rejects invalid input and returns a safe error response when applicable."""
        settings.DEBUG = is_debug

        if category != "danger":
            with pytest.raises(TypeCheckError):
                getattr(mo_response_kit, "json_response")(
                    code=code, category=category, data=default_data
                )
        else:
            result = getattr(mo_response_kit, "json_response")(
                code=code, category=category, data=default_data
            )
            assert result.status_code == 500
            result = result.data
            assert result["status"] == "exception"
            assert result["message"]["code"] == expected_error_code
            if is_debug:
                assert "ValueError" in result["message"]["description"]
                captured = capsys.readouterr()
                assert "Traceback" in captured.out
            else:
                assert "ValueError" not in result["message"]["description"]
                with caplog.at_level(logging.ERROR):
                    assert any("Traceback" in rec.message for rec in caplog.records)

    def test_bundled_responses_csv_has_required_headers(self):
        """GUARD: Bundled resources/responses.csv must always contain all REQUIRED_HEADERS.

        Catches schema drift — if a header is renamed in the bundled CSV without
        updating REQUIRED_HEADERS (or vice versa), this test fails immediately.
        """
        with open(DEFAULT_RESPONSES_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            actual = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = [h for h in REQUIRED_HEADERS if h not in actual]
        assert not missing, f"Bundled responses.csv is missing required headers: {missing}"

    def test_load_responses_csv_fallback_logic(self, tmp_path, monkeypatch):
        """BOUNDARY: Uses bundled fallback CSV when config/responses.csv is missing.

        The fallback must NOT copy the CSV to disk — it reads from the bundled resource
        directly, leaving the project tree untouched.
        """
        bundled_csv = tmp_path / "bundled_responses.csv"
        bundled_csv.write_text(
            "code,title,description,http_status\nFALLBACK,Fallback,Internal File,200"
        )
        target_csv_path = tmp_path / "config" / "responses.csv"

        monkeypatch.setattr(
            "apps.django_mindoff.components.response_kit.DEFAULT_RESPONSES_CSV",
            bundled_csv,
        )
        load_responses_csv(csv_location=str(target_csv_path))

        assert not target_csv_path.exists(), "Fallback must not copy CSV to disk."
        assert "FALLBACK" in MINDOFF_RESPONSES
        assert MINDOFF_RESPONSES["FALLBACK"]["title"] == "Fallback"

    def _write_csv(self, tmp_path, headers, data):
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        csv_file_path = config_dir / "responses.csv"
        with open(csv_file_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(data)
        return csv_file_path


class TestFileResponse(MindoffTestCase):
    def test_from_disk_valid(self, tmp_path):
        """ACCEPTANCE: Serves a disk file as an attachment with expected content."""
        test_file = tmp_path / "sample.txt"
        test_file.write_text("hello world")
        response = mo_response_kit.file_response(str(test_file))
        assert isinstance(response, FileResponse)
        header = response["Content-Disposition"]
        assert all(
            part in header for part in ["attachment;", "filename=", "sample.txt"]
        )
        content = b"".join(response)
        assert content == b"hello world"

    def test_from_memory_with_filename_valid(self):
        """ACCEPTANCE: Serves in-memory file data using the provided filename."""
        file_data = io.BytesIO(b"hello memory")
        response = mo_response_kit.file_response(file_data, filename="mem.txt")
        assert isinstance(response, FileResponse)
        assert response["Content-Disposition"] == 'attachment; filename="mem.txt"'
        content = b"".join(response)
        assert content == b"hello memory"

    def test_from_memory_no_filename_valid(self, monkeypatch):
        """BOUNDARY: Generates a default filename when in-memory data has no filename."""
        monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0))
        file_data = io.BytesIO(b"hello no name")
        response = mo_response_kit.file_response(file_data)
        assert isinstance(response, FileResponse)
        assert response["Content-Disposition"].endswith('.bin"')
        content = b"".join(response)
        assert content == b"hello no name"

    def test_wrong_type_invalid(self):
        """REJECTION: Returns an error payload for unsupported file input types."""
        response = mo_response_kit.file_response(123)
        response = response.data
        assert response["message"]["code"] == "UNEXPECTED_ERR"
        assert response["status"] == "exception"


class TestHtmlResponse(MindoffTestCase):
    @pytest.mark.parametrize(
        "html, status_code",
        [
            ("<h1>hellow</h1>", 200),
            ("<h1>world</h1>", 200),
            ("<h1>bad request</h1>", 400),
            ("<h1>Not Found</h1>", 400),
        ],
    )
    def test_html_response_valid(self, html, status_code):
        """ACCEPTANCE: Returns an HTML response with matching status and body."""
        response = mo_response_kit.html_response(html, status_code=status_code)
        assert isinstance(response, HttpResponse)
        assert response.status_code == status_code
        assert response["Content-Type"] == "text/html"
        assert response.content == html.encode()


class TestTextResponse(MindoffTestCase):
    @pytest.mark.parametrize(
        "text, status_code",
        [
            ("hello", 200),
            ("world", 200),
            ("bad request", 400),
        ],
    )
    def test_text_response_valid(self, text, status_code):
        """ACCEPTANCE: Returns a plain-text response with matching status and body."""
        response = mo_response_kit.text_response(text, status_code=status_code)
        assert isinstance(response, HttpResponse)
        assert response.status_code == status_code
        assert response["Content-Type"] == "text/plain"
        assert response.content == text.encode()


class TestExceptionHandler(MindoffTestCase):
    def test_exception_handler_valid(self, rf):
        """ACCEPTANCE: api_guardian passes through successful view responses."""

        @mo_api_kit.api_guardian
        def view(request):
            return mo_response_kit.json_response(
                code="SUCCESS", category="success", data=default_data
            )

        request = rf.get("/")
        response = view(request)
        assert response.status_code == 200
        result = response.data
        assert result["status"] == "ok"

    @pytest.mark.parametrize("is_debug", [True, False])
    def test_exception_handler_invalid(self, rf, settings, capsys, caplog, is_debug):
        """REJECTION: api_guardian converts raised validation errors into fail responses."""
        from ...components.validation_kit import mo_validation_kit

        @mo_api_kit.api_guardian
        def view(request):
            mo_validation_kit.ensure_equal(1, 2, msg="boom", is_exception=True)

        settings.DEBUG = is_debug
        request = rf.get("/")
        response = view(request)
        assert response.status_code == 400
        result = response.data
        assert result["status"] == "fail"
        assert result["message"]["code"] == "VALIDATION_ERR"
        if is_debug == True:
            assert "ValueError" in result["message"]["description"]
            captured = capsys.readouterr()
            assert "Traceback" in captured.out
            assert "boom" in captured.out
        else:
            assert "ValueError" not in result["message"]["description"]
            with caplog.at_level(logging.ERROR):
                assert any("Traceback" in rec.message for rec in caplog.records)
                assert any("boom" in rec.message for rec in caplog.records)
