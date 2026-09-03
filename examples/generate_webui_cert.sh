#!/bin/bash
# Generate client certificates for web_ui.py
# Signs client certificate using taurus-backend's CA certificate

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_CERTS_DIR="${PROJECT_ROOT}/../taurus-backend/certs"
WEBUI_TLS_DIR="${SCRIPT_DIR}/tls"

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

# Create web_ui TLS certificate directory
mkdir -p "${WEBUI_TLS_DIR}"

echo "========================================="
echo "Web UI Client Certificate Generation"
echo "========================================="
echo "CA certificate: ${CA_CERT}"
echo "CA private key: ${CA_KEY}"
echo "Output directory: ${WEBUI_TLS_DIR}"
echo "========================================="

# Certificate name (can be passed as argument)
CERT_NAME="${1:-webui-client}"
CERT_DAYS="${2:-365}"

echo "Certificate name: ${CERT_NAME}"
echo "Validity: ${CERT_DAYS} days"
echo "========================================="

# 1. Generate client private key
echo "📝 Step 1: Generating client private key..."
openssl genrsa -out "${WEBUI_TLS_DIR}/${CERT_NAME}.key" 2048
chmod 600 "${WEBUI_TLS_DIR}/${CERT_NAME}.key"
echo "✅ Private key generated successfully: ${WEBUI_TLS_DIR}/${CERT_NAME}.key"

# 2. Generate CSR (Certificate Signing Request)
echo "📝 Step 2: Generating Certificate Signing Request (CSR)..."
openssl req -new \
    -key "${WEBUI_TLS_DIR}/${CERT_NAME}.key" \
    -out "${WEBUI_TLS_DIR}/${CERT_NAME}.csr" \
    -subj "/C=CN/ST=Beijing/L=Beijing/O=TaurusOps/CN=${CERT_NAME}"
echo "✅ CSR generated successfully: ${WEBUI_TLS_DIR}/${CERT_NAME}.csr"

# 3. Sign certificate using CA
echo "📝 Step 3: Signing certificate with CA..."
openssl ca \
    -config "${OPENSSL_CNF}" \
    -cert "${CA_CERT}" \
    -keyfile "${CA_KEY}" \
    -in "${WEBUI_TLS_DIR}/${CERT_NAME}.csr" \
    -out "${WEBUI_TLS_DIR}/${CERT_NAME}.crt" \
    -days "${CERT_DAYS}" \
    -batch \
    -extensions v3_client

echo "✅ Certificate signed successfully: ${WEBUI_TLS_DIR}/${CERT_NAME}.crt"

# 4. Copy CA certificate to web_ui TLS directory
echo "📝 Step 4: Copying CA certificate to web_ui TLS directory..."
cp "${CA_CERT}" "${WEBUI_TLS_DIR}/ca.crt"
echo "✅ CA certificate copied: ${WEBUI_TLS_DIR}/ca.crt"

# 5. Verify certificate
echo "📝 Step 5: Verifying certificate..."
openssl verify -CAfile "${WEBUI_TLS_DIR}/ca.crt" "${WEBUI_TLS_DIR}/${CERT_NAME}.crt"
echo "✅ Certificate verification successful"

# 6. Display certificate information
echo ""
echo "========================================="
echo "Certificate Information"
echo "========================================="
openssl x509 -in "${WEBUI_TLS_DIR}/${CERT_NAME}.crt" -noout -subject -issuer -dates -serial

echo ""
echo "========================================="
echo "Certificate generation complete!"
echo "========================================="
echo "Certificate file: ${WEBUI_TLS_DIR}/${CERT_NAME}.crt"
echo "Private key file: ${WEBUI_TLS_DIR}/${CERT_NAME}.key"
echo "CA certificate: ${WEBUI_TLS_DIR}/ca.crt"
echo "========================================="
echo ""
echo "Usage example:"
echo "  cd examples"
echo "  python web_ui.py \\"
echo "    --client-cert-file tls/${CERT_NAME}.crt \\"
echo "    --client-key-file tls/${CERT_NAME}.key \\"
echo "    --client-ca-file tls/ca.crt"
echo ""