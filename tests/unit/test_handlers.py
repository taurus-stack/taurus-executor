import pytest
from unittest.mock import Mock, patch

from executor_core.services.handlers import ClientServicer
from executor_core.services.generated.executor.v1 import command_service_pb2


@pytest.fixture
def mock_state_manager():
    return Mock()


@pytest.fixture
def client_servicer(mock_state_manager):
    return ClientServicer(state_manager=mock_state_manager)


@pytest.mark.asyncio
async def test_get_status(client_servicer):
    mock_context = Mock()
    mock_context.peer.return_value = "ipv4:127.0.0.1:12345"

    request = command_service_pb2.StatusRequest()

    with patch('executor_core.services.handlers.psutil') as mock_psutil, \
         patch('executor_core.services.handlers.time') as mock_time, \
         patch('executor_core.services.handlers.os') as mock_os, \
         patch('executor_core.services.handlers.settings') as mock_settings:

        mock_psutil.boot_time.return_value = 1000000
        mock_time.time.return_value = 1000300
        mock_psutil.cpu_percent.return_value = 25.5
        mock_vmem = Mock()
        mock_vmem.percent = 60.2
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = Mock()
        mock_disk.percent = 55.0
        mock_psutil.disk_usage.return_value = mock_disk
        mock_os.uname.return_value.nodename = "test-host"
        mock_os.getloadavg.return_value = (1.0, 2.0, 3.0)
        mock_settings.current_version = "1.2.3"

        response = await client_servicer.GetStatus(request, mock_context)

        assert isinstance(response, command_service_pb2.StatusResponse)
        assert response.version == "1.2.3"
        assert response.uptime == "300.00s"
        assert response.hostname == "test-host"
        assert response.cpu_usage == 25.5
        assert response.memory_usage == 60.2
        assert response.disk_usage == 55.0
        assert list(response.load_avg) == [1.0, 2.0, 3.0]

        mock_psutil.boot_time.assert_called_once()
        mock_psutil.cpu_percent.assert_called_with(interval=0.5)
        mock_psutil.virtual_memory.assert_called()
        mock_psutil.disk_usage.assert_called_with('/')
        mock_os.uname.assert_called_once()
        mock_os.getloadavg.assert_called_once()