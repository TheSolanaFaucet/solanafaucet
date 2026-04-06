"""
═══════════════════════════════════════════════════════════════════════════════
  SOLANA FAUCET — BACKEND
  Author: AragonCrypto
  Description: Flask Blueprint backend for the Solana Faucet.
               Handles SOL airdrops, rate limiting, email verification,
               stats, activation toggles, and DRAG-AND-DROP CAPTCHA.
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import time
import random
import string
import hashlib
import uuid
import traceback
import smtplib
import ssl
from datetime import datetime
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import Blueprint, render_template, request, jsonify

# ─── SOLANA IMPORTS ──────────────────────────────────────────────────────────
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.compute_budget import set_compute_unit_price

# ═════════════════════════════════════════════════════════════════════════════
# DYNAMIC PATH & SECRETS IMPORT
# ═════════════════════════════════════════════════════════════════════════════

# 1. Get the absolute directory of THIS specific python file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. Add this directory to sys.path so Python knows to look for imports here
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 3. Now we can safely import our local secrets file
try:
    from faucet_private_28395202573 import (
        RPC_URL,
        SENDER_SECRET_BASE58,
        FAUCET_WALLET_ADDRESS,
        SMTP_EMAIL,
        SMTP_APP_PASSWORD
    )
except ImportError:
    print(f"[!] ERROR: faucet_secrets.py not found in directory: {BASE_DIR}")
    print("[!] Please create it and add your credentials.")
    exit(1)


# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION — ACTIVATION VARIABLES
# ═════════════════════════════════════════════════════════════════════════════

FAUCET_ACTIVE = False          # Set True to enable SOL payouts
CA_ACTIVE = False              # Set True once $FAUCET token is deployed
EMAIL_REQUIRED = True          # Set True to gate claims behind email codes
CAPTCHA_REQUIRED = True        # Set True to require drag-and-drop human verification

SOL_PER_CLAIM = 0.001
LAMPORTS_PER_SOL = 1_000_000_000
COOLDOWN_SECONDS = 300
PRIORITY_FEE_MICROLAMPORTS = 5000

CONTRACT_ADDRESS = "FAUCETfkQndheSXFDNDYdke7okEb9XThDeFPD4CCpQQv"
POOL_ADDRESS = "FAUCETfkQndheSXFDNDYdke7okEb9XThDeFPD4CCpQQv"
TRADE_URL = "https://pump.fun/coin/FAUCETfkQndheSXFDNDYdke7okEb9XThDeFPD4CCpQQv"

FAUCET_DISABLED_MESSAGE = "The faucet is currently offline. Follow @TheSolanaFaucet for updates."

# ─── EMAIL VERIFICATION CONFIG ───────────────────────────────────────────────
EMAIL_CODE_LENGTH = 6
EMAIL_CODE_EXPIRY_SECONDS = 300
EMAIL_RESEND_COOLDOWN_SECONDS = 60
CAPTCHA_EXPIRY_SECONDS = 300


# ═════════════════════════════════════════════════════════════════════════════
# FILE STORAGE
# ═════════════════════════════════════════════════════════════════════════════
# We already defined BASE_DIR above, so we just append the file names here
CLAIMS_DB_FILE = os.path.join(BASE_DIR, "faucet_claims.json")
CODES_DB_FILE = os.path.join(BASE_DIR, "faucet_email_codes.json")
CAPTCHAS_DB_FILE = os.path.join(BASE_DIR, "faucet_captchas.json")


# ═════════════════════════════════════════════════════════════════════════════
# BLUEPRINT
# ═════════════════════════════════════════════════════════════════════════════
solanafaucet_bp = Blueprint(
    'solanafaucet',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/solanafaucet'
)


# ═════════════════════════════════════════════════════════════════════════════
# EMAIL GENERATION & SENDING HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def create_email_html(code):
    current_year = datetime.now().year

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Your Verification Code</title>
        <style>
            body {{ margin: 0; padding: 0; background-color: #fafafa; }}
            table {{ border-spacing: 0; border-collapse: collapse; }}
            td {{ padding: 0; }}
            .container {{ width: 100%; max-width: 540px; margin: 0 auto; }}
        </style>
    </head>
    <body style="margin: 0; padding: 0; background-color: #fafafa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased;">
        <table width="100%" border="0" cellspacing="0" cellpadding="0" bgcolor="#fafafa" style="background-color: #fafafa; padding: 40px 20px;">
            <tr>
                <td align="center">
                    <table class="container" border="0" cellspacing="0" cellpadding="0" style="width: 100%; max-width: 540px; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 8px 24px rgba(0,0,0,0.05); border: 1px solid rgba(0,0,0,0.04);">

                        <!-- Gradient Banner Header -->
                        <tr>
                            <td align="center" style="background: linear-gradient(135deg, #9945FF 0%, #14F195 100%); background-color: #111111; padding: 36px 20px;">
                                <!-- Image Logo -->
                                <div style="display: inline-block; padding: 12px; background: rgba(255,255,255,0.15); border-radius: 14px; margin-bottom: 16px;">
                                    <img src="https://i.imgur.com/us2XdMC.png" alt="Solana Logo" width="200" style="display: block; border: 0; height: auto;" />
                                </div>
                            </td>
                        </tr>

                        <!-- Body Content -->
                        <tr>
                            <td align="center" style="padding: 48px 32px;">
                                <h2 style="margin: 0 0 16px; font-size: 20px; color: #111111; font-weight: 600; letter-spacing: -0.3px;">Verify your claim</h2>
                                <p style="margin: 0 0 32px; font-size: 15px; color: #666666; line-height: 1.6; max-width: 380px;">
                                    You requested a verification code to claim your free <strong>0.001 SOL</strong>. Please use the code below to complete your claim.
                                </p>

                                <!-- Code Box -->
                                <table border="0" cellspacing="0" cellpadding="0" style="margin: 0 auto;">
                                    <tr>
                                        <td align="center" bgcolor="#f8f9fa" style="background-color: #fafafa; border: 1px solid #eeeeee; border-radius: 12px; padding: 20px 32px;">
                                            <div style="font-family: 'Courier New', Courier, monospace; font-size: 36px; font-weight: 700; color: #111111; letter-spacing: 12px; line-height: 1; margin-left: 12px;">
                                                {code}
                                            </div>
                                        </td>
                                    </tr>
                                </table>

                                <p style="margin: 32px 0 0; font-size: 14px; color: #999999; line-height: 1.6;">
                                    This code will expire in <strong>5 minutes</strong>. If you did not request this, you can safely ignore this email.
                                </p>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td align="center" style="background-color: #fafafa; padding: 24px; border-top: 1px solid rgba(0,0,0,0.04);">
                                <p style="margin: 0; font-size: 12px; color: #aaaaaa;">
                                    &copy; {current_year} Solana Faucet. Built by AragonCrypto.<br>
                                    <a href="https://x.com/TheSolanaFaucet" style="color: #0dba74; text-decoration: none;">@TheSolanaFaucet</a>
                                </p>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    return html

def create_email_plaintext(code):
    return f"""Solana Faucet

Verify your claim.
You requested a verification code to claim your free 0.001 SOL.

Your verification code is: {code}

This code will expire in 5 minutes. If you did not request this, you can safely ignore this email.

--
Solana Faucet by AragonCrypto
@TheSolanaFaucet
"""

def send_verification_email(receiver_email, code):
    message = MIMEMultipart("alternative")
    message["Subject"] = f"{code} is your Solana Faucet verification code"
    message["From"] = f"Solana Faucet <{SMTP_EMAIL}>"
    message["To"] = receiver_email

    part1 = MIMEText(create_email_plaintext(code), "plain")
    part2 = MIMEText(create_email_html(code), "html")

    message.attach(part1)
    message.attach(part2)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_EMAIL, receiver_email, message.as_string())


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _read_json(filepath):
    if not os.path.exists(filepath):
        _write_json(filepath, [])
        return[]
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return[]

def _write_json(filepath, data):
    tmp = filepath + ".tmp"
    try:
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, filepath)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

def get_claims(): return _read_json(CLAIMS_DB_FILE)
def save_claims(data): _write_json(CLAIMS_DB_FILE, data)
def get_email_codes(): return _read_json(CODES_DB_FILE)
def save_email_codes(data): _write_json(CODES_DB_FILE, data)
def get_captchas(): return _read_json(CAPTCHAS_DB_FILE)
def save_captchas(data): _write_json(CAPTCHAS_DB_FILE, data)


# ═════════════════════════════════════════════════════════════════════════════
# UTILITY HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def get_client_ip():
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0].split(",")[0].strip()
    return request.remote_addr

def generate_code():
    return ''.join(random.choices(string.digits, k=EMAIL_CODE_LENGTH))

def hash_email(email):
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()

def validate_solana_address(addr):
    if not addr or not isinstance(addr, str):
        return False
    addr = addr.strip()
    if len(addr) < 32 or len(addr) > 44:
        return False
    base58_chars = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    if not all(c in base58_chars for c in addr):
        return False
    try:
        Pubkey.from_string(addr)
        return True
    except Exception:
        return False

def check_cooldown(wallet, ip, email_hash=None):
    claims = get_claims()
    now = time.time()
    for entry in reversed(claims):
        ts = entry.get("timestamp", 0)
        elapsed = now - ts
        if elapsed >= COOLDOWN_SECONDS:
            break
        remaining = int(COOLDOWN_SECONDS - elapsed)
        if entry.get("wallet") == wallet: return True, remaining
        if entry.get("ip") == ip: return True, remaining
        if email_hash and entry.get("email_hash") == email_hash: return True, remaining
    return False, 0

def get_faucet_balance():
    if not SENDER_SECRET_BASE58 and not FAUCET_WALLET_ADDRESS:
        return 0.0
    try:
        client = Client(RPC_URL)
        if FAUCET_WALLET_ADDRESS:
            pubkey = Pubkey.from_string(FAUCET_WALLET_ADDRESS)
        else:
            kp = Keypair.from_base58_string(SENDER_SECRET_BASE58)
            pubkey = kp.pubkey()
        resp = client.get_balance(pubkey)
        if resp.value is not None:
            return resp.value / LAMPORTS_PER_SOL
    except Exception as e:
        print(f"[Faucet] Balance check failed: {e}")
    return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# SOLANA TRANSFER — NATIVE SOL
# ═════════════════════════════════════════════════════════════════════════════

def execute_sol_payout(receiver_wallet_str):
    print(f"[Faucet] Sending {SOL_PER_CLAIM} SOL to {receiver_wallet_str}")
    try:
        client = Client(RPC_URL)
        sender_keypair = Keypair.from_base58_string(SENDER_SECRET_BASE58)
        sender_pubkey = sender_keypair.pubkey()

        try:
            receiver_pubkey = Pubkey.from_string(receiver_wallet_str)
        except Exception:
            return False, "Invalid receiver wallet address."

        balance_resp = client.get_balance(sender_pubkey)
        sender_balance = balance_resp.value if balance_resp.value else 0
        lamports_to_send = int(SOL_PER_CLAIM * LAMPORTS_PER_SOL)

        min_required = lamports_to_send + 10_000
        if sender_balance < min_required:
            return False, "Faucet wallet has insufficient balance."

        instructions =[]
        if PRIORITY_FEE_MICROLAMPORTS > 0:
            priority_ix = set_compute_unit_price(PRIORITY_FEE_MICROLAMPORTS)
            instructions.append(priority_ix)

        transfer_ix = transfer(
            TransferParams(
                from_pubkey=sender_pubkey,
                to_pubkey=receiver_pubkey,
                lamports=lamports_to_send,
            )
        )
        instructions.append(transfer_ix)

        latest_blockhash_resp = client.get_latest_blockhash()
        recent_blockhash = latest_blockhash_resp.value.blockhash

        msg = MessageV0.try_compile(
            payer=sender_pubkey,
            instructions=instructions,
            address_lookup_table_accounts=[],
            recent_blockhash=recent_blockhash,
        )

        tx = VersionedTransaction(msg,[sender_keypair])
        sig_resp = client.send_transaction(tx)
        sig_str = str(sig_resp.value)

        print(f"[Faucet] TX Success: https://solscan.io/tx/{sig_str}")
        return True, sig_str

    except Exception as e:
        error_msg = str(e)
        print(f"[Faucet] TX Failed: {traceback.format_exc()}")
        return False, error_msg


# ═════════════════════════════════════════════════════════════════════════════
# ANTI-BOT MIDDLEWARE
# ═════════════════════════════════════════════════════════════════════════════

def require_human_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        data = request.get_json(silent=True) or {}
        token = data.get("human_token", "")
        if not token or not token.startswith("V-"):
            return jsonify({"success": False, "message": "Bot detected."}), 403
        if data.get("website", ""):
            return jsonify({"success": False, "message": "Bot detected."}), 403
        return f(*args, **kwargs)
    return decorated


# ═════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═════════════════════════════════════════════════════════════════════════════

@solanafaucet_bp.route('/')
def index():
    return render_template('solanafaucet_index.html')


@solanafaucet_bp.route('/api/config', methods=['GET'])
def api_config():
    return jsonify({
        "faucet_active": FAUCET_ACTIVE,
        "ca_active": CA_ACTIVE,
        "email_required": EMAIL_REQUIRED,
        "captcha_required": CAPTCHA_REQUIRED,
        "contract_address": CONTRACT_ADDRESS if CA_ACTIVE else "",
        "pool_address": POOL_ADDRESS if CA_ACTIVE else "",
        "trade_url": TRADE_URL if CA_ACTIVE else "",
        "sol_per_claim": SOL_PER_CLAIM,
        "cooldown_seconds": COOLDOWN_SECONDS,
        "faucet_wallet": FAUCET_WALLET_ADDRESS,
        "faucet_disabled_message": FAUCET_DISABLED_MESSAGE if not FAUCET_ACTIVE else "",
    })


@solanafaucet_bp.route('/api/captcha/generate', methods=['GET'])
def api_generate_captcha():
    cryptos =["Solana", "Bitcoin", "Ethereum", "Dogecoin", "Pepe", "Chainlink", "Cardano", "Polkadot"]
    target = random.choice(cryptos)
    decoys = random.sample([c for c in cryptos if c != target], 3)
    options = [target] + decoys
    random.shuffle(options)

    challenge_id = str(uuid.uuid4())
    correct_id = None
    frontend_options =[]

    for opt in options:
        opt_id = str(uuid.uuid4())
        frontend_options.append({"id": opt_id, "name": opt})
        if opt == target:
            correct_id = opt_id

    captchas = get_captchas()
    now = time.time()

    captchas = [c for c in captchas if c["expires"] > now]
    captchas.append({
        "challenge_id": challenge_id,
        "correct_id": correct_id,
        "expires": now + CAPTCHA_EXPIRY_SECONDS
    })
    save_captchas(captchas)

    return jsonify({
        "success": True,
        "challenge_id": challenge_id,
        "instruction": f"Drag <strong>{target}</strong> into the vault.",
        "options": frontend_options
    })


@solanafaucet_bp.route('/api/send-code', methods=['POST'])
def api_send_code():
    if not EMAIL_REQUIRED:
        return jsonify({"success": False, "message": "Email verification is not enabled."}), 400

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email or "@" not in email or "." not in email:
        return jsonify({"success": False, "message": "Enter a valid email address."}), 400

    ip = get_client_ip()
    eh = hash_email(email)
    now = time.time()

    codes = get_email_codes()
    for entry in reversed(codes):
        if entry.get("email_hash") == eh or entry.get("ip") == ip:
            elapsed = now - entry.get("created_at", 0)
            if elapsed < EMAIL_RESEND_COOLDOWN_SECONDS:
                remaining = int(EMAIL_RESEND_COOLDOWN_SECONDS - elapsed)
                return jsonify({
                    "success": False,
                    "message": f"Wait {remaining}s before requesting a new code."
                }), 429

    code = generate_code()

    # Attempt to send the email via Gmail SMTP
    try:
        send_verification_email(email, code)
        print(f"[Faucet] Real Verification email sent to {email}")
    except Exception as e:
        print(f"[Faucet] SMTP Error sending email to {email}: {e}")
        return jsonify({"success": False, "message": "Failed to send email. Ensure backend SMTP configs are set."}), 500

    codes.append({
        "email_hash": eh,
        "ip": ip,
        "code": code,
        "created_at": now,
        "used": False,
    })

    codes =[c for c in codes if now - c.get("created_at", 0) < 600]
    save_email_codes(codes)

    # Removed "demo_code" from response. The frontend will now output: "Check your email."
    return jsonify({
        "success": True,
        "message": f"Code sent to {email}."
    })


@solanafaucet_bp.route('/api/verify-code', methods=['POST'])
def api_verify_code():
    if not EMAIL_REQUIRED:
        return jsonify({"success": False, "message": "Email verification is not enabled."}), 400

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()

    if not email or not code:
        return jsonify({"success": False, "message": "Email and code are required."}), 400

    eh = hash_email(email)
    now = time.time()
    codes = get_email_codes()

    for entry in reversed(codes):
        if entry.get("email_hash") == eh and not entry.get("used", False):
            if now - entry.get("created_at", 0) > EMAIL_CODE_EXPIRY_SECONDS:
                return jsonify({"success": False, "message": "Code expired. Request a new one."}), 400

            if entry.get("code") == code:
                entry["used"] = True
                save_email_codes(codes)

                verification_token = hashlib.sha256(
                    f"{eh}:{code}:{now}:{random.random()}".encode()
                ).hexdigest()[:32]

                return jsonify({
                    "success": True,
                    "message": "Email verified.",
                    "verification_token": verification_token,
                    "email_hash": eh,
                })
            else:
                return jsonify({"success": False, "message": "Incorrect code."}), 400

    return jsonify({"success": False, "message": "No pending code found. Send a new one."}), 400


@solanafaucet_bp.route('/api/claim', methods=['POST'])
@require_human_token
def api_claim():
    if not FAUCET_ACTIVE:
        return jsonify({
            "success": False,
            "message": FAUCET_DISABLED_MESSAGE,
            "disabled": True,
        }), 503

    data = request.get_json(silent=True) or {}
    wallet = (data.get("wallet_address") or "").strip()
    ip = get_client_ip()

    if not validate_solana_address(wallet):
        return jsonify({
            "success": False,
            "message": "Enter a valid Solana wallet address (32-44 base58 characters)."
        }), 400

    email_hash = None
    if EMAIL_REQUIRED:
        verification_token = data.get("verification_token", "")
        email_hash = data.get("email_hash", "")
        if not verification_token or not email_hash:
            return jsonify({
                "success": False,
                "message": "Email verification is required. Verify your email first."
            }), 400

    if CAPTCHA_REQUIRED:
        c_id = data.get("captcha_id")
        c_ans = data.get("captcha_answer")
        if not c_id or not c_ans:
            return jsonify({"success": False, "message": "Human verification is required."}), 400

        captchas = get_captchas()
        valid = False
        now = time.time()
        for c in captchas:
            if c["challenge_id"] == c_id:
                if now > c["expires"]:
                    return jsonify({"success": False, "message": "Captcha expired. Please reload and try again."}), 400
                if c["correct_id"] == c_ans:
                    valid = True
                break

        if not valid:
            return jsonify({"success": False, "message": "Incorrect captcha verification."}), 400

        captchas = [c for c in captchas if c["challenge_id"] != c_id]
        save_captchas(captchas)

    on_cd, remaining = check_cooldown(wallet, ip, email_hash)
    if on_cd:
        minutes = remaining // 60
        seconds = remaining % 60
        return jsonify({
            "success": False,
            "message": f"Cooldown active. Wait {minutes}m {seconds}s.",
            "cooldown_remaining": remaining,
        }), 429

    success, result = execute_sol_payout(wallet)

    if not success:
        return jsonify({
            "success": False,
            "message": f"Transaction failed: {result}",
        }), 500

    claims = get_claims()
    now = time.time()
    claim_record = {
        "timestamp": now,
        "readable_time": datetime.utcfromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "wallet": wallet,
        "ip": ip,
        "email_hash": email_hash or "",
        "amount_sol": SOL_PER_CLAIM,
        "tx_signature": result,
    }
    claims.append(claim_record)
    save_claims(claims)

    print(f"[Faucet] Claim #{len(claims)} — {wallet} — TX: {result}")

    return jsonify({
        "success": True,
        "message": f"Sent {SOL_PER_CLAIM} SOL to your wallet!",
        "tx_signature": result,
        "tx_link": f"https://solscan.io/tx/{result}",
        "amount": SOL_PER_CLAIM,
    })


_balance_cache = {"value": 0.0, "ts": 0}

@solanafaucet_bp.route('/api/stats', methods=['GET'])
def api_stats():
    claims = get_claims()
    total_claims = len(claims)
    total_sol = round(total_claims * SOL_PER_CLAIM, 6)
    unique_wallets = len(set(c.get("wallet", "") for c in claims))

    global _balance_cache
    now = time.time()
    if now - _balance_cache["ts"] > 30:
        _balance_cache["value"] = get_faucet_balance()
        _balance_cache["ts"] = now

    return jsonify({
        "total_claims": total_claims,
        "total_sol_distributed": total_sol,
        "faucet_balance": _balance_cache["value"],
        "unique_wallets": unique_wallets,
        "sol_per_claim": SOL_PER_CLAIM,
        "cooldown_seconds": COOLDOWN_SECONDS,
    })


@solanafaucet_bp.route('/api/recent-claims', methods=['GET'])
def api_recent_claims():
    claims = get_claims()
    recent = claims[-20:] if len(claims) > 20 else claims
    recent.reverse()

    sanitized =[]
    for c in recent:
        w = c.get("wallet", "")
        sanitized.append({
            "wallet_short": w[:4] + "..." + w[-4:] if len(w) > 8 else w,
            "amount": c.get("amount_sol", SOL_PER_CLAIM),
            "tx_signature": c.get("tx_signature", ""),
            "time_ago": _time_ago(c.get("timestamp", 0)),
        })

    return jsonify({"claims": sanitized})

def _time_ago(ts):
    diff = int(time.time() - ts)
    if diff < 60:
        return f"{diff}s ago"
    elif diff < 3600:
        return f"{diff // 60}m ago"
    elif diff < 86400:
        return f"{diff // 3600}h ago"
    else:
        return f"{diff // 86400}d ago"