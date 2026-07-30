from pathlib import Path
import tomllib


def test_mcp_dependency_excludes_unsupported_v2():
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )

    assert "mcp>=1.0,<2" in project["project"]["dependencies"]
