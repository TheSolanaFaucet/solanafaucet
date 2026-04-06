# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Solana Faucet — a web app that distributes free SOL (0.001 per claim) with a 5-minute cooldown. Two UI modes: a modern interface and a retro recreation of the 2010 Bitcoin Faucet. Funded by the upcoming $FAUCET token.

## Architecture

This is a two-file application:

- **`server.py`** — Flask Blueprint (`solanafaucet_bp`, mounted at `/solanafaucet`). Handles all backend logic: SOL transfers, rate limiting (IP + wallet + email hash), email verification via Gmail SMTP, drag-and-drop captcha generation/validation, and stats.
- **`index.html`** — Single-file frontend with all HTML, CSS, and JS inlined. Contains both the Modern and Original faucet UIs, toggled client-side. Communicates with the backend via JSON API calls.

**No build system, no package manager, no separate JS/CSS files.** The frontend is fully self-contained.

## Secrets & Configuration

Secrets are imported from a local file `faucet_private_28395202573.py` (not in repo). Required exports:
- `RPC_URL`, `SENDER_SECRET_BASE58`, `FAUCET_WALLET_ADDRESS`, `SMTP_EMAIL`, `SMTP_APP_PASSWORD`

Feature flags at the top of `server.py` control runtime behavior:
- `FAUCET_ACTIVE` — enables/disables SOL payouts
- `CA_ACTIVE` — shows $FAUCET token contract info
- `EMAIL_REQUIRED` — gates claims behind email verification
- `CAPTCHA_REQUIRED` — requires drag-and-drop captcha

## Data Storage

JSON flat files in the same directory as `server.py`:
- `faucet_claims.json` — claim history (wallet, IP, email hash, tx signature)
- `faucet_email_codes.json` — pending verification codes
- `faucet_captchas.json` — active captcha challenges

## Running

The server is a Flask Blueprint, not a standalone app. It must be registered with a parent Flask application. There is no `app.py` or entry point in this repo — the Blueprint is imported and mounted elsewhere.

Dependencies: `flask`, `solana`, `solders`

## API Endpoints

All routes are prefixed with `/solanafaucet`:

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Serve the frontend |
| `/api/config` | GET | Feature flags and faucet settings |
| `/api/captcha/generate` | GET | Generate drag-and-drop captcha challenge |
| `/api/send-code` | POST | Send email verification code |
| `/api/verify-code` | POST | Verify email code, return token |
| `/api/claim` | POST | Claim SOL (requires human_token, optional email + captcha) |
| `/api/stats` | GET | Faucet statistics (cached balance, claim count) |
| `/api/recent-claims` | GET | Last 20 claims (sanitized) |

## Anti-Bot Measures

- `require_human_token` decorator on `/api/claim` — checks for a `human_token` field starting with `"V-"` and a honeypot `website` field
- Cooldown enforced per wallet address, IP, and email hash
- Captcha: server picks a random crypto name, client must drag the correct one into a "vault"

## Solana Integration

Uses `solders` for transaction construction (VersionedTransaction with MessageV0) and `solana.rpc.api.Client` for RPC calls. Transactions include a priority fee instruction (`set_compute_unit_price`). Balance is cached for 30 seconds.
