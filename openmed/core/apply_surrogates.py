"""Per-patient surrogates for the ``replace`` apply treatment (mt.3 E4-E6).

``replace`` renders openmed's existing anonymizer surrogates, keyed per
patient so the same value gets the same surrogate in every document of that
patient:

* ``patient_secret = HMAC-SHA256(secret, SURROGATE_DOMAIN + patient_key)``.
  The caller's ``secret`` is used, never the audit key, and the domain keeps
  it independent of the date-shift HMAC.
* The surrogate comes from ``Anonymizer(lang="en", locale="en_AU",
  consistent=True, seed=int.from_bytes(patient_secret[:8], "big"))``. The
  value it is given is the :func:`consistency_key` of the source, not the raw
  surface, so ``Jane CITIZEN`` and ``jane  citizen`` share one surrogate.
* A request-local ``SurrogateVault.in_memory(hmac_secret=patient_secret)``
  enforces one surrogate per source within the call and retries a candidate
  that collides with another source's surrogate or contains the source. It is
  dropped when the call returns: nothing is written to disk, cached across
  requests or held in module state. The vault keys entries by an HMAC of the
  source, so it never holds an original value in clear, and the raw
  ``patient_key`` is only ever an HMAC input.

Fail-closed rules. :meth:`PatientSurrogates.surrogate` returns ``None``, and
apply renders a ``[LABEL_n]`` tag with action ``mark``, when:

* the label is not in :data:`SURROGATE_LABELS`;
* the generator fails (the engine's ``[LABEL]`` fallback);
* every retry collides with a surrogate already given to a different source
  in this call, under any label, equals (normalised) *any* source value of
  the call, or, for a name label, shares a word with any source name in the
  call, or, for an address label, shares a significant word with any source
  address in the call. The 30-number ACMA fictional mobile pool is the realistic
  case: a call with more distinct mobiles than the pool can hold gives tags
  for the excess.

Apply registers every span's source with :meth:`PatientSurrogates.reserve`
before rendering. Two different sources in one call therefore never share a
surrogate, a surrogate never equals any source of the call (so a real value
is never moved onto another person), and a name surrogate never keeps a real
name token (``Mary Jones`` never becomes ``Shelly Jones``), and an address or
location surrogate never keeps a significant source address word (``12
George Street`` never becomes ``8 George Circle``). Words are compared after
NFKD folding with combining marks stripped and casefolding, so ``José``
matches ``Jose``. Significant address words exclude numbers, single letters,
unit or level markers, street-type words, state names and abbreviations, and
postcodes; the street name, suburb and property name remain. Surrogates
are checksum-valid fakes, so one may coincide with a real person's number; it
is never the source's own.

Determinism limits (documented, not fixed): surrogates are stable per release
(a Faker or generator change can change them); a collision retry depends on
the other values in the same call, so a value can get a different surrogate
across documents (for names and identifiers this is rare; for a mobile, from
the 30-number fictional pool, it is roughly (N-1)/30 when a call holds N
distinct mobiles); and the pool-collision guarantee holds within one call
only. Because candidates are checked against every source
of the call, the other values of a call can force a retry and so change a
surrogate.

The anonymizer (and with it Faker) is imported only when a
:class:`PatientSurrogates` is built, so ``mark``-only callers never load it.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from collections.abc import Iterable
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .anonymizer.engine import Anonymizer

__all__ = [
    "ADDRESS_LABELS",
    "NAME_LABELS",
    "SURROGATE_DOMAIN",
    "SURROGATE_LABELS",
    "PatientSurrogates",
    "consistency_key",
    "restates_source",
]

SURROGATE_DOMAIN: Final = b"openmed.apply.surrogate.v1\x00"
_RETRY_DOMAIN: Final = b"openmed.apply.surrogate.retry.v1\x00"

_AU_ID_LABELS: Final[frozenset[str]] = frozenset(
    {
        "MEDICARE_NUMBER",
        "TFN",
        "IHI",
        "ABN",
        "ACN",
        "AHPRA_NUMBER",
        "DVA_NUMBER",
        "MEDICARE_PROVIDER_NUMBER",
    }
)
# Identifier labels whose consistency key is their compact alphanumerics.
_ID_LABELS: Final[frozenset[str]] = _AU_ID_LABELS | {"ID_NUM", "ACCOUNT_NUMBER"}

SURROGATE_LABELS: Final[frozenset[str]] = frozenset(
    {
        "PERSON",
        "FIRST_NAME",
        "MIDDLE_NAME",
        "LAST_NAME",
        "EMAIL",
        "PHONE",
        "STREET_ADDRESS",
        "LOCATION",
        "ZIPCODE",
        "ORGANIZATION",
        "URL",
        "USERNAME",
    }
    | _ID_LABELS
)

NAME_LABELS: Final[frozenset[str]] = frozenset(
    {"PERSON", "FIRST_NAME", "MIDDLE_NAME", "LAST_NAME"}
)

ADDRESS_LABELS: Final[frozenset[str]] = frozenset({"STREET_ADDRESS", "LOCATION"})

# Words that carry no identifying content in an Australian address: unit and
# level markers, street types (AS 4590 names and common abbreviations), state
# abbreviations and a few connectives. Numbers, postcodes and single letters
# are dropped separately; full state names are removed as phrases.
_ADDRESS_GENERIC_WORDS: Final[frozenset[str]] = frozenset(
    {
        # unit, level and building markers
        "unit", "u", "flat", "apartment", "apt", "suite", "ste", "level",
        "lvl", "floor", "fl", "shop", "lot", "villa", "townhouse", "building",
        "bldg", "po", "box", "gpo", "locked", "bag", "rmb", "rsd",
        # street types
        "street", "st", "road", "rd", "avenue", "ave", "av", "lane", "ln",
        "drive", "dr", "court", "ct", "place", "pl", "crescent", "cres",
        "cr", "parade", "pde", "highway", "hwy", "terrace", "tce",
        "boulevard", "blvd", "bvd", "close", "cl", "way", "wy", "circuit",
        "cct", "grove", "gr", "gv", "square", "sq", "esplanade", "esp",
        "circle", "cir", "row", "walk", "wk", "track", "trk", "trail",
        "parkway", "pkwy", "rise", "view", "vw", "mews", "loop", "link",
        "promenade", "prom", "quay", "alley", "arcade", "arc", "bend",
        "brae", "chase", "concourse", "cove", "crest", "cross", "crossing",
        "end", "entrance", "freeway", "fwy", "gardens", "gdns", "gate",
        "glade", "glen", "green", "grn", "heights", "hts", "hill", "junction",
        "lookout", "mall", "meander", "motorway", "nook", "outlook", "park",
        "pass", "path", "pathway", "plaza", "pocket", "point", "pt",
        "reserve", "retreat", "ridge", "ramble", "ring", "route", "strip",
        "turn", "vista", "wharf", "wynd", "boardwalk", "bypass", "causeway",
        "corner", "cnr", "east", "west", "north", "south", "upper", "lower",
        # states and country
        "nsw", "vic", "qld", "sa", "wa", "tas", "nt", "act", "australia",
        # connectives
        "of", "the", "and", "on", "at", "near",
    }
)  # fmt: skip
_STATE_NAME_RE: Final = re.compile(
    r"\b(?:new\s+south\s+wales|victoria|queensland|south\s+australia|"
    r"western\s+australia|tasmania|northern\s+territory|"
    r"australian\s+capital\s+territory)\b",
    re.IGNORECASE,
)

_LANG: Final = "en"
_LOCALE: Final = "en_AU"
_NON_DIGIT_RE: Final = re.compile(r"\D")
_NON_ALNUM_RE: Final = re.compile(r"[^0-9A-Za-z]")
_ENGINE_TAG_RE: Final = re.compile(r"\[[^\[\]]+\]")
_NAME_TOKEN_RE: Final = re.compile(r"[^\W\d_]+")


def consistency_key(source: str, label: str) -> str:
    """Return the value a source is keyed and seeded by.

    ``PHONE`` uses its national digits (``+61 4…`` and ``04…`` share a key).
    Identifier labels use their alphanumerics, upper-cased, so the layout
    does not matter but letters still do (``NX123456`` and ``QX123456`` stay
    distinct). Every other label is casefolded with whitespace collapsed,
    the same key mark numbering uses.

    Args:
        source: The span's source text.
        label: The span's canonical label.

    Returns:
        The consistency key; empty when the source has no keyable content.
    """
    if label == "PHONE":
        digits = _NON_DIGIT_RE.sub("", source)
        if source.strip().startswith("+61") and digits.startswith("61"):
            rest = digits[2:]
            return rest if rest.startswith("0") else "0" + rest
        return digits
    if label in _ID_LABELS:
        return _NON_ALNUM_RE.sub("", source).upper()
    return " ".join(source.split()).casefold()


def _text_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def _forms(value: str, label: str) -> set[str]:
    """Normalised forms a value is compared by: its label key and text key."""
    return {form for form in (consistency_key(value, label), _text_key(value)) if form}


def _fold_word(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def _name_tokens(value: str) -> set[str]:
    return {_fold_word(token) for token in _NAME_TOKEN_RE.findall(value)}


def _address_words(value: str) -> set[str]:
    without_states = _STATE_NAME_RE.sub(" ", value)
    return {
        word
        for word in _name_tokens(without_states)
        if len(word) > 1 and word not in _ADDRESS_GENERIC_WORDS
    }


def restates_source(value: str, source: str, label: str) -> bool:
    """Return whether ``value`` normalises to, or contains, ``source``.

    Both are compared by the label's :func:`consistency_key` and by their
    casefolded, whitespace-collapsed text, so ``jane  CITIZEN`` and
    ``Ms Jane Citizen (pt)`` both restate ``Jane Citizen``.

    Args:
        value: A candidate rendering (a caller ``replacement``, say).
        source: The span's source text.
        label: The span's canonical label.

    Returns:
        True when a normalised form of ``value`` contains the same normalised
        form of ``source`` (equality included).
    """
    pairs = (
        (consistency_key(value, label), consistency_key(source, label)),
        (_text_key(value), _text_key(source)),
    )
    return any(src and src in val for val, src in pairs)


def _patient_secret(patient_key: str, secret: str) -> bytes:
    return hmac.new(
        secret.encode("utf-8"),
        SURROGATE_DOMAIN + patient_key.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def _seed(material: bytes) -> int:
    return int.from_bytes(material[:8], "big")


class PatientSurrogates:
    """Request-local surrogate source for one patient.

    Build one per apply call and drop it afterwards. It holds no original
    value in clear: the vault keys entries by HMAC, and the local cache is
    keyed by ``(label, HMAC of the consistency key)``.

    Args:
        patient_key: The caller's stable (already HMAC'd) patient key.
        secret: The caller's secret paired with ``patient_key``.
    """

    def __init__(self, *, patient_key: str, secret: str) -> None:
        # Imported here so that ``mark``-only callers never load the anonymizer
        # or Faker.
        from .surrogate_vault import SurrogateVault

        self._patient_secret = _patient_secret(patient_key, secret)
        self._vault = SurrogateVault.in_memory(hmac_secret=self._patient_secret)
        self._anonymizers: dict[int, Anonymizer] = {}
        # (label, keyed hash) -> rendered surrogate, or None for a tag.
        self._results: dict[tuple[str, str], str | None] = {}
        # Every surrogate given out in this call, across labels.
        self._given: set[str] = set()
        # Normalised forms of every source value of the call, and every token
        # of every source name. Held only for the call; never persisted.
        self._reserved: set[str] = set()
        self._reserved_name_tokens: set[str] = set()
        self._reserved_address_words: set[str] = set()

    def reserve(self, sources: Iterable[tuple[str, str]]) -> None:
        """Register source values that no surrogate may restate.

        Apply passes every span's ``(source, label)`` of the call before
        rendering. A candidate equal to any of them (by normalised form) is
        rejected, a name-label candidate sharing a folded word with any source
        name is rejected, and an address-label candidate sharing a folded word
        with any significant source address word is rejected.

        Args:
            sources: ``(source text, canonical label)`` pairs.
        """
        for source, label in sources:
            self._reserved |= _forms(source, label)
            if label in NAME_LABELS:
                self._reserved_name_tokens |= _name_tokens(source)
            if label in ADDRESS_LABELS:
                self._reserved_address_words |= _address_words(source)

    def _anonymizer(self, attempt: int) -> Anonymizer:
        anonymizer = self._anonymizers.get(attempt)
        if anonymizer is None:
            from .anonymizer.engine import Anonymizer

            if attempt == 0:
                seed = _seed(self._patient_secret)
            else:
                material = _RETRY_DOMAIN + str(attempt).encode("ascii")
                seed = _seed(
                    hmac.new(self._patient_secret, material, hashlib.sha256).digest()
                )
            anonymizer = Anonymizer(
                lang=_LANG, locale=_LOCALE, consistent=True, seed=seed
            )
            self._anonymizers[attempt] = anonymizer
        return anonymizer

    def surrogate(self, source: str, label: str) -> str | None:
        """Return this patient's surrogate for ``source``.

        Args:
            source: The span's source text.
            label: The span's canonical label.

        Returns:
            The surrogate, or ``None`` when apply must fall back to a tag
            (unsupported label, generator failure, or no candidate that is
            distinct from the source and from every other source's surrogate
            in this call).
        """
        if label not in SURROGATE_LABELS:
            return None
        key = consistency_key(source, label)
        if not key:
            return None
        self.reserve([(source, label)])
        cache_key = (label, self._vault.text_hash(key))
        if cache_key in self._results:
            return self._results[cache_key]
        produced: set[str] = set()

        def create(attempt: int) -> str:
            candidate = self._anonymizer(attempt).surrogate(key, label)
            if not isinstance(candidate, str) or not candidate.strip():
                return ""
            if _ENGINE_TAG_RE.search(candidate):
                return ""
            if candidate in self._given or _restates(candidate, key, label):
                return ""
            if _forms(candidate, label) & self._reserved:
                return ""
            if label in NAME_LABELS and (
                _name_tokens(candidate) & self._reserved_name_tokens
            ):
                return ""
            if label in ADDRESS_LABELS and (
                _name_tokens(candidate) & self._reserved_address_words
            ):
                return ""
            produced.add(candidate)
            return candidate

        result = self._vault.get_or_create(
            key, label=label, lang=_LANG, create_surrogate=create
        )
        # The vault's last resort is ``<base>_<hash>``, never a candidate; that
        # is pool exhaustion, which apply renders as a tag.
        surrogate = result if result in produced else None
        self._results[cache_key] = surrogate
        if surrogate is not None:
            self._given.add(surrogate)
        return surrogate


def _restates(candidate: str, key: str, label: str) -> bool:
    candidate_key = consistency_key(candidate, label)
    if candidate_key == key:
        return True
    if label == "PHONE" or label in _ID_LABELS:
        shorter, longer = sorted((candidate_key, key), key=len)
        return len(shorter) >= 4 and longer.startswith(shorter)
    return len(key) >= 4 and key in candidate_key
