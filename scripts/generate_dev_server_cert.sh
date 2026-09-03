#!/bin/bash
# Development environment server certificate generation script
# Generate server certificate with localhost SAN for local development

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_CERTS_DIR="${PROJECT_ROOT}/../taurus-backend/certs"
EXECUTOR_TLS_DIR="${PROJECT_ROOT}/tls"
CA_KEY="${BACKEND_CERTS_DIR}/ca.key"
CA_CERT="${BACKEND_CERTS_DIR}/ca.crt"

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

# Create TLS directory
mkdir -p "${EXECUTOR_TLS_DIR}"

echo "========================================="
echo "Development Environment Server Certificate Generation"
echo "========================================="
echo "CA certificate: ${CA_CERT}"
echo "CA private key: ${CA_KEY}"
echo "Output directory: ${EXECUTOR_TLS_DIR}"
echo "========================================="

# Certificate name (can be passed as argument)
CERT_NAME="${1:-taurus-server-dev}"
CERT_DAYS="${2:-365}"
GRPC_SERVER_NAME="${GRPC_SERVER_NAME:-taurus-grpc-server}"

echo "Certificate name: ${CERT_NAME}"
echo "Validity: ${CERT_DAYS} days"
echo "gRPC service name: ${GRPC_SERVER_NAME}"
echo "SAN: DNS:${GRPC_SERVER_NAME}, DNS:localhost, IP:127.0.0.1"
echo "========================================="

# 1. Generate server private key
echo "📝 Step 1: Generating server private key..."
openssl genrsa -out "${EXECUTOR_TLS_DIR}/server.key" 2048
chmod 600 "${EXECUTOR_TLS_DIR}/server.key"
echo "✅ Private key generated successfully: ${EXECUTOR_TLS_DIR}/server.key"

# 2. Generate CSR (Certificate Signing Request) with SAN
echo "📝 Step 2: Generating Certificate Signing Request (CSR)..."
openssl req -new \
    -key "${EXECUTOR_TLS_DIR}/server.key" \
    -out "${EXECUTOR_TLS_DIR}/server.csr" \
    -subj "/C=CN/ST=Beijing/L=Beijing/O=TaurusOps/CN=${GRPC_SERVER_NAME}" \
    -addext "subjectAltName=DNS:${GRPC_SERVER_NAME},DNS:localhost,IP:127.0.0.1"
echo "✅ CSR generated successfully: ${EXECUTOR_TLS_DIR}/server.csr"

# 3. Create temporary OpenSSL config file for signing
TEMP_CNF="${EXECUTOR_TLS_DIR}/openssl_temp.cnf"
cat > "${TEMP_CNF}" << EOF
[v3_server]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:${GRPC_SERVER_NAME},DNS:localhost,IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

# 4. Sign certificate using CA (use x509 command to sign directly, avoiding CA database issues)
echo "📝 Step 3: Signing certificate with CA..."
openssl x509 -req \
    -in "${EXECUTOR_TLS_DIR}/server.csr" \
    -CA "${CA_CERT}" \
    -CAkey "${CA_KEY}" \
    -CAcreateserial \
    -out "${EXECUTOR_TLS_DIR}/server.crt" \
    -days "${CERT_DAYS}" \
    -extfile "${TEMP_CNF}" \
    -extensions v3_server

echo "✅ Certificate signed successfully: ${EXECUTOR_TLS_DIR}/server.crt"

# 5. Copy CA certificate to TLS directory (if not exists)
if [ ! -f "${EXECUTOR_TLS_DIR}/ca.crt" ]; then
    echo "📝 Step 4: Copying CA certificate to TLS directory..."
    cp "${CA_CERT}" "${EXECUTOR_TLS_DIR}/ca.crt"
    echo "✅ CA certificate copied: ${EXECUTOR_TLS_DIR}/ca.crt"
else
    echo "📝 Step 4: CA certificate already exists, skipping copy"
fi

# 6. Verify certificate
echo "📝 Step 5: Verifying certificate..."
openssl verify -CAfile "${EXECUTOR_TLS_DIR}/ca.crt" "${EXECUTOR_TLS_DIR}/server.crt"
echo "✅ Certificate verification successful"

# 7. Display certificate information
echo ""
echo "========================================="
echo "Certificate Information"
echo "========================================="
openssl x509 -in "${EXECUTOR_TLS_DIR}/server.crt" -noout -subject -issuer -dates -ext subjectAltName

# 8. Clean up temporary files
rm -f "${TEMP_CNF}" "${EXECUTOR_TLS_DIR}/server.csr"

echo ""
echo "========================================="
echo "Certificate generation complete!"
echo "========================================="
echo "Server certificate: ${EXECUTOR_TLS_DIR}/server.crt"
echo "Server private key: ${EXECUTOR_TLS_DIR}/server.key"
echo "CA certificate: ${EXECUTOR_TLS_DIR}/ca.crt"
echo "========================================="
echo ""
echo "Usage:"
echo "  Set environment variables to point to the new certificates:"
echo "  export TLS_CERT_PATH=${EXECUTOR_TLS_DIR}/server.crt"
echo "  export TLS_KEY_PATH=${EXECUTOR_TLS_DIR}/server.key"
echo "  export TLS_CA_PATH=${EXECUTOR_TLS_DIR}/ca.crt"
echo ""
echo "  Or configure in .env file:"
echo "  TLS_CERT_PATH=${EXECUTOR_TLS_DIR}/server.crt"
echo "  TLS_KEY_PATH=${EXECUTOR_TLS_DIR}/server.key"
echo "  TLS_CA_PATH=${EXECUTOR_TLS_DIR}/ca.crt"