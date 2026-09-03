import os

def add_imports_to_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    header = """import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

"""

    if not content.startswith('import sys'):
        content = header + content

    with open(filepath, 'w') as f:
        f.write(content)

def fix_grpc_imports(filepath):
    """Fix imports in generated grpc files to use relative imports."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Replace 'from executor.v1 import' with relative import
    if 'from executor.v1 import command_service_pb2' in content:
        content = content.replace(
            'from executor.v1 import command_service_pb2',
            'from . import command_service_pb2'
        )
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Fixed imports in {filepath}")

def main():
    # Process server-side generated files
    server_base = 'src/executor_core/services/generated'
    server_executor_v1 = os.path.join(server_base, 'executor', 'v1')
    if os.path.exists(server_executor_v1):
        for filename in os.listdir(server_executor_v1):
            if filename.endswith('_grpc.py'):
                filepath = os.path.join(server_executor_v1, filename)
                add_imports_to_file(filepath)
                fix_grpc_imports(filepath)
                print(f"Processed server: {filename}")
    
    # Process client-side generated files
    client_base = 'manage/sdk/generated'
    client_executor_v1 = os.path.join(client_base, 'executor', 'v1')
    if os.path.exists(client_executor_v1):
        for filename in os.listdir(client_executor_v1):
            if filename.endswith('_grpc.py'):
                filepath = os.path.join(client_executor_v1, filename)
                add_imports_to_file(filepath)
                fix_grpc_imports(filepath)
                print(f"Processed executor: {filename}")

if __name__ == '__main__':
    main()