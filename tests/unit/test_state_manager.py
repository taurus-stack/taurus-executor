import pytest
import asyncio
import json
from unittest.mock import mock_open, patch
from pathlib import Path
from executor_core.infra.state_manager import StateManager


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def state_manager(state_file):
    return StateManager(state_file=state_file)


@pytest.mark.asyncio
async def test_get_nonexistent_file(state_manager):
    """Test getting state from nonexistent file."""
    result = await state_manager.get("key")
    assert result is None


@pytest.mark.asyncio
async def test_set_and_get(state_manager):
    """Test setting and getting state."""
    await state_manager.set("key1", "value1")
    result = await state_manager.get("key1")
    assert result == "value1"


@pytest.mark.asyncio
async def test_set_multiple_and_get(state_manager):
    """Test setting multiple values and getting them."""
    await state_manager.set("key1", "value1")
    await state_manager.set("key2", "value2")
    
    result1 = await state_manager.get("key1")
    result2 = await state_manager.get("key2")
    
    assert result1 == "value1"
    assert result2 == "value2"


@pytest.mark.asyncio
async def test_overwrite_value(state_manager):
    """Test overwriting existing value."""
    await state_manager.set("key", "value1")
    await state_manager.set("key", "value2")
    
    result = await state_manager.get("key")
    assert result == "value2"


@pytest.mark.asyncio
async def test_delete_key(state_manager):
    """Test deleting a key."""
    await state_manager.set("key", "value")
    await state_manager.delete("key")
    
    result = await state_manager.get("key")
    assert result is None


@pytest.mark.asyncio
async def test_delete_nonexistent_key(state_manager):
    """Test deleting a key that doesn't exist."""
    # Should not raise an exception
    await state_manager.delete("nonexistent_key")


@pytest.mark.asyncio
async def test_corrupted_file_handling(state_manager, state_file):
    """Test handling of corrupted state file."""
    # Create a corrupted state file
    with open(state_file, "w") as f:
        f.write("invalid json")
    
    # Should handle gracefully and treat as empty
    result = await state_manager.get("key")
    assert result is None
    
    # Should be able to set new values
    await state_manager.set("key", "value")
    result = await state_manager.get("key")
    assert result == "value"


@pytest.mark.asyncio
async def test_get_full_status(state_manager):
    """Test getting full system status with metrics."""
    status = await state_manager.get_full_status()
    
    assert "cpu" in status
    assert "memory" in status
    assert "disk" in status
    assert "network" in status
    assert "uptime" in status
    
    assert "percent" in status["cpu"]
    assert "total" in status["memory"]
    assert "percent" in status["disk"]


@pytest.mark.asyncio
async def test_increment_counter(state_manager):
    """Test atomic counter increment."""
    result = await state_manager.increment_counter("counter1")
    assert result == 1
    
    result = await state_manager.increment_counter("counter1")
    assert result == 2
    
    result = await state_manager.increment_counter("counter1", 5)
    assert result == 7


@pytest.mark.asyncio
async def test_get_all(state_manager):
    """Test getting all state data."""
    await state_manager.set("key1", "value1")
    await state_manager.set("key2", "value2")
    
    all_data = await state_manager.get_all()
    assert all_data["key1"] == "value1"
    assert all_data["key2"] == "value2"


@pytest.mark.asyncio
async def test_concurrent_operations(state_manager):
    """Test concurrent state operations with lock."""
    async def set_values():
        for i in range(10):
            await state_manager.set(f"key_{i}", f"value_{i}")
    
    # Run multiple concurrent operations
    await asyncio.gather(*[set_values() for _ in range(3)])
    
    # Verify all values were set correctly
    for i in range(10):
        result = await state_manager.get(f"key_{i}")
        assert result == f"value_{i}"