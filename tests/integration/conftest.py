import asyncio
import sys
import os

import grpc
import pytest

# Add the project root directory to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


# This fixture starts a real client server for the duration of the tests
@pytest.fixture
async def client_server():
    # Import and run the main server in the background
    from executor_core.services.server import serve
    server_task = asyncio.create_task(serve())
    
    # Wait for server to start and verify it's accepting connections
    max_retries = 10
    retry_count = 0
    while retry_count < max_retries:
        try:
            # Try to establish a connection to verify server is running
            channel = grpc.aio.insecure_channel("localhost:50051")
            await asyncio.wait_for(channel.channel_ready(), timeout=1.0)
            await channel.close()
            break
        except (asyncio.TimeoutError, OSError):
            retry_count += 1
            await asyncio.sleep(0.5)
    
    if retry_count >= max_retries:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
        raise RuntimeError("Failed to start client server within timeout")
    
    yield "localhost:50051"
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass