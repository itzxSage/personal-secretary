"""Public, deterministic signing material for hermetic fixtures ONLY.

Never enroll this key in a real deployment. The deployed authority module only
verifies signatures and neither imports this module nor holds a private key.
"""

import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from secretary_service.authority import Approval

FIXTURE_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(
    hashlib.sha256(b"lifeos-public-fixture-approval-v1").digest()
)
FIXTURE_PUBLIC_KEY = FIXTURE_PRIVATE_KEY.public_key().public_bytes_raw().hex()


def sign_fixture_approval(approval: Approval, action_class: str = "calendar.apply") -> Approval:
    """Sign fixture inputs at the simulated client, never incoming server requests."""
    signature = FIXTURE_PRIVATE_KEY.sign(approval.signing_bytes(action_class)).hex()
    return approval.model_copy(update={"signature": signature})
