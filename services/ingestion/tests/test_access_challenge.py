from pathlib import Path

from ingestion.access_challenge import AccessChallengeReason, classify_access_challenge
from ingestion.import_models import FetchedDocument


def _doc(html: str, *, final_url: str | None = None) -> FetchedDocument:
    return FetchedDocument(
        requested_url="https://www.publisher.test/recipe",
        final_url=final_url or "https://www.publisher.test/recipe",
        html=html,
        content_type="text/html",
        byte_count=len(html.encode("utf-8")),
    )


def test_perfdrive_final_host_is_challenge() -> None:
    reason = classify_access_challenge(
        _doc("<html><body>ok</body></html>", final_url="https://validate.perfdrive.com/?x=1")
    )
    assert reason is AccessChallengeReason.PERFDRIVE_HOST


def test_radware_fixture_is_challenge() -> None:
    html = (Path(__file__).parent / "fixtures/access_challenge_radware.html").read_text(
        encoding="utf-8"
    )
    assert classify_access_challenge(_doc(html)) is AccessChallengeReason.RADWARE_MARKERS


def test_normal_recipe_html_is_not_challenge() -> None:
    html = (
        "<html><head><title>Pasta</title></head>"
        "<body><h1>Pasta</h1><ul><li>salt</li></ul><p>Boil water.</p></body></html>"
    )
    assert classify_access_challenge(_doc(html)) is None


def test_sparse_shell_is_not_challenge() -> None:
    html = "<html><body><div id='root'>Loading</div><script></script></body></html>"
    assert classify_access_challenge(_doc(html)) is None
