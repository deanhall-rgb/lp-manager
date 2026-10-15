"""Release identity must remain consistent across package and API metadata."""

from lp_manager import __version__
from lp_manager.product import APP_VERSION, APP_DISPLAY_VERSION, APP_USER_AGENT, app_metadata


def test_package_version_tracks_canonical_product_version():
    assert __version__ == APP_VERSION


def test_public_release_identity_is_consistent():
    metadata = app_metadata()
    assert metadata["version"] == __version__
    assert metadata["display_version"] == APP_DISPLAY_VERSION == f"v{__version__}"
    assert APP_USER_AGENT == f"LP-Manager/{__version__}"
