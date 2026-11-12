# Security

## Reporting

Please report vulnerabilities privately via GitHub Security Advisories on this repository.
Do not open a public issue.

## What runs where

- **You are the host.** The Telegram session, `api_hash` and the agent token hash are files on
  your server's disk (`/data`). Anyone with access to your hosting account or that disk can read
  them. Protect the hosting and GitHub accounts with 2FA.
- **Your agent** gets an HTTPS address and a bearer token. It can read only chats in the `Agent`
  folder and cannot send anything. If the token leaks, rotate it in `/setup`.
- **Supply chain.** Whoever controls the code your server runs can read the session. Deploy from
  your own copy, update deliberately, and review changes before pulling them.
- **Chat content is untrusted input** for your agent: anyone in a shared chat can write text
  aimed at it.

## Kill switch

Telegram → Settings → Devices → end this server's session. Access stops immediately, whatever
state the server is in.

## Hardening built in

HTTPS-only setup and API, first-visitor owner claim with sign-in lockout, folder pinned by id and title (fails
closed on any change), media served with `nosniff` + CSP sandbox, exactly one process per session.
