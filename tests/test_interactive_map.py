from pathlib import Path

from Backend.app import app


def test_interactive_map_route():
    assert any(getattr(route, "path", None) == "/map" for route in app.routes)


def test_export_mount_exists():
    assert Path(__file__).parents[1].joinpath("exports").exists()
