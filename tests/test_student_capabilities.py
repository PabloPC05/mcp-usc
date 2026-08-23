from __future__ import annotations

import math
from typing import Any

import pytest

from mcp_usc.student_capabilities import (
    CAPABILITIES,
    MAX_ARGUMENT_BYTES,
    MAX_ARGUMENT_DEPTH,
    MAX_ARGUMENT_NODES,
    MAX_ARGUMENT_STRING,
    bind_account,
    capability_catalog,
    get_capability,
    sanitise_result,
    validate_arguments,
)


def _nested(depth: int, leaf: Any = "value") -> Any:
    result = leaf
    for _ in range(depth):
        result = {"child": result}
    return result


def test_catalog_is_unique_and_split_into_read_and_action_allowlists() -> None:
    catalog = capability_catalog()

    assert len(catalog) == len(CAPABILITIES)
    assert len(CAPABILITIES) > 250
    assert sum(item.access == "read" for item in CAPABILITIES.values()) > 190
    assert sum(item.access == "action" for item in CAPABILITIES.values()) > 90
    assert set(item.access for item in CAPABILITIES.values()) == {"read", "action"}
    assert all(name == item.function for name, item in CAPABILITIES.items())
    assert len({item["function"] for item in catalog}) == len(catalog)


def test_catalog_contains_representative_student_capabilities() -> None:
    expected = {
        "core_user_get_user_preferences": ("account", "read", False),
        "gradereport_user_get_grade_items": ("grades_and_completion", "read", False),
        "core_message_get_messages": ("messages_and_notifications", "read", False),
        "mod_lesson_get_pages": ("activities", "read", False),
        "core_user_update_user_preferences": ("account", "action", False),
        "mod_forum_add_discussion_post": ("forums", "action", False),
        "mod_forum_delete_post": ("forums", "action", True),
        "mod_quiz_view_quiz": ("activity_tracking", "action", False),
    }

    assert {
        name: (
            CAPABILITIES[name].category,
            CAPABILITIES[name].access,
            CAPABILITIES[name].destructive,
        )
        for name in expected
    } == expected


def test_catalog_filters_and_reports_token_availability_without_hiding_entries() -> None:
    available = {"core_user_get_user_preferences"}

    items = capability_catalog(
        category="account",
        access="read",
        available_functions=available,
    )

    assert items == sorted(items, key=lambda item: (item["category"], item["function"]))
    assert items
    assert all(item["category"] == "account" and item["access"] == "read" for item in items)
    availability = {item["function"]: item["available_for_configured_token"] for item in items}
    assert availability["core_user_get_user_preferences"] is True
    assert availability["core_user_get_private_files_info"] is False


def test_catalog_uses_unknown_availability_when_gateway_cannot_enumerate_functions() -> None:
    assert all(
        item["available_for_configured_token"] is None
        for item in capability_catalog(access="action")
    )


@pytest.mark.parametrize(
    ("function", "access"),
    [
        ("totally_unknown_function", "read"),
        ("core_user_get_user_preferences", "action"),
        ("core_user_update_user_preferences", "read"),
        ("", "read"),
    ],
)
def test_get_capability_enforces_the_exact_access_allowlist(function: str, access: str) -> None:
    with pytest.raises(ValueError, match="lista blanca|obligatorio"):
        get_capability(function, access)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("category", "access", "message"),
    [
        ("unknown", None, "category"),
        (None, "write", "access"),
    ],
)
def test_catalog_rejects_unknown_filters(
    category: str | None, access: str | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        capability_catalog(category=category, access=access)  # type: ignore[arg-type]


def test_validate_arguments_accepts_and_copies_json_values() -> None:
    original = {
        "none": None,
        "boolean": True,
        "integer": 7,
        "number": 2.5,
        "text": "USC",
        "array": [1, {"nested": "ok"}],
        "tuple_is_normalised": (2, 3),
    }

    clean = validate_arguments(original)

    assert clean == {**original, "tuple_is_normalised": [2, 3]}
    assert clean is not original
    assert clean["array"] is not original["array"]
    assert validate_arguments(None) == {}


@pytest.mark.parametrize("value", [[], "text", 3, b"bytes", {1, 2}])
def test_validate_arguments_requires_a_json_object_at_the_root(value: Any) -> None:
    with pytest.raises(ValueError, match="objeto JSON"):
        validate_arguments(value)


@pytest.mark.parametrize(
    "arguments",
    [
        {1: "non-string"},
        {"": "empty"},
        {"x" * 101: "too long"},
        {"nested": object()},
        {"nested": b"not-json"},
        {"nested": {1, 2}},
    ],
)
def test_validate_arguments_rejects_non_json_keys_and_values(arguments: dict[Any, Any]) -> None:
    with pytest.raises(ValueError, match="claves|valores JSON"):
        validate_arguments(arguments)


@pytest.mark.parametrize(
    "secret_key",
    [
        "ACCESS_TOKEN",
        "Authorization",
        "COOKIE",
        "MoodleSession",
        "Password",
        "SESSKEY",
        "Token",
        "WSTOKEN",
        "wsfunction",
        "moodlewsrestformat",
        "client-secret",
        "pluginAccessToken",
        "resourcekey",
        "servicesalt",
        "instructorcustomparameters",
    ],
)
def test_validate_arguments_rejects_secret_keys_recursively(secret_key: str) -> None:
    with pytest.raises(ValueError, match="sensible"):
        validate_arguments({"outer": [{secret_key: "must-not-pass"}]})


@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf])
def test_validate_arguments_rejects_non_finite_numbers_not_valid_in_json(number: float) -> None:
    with pytest.raises(ValueError, match="NaN|infinit"):
        validate_arguments({"number": number})


def test_validate_arguments_enforces_depth_node_and_string_limits() -> None:
    assert validate_arguments(_nested(MAX_ARGUMENT_DEPTH))
    with pytest.raises(ValueError, match="profundidad"):
        validate_arguments(_nested(MAX_ARGUMENT_DEPTH + 1))

    allowed_items = MAX_ARGUMENT_NODES - 2  # root mapping and its list are nodes too
    assert validate_arguments({"items": [None] * allowed_items})
    with pytest.raises(ValueError, match="demasiados"):
        validate_arguments({"items": [None] * (allowed_items + 1)})

    assert validate_arguments({"text": "x" * MAX_ARGUMENT_STRING})
    with pytest.raises(ValueError, match="límite"):
        validate_arguments({"text": "x" * (MAX_ARGUMENT_STRING + 1)})

    with pytest.raises(ValueError, match="total"):
        validate_arguments({f"field{index}": "x" * MAX_ARGUMENT_STRING for index in range(11)})
    assert MAX_ARGUMENT_BYTES == 1_000_000


def test_bind_account_allows_current_identity_and_moodle_zero_sentinel() -> None:
    arguments = {
        "userid": 5,
        "nested": [{"user_id_to": 0}, {"USERIDFROM": 5}],
        "unrelated_user": 999,
    }

    assert bind_account(arguments, 5) is arguments


@pytest.mark.parametrize("key", ["userid", "user_id", "USERIDTO", "user_id_from"])
def test_bind_account_rejects_another_identity_at_any_depth(key: str) -> None:
    with pytest.raises(ValueError, match="identidad autenticada"):
        bind_account({"outer": [{key: 6}]}, user_id=5)


def test_profile_lookup_is_limited_to_the_authenticated_account() -> None:
    with pytest.raises(ValueError, match="perfil genérico"):
        bind_account(
            {"field": "email", "values": ["other@example.test"]},
            5,
            function="core_user_get_users_by_field",
        )


def test_sanitise_result_removes_markup_scripts_and_normalises_containers() -> None:
    class MarkedUpValue:
        def __str__(self) -> str:
            return "<i>objeto</i>"

    result = sanitise_result(
        {
            "html": "<p>Hola&nbsp;<b>USC</b><script>steal()</script></p>",
            "tuple": ("<em>uno</em>", MarkedUpValue()),
        }
    )

    assert result == {"html": "Hola USC", "tuple": ["uno", "objeto"]}


def test_sanitise_result_redacts_secret_fields_and_url_parameters() -> None:
    result = sanitise_result(
        {
            "sesskey": "private-session-key",
            "nested": {
                "url": ("https://cv.usc.es/path?id=7&token=private-token&signature=signed"),
                "resourcekey": "lti-key",
                "servicesalt": "lti-salt",
                "instructorcustomparameters": "lti-internal-parameters",
            },
        }
    )

    encoded = str(result)
    assert "private-session-key" not in encoded
    assert "private-token" not in encoded
    assert "signed" not in encoded
    assert "lti-key" not in encoded
    assert "lti-salt" not in encoded
    assert "lti-internal-parameters" not in encoded
    assert "[REDACTED]" in encoded or "%5BREDACTED%5D" in encoded


@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf])
def test_sanitise_result_rejects_non_finite_numbers(number: float) -> None:
    with pytest.raises(ValueError, match="finito"):
        sanitise_result({"number": number})


def test_sanitise_result_limits_keys_strings_depth_nodes_and_total_bytes() -> None:
    result = sanitise_result({"k" * 101: "x" * (MAX_ARGUMENT_STRING + 1)})
    assert list(result) == ["k" * 100]
    assert len(result["k" * 100]) == MAX_ARGUMENT_STRING

    with pytest.raises(ValueError, match="compleja"):
        sanitise_result(_nested(21))
    with pytest.raises(ValueError, match="compleja"):
        sanitise_result([None] * 20_000)
    with pytest.raises(ValueError, match="2 MB"):
        sanitise_result(["x" * MAX_ARGUMENT_STRING for _ in range(21)])
