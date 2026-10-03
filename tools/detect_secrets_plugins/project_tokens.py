import re

from detect_secrets.plugins.base import RegexBasedDetector

_SECRET_NAME = r"\w*(?:token|secret|api_?key|password)\w*"


class ProjectTokenAssignmentDetector(RegexBasedDetector):
    """Catch literal values assigned to secret-like names.

    The stock plugins miss short, low-entropy credentials such as Deriv API tokens,
    and "token" is not one of their keywords.
    """

    secret_type = "Project Token Assignment"

    denylist = (
        # Quoted literal: Python, YAML, JSON.
        re.compile(rf"""(?i)\b{_SECRET_NAME}["']?\s*[:=]\s*["']([^"'\s]{{8,}})["']"""),
        # Unquoted .env line with a value. Case-sensitive: env names are uppercase, and this
        # keeps Python attribute reads such as `token = settings.deriv_api_token` out.
        re.compile(r"""^\s*[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|PASSWORD)\s*=\s*([^\s"'#]{8,})\s*$"""),
    )
