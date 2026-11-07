"""Validate URL structure without normalizing signed values or echoing them."""

from urllib.parse import urlsplit


def valid_http_url(value: str, *, https_only=False, allow_fragment=True) -> bool:
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return False
    try:
        parts = urlsplit(value)
        # Access validates nonnumeric and out-of-range ports.
        _ = parts.port
        return bool(
            parts.scheme in (("https",) if https_only else ("http", "https"))
            and parts.hostname
            and parts.username is None
            and parts.password is None
            and (allow_fragment or "#" not in value)
        )
    except ValueError:
        return False
