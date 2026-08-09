import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from bs4 import BeautifulSoup
from yarl import URL

from ingestion.import_models import FetchedDocument, ParseFailureCode

_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_RECIPE_MARKER = re.compile(r"recipe", re.IGNORECASE)
_APPLICATION_STATE_MARKER = re.compile(r"__NEXT_DATA__|__APOLLO_STATE__", re.IGNORECASE)


class ShellReason(StrEnum):
    SPARSE_NO_RECIPE = "sparse_no_recipe"
    EMPTY_APP_ROOT = "empty_app_root"
    APPLICATION_STATE_ONLY = "application_state_only"


@dataclass(frozen=True, slots=True)
class _JsonObjectPairs:
    pairs: list[tuple[str, object]]


def _normalize_dns_hostname(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("registry hostnames must be strings")

    has_terminal_dot = value.endswith(".")
    hostname = value[:-1] if has_terminal_dot else value
    if not hostname:
        raise ValueError("registry hostnames must not be empty")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("registry hostnames must not be IP literals")

    try:
        normalized_input = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("registry hostnames must be valid IDNA hostnames") from exc
    if normalized_input.endswith(".") and not has_terminal_dot:
        normalized_input = normalized_input[:-1]

    labels: list[str] = []
    for label in normalized_input.split("."):
        if not label or "*" in label:
            raise ValueError("registry hostnames must not contain wildcard or empty labels")
        if not _DNS_LABEL.fullmatch(label):
            raise ValueError("registry hostnames contain an invalid DNS label")
        if len(label.encode("ascii")) > 63:
            raise ValueError("registry hostname labels must be at most 63 bytes")
        labels.append(label)

    normalized_hostname = ".".join(labels)
    if len(normalized_hostname.encode("ascii")) > 253:
        raise ValueError("registry hostnames must be at most 253 bytes")
    try:
        ipaddress.ip_address(normalized_hostname)
    except ValueError:
        pass
    else:
        raise ValueError("registry hostnames must not be IP literals")
    return normalized_hostname


@dataclass(frozen=True, slots=True)
class ServerRenderedVariantRegistry:
    hosts: Mapping[str, str]

    @classmethod
    def empty(cls) -> "ServerRenderedVariantRegistry":
        return cls(MappingProxyType({}))

    @classmethod
    def from_json(cls, raw: str) -> "ServerRenderedVariantRegistry":
        decoded = json.loads(raw, object_pairs_hook=_JsonObjectPairs)
        if not isinstance(decoded, _JsonObjectPairs):
            raise ValueError("server-rendered variant registry must be a JSON object")

        normalized: dict[str, str] = {}
        raw_sources: set[str] = set()
        for source, target in decoded.pairs:
            if source in raw_sources:
                raise ValueError("server-rendered variant registry has a duplicate source")
            raw_sources.add(source)
            source_host = _normalize_dns_hostname(source)
            target_host = _normalize_dns_hostname(target)
            if source_host in normalized:
                raise ValueError("server-rendered variant registry has a duplicate source")
            normalized[source_host] = target_host
        return cls(MappingProxyType(normalized))

    def candidate_url(self, primary_final_url: str) -> str | None:
        try:
            primary = URL(primary_final_url, encoded=True)
            port = primary.port
        except (TypeError, ValueError, UnicodeError):
            return None
        if primary.scheme != "https" or primary.raw_host is None or port != 443:
            return None

        try:
            source_host = _normalize_dns_hostname(primary.raw_host)
        except ValueError:
            return None
        target = self.hosts.get(source_host)
        return None if target is None else str(primary.with_host(target))


def _has_empty_application_root(soup: BeautifulSoup) -> bool:
    for node in soup.find_all(True):
        if node.get("id") not in {"root", "app"} and not node.has_attr("data-reactroot"):
            continue
        if not node.get_text(" ", strip=True) and not _has_recipe_sections(soup):
            return True
    return False


def _has_recipe_sections(soup: BeautifulSoup) -> bool:
    if soup.find(["article", "main"]) is not None:
        return True
    for node in soup.find_all(["div", "section"]):
        values = [node.get("id"), node.get("class"), node.get("aria-label")]
        if any(_RECIPE_MARKER.search(str(value)) for value in values if value is not None):
            return True
    return False


def _has_application_state_without_recipe_sections(
    document: FetchedDocument, soup: BeautifulSoup
) -> bool:
    has_state_script = _APPLICATION_STATE_MARKER.search(document.html) is not None
    has_application_root = soup.find(id={"root", "app"}) is not None or any(
        node.has_attr("data-reactroot") for node in soup.find_all(True)
    )
    return (has_state_script or has_application_root) and not _has_recipe_sections(soup)


def classify_shell(
    document: FetchedDocument,
    failure: ParseFailureCode,
) -> ShellReason | None:
    if failure not in {
        ParseFailureCode.NO_RECIPE_FOUND,
        ParseFailureCode.INCOMPLETE_RECIPE,
    }:
        return None

    soup = BeautifulSoup(document.html, "html.parser")
    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    visible = " ".join(soup.get_text(" ", strip=True).split())
    if len(visible) < 2_000:
        return ShellReason.SPARSE_NO_RECIPE
    if _has_empty_application_root(soup):
        return ShellReason.EMPTY_APP_ROOT
    if _has_application_state_without_recipe_sections(document, soup):
        return ShellReason.APPLICATION_STATE_ONLY
    return None
