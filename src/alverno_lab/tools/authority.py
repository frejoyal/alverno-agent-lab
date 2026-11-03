"""Single-use HMAC approval tokens bound to the exact arguments of one write (ADR 0013).

:class:`ApprovalAuthority` is held by the envelope, outside the graph. It keeps a random
HMAC key per instance (``secrets.token_bytes(32)``), so per process: no approval
survives a restart and no token minted by another authority verifies here. The key never
leaves memory: it is absent from ``repr``, ``str`` and every exception message, the
classes have no ``__dict__``, and pickling or copying an authority or a verifier is
refused.

A token holds no content: the action id, the write tool, the SHA-256 of the canonical
arguments, a random nonce and the MAC over those four fields.

The executor never receives the authority, only its :class:`ApprovalVerifier`
(:attr:`ApprovalAuthority.verifier`): a facet that verifies and redeems tokens and has
no ``mint``, so code that holds the executor cannot approve its own write.
:meth:`ApprovalVerifier.redeem` checks a token for the tool about to run and the digest
of the arguments about to be passed, and marks its nonce used, in one step under a lock:
of two concurrent uses of one token, exactly one gets ``"ok"``. Arguments changed after
the approval give another digest, so the token is refused.

Every check reads a snapshot of the token's five fields, taken once, of exact type
``str`` and matching their patterns. A token built with ``model_construct``, a subclass
of :class:`ApprovalToken` or a field of a ``str`` subclass (whose ``__eq__`` or
``__hash__`` could lie to the nonce set) is not genuine.

Ordering is the envelope's job (I5), not this module's: ADR 0013 mints a token **only
after** the ``approved`` decision record is written to the access log, and a log that
cannot be written means no token at all (``audit_unavailable``).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from typing import Literal, NamedTuple, NoReturn, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alverno_lab.connectors.types import ActionId, Sha256Hex, WriteToolName

__all__ = [
    "KEY_BYTES",
    "ApprovalAuthority",
    "ApprovalError",
    "ApprovalToken",
    "ApprovalVerifier",
    "VerifyResult",
]

VerifyResult = Literal["ok", "bad_mac", "wrong_tool", "wrong_args", "reused"]

KEY_BYTES = 32
# Domain separation: a MAC of this scheme never equals a MAC of another use of the key.
_MAC_DOMAIN = "alverno-approval-v1"
_WRITE_TOOLS: frozenset[str] = frozenset(get_args(WriteToolName))
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class ApprovalError(ValueError):
    """Minting or consuming a token failed. The message is ``<step>:<rule>``, no value."""


class ApprovalToken(BaseModel):
    """Proof that a logged human decision approved these exact arguments, usable once.

    Any caller can build one; only the authority's verifier says whether it is genuine.
    It satisfies the connectors' ``WriteToken`` protocol.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    action_id: ActionId
    tool: WriteToolName
    args_sha256: Sha256Hex
    nonce: str = Field(pattern=r"^[0-9a-f]{32}$", repr=False)
    mac: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)


class _Fields(NamedTuple):
    """A token's fields, read once, each of exact type ``str``."""

    action_id: str
    tool: str
    args_sha256: str
    nonce: str
    mac: str


def _snapshot(token: object) -> _Fields | None:
    """The plain-``str`` fields of a well-formed token, or ``None``."""
    if type(token) is not ApprovalToken:
        return None
    # ``__dict__`` holds the field values, also for a ``model_construct`` token, which
    # may lack some of them or hold another type.
    values = vars(token)
    fields = [values.get(name) for name in _Fields._fields]
    if not all(type(value) is str for value in fields):
        return None
    snap = _Fields(*(str(value) for value in fields))
    well_formed = (
        _HEX32.fullmatch(snap.action_id) is not None
        and snap.tool in _WRITE_TOOLS
        and _HEX64.fullmatch(snap.args_sha256) is not None
        and _HEX32.fullmatch(snap.nonce) is not None
        and _HEX64.fullmatch(snap.mac) is not None
    )
    return snap if well_formed else None


def _message(action_id: str, tool: str, args_sha256: str, nonce: str) -> bytes:
    # Every field has a fixed pattern without a line break, so the join is unambiguous.
    return "\n".join((_MAC_DOMAIN, action_id, tool, args_sha256, nonce)).encode("utf-8")


def _mac(key: bytes, action_id: str, tool: str, args_sha256: str, nonce: str) -> str:
    return hmac.new(key, _message(action_id, tool, args_sha256, nonce), hashlib.sha256).hexdigest()


class _Sealed:
    """No ``repr`` of the key, no pickling, no copy."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(key=<hidden>)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError(f"{type(self).__name__}:not_picklable")

    def __getstate__(self) -> NoReturn:
        raise TypeError(f"{type(self).__name__}:not_picklable")

    def __copy__(self) -> NoReturn:
        raise TypeError(f"{type(self).__name__}:not_copyable")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError(f"{type(self).__name__}:not_copyable")


class ApprovalVerifier(_Sealed):
    """Verifies and redeems the tokens of one authority; it cannot mint.

    Built by :class:`ApprovalAuthority` only. Every method holds one lock, so a check and
    the marking of a nonce are atomic.
    """

    __slots__ = ("_key", "_lock", "_used")

    def __init__(self, key: bytes) -> None:
        self._key = key
        self._used: set[str] = set()
        self._lock = threading.Lock()

    def _genuine(self, snap: _Fields) -> bool:
        expected = _mac(self._key, snap.action_id, snap.tool, snap.args_sha256, snap.nonce)
        return hmac.compare_digest(expected, snap.mac)

    def _check(self, snap: _Fields | None, tool: object, args_sha256: object) -> VerifyResult:
        # Caller holds the lock. Checked in order: the MAC (a forged or tampered token,
        # or one minted by another authority, gives ``bad_mac``), the tool, the argument
        # digest, then the nonce (``reused`` once redeemed or consumed).
        if snap is None or not self._genuine(snap):
            return "bad_mac"
        if type(tool) is not str or snap.tool != tool:
            return "wrong_tool"
        if type(args_sha256) is not str or not hmac.compare_digest(
            snap.args_sha256.encode("ascii"), args_sha256.encode("utf-8", "surrogatepass")
        ):
            return "wrong_args"
        if snap.nonce in self._used:
            return "reused"
        return "ok"

    def verify(self, token: ApprovalToken, tool: str, args_sha256: str) -> VerifyResult:
        """Check a token for this tool and these arguments, without spending it."""
        with self._lock:
            return self._check(_snapshot(token), tool, args_sha256)

    def redeem(self, token: ApprovalToken, tool: str, args_sha256: str) -> VerifyResult:
        """Check a token and, when the verdict is ``"ok"``, spend it, in one atomic step.

        Never raises. A refused token is not spent.
        """
        snap = _snapshot(token)
        with self._lock:
            verdict = self._check(snap, tool, args_sha256)
            if verdict == "ok" and snap is not None:
                self._used.add(snap.nonce)
            return verdict

    def consume(self, token: ApprovalToken) -> None:
        """Spend a token; refuse one that is not genuine or is already spent."""
        rule: str | None = None
        with self._lock:
            snap = _snapshot(token)
            if snap is None or not self._genuine(snap):
                rule = "consume:bad_mac"
            elif snap.nonce in self._used:
                rule = "consume:reused"
            else:
                self._used.add(snap.nonce)
        if rule is not None:
            raise ApprovalError(rule)


def _new_token(
    action_id: str, tool: WriteToolName, args_sha256: str, nonce: str, mac: str
) -> object:
    # The ValidationError is dropped here, so the caller raises outside any except
    # block and no exception chain keeps the invalid fields.
    try:
        return ApprovalToken(
            action_id=action_id, tool=tool, args_sha256=args_sha256, nonce=nonce, mac=mac
        )
    except (ValidationError, TypeError, AttributeError):
        return None


class ApprovalAuthority(_Sealed):
    """Mints approval tokens with a per-instance HMAC key; held by the envelope only."""

    __slots__ = ("_key", "_verifier")

    def __init__(self, key: bytes | None = None) -> None:
        if key is None:
            key = secrets.token_bytes(KEY_BYTES)
        elif not isinstance(key, bytes) or len(key) < KEY_BYTES:
            raise ApprovalError("authority:key_too_short")
        self._key = key
        self._verifier = ApprovalVerifier(key)

    @property
    def verifier(self) -> ApprovalVerifier:
        """The facet to give the executor: verify and redeem, no mint."""
        return self._verifier

    def mint(self, action_id: str, tool: WriteToolName, args_sha256: str) -> ApprovalToken:
        """A fresh token for one write; call only after the ``approved`` record is logged."""
        nonce = secrets.token_hex(16)
        token: object = None
        if all(type(value) is str for value in (action_id, tool, args_sha256)):
            mac = _mac(self._key, action_id, tool, args_sha256, nonce)
            token = _new_token(action_id, tool, args_sha256, nonce, mac)
        if not isinstance(token, ApprovalToken):
            raise ApprovalError("mint:invalid_fields")
        return token

    def verify(self, token: ApprovalToken, tool: str, args_sha256: str) -> VerifyResult:
        """:meth:`ApprovalVerifier.verify` of this authority's verifier."""
        return self._verifier.verify(token, tool, args_sha256)

    def consume(self, token: ApprovalToken) -> None:
        """:meth:`ApprovalVerifier.consume` of this authority's verifier."""
        self._verifier.consume(token)
