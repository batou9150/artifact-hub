"""Signing key for the access tokens this service issues (ES256, P-256).

The private key comes from OAUTH_SIGNING_KEY (PEM, Secret Manager in deployed
environments). The `kid` is the RFC 7638 thumbprint, so rotating the key changes
the kid and verifiers pick the right key from the JWKS.
"""
from __future__ import annotations

import base64
import hashlib
import json

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ALGORITHM = "ES256"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SigningKey:
    def __init__(self, private_key: ec.EllipticCurvePrivateKey):
        if not isinstance(private_key, ec.EllipticCurvePrivateKey) or private_key.curve.name != "secp256r1":
            raise ValueError("OAUTH_SIGNING_KEY must be an EC P-256 private key")
        self._private = private_key
        numbers = private_key.public_key().public_numbers()
        self._jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": _b64(numbers.x.to_bytes(32, "big")),
            "y": _b64(numbers.y.to_bytes(32, "big")),
        }
        canonical = json.dumps(self._jwk, separators=(",", ":"), sort_keys=True).encode()
        self.kid = _b64(hashlib.sha256(canonical).digest())

    @classmethod
    def from_pem(cls, pem: str) -> "SigningKey":
        return cls(serialization.load_pem_private_key(pem.encode(), password=None))

    @classmethod
    def generate(cls) -> "SigningKey":
        return cls(ec.generate_private_key(ec.SECP256R1()))

    def private_pem(self) -> str:
        return self._private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()).decode()

    def jwks(self) -> dict:
        return {"keys": [{**self._jwk, "kid": self.kid, "use": "sig", "alg": ALGORITHM}]}

    def sign(self, claims: dict) -> str:
        return jwt.encode(claims, self._private, algorithm=ALGORITHM,
                          headers={"kid": self.kid, "typ": "at+jwt"})

    def verify(self, token: str, *, issuer: str, audience: str) -> dict:
        """Raises jwt.PyJWTError on any failure."""
        header = jwt.get_unverified_header(token)
        if header.get("kid") != self.kid or header.get("typ") != "at+jwt":
            raise jwt.InvalidTokenError("not an access token of this issuer")
        return jwt.decode(token, self._private.public_key(), algorithms=[ALGORITHM], issuer=issuer,
                          audience=audience, leeway=30,
                          options={"require": ["exp", "iat", "iss", "aud", "sub", "client_id"]})
