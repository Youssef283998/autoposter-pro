# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

## Reporting a Vulnerability

Please report security vulnerabilities by email to security@example.com.

Do not open a public issue for security reports.

We aim to acknowledge reports within 48 hours and provide a fix or mitigation within 7 days.

## Scope

AutoPoster Pro is a self-hosted application. The following are in scope:

- Credential storage and encryption
- OAuth token handling
- Web dashboard authentication and CSRF protection
- Input validation and path traversal prevention
- Dependency vulnerabilities (tracked via pip-audit in CI)

The following are out of scope:

- Vulnerabilities in third-party platform APIs
- Physical access to the host machine
- Social engineering of the operator
