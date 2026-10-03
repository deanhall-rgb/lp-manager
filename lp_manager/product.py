from __future__ import annotations

APP_VERSION = "0.9.7.3.1"
APP_RELEASE = "Capital-aware Advisor validation hotfix"
APP_DISPLAY_VERSION = f"v{APP_VERSION}"
APP_USER_AGENT = f"LP-Manager/{APP_VERSION}"

# This is the compatibility contract for the cross-chain opportunity record.
# It is intentionally separate from the application release number so later
# ranking/UI patches can evolve without silently changing persisted data shape.
OPPORTUNITY_SCHEMA_VERSION = "1.0"


def app_metadata() -> dict[str, str]:
    return {
        "name": "LP Manager",
        "version": APP_VERSION,
        "display_version": APP_DISPLAY_VERSION,
        "release": APP_RELEASE,
        "opportunity_schema_version": OPPORTUNITY_SCHEMA_VERSION,
    }
