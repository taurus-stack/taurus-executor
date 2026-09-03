# Client Upgrade Scheme

## Overview

Taurus Executor supports **zero-downtime automatic upgrades**. The client periodically checks the server for new versions. When a new version is found, it automatically downloads, verifies, and installs it to an isolated directory, starts the new process and performs health checks, then shuts down the old process only after confirming everything is working correctly.

## Architecture Design

### Zero-Downtime Upgrade Flow Diagram

```
┌─────────────────┐
│  Old version     │  Port: 50051
│  process running │  Directory: /opt/taurus-executor/
│  (PID: 12345)    │
└────────┬────────┘
         │
         │ 1. Check for updates (new version found)
         │ 2. Download new version package
         │ 3. Verify SHA256
         │ 4. Install to isolated directory
         ↓
┌─────────────────┐
│  New version     │  Directory: /opt/taurus-executor/versions/v1.1.0/
│  installed       │  Binary: taurus-executor
│  (not started)   │
└────────┬────────┘
         │
         │ 5. Start new process (port +1)
         ↓
┌─────────────────┐
│  New version     │  Port: 50052
│  process running │  Directory: /opt/taurus-executor/versions/v1.1.0/
│  (PID: 12346)    │
└────────┬────────┘
         │
         │ 6. Health check (connect to port 50052)
         │ 7. Check passed, wait 2 seconds
         │ 8. Send SIGTERM to old process
         ↓
┌─────────────────┐
│  Old process     │  PID 12345 exits
│  exits           │  New process continues
│  New process     │  Port: 50052 ✅
│  keeps running   │
└─────────────────┘
```

### Directory Structure

```
/opt/taurus-executor/
├── taurus-executor              # Old version binary (currently running)
├── .env                      # Configuration file
├── tls/                      # TLS certificates
│   ├── ca.crt
│   ├── client.crt
│   └── client.key
└── versions/                 # New versions directory
    └── v1.1.0/
        ├── taurus-executor      # New version binary
        ├── .env              # Copied configuration file
        └── tls/              # Copied TLS certificates
```

## Existing Implementation

### 1. Client Upgrader

**File**: `src/executor_core/updater/self_updater.py`

#### Upgrade State Machine

```python
class UpdateState(Enum):
    IDLE = "idle"              # Idle
    CHECKING = "checking"      # Checking for updates
    DOWNLOADING = "downloading" # Downloading
    VERIFYING = "verifying"    # Verifying
    INSTALLING = "installing"  # Installing
    SWITCHING = "switching"    # Switching (starting new process)
    HEALTH_CHECK = "health_check"  # Health checking
    UPDATED = "updated"        # Updated
    FAILED = "failed"          # Failed
```

#### Zero-Downtime Upgrade Flow

```python
async def check_and_apply_update(self) -> bool:
    # 1. Check state
    # 2. Fetch latest version info
    update_info = await self._fetch_latest_version_info()

    # 3. Compare versions
    if update_info["version"] == settings.current_version:
        return True  # Already on latest version

    # 4. Download new version (with retries)
    new_binary_data = await self._download_with_retry(url, sha256)

    # 5. Verify checksum
    self._verify_checksum(new_binary_data, sha256)

    # 6. Install to isolated directory (don't replace current files)
    await self._install_new_binary_with_rollback(new_binary_data)

    # 7. Graceful switch: start new process → health check → shut down old process
    await self._graceful_switch()
```

#### Graceful Switch Flow

```python
async def _graceful_switch(self) -> bool:
    # 1. Start new version process (port +1)
    new_process = await self._start_new_version()

    # 2. Health check (connect to new port)
    is_healthy = await self._check_new_process_health(new_process)

    # 3. If health check passes, shut down old process
    if is_healthy:
        os.kill(self.current_pid, signal.SIGTERM)
        return True

    # 4. If health check fails, rollback
    await self._rollback_update()
    return False
```

#### Security Mechanisms

1. **Checksum Verification**: Use SHA256 to verify downloaded file integrity
2. **Isolated Directory Installation**: Do not replace the currently running binary
3. **Health Checks**: Verify the port is connectable after starting the new process
4. **Rollback Support**: Clean up the new version directory if upgrade fails
5. **Retry Mechanism**: Automatically retry downloads 3 times (exponential backoff)
6. **Zero-Downtime**: The old process only exits after the new process passes health checks

### 2. Server Package Management

**File**: `taurus-backend/taurus/views.py`

#### Package Download Endpoint

```
GET /api/taurus/executor/register/download-package/?platform=linux&arch=x86_64
```

**Features**:
- Returns the latest version installation package based on platform and architecture
- Automatically selects the package with the highest version number
- Returns gzip compressed files

#### Package Directory Structure

```
taurus-backend/client_packages/
├── taurus-executor-1.0.0-linux-x86_64.tar.gz
├── taurus-executor-1.0.0-linux-arm64.tar.gz
├── taurus-executor-1.1.0-linux-x86_64.tar.gz
└── README.md
```

**Naming Convention**: `taurus-executor-{version}-{platform}[-{arch}].tar.gz`

### 3. Version Check Configuration

**File**: `src/executor_core/infra/config.py`

```python
class Settings(BaseSettings):
    update_server_url: str = "http://localhost:8000"
    current_version: str = "0.1.0"
    check_update_interval_seconds: int = 3600  # Check once per hour
```

### 4. Periodic Upgrade Checks

**File**: `src/executor_core/services/server.py`

```python
async def updater_periodic_check(updater: SelfUpdater) -> None:
    """Periodically check for updates."""
    while True:
        try:
            await asyncio.sleep(settings.check_update_interval_seconds)
            logger.info("Checking for updates...")
            await updater.check_and_apply_update()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error during update check: {e}")
```

## Current Issues

### 1. Missing Version Information Endpoint

The client's `_fetch_latest_version_info()` method needs to call the server's version information endpoint, but the server **has not yet implemented** this endpoint.

### 2. Single Upgrade Trigger Method

Currently only **scheduled checks** are supported. Not supported:
- Server actively pushes upgrade notifications
- Admin manually triggers upgrades
- Client manually triggers upgrades

### 3. Opaque Upgrade Process

- Lack of upgrade progress reporting
- Lack of upgrade history records
- Lack of upgrade failure alerts

### 4. Port Conflict Issue

The new version uses `port + 1`, which may cause port occupation if multiple versions are upgraded simultaneously.

**Solutions**:
- Use systemd socket activation mechanism
- Or use dynamic port allocation
- Or update systemd configuration after the switch is complete

## Improvement Proposals

### Proposal 1: Complete Server Version Management Endpoints (Recommended)

#### 1. Add Version Information Endpoint

Add in `taurus/views.py`:

```python
@action(detail=False, methods=['get'], permission_classes=[AllowAny])
def latest_version(self, request):
    """
    Get latest version information
    Supported parameters: platform, arch
    """
    platform = request.GET.get('platform', 'linux')
    arch = request.GET.get('arch', 'x86_64')

    package_dir = getattr(settings, 'CLIENT_PACKAGE_DIR', None)
    if not package_dir or not os.path.exists(package_dir):
        return ErrorResponse(msg="Version service not configured")

    # Find all matching packages
    pattern = f"taurus-executor-*-{platform}*.tar.gz"
    packages = glob.glob(os.path.join(package_dir, pattern))

    if not packages:
        return ErrorResponse(msg=f"No installation packages found for platform {platform}")

    # Extract version information
    def extract_version(filepath):
        filename = os.path.basename(filepath)
        match = re.search(r'taurus-executor-(\d+\.\d+\.\d+)', filename)
        return match.group(1) if match else '0.0.0'

    # Select latest version
    packages.sort(key=extract_version, reverse=True)
    latest_package = packages[0]
    latest_version = extract_version(latest_package)

    # Calculate checksum
    import hashlib
    sha256 = hashlib.sha256()
    with open(latest_package, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)

    return SuccessResponse(data={
        'version': latest_version,
        'download_url': f"{request.build_absolute_uri('/api/taurus/executor/register/download-package/')}?platform={platform}&arch={arch}",
        'sha256': sha256.hexdigest(),
        'release_notes': f"Taurus Executor v{latest_version}",
        'file_size': os.path.getsize(latest_package),
    })
```

#### 2. Add Upgrade History Records

Create `ClientUpdateRecord` model:

```python
class ClientUpdateRecord(CoreModel):
    STATUS_CHOICES = [
        (0, 'Upgrading'),
        (1, 'Upgrade Successful'),
        (2, 'Upgrade Failed'),
        (3, 'Rolled Back'),
    ]

    host = models.ForeignKey('Host', on_delete=models.CASCADE, verbose_name="Host")
    from_version = models.CharField(max_length=50, verbose_name="Original Version")
    to_version = models.CharField(max_length=50, verbose_name="Target Version")
    status = models.IntegerField(choices=STATUS_CHOICES, default=0, verbose_name="Status")
    error_message = models.TextField(verbose_name="Error Message", null=True, blank=True)
    started_at = models.DateTimeField(verbose_name="Start Time")
    completed_at = models.DateTimeField(verbose_name="Completion Time", null=True, blank=True)
```

#### 3. Heartbeat Endpoint Returns Upgrade Notifications

Add upgrade prompts to heartbeat responses:

```python
# Check if there's a new version
if has_new_version(host):
    commands.append({
        'type': 'update_available',
        'version': latest_version,
        'message': f'New version {latest_version} found, upgrade recommended',
        'priority': 'low',  # low/medium/high
    })
```

### Proposal 2: Support Server Active Push Upgrades

#### 1. Add Remote Upgrade Endpoint

Trigger upgrades via gRPC commands:

```python
# In handlers.py
async def Maintenance(self, request, context):
    if request.action == "update":
        # Trigger upgrade
        asyncio.create_task(self._trigger_update(request.update))
        return command_service_pb2.MaintenanceResponse(
            success=True,
            message="Update process initiated in the background."
        )
```

#### 2. Server Batch Upgrade Endpoint

```python
@action(detail=False, methods=['post'])
def batch_update(self, request):
    """Batch upgrade clients"""
    host_ids = request.data.get('host_ids', [])
    target_version = request.data.get('target_version')

    # Send upgrade commands to specified hosts
    for host_id in host_ids:
        send_update_command(host_id, target_version)

    return SuccessResponse(msg="Upgrade commands have been sent")
```

### Proposal 3: Complete Upgrade Monitoring

#### 1. Upgrade Progress Reporting

Periodically report progress during the upgrade process:

```python
async def _download_with_retry(self, url, sha256, max_retries=3):
    for attempt in range(max_retries):
        try:
            # Report download progress
            await self._report_progress('downloading', progress=0)
            data = await self._download(url)
            await self._report_progress('downloading', progress=100)
            return data
        except Exception as e:
            await self._report_progress('failed', error=str(e))
```

#### 2. Upgrade History Record Query

```python
@action(detail=True, methods=['get'])
def update_history(self, request, pk=None):
    """Query host upgrade history"""
    host = self.get_object()
    records = ClientUpdateRecord.objects.filter(host=host).order_by('-started_at')

    serializer = ClientUpdateRecordSerializer(records, many=True)
    return SuccessResponse(data=serializer.data)
```

## Deployment Process

### 1. Release a New Version

```bash
# 1. Package new version
cd taurus-executor
bash scripts/package.sh --version 1.1.0

# 2. Copy to server package directory
cp dist/taurus-executor-1.1.0-linux-x86_64.tar.gz \
   ../taurus-backend/client_packages/

# 3. Client automatically detects and upgrades (wait up to check_update_interval_seconds)
# Zero-downtime upgrade process:
#   - Old version continues running on port 50051
#   - New version starts on port 50052
#   - After health check passes, old version exits
```

### 2. Manually Trigger Upgrade

```bash
# Trigger via gRPC command
curl -X POST http://server:8000/api/taurus/host/{host_id}/trigger_update/ \
  -H "Authorization: Bearer <token>" \
  -d '{"target_version": "1.1.0"}'
```

### 3. View Upgrade Status

```bash
# Query upgrade history
curl -X GET http://server:8000/api/taurus/host/{host_id}/update_history/ \
  -H "Authorization: Bearer <token>"

# View client logs
tail -f /var/log/taurus-executor/taurus-executor.log | grep "upgrade\|update"
```

## Security Considerations

### 1. Package Integrity Verification

- Use SHA256 checksums to verify downloaded files
- Prevent man-in-the-middle attacks and file tampering

### 2. Upgrade Permission Control

- Batch upgrades require administrator privileges
- Single client upgrades can be automatically triggered via heartbeat

### 3. Rollback Mechanism

- If the new version health check fails, automatically clean up the new version directory
- The old version continues running, unaffected

### 4. Upgrade Window Control

- Configurable upgrade time window (avoid peak business hours)
- Support canary upgrades (phased upgrades)

### 5. Zero-Downtime Guarantee

- The old process only exits after the new process passes health checks
- Services remain available during the upgrade process
- Only the moment of switching (< 1 second) may have request loss

## Configuration Items

### Client Configuration

```bash
# .env file
UPDATE_SERVER_URL=http://taurus-server:8000
CURRENT_VERSION=1.0.0
CHECK_UPDATE_INTERVAL_SECONDS=3600
GRPC_PORT=50051
METRICS_PORT=9090
```

### Server Configuration

```python
# application/settings.py
CLIENT_PACKAGE_DIR = os.path.join(BASE_DIR, "client_packages")
```

## Summary

### Existing Features

✅ Client automatic upgrader (download, verify, install, rollback)
✅ Server package download endpoint
✅ Scheduled upgrade checks
✅ Checksum verification
✅ **Zero-downtime upgrades** (isolated directory installation + health checks)
✅ **Graceful switching** (new process starts before old process shuts down)
✅ **Automatic rollback** (clean up new version when health check fails)

### Features to Implement

❌ Version information endpoint (`/api/taurus/executor/latest-version/`)
❌ Upgrade history records
❌ Server active push upgrades
❌ Batch upgrade endpoints
❌ Upgrade progress reporting
❌ Upgrade time window control
❌ Canary upgrade support

### Recommended Priority

1. **High Priority**: Implement version information endpoint (required for client upgrades)
2. **Medium Priority**: Upgrade history records, upgrade progress reporting
3. **Low Priority**: Batch upgrades, canary upgrades, time window control