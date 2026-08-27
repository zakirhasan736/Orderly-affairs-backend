from datetime import timedelta

from datetime import timedelta

from app.auth.claim_tokens import (
    CLAIM_TOKEN_TTL,
    claim_expiry,
    claim_is_expired,
    generate_claim_token,
    hash_claim_token,
    tokens_match,
)


class TestClaimTokens:
    def test_hash_is_deterministic_and_not_plaintext(self):
        token = generate_claim_token()
        digest = hash_claim_token(token)
        assert digest != token
        assert tokens_match(token, digest)
        assert not tokens_match("other", digest)

    def test_expiry_in_future(self):
        expires = claim_expiry()
        assert claim_is_expired(expires) is False
        assert CLAIM_TOKEN_TTL == timedelta(hours=72)
