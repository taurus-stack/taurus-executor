.PHONY: help install dev-install test lint format clean build run package

help:
	@echo "Available commands:"
	@echo "  install      Install the package"
	@echo "  dev-install  Install the package with dev dependencies"
	@echo "  build        Generate gRPC code from .proto files"
	@echo "  format       Format code with black and isort"
	@echo "  lint         Lint code with flake8 and mypy"
	@echo "  test         Run all tests"
	@echo "  clean        Clean up build artifacts"
	@echo "  run          Run the client"
	@echo "  package      Package client as binary using PyInstaller"

install:
	poetry install 

dev-install:
	poetry install --with dev

build:
	@echo "Generating gRPC code..."
	python3 -m grpc_tools.protoc \
		-I. \
		--proto_path=proto \
		--python_out=src/executor_core/services/generated \
		--grpc_python_out=src/executor_core/services/generated \
		executor/v1/command_service.proto
	@echo "Code generation complete."
	python3 scripts/process_grpc_files.py

format:
	black src/ tests/
	isort src/ tests/

lint:
	flake8 src/ tests/
	mypy src/

test:
	pytest

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type d -name __pycache__ -delete
	find . -type f -name "*.pyc" -delete

run: build
	python3 -m executor_core.main


.PHONY: build-sdk
build-sdk:
	@echo "Generating gRPC code for the SDK..."
	cd manage && python3 -m grpc_tools.protoc \
		-I. \
		--proto_path=../proto \
		--python_out=sdk/generated \
		--grpc_python_out=sdk/generated \
		executor/v1/command_service.proto
	@echo "SDK code generation complete."
	python3 scripts/process_grpc_files.py

# You can also add a 'build' target that builds both server and SDK
.PHONY: build-all
build-all: build build-sdk

package: build-all
	@echo "Packaging client as binary..."
	bash scripts/package.sh $(filter-out $@,$(MAKECMDGOALS))