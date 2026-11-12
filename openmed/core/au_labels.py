"""Australian display labels for fork output.

openmed's canonical label for a postal code is ``ZIPCODE``. Australian readers
call it a postcode, so output under a policy whose ``implied_locale`` is
``en_AU`` (the ``ccia_*`` profiles) shows ``POSTCODE`` instead: lean
``/v1/detect`` findings, ``[POSTCODE_n]`` tags rendered by ``apply_spans``,
and the audit span ``label``. Inside openmed, and in the audit's
``canonical_label`` and the Safe Harbor attestation, the label stays
``ZIPCODE``. Every other policy keeps ``ZIPCODE`` everywhere.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from .policy import PolicyProfile

__all__ = [
    "AU_DISPLAY_LABELS",
    "AU_LOCALE",
    "canonical_input_label",
    "display_label",
    "display_labels_for",
]

AU_LOCALE: Final = "en_AU"

#: Canonical label -> label shown in AU output.
AU_DISPLAY_LABELS: Final[Mapping[str, str]] = MappingProxyType({"ZIPCODE": "POSTCODE"})

_INPUT_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {shown: canonical for canonical, shown in AU_DISPLAY_LABELS.items()}
)
_NO_DISPLAY_LABELS: Final[Mapping[str, str]] = MappingProxyType({})


def display_labels_for(profile: PolicyProfile) -> Mapping[str, str]:
    """Return the canonical-to-display label map for a policy.

    Args:
        profile: The resolved policy profile.

    Returns:
        :data:`AU_DISPLAY_LABELS` when the profile implies ``en_AU``, otherwise
        an empty mapping (labels are shown as their canonical names).
    """
    if profile.implied_locale == AU_LOCALE:
        return AU_DISPLAY_LABELS
    return _NO_DISPLAY_LABELS


def display_label(label: str, profile: PolicyProfile) -> str:
    """Return the label to show for ``label`` under ``profile``.

    Args:
        label: A canonical label.
        profile: The resolved policy profile.

    Returns:
        The AU display label under an ``en_AU`` profile, else ``label``.
    """
    return display_labels_for(profile).get(label, label)


def canonical_input_label(label: str) -> str:
    """Map a display label a caller sends back (``POSTCODE``) to its canonical.

    Args:
        label: A label from a caller request.

    Returns:
        The canonical label, or ``label`` unchanged when it is not a display
        label.
    """
    return _INPUT_ALIASES.get(label, label)
