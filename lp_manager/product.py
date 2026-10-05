from __future__ import annotations

APP_VERSION = "0.9.7.5.4"
APP_RELEASE = "Profit-First Advisor Alignment"
APP_DISPLAY_VERSION = f"v{APP_VERSION}"
APP_USER_AGENT = f"LP-Manager/{APP_VERSION}"

# This is the compatibility contract for the cross-chain opportunity record.
# It is intentionally separate from the application release number so later
# ranking/UI patches can evolve without silently changing persisted data shape.
OPPORTUNITY_SCHEMA_VERSION = "1.0"
ECONOMICS_MODEL_VERSION = "ECONOMICS_V2_CONSERVATIVE_HORIZON_BLEND"


def app_metadata() -> dict[str, str]:
    return {
        "name": "LP Manager",
        "version": APP_VERSION,
        "display_version": APP_DISPLAY_VERSION,
        "release": APP_RELEASE,
        "opportunity_schema_version": OPPORTUNITY_SCHEMA_VERSION,
        "economics_model_version": ECONOMICS_MODEL_VERSION,
    }
