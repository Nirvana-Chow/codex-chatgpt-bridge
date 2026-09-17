from pathlib import Path
import pytest
from readonly_mcp.lab import create_lab
from readonly_mcp.service import QueryService


@pytest.fixture
def lab(tmp_path: Path):
    root = tmp_path / "lab"
    create_lab(root)
    return root


@pytest.fixture
def service(lab):
    result = QueryService(lab)
    yield result
    result.close()
