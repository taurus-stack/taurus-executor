#!/bin/bash
# SDK client certificate signing script
# Sign SDK certificate using taurus-backend's CA certificate

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_CERTS_DIR="${PROJECT_ROOT}/../taurus-backend/certs"
SDK_CERTS_DIR="${PROJECT_ROOT}/certs/sdk"
CA_KEY="${BACKEND_CERTS_DIR}/ca.key"
CA_CERT="${BACKEND_CERTS_DIR}/ca.crt"
OPENSSL_CNF="${BACKEND_CERTS_DIR}/openssl.cnf"

# Check if CA certificate exists
if [ ! -f "${CA_KEY}" ]; then
    echo "❌ Error: CA private key not found - ${CA_KEY}"
    echo "Please generate CA certificate in taurus-backend first"
    exit 1
fi

if [ ! -f "${CA_CERT}" ]; then
    echo "❌ Error: CA certificate not found - ${CA_CERT}"
    echo "Please generate CA certificate in taurus-backend first"
    exit 1
fi

if [ ! -f "${OPENSSL_CNF}" ]; then
    echo "❌ Error: OpenSSL config file not found - ${OPENSSL_CNF}"
    exit 1
fi

# Create SDK certificate directory
mkdir -p "${SDK_CERTS_DIR}"

echo "========================================="
echo "SDK Client Certificate Signing"
echo "========================================="
echo "CA certificate: ${CA_CERT}"
echo "CA private key: ${CA_KEY}"
echo "Output directory: ${SDK_CERTS_DIR}"
echo "========================================="

# Certificate name (can be passed as argument)
CERT_NAME="${1:-taurus-sdk}"
CERT_DAYS="${2:-365}"

echo "Certificate name: ${CERT_NAME}"
echo "Validity: ${CERT_DAYS} days"
echo "========================================="

# 1. Generate SDK private key
echo "📝 Step 1: Generating SDK private key..."
openssl genrsa -out "${SDK_CERTS_DIR}/${CERT_NAME}.key" 2048
chmod 600 "${SDK_CERTS_DIR}/${CERT_NAME}.key"
echo "✅ Private key generated successfully: ${SDK_CERTS_DIR}/${CERT_NAME}.key"

# 2. Generate CSR (Certificate Signing Request)
echo "📝 Step 2: Generating Certificate Signing Request (CSR)..."
openssl req -new \
    -key "${SDK_CERTS_DIR}/${CERT_NAME}.key" \
    -out "${SDK_CERTS_DIR}/${CERT_NAME}.csr" \
    -subj "/C=CN/ST=Beijing/L=Beijing/O=TaurusOps/CN=${CERT_NAME}"
echo "✅ CSR generated successfully: ${SDK_CERTS_DIR}/${CERT_NAME}.csr"

# 3. Sign certificate using CA
echo "📝 Step 3: Signing certificate with CA..."
openssl ca \
    -config "${OPENSSL_CNF}" \
    -cert "${CA_CERT}" \
    -keyfile "${CA_KEY}" \
    -in "${SDK_CERTS_DIR}/${CERT_NAME}.csr" \
    -out "${SDK_CERTS_DIR}/${CERT_NAME}.crt" \
    -days "${CERT_DAYS}" \
    -batch \
    -extensions v3_client

echo "✅ Certificate signed successfully: ${SDK_CERTS_DIR}/${CERT_NAME}.crt"

# 4. Copy CA certificate to SDK directory
echo "📝 Step 4: Copying CA certificate to SDK directory..."
cp "${CA_CERT}" "${SDK_CERTS_DIR}/ca.crt"
echo "✅ CA certificate copied: ${SDK_CERTS_DIR}/ca.crt"

# 5. Verify certificate
echo "📝 Step 5: Verifying certificate..."
openssl verify -CAfile "${SDK_CERTS_DIR}/ca.crt" "${SDK_CERTS_DIR}/${CERT_NAME}.crt"
echo "✅ Certificate verification successful"

# 6. Display certificate information
echo ""
echo "========================================="
echo "Certificate Information"
echo "========================================="
openssl x509 -in "${SDK_CERTS_DIR}/${CERT_NAME}.crt" -noout -subject -issuer -dates -serial

echo ""
echo "========================================="
echo "Certificate signing complete!"
echo "========================================="
echo "Certificate file: ${SDK_CERTS_DIR}/${CERT_NAME}.crt"
echo "Private key file: ${SDK_CERTS_DIR}/${CERT_NAME}.key"
echo "CA certificate: ${SDK_CERTS_DIR}/ca.crt"
echo "========================================="
echo ""
echo "Usage example (Python SDK):"
echo '```python'
echo 'import grpc'
echo ''
echo '# Load certificates'
echo "with open('${SDK_CERTS_DIR}/ca.crt', 'rb') as f:"
echo '    ca_cert = f.read()'
echo "with open('${SDK_CERTS_DIR}/${CERT_NAME}.crt', 'rb') as f:"
echo '    client_cert = f.read()'
echo "with open('${SDK_CERTS_DIR}/${CERT_NAME}.key', 'rb') as f:"
echo '    client_key = f.read()'
echo ''
echo '# Create SSL credentials'
echo 'credentials = grpc.ssl_channel_credentials('
echo '    root_certificates=ca_cert,'
echo '    private_key=client_key,'
echo '    certificate_chain=client_cert,'
echo ')'
echo ''
echo '# Create gRPC channel (Option 2: bypass IP SAN matching via ssl_target_name_override)'
echo "channel = grpc.secure_channel('10.0.1.123:50051', credentials, options=["
echo "    ('grpc.ssl_target_name_override', 'taurus-grpc-server'),"
echo "])"
echo '```'