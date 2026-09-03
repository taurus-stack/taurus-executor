# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

We take the security of Taurus Executor seriously. If you believe you have found a security vulnerability, please report it to us as described below.

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to [taurus-stack@outlook.com](mailto:taurus-stack@outlook.com).

You should receive a response within 48 hours. If for some reason you do not, please follow up via email to ensure we received your original message.

Please include the requested information listed below (as much as you can provide) to help us better understand the nature and scope of the possible issue:

- Type of issue (e.g. buffer overflow, SQL injection, cross-site scripting, etc.)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit the issue

This information will help us triage your report more quickly.

## Preferred Languages

We prefer all communications to be in English.

## Security Best Practices

When deploying Taurus Executor in production:

1. **Always use mTLS**: Enable mutual TLS authentication for all gRPC communications
2. **Keep certificates secure**: Store private keys securely and rotate them regularly
3. **Use strong passwords**: For session-based authentication, use strong, unique passwords
4. **Limit network exposure**: Only expose the gRPC port to trusted networks
5. **Regular updates**: Keep Taurus Executor updated to the latest version
6. **Monitor logs**: Regularly review logs for suspicious activity
7. **Use firewall rules**: Restrict access to the gRPC port using firewall rules
8. **Audit permissions**: Regularly audit command execution permissions

## Security Features

Taurus Executor includes several security features:

- **mTLS Authentication**: Mutual TLS for secure client-server communication
- **Certificate Revocation**: CRL checking for revoked certificates
- **Ticket-based Auth**: Temporary ticket-based authentication for sessions
- **Command Validation**: Validation of command execution parameters
- **Audit Logging**: Comprehensive logging of all command execution