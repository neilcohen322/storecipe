from enum import StrEnum

from yarl import URL

from ingestion.import_models import FetchedDocument


class AccessChallengeReason(StrEnum):
    PERFDRIVE_HOST = "perfdrive_host"
    RADWARE_MARKERS = "radware_markers"
    CAPTCHA_INTERSTITIAL = "captcha_interstitial"


def _perfdrive_host(final_url: str | None) -> bool:
    if not final_url:
        return False
    host = URL(final_url).host
    return host is not None and host.casefold() == "validate.perfdrive.com"


def _radware_markers(text: str) -> bool:
    lowered = text.casefold()
    return "radware block page" in lowered or "botmanager_support@radware.com" in lowered


def _captcha_interstitial(text: str) -> bool:
    lowered = text.casefold()
    return (
        "you are a bot" in lowered
        and "captcha" in lowered
        and (
            "activity and behavior on this website" in lowered
            or "we cannot process your request" in lowered
        )
    )


def contains_access_challenge_markers(text: str) -> bool:
    """Return True when visible text matches challenge/bot-wall markers."""

    return _radware_markers(text) or _captcha_interstitial(text)


def classify_access_challenge(document: FetchedDocument) -> AccessChallengeReason | None:
    if _perfdrive_host(document.final_url):
        return AccessChallengeReason.PERFDRIVE_HOST
    if _radware_markers(document.html):
        return AccessChallengeReason.RADWARE_MARKERS
    if _captcha_interstitial(document.html):
        return AccessChallengeReason.CAPTCHA_INTERSTITIAL
    return None
