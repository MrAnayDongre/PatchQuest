"""Data leaving the trusted boundary, expressed as ordinary policy actions (no second policy system).

Two families of action id, matched by the same ``fnmatch`` rules as every other action:

* ``network.read.domain:<host>``            a fetch that writes nothing remote (``SideEffect.NETWORK_READ``);
  writing to an external system is the existing ``action.<connector.action>`` (``SideEffect.EXTERNAL_WRITE``).
* ``artifact.disclose:<class>:<destination>`` data crossing out of the runtime. ``<destination>`` is either a
  destination kind (``model_provider``, ``connector``, ``portable_bundle``, ``external_api``) or a specific name
  (``github``, ``slack``, a plugin); both are evaluated and the stricter answer wins.

``secret`` is never disclosable: the system floor denies it and a stored policy can only add restrictions.
Anything outside the vocabulary below is refused rather than guessed at.
"""

from __future__ import annotations

from urllib.parse import urlsplit

DATA_CLASSES = ("metadata", "logs", "diff", "source_code", "test_output", "model_io", "trajectory", "artifact", "secret")
DESTINATION_KINDS = ("model_provider", "connector", "portable_bundle", "external_api")

NETWORK_READ_PREFIX = "network.read.domain:"
DISCLOSE_PREFIX = "artifact.disclose:"


def network_read_action(url_or_host: str) -> str:
    """``network.read.domain:<host>`` for a URL or bare host (lower-cased, no port, no credentials)."""
    text = url_or_host.strip()
    host = urlsplit(text if "//" in text else f"//{text}").hostname or ""
    return NETWORK_READ_PREFIX + host.lower().rstrip(".")


def disclosure_actions(data_class: str, kind: str, name: str | None = None) -> tuple[str, ...]:
    """Every action id that governs disclosing ``data_class`` to a destination of ``kind`` called ``name``."""
    if data_class not in DATA_CLASSES:
        raise ValueError(f"unknown data class {data_class!r} (one of {', '.join(DATA_CLASSES)})")
    if kind not in DESTINATION_KINDS:
        raise ValueError(f"unknown destination kind {kind!r} (one of {', '.join(DESTINATION_KINDS)})")
    ids = [f"{DISCLOSE_PREFIX}{data_class}:{kind}"]
    if name and name != kind:
        ids.append(f"{DISCLOSE_PREFIX}{data_class}:{name.lower()}")
    return tuple(ids)
