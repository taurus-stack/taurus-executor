#!/bin/bash
#
# Taurus Executor packaging script
# Packages executor_core into a binary executable using PyInstaller
#
# Usage: ./scripts/package.sh [options]
#
# Options:
#   --version <VER>     Version number (default: read from pyproject.toml)
#   --platform <PLAT>   Target platform: linux, darwin (default: current platform)
#   --clean             Clean build cache
#   --debug             Enable debug mode
#   --help              Show help
#

set -euo pipefail

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

# Project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Default values
VERSION=""
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')"
CLEAN=false
DEBUG=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --version|-v)
            VERSION="$2"
            shift 2
            ;;
        --platform)
            PLATFORM="$2"
            shift 2
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --debug)
            DEBUG=true
            shift
            ;;
        --help)
            echo "Taurus Executor Packaging Script"
            echo ""
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --version, -v <VER> Version number (default: read from pyproject.toml)"
            echo "  --platform <PLAT>   Target platform: linux, darwin (default: current platform)"
            echo "  --clean             Clean build cache"
            echo "  --debug             Enable debug mode"
            echo "  --help              Show help"
            exit 0
            ;;
        *)
            log_error "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# Check dependencies
check_dependencies() {
    log_step "Checking dependencies..."

    if ! command -v python3 &> /dev/null; then
        log_error "python3 is not installed"
        exit 1
    fi

    # Check poetry
    if ! command -v poetry &> /dev/null; then
        log_warn "poetry not found, installing dependencies via pip"
        pip3 install pyinstaller >/dev/null 2>&1 || {
            log_error "pyinstaller installation failed"
            exit 1
        }
    else
        # Install using poetry
        poetry install --with dev >/dev/null 2>&1 || {
            log_error "poetry install failed"
            exit 1
        }
    fi

    # Verify pyinstaller
    if ! python3 -c "import PyInstaller" 2>/dev/null; then
        log_error "PyInstaller is not installed"
        exit 1
    fi

    log_info "Dependency check passed"
}

# Get version number
get_version() {
    if [[ -n "$VERSION" ]]; then
        echo "$VERSION"
        return
    fi

    # Read from pyproject.toml
    VERSION=$(python3 -c "
import re
with open('pyproject.toml', 'r') as f:
    content = f.read()
    match = re.search(r'version\s*=\s*\"([^\"]+)\"', content)
    if match:
        print(match.group(1))
    else:
        print('0.1.0')
")
    echo "$VERSION"
}

# Generate gRPC code
generate_grpc() {
    log_step "Generating gRPC code..."

    # Generate server code first
    make build 2>/dev/null || {
        log_warn "make build failed, attempting manual generation..."
        python3 -m grpc_tools.protoc \
            -I. \
            --proto_path=proto \
            --python_out=src/executor_core/services/generated \
            --grpc_python_out=src/executor_core/services/generated \
            client/v1/command_service.proto

        python3 scripts/process_grpc_files.py
    }

    # Generate SDK code
    make build-sdk 2>/dev/null || {
        log_warn "make build-sdk failed, attempting manual generation..."
        cd manage && python3 -m grpc_tools.protoc \
            -I. \
            --proto_path=../proto \
            --python_out=sdk/generated \
            --grpc_python_out=sdk/generated \
            client/v1/command_service.proto
        cd ..
        python3 scripts/process_grpc_files.py
    }

    log_info "gRPC code generation completed"
}

# Clean build cache
clean_build() {
    log_step "Cleaning build cache..."
    rm -rf build/ dist/ *.spec.bak
    rm -rf __pycache__
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    log_info "Cleanup finished"
}

# Package
package() {
    local version="$1"
    local arch="$(uname -m)"
    local binary_name="taurus-executor"
    local package_name="taurus-executor-v${version}-${PLATFORM}-${arch}.tar.gz"

    log_step "Starting to package Taurus Executor v${version}..."
    log_info "Target platform: ${PLATFORM}"
    log_info "Architecture: ${arch}"

    # Clear dist and build directories
    rm -rf dist/* build/*
    mkdir -p dist build

    # Compile the binary first
    PYINSTALLER_ARGS=(
        --clean
        --noconfirm
        --distpath "dist"
        --workpath "build"
    )

    if [[ "$DEBUG" == "true" ]]; then
        PYINSTALLER_ARGS+=(--debug all)
    fi

    # Run packaging (spec file is in the scripts directory)
    local spec_file="${SCRIPT_DIR}/taurus-executor.spec"
    if [[ ! -f "${spec_file}" ]]; then
        log_error "spec file not found: ${spec_file}"
        exit 1
    fi

    if command -v poetry &> /dev/null; then
        poetry run pyinstaller "${PYINSTALLER_ARGS[@]}" "${spec_file}"
    else
        python3 -m PyInstaller "${PYINSTALLER_ARGS[@]}" "${spec_file}"
    fi

    # Verify binary file
    if [[ ! -f "dist/${binary_name}" ]]; then
        log_error "Packaging failed, binary not found: dist/${binary_name}"
        exit 1
    fi

    log_info "Binary compiled successfully"
    ls -lh "dist/${binary_name}"
    file "dist/${binary_name}"

    # Create tar.gz package
    log_step "Creating tar.gz release package..."

    local temp_dir=$(mktemp -d)
    trap "rm -rf ${temp_dir}" EXIT

    # Copy binary to temp directory
    cp "dist/${binary_name}" "${temp_dir}/${binary_name}"
    chmod 0755 "${temp_dir}/${binary_name}"

    # Create tar.gz package (binary directly inside, no subdirectory)
    tar -czf "dist/${package_name}" -C "${temp_dir}" "${binary_name}"

    log_info "Release package created: dist/${package_name}"
    ls -lh "dist/${package_name}"

    # Generate checksum file
    local checksum_file="dist/${package_name}.sha256"
    if command -v sha256sum &> /dev/null; then
        sha256sum "dist/${package_name}" > "${checksum_file}"
    else
        shasum -a 256 "dist/${package_name}" > "${checksum_file}"
    fi

    log_info "Checksum file: ${checksum_file}"
    cat "${checksum_file}"

    # Verify package contents
    log_step "Verifying release package..."
    tar -tzf "dist/${package_name}"

    # Copy to backend download directory (overwrites existing files)
    local backend_dir="${PROJECT_ROOT}/../taurus-backend/app_packages/taurus-executor"
    if [[ -d "${backend_dir}" ]]; then
        log_step "Copying to backend download directory..."
        cp -f "dist/${package_name}" "${backend_dir}/"
        cp -f "dist/${package_name}.sha256" "${backend_dir}/"
        log_info "Copied to: ${backend_dir}/"
        ls -lh "${backend_dir}/${package_name}"*
    else
        log_warn "backend download directory not found, skipping copy: ${backend_dir}"
    fi
}

# Main flow
main() {
    echo "========================================"
    echo "  Taurus Executor Packaging Tool"
    echo "========================================"
    echo ""

    check_dependencies

    VERSION=$(get_version)
    log_info "Version: ${VERSION}"

    if [[ "$CLEAN" == "true" ]]; then
        clean_build
    fi

    generate_grpc
    package "$VERSION"

    echo ""
    echo "========================================"
    log_info "Packaging finished!"
    echo "========================================"
    echo ""
    echo "Output file: dist/taurus-executor-v${VERSION}-${PLATFORM}-$(uname -m).tar.gz"
    echo ""
}

main