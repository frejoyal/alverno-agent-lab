"""Microsoft Entra ID access tokens for the Quality API (docs/adr/0026).

:class:`TokenVerifier` accepts a bearer token only when it is an RS256 JWT signed by a
key of the configured JWKS, issued by the configured tenant for the API's audience,
currently valid, and carrying the app role ``Quality.Ask``. Every rejection names its
code; none echoes the token. In the lab the keys are test keys made by
:func:`make_test_keys`, never a real tenant's.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

REQUIRED_ROLE = "Quality.Ask"
REQUIRED_CLAIMS = ["exp", "nbf", "iat", "aud", "iss", "tid", "oid"]


def issuer_of(tenant_id: str) -> str:
    """Issuer of a Microsoft Entra ID v2.0 access token."""
    return f"https://login.microsoftonline.com/{tenant_id}/v2.0"


class TokenRejected(Exception):
    """The call is refused: 401 for an invalid token, 403 for a missing role."""

    def __init__(self, status: int, code: str, oid: str | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        # Known only when the token itself is valid (403): the user may be logged.
        self.oid = oid


@dataclasses.dataclass(frozen=True)
class Principal:
    oid: str
    tid: str
    roles: tuple[str, ...]


class TokenVerifier:
    def __init__(
        self,
        jwks: Mapping[str, Any],
        *,
        tenant_id: str,
        audience: str,
        required_role: str = REQUIRED_ROLE,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not tenant_id or not audience:
            raise ValueError("tenant_id and audience are required")
        self.keys = {key.key_id: key for key in jwt.PyJWKSet.from_dict(dict(jwks)).keys}
        self.tenant_id = tenant_id
        self.audience = audience
        self.required_role = required_role
        self.clock = clock

    @classmethod
    def from_file(cls, path: Path, *, tenant_id: str, audience: str) -> TokenVerifier:
        return cls(
            json.loads(path.read_text(encoding="utf-8")), tenant_id=tenant_id, audience=audience
        )

    def verify(self, authorization: str | None) -> Principal:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise TokenRejected(401, "missing_token")
        token = token.strip()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise TokenRejected(401, "malformed_token") from None
        if header.get("alg") != "RS256":
            raise TokenRejected(401, "algorithm")
        key = self.keys.get(str(header.get("kid", "")))
        if key is None:
            raise TokenRejected(401, "unknown_key")
        now = self.clock()
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=issuer_of(self.tenant_id),
                options={"require": REQUIRED_CLAIMS, "verify_iat": False},
                leeway=0,
                # PyJWT reads the time itself; the clock is checked below for tests.
            )
        except jwt.ExpiredSignatureError:
            raise TokenRejected(401, "expired") from None
        except jwt.ImmatureSignatureError:
            raise TokenRejected(401, "not_yet_valid") from None
        except jwt.InvalidAudienceError:
            raise TokenRejected(401, "audience") from None
        except jwt.InvalidIssuerError:
            raise TokenRejected(401, "issuer") from None
        except jwt.InvalidSignatureError:
            raise TokenRejected(401, "signature") from None
        except jwt.PyJWTError:
            raise TokenRejected(401, "invalid_token") from None
        if not float(claims["nbf"]) <= now < float(claims["exp"]):
            raise TokenRejected(401, "expired" if now >= float(claims["exp"]) else "not_yet_valid")
        if claims.get("tid") != self.tenant_id:
            raise TokenRejected(401, "tenant")
        roles = claims.get("roles") or []
        if not isinstance(roles, list) or self.required_role not in roles:
            raise TokenRejected(403, "missing_role", oid=str(claims["oid"]))
        return Principal(oid=str(claims["oid"]), tid=str(claims["tid"]), roles=tuple(roles))


# --------------------------------------------------------------------------- test keys
@dataclasses.dataclass(frozen=True)
class TestKeys:
    """A throwaway RSA key pair and its JWKS (lab and tests only)."""

    __test__ = False  # not a pytest class, despite its name

    private: rsa.RSAPrivateKey
    kid: str
    jwks: dict[str, Any]

    def token(
        self,
        *,
        tenant_id: str,
        audience: str,
        roles: tuple[str, ...] = (REQUIRED_ROLE,),
        oid: str = "00000000-0000-0000-0000-000000000001",
        issuer: str | None = None,
        lifetime: int = 3600,
        not_before: float | None = None,
        now: float | None = None,
        algorithm: str = "RS256",
    ) -> str:
        moment = time.time() if now is None else now
        claims: dict[str, Any] = {
            "iss": issuer or issuer_of(tenant_id),
            "aud": audience,
            "tid": tenant_id,
            "oid": oid,
            "iat": int(moment),
            "nbf": int(moment if not_before is None else not_before),
            "exp": int(moment + lifetime),
        }
        if roles:
            claims["roles"] = list(roles)
        if algorithm == "HS256":
            return jwt.encode(claims, "x" * 32, algorithm="HS256", headers={"kid": self.kid})
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": self.kid})


def make_test_keys(kid: str = "alverno-test-key") -> TestKeys:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return TestKeys(private=private, kid=kid, jwks={"keys": [public]})
