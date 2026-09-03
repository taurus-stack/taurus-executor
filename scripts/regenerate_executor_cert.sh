#!/bin/bash
# Regenerate executor server TLS certificate
# Signed using taurus-backend's CA certificate, includes SAN extension

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_CERTS_DIR="${PROJECT_ROOT}/../taurus-backend/certs"
EXECUTOR_TLS_DIR="${PROJECT_ROOT}/tls"
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
    exit 1
fi

# Create executor TLS directory
mkdir -p "${EXECUTOR_TLS_DIR}"

echo "========================================="
echo "Executor Server Certificate Regeneration"
echo "========================================="
echo "CA certificate: ${CA_CERT}"
echo "CA private key: ${CA_KEY}"
echo "Output directory: ${EXECUTOR_TLS_DIR}"
echo "========================================="

# Certificate name
CERT_NAME="server"  # executor uses server.crt as gRPC server certificate
CERT_DAYS="${1:-365}"
GRPC_SERVER_NAME="${GRPC_SERVER_NAME:-taurus-grpc-server}"

echo "Certificate name: ${CERT_NAME}"
echo "Validity: ${CERT_DAYS} days"
echo "gRPC service name: ${GRPC_SERVER_NAME}"
echo "SAN: DNS:${GRPC_SERVER_NAME}"
echo "========================================="

# 1. Generate private key
echo "📝 Step 1: Generating private key..."
openssl genrsa -out "${EXECUTOR_TLS_DIR}/${CERT_NAME}.key 2048
chmod 600 "${EXECUTOR_TLS_DIR}/${CERT_NAME}.key"
echo "✅ Private key generated successfully: ${EXECUTOR_TLS_DIR}/${CERT_NAME}.key"

# 2. Generate CSR
echo "📝 Step 2: Generating Certificate Signing Request (CSR)..."
openssl req -new \
    -key "${EXECUTOR_TLS_DIR}/${CERT_NAME}.key" \
    -out "${EXECUTOR_TLS_DIR}/${CERT_NAME}.csr" \
    -subj "/C=CN/ST=Beijing/L=Beijing/O=TaurusOps/CN=${GRPC_SERVER_NAME}" \
    -addext "subjectAltName=DNS:${GRPC_SERVER_NAME}"
echo "✅ CSR generated successfully: ${EXECUTOR_TLS_DIR}/${CERT_NAME}.csr"

# 3. Sign certificate using CA (includes SAN extension, using v3_server extension)
echo "📝 Step 3: Signing server certificate with CA (includes SAN extension)..."

# Create temporary config file for signing server certificate
TEMP_CNF="${EXECUTOR_TLS_DIR}/openssl_temp.cnf"
cat > "${TEMP_CNF}" << EOF
[v3_server_with_san]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:${GRPC_SERVER_NAME}
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

openssl ca \
    -config "${OPENSSL_CNF}" \
    -cert "${CA_CERT}" \
    -keyfile "${CA_KEY}" \
    -in "${EXECUTOR_TLS_DIR}/${CERT_NAME}.csr" \
    -out "${EXECUTOR_TLS_DIR}/${CERT_NAME}.crt" \
    -days "${CERT_DAYS}" \
    -batch \
    -extfile "${TEMP_CNF}" \
    -extensions v3_server_with_san

# Clean up temporary config file
rm -f "${TEMP_CNF}"

echo "✅ Certificate signed successfully: ${EXECUTOR_TLS_DIR}/${CERT_NAME}.crt"

# 4. Clean up CSR file
rm -f "${EXECUTOR_TLS_DIR}/${CERT_NAME}.csr

# 5. Verify certificate
echo "📝 Step 4: Verifying certificate..."
openssl verify -CAfile "${CA_CERT}" "${EXECUTOR_TLS_DIR}/${CERT_NAME}.crt"
echo "✅ Certificate verification successful"

# 6. Display certificate information
echo ""
echo "========================================="
echo "Certificate Information"
echo "========================================="
openssl x509 -in "${EXECUTOR_TLS_DIR}/${CERT_NAME}.crt" -noout -subject -issuer -dates -serial
echo ""
echo "SAN extension:"
openssl x509 -in "${EXECUTOR_TLS_DIR}/${CERT_NAME}.crt" -noout -text | grep -A 1 "Subject Alternative Name"

echo ""
echo "========================================="
echo "Certificate regeneration complete!"
echo "========================================="
echo "Certificate file: ${EXECUTOR_TLS_DIR}/${CERT_NAME}.crt"
echo "Private key file: ${EXECUTOR_TLS_DIR}/${CERT_NAME}.key"
echo "CA certificate: ${CA_CERT}"
echo "========================================="
echo ""
echo "⚠️  Please restart the executor service to use the new certificate"