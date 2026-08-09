import base64
import json

import pytest
from bs4 import BeautifulSoup

import ingestion.server_rendered_variants as variants
from ingestion.import_models import FetchedDocument, ParseFailureCode
from ingestion.server_rendered_variants import (
    ServerRenderedVariantRegistry,
    ShellReason,
    classify_shell,
)


def document(html: str) -> FetchedDocument:
    return FetchedDocument(
        requested_url="https://www.publisher.test/recipes/a",
        final_url="https://www.publisher.test/recipes/a",
        html=html,
        content_type="text/html",
        byte_count=len(html.encode()),
    )


def test_registry_is_empty_by_default() -> None:
    from ingestion.config import Settings

    key = base64.b64encode(b"t" * 32).decode()
    settings = Settings(payload_active_key_id="test", payload_keyring=f"test={key}")

    assert (
        settings.server_rendered_variant_registry.candidate_url(
            "https://www.publisher.test/recipes/a?tag=1&tag=2"
        )
        is None
    )


def test_empty_registry_is_immutable_and_has_no_hosts() -> None:
    registry = ServerRenderedVariantRegistry.empty()

    assert dict(registry.hosts) == {}
    with pytest.raises(TypeError):
        registry.hosts["www.publisher.test"] = "mobile.publisher.test"  # type: ignore[index]


def test_exact_registry_changes_only_case_normalized_host() -> None:
    registry = ServerRenderedVariantRegistry.from_json(
        '{"www.publisher.test":"mobile.publisher.test"}'
    )

    assert (
        registry.candidate_url("https://WWW.PUBLISHER.TEST:443/a%2Fb?tag=1&tag=2")
        == "https://mobile.publisher.test/a%2Fb?tag=1&tag=2"
    )


def test_candidate_url_preserves_raw_path_and_ordered_query_encoding() -> None:
    registry = ServerRenderedVariantRegistry.from_json(
        '{"www.publisher.test":"mobile.publisher.test"}'
    )

    assert (
        registry.candidate_url("https://www.publisher.test/a%61%2Fb?x=%7e&tag=2&tag=1&tag=2")
        == "https://mobile.publisher.test/a%61%2Fb?x=%7e&tag=2&tag=1&tag=2"
    )


def test_registry_normalizes_one_terminal_dot() -> None:
    registry = ServerRenderedVariantRegistry.from_json(
        '{"WWW.PUBLISHER.TEST.":"MOBILE.PUBLISHER.TEST."}'
    )

    assert registry.candidate_url("https://www.publisher.test/recipes/a") == (
        "https://mobile.publisher.test/recipes/a"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://publisher.test/a",
        "https://www.publisher.test.attacker.test/a",
        "https://attacker-www.publisher.test/a",
        "https://unknown.test/a",
    ],
)
def test_registry_never_uses_suffix_matching(url: str) -> None:
    registry = ServerRenderedVariantRegistry.from_json(
        '{"www.publisher.test":"mobile.publisher.test"}'
    )

    assert registry.candidate_url(url) is None


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"":"mobile.publisher.test"}',
        '{"www.publisher.test":""}',
        '{"https://www.publisher.test":"mobile.publisher.test"}',
        '{"www.publisher.test/path":"mobile.publisher.test"}',
        '{"www.publisher.test:443":"mobile.publisher.test"}',
        '{"www.publisher.test..":"mobile.publisher.test"}',
        '{"user@www.publisher.test":"mobile.publisher.test"}',
        '{"127.0.0.1":"mobile.publisher.test"}',
        '{"*.publisher.test":"mobile.publisher.test"}',
        '{"www.publisher.test":"*.mobile.publisher.test"}',
        '{"' + "a" * 64 + '.test":"mobile.publisher.test"}',
        '{"www.publisher.test":"' + "a" * 64 + '.test"}',
        '{"' + ".".join("a" * 63 for _ in range(4)) + '.test":"mobile.publisher.test"}',
    ],
)
def test_registry_rejects_invalid_hostnames(raw: str) -> None:
    with pytest.raises(ValueError):
        ServerRenderedVariantRegistry.from_json(raw)


def test_registry_rejects_duplicate_normalized_source_hosts() -> None:
    raw = json.dumps(
        {
            "WWW.PUBLISHER.TEST": "mobile.publisher.test",
            "www.publisher.test": "other.publisher.test",
        }
    )

    with pytest.raises(ValueError, match="duplicate source"):
        ServerRenderedVariantRegistry.from_json(raw)


def test_registry_rejects_byte_for_byte_duplicate_source_hosts() -> None:
    raw = (
        '{"www.publisher.test":"mobile.publisher.test","www.publisher.test":"other.publisher.test"}'
    )

    with pytest.raises(ValueError, match="duplicate source"):
        ServerRenderedVariantRegistry.from_json(raw)


@pytest.mark.parametrize("host", ["１２７.０.０.１", "１２７．０．０．１"])
def test_registry_rejects_ip_literals_after_idna_normalization(host: str) -> None:
    with pytest.raises(ValueError, match="IP literals"):
        ServerRenderedVariantRegistry.from_json(json.dumps({host: "mobile.publisher.test"}))


def test_registry_rejects_non_string_host_values() -> None:
    with pytest.raises(ValueError):
        ServerRenderedVariantRegistry.from_json('{"www.publisher.test": ["mobile.publisher.test"]}')


@pytest.mark.parametrize(
    "url",
    [
        "http://www.publisher.test/recipes/a",
        "https://www.publisher.test:8443/recipes/a",
        "https://127.0.0.1/recipes/a",
    ],
)
def test_registry_only_candidates_https_default_port(url: str) -> None:
    registry = ServerRenderedVariantRegistry.from_json(
        '{"www.publisher.test":"mobile.publisher.test"}'
    )

    assert registry.candidate_url(url) is None


def test_sparse_failed_document_is_classified_as_shell() -> None:
    assert (
        classify_shell(document('<div id="root"></div>'), ParseFailureCode.NO_RECIPE_FOUND)
        is ShellReason.SPARSE_NO_RECIPE
    )


def test_empty_application_root_is_classified_after_sparse_threshold() -> None:
    html = '<div id="root"></div><p>' + ("useful text " * 300) + "</p>"

    assert classify_shell(document(html), ParseFailureCode.INCOMPLETE_RECIPE) is (
        ShellReason.EMPTY_APP_ROOT
    )


def test_useful_long_recipe_content_with_an_unrelated_empty_root_is_not_a_shell() -> None:
    html = (
        '<div id="root"></div><div id="app"></div><div data-reactroot></div>'
        '<main><section class="recipe-content"><p>'
        + ("cook the ingredients " * 150)
        + "</p></section></main>"
    )

    assert classify_shell(document(html), ParseFailureCode.NO_RECIPE_FOUND) is None


def test_recipe_section_detection_runs_once_for_many_empty_root_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markers = "".join(
        '<div id="root"></div><div id="app"></div><div data-reactroot></div>' for _ in range(50)
    )
    html = (
        markers
        + '<main><section class="recipe-content"><p>'
        + ("cook the ingredients " * 150)
        + "</p></section></main>"
    )
    calls = 0
    original = variants._has_recipe_sections

    def recipe_sections_spy(soup: BeautifulSoup) -> bool:
        nonlocal calls
        calls += 1
        return original(soup)

    monkeypatch.setattr(variants, "_has_recipe_sections", recipe_sections_spy)

    assert classify_shell(document(html), ParseFailureCode.NO_RECIPE_FOUND) is None
    assert calls == 1


def test_application_state_without_recipe_sections_is_classified() -> None:
    html = (
        '<script id="__NEXT_DATA__">{"buildId":"test"}</script>'
        '<div id="app"><p>' + ("application content " * 150) + "</p></div>"
    )

    assert classify_shell(document(html), ParseFailureCode.NO_RECIPE_FOUND) is (
        ShellReason.APPLICATION_STATE_ONLY
    )


def test_useful_long_recipe_content_returns_none() -> None:
    html = "<main><h1>Recipe</h1><p>" + ("cook the ingredients " * 150) + "</p></main>"

    assert classify_shell(document(html), ParseFailureCode.NO_RECIPE_FOUND) is None


def test_complete_json_ld_document_is_not_a_shell_failure() -> None:
    from ingestion.jsonld import parse_recipe_jsonld

    html = (
        '<script type="application/ld+json">'
        '{"@type":"Recipe","name":"Complete",'
        '"recipeIngredient":["1 cup water"],"recipeInstructions":["Mix"]}'
        "</script>"
    )

    assert parse_recipe_jsonld(document(html)).title == "Complete"
