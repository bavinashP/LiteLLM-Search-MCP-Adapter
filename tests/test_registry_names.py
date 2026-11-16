import pytest

from adapter.registry import RegistryError, mcp_tool_name


def test_tool_name_is_stable_and_namespaced():
    assert mcp_tool_name("spp_Parallel") == "litellm_search__spp_parallel"


def test_tool_name_rejects_names_without_safe_characters():
    with pytest.raises(RegistryError):
        mcp_tool_name("🔥")
