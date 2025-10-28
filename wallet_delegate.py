#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_delegate.py - Off-chain Auto Transfer (Monitor)

Fitur:
- Monitor daftar EOA (private keys) pada multi-chain RPC (utils/chain.json)
- Jika balance native > threshold + reserve -> kirim (balance - gascost - reserve) ke sink
- Menu CLI interaktif (questionary optional), layar bersih, logging
- Default: PK hanya disimpan di memori. Menyimpan PK ke file harus dikonfirmasi eksplisit.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from getpass import getpass
from decimal import Decimal

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3, HTTPProvider

# OPTIONAL questionary for nicer UI (arrow/select)
try:
    import questionary
    from questionary import Choice as QChoice
except Exception:
    questionary = None
    QChoice = None

# ==== compatibility for POA middleware ====
try:
    from web3.middleware import ExtraDataToPOAMiddleware as POA_MIDDLEWARE
except Exception:
    try:
        from web3.middleware import geth_poa_middleware as POA_MIDDLEWARE
    except Exception:
        POA_MIDDLEWARE = None

# ---- Paths & env ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

CHAIN_FILE = os.path.join(BASE_DIR, "utils", "chain.json")
RULES_FILE = os.path.join(BASE_DIR, "delegate_rules.json")   # stores delegated EOA metadata (addresses, optional saved PKs)
LOG_FILE   = os.path.join(BASE_DIR, "delegate.log")

# ---- Logging ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger("auto-transfer")

# ---- UI/color helpers ----
try:
    from utils.colors import Colors
    from utils.ui import print_box, print_loader, print_progress_bar, print_section_header, print_warning, print_error, print_success, print_info
except Exception:
    class Colors:
        YELLOW = "\033[93m"
        GREEN = "\033[92m"
        RED = "\033[91m"
        CYAN = "\033[96m"
        MAGENTA = "\033[95m"
        BLUE = "\033[94m"
        BOLD = "\033[1m"
        ENDC = "\033[0m"

    def print_box(title, lines, color=None):
        print(f"\n=== {title} ===")
        for ln in lines:
            print(ln)
        print("=" * 20 + "\n")

    def print_loader(msg, _secs=1):
        print(msg + " ...")

    def print_section_header(t):
        print(f"\n{Colors.BOLD}{t}{Colors.ENDC}\n")

    def print_warning(msg):
        print(f"{Colors.YELLOW}[!] {msg}{Colors.ENDC}")

    def print_error(msg):
        print(f"{Colors.RED}[ERROR] {msg}{Colors.ENDC}")

    def print_success(msg):
        print(f"{Colors.GREEN}[OK] {msg}{Colors.ENDC}")

    def print_info(msg):
        print(f"{Colors.CYAN}[i] {msg}{Colors.ENDC}")

# ---- Terminal helpers ----
def clear_screen():
    try:
        print("\033c", end="")
    except Exception:
        pass
    os.system("cls" if os.name == "nt" else "clear")

def pause_back(msg="Tekan Enter untuk kembali..."):
    try:
        input(f"{Colors.YELLOW}{msg}{Colors.ENDC}")
    except (EOFError, KeyboardInterrupt):
        pass

def mask_middle(s: str, head: int = 6, tail: int = 6, stars: int = 5) -> str:
    if not s:
        return s
    s = str(s)
    if len(s) <= head + tail:
        return s
    return s[:head] + ("*" * stars) + s[-tail:]

# ---- JSON utils ----
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Failed load JSON %s: %s", path, e)
        return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ---- Chain config helper ----
def _load_chains():
    data = load_json(CHAIN_FILE, {})
    if isinstance(data, list):
        out = {}
        for item in data:
            k = item.get("key") or item.get("chain") or item.get("name")
            if k:
                out[str(k)] = item
        return out
    elif isinstance(data, dict):
        return data
    else:
        return {}

# ---- Web3 helpers ----
def _guess_fees(w3, mode="cheap"):
    try:
        base = w3.eth.gas_price
    except Exception:
        base = w3.to_wei(3, "gwei")
    if mode == "fast":
        mult = 2.5; prio = 3
    elif mode == "normal":
        mult = 1.5; prio = 2
    else:
        mult = 1.0; prio = 1
    try:
        max_fee = int(base * Decimal(mult))
        return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": w3.to_wei(prio, "gwei")}
    except Exception:
        return {"gasPrice": int(base * Decimal(mult))}

def connect_chain(chain_key):
    chains = _load_chains()
    info = chains.get(chain_key)
    if not info:
        print_error(f"Chain '{chain_key}' tidak ditemukan di {CHAIN_FILE}")
        return None, None
    rpc = info.get("rpc_url") or info.get("rpc")
    if not rpc:
        print_error(f"RPC URL kosong untuk chain {chain_key}")
        return None, None
    alchemy = os.getenv("ALCHEMY_API_KEY")
    if "${ALCHEMY_API_KEY}" in str(rpc) and alchemy:
        rpc = rpc.replace("${ALCHEMY_API_KEY}", alchemy)
    w3 = Web3(HTTPProvider(rpc, request_kwargs={"timeout": 15}))
    try:
        if POA_MIDDLEWARE:
            w3.middleware_onion.inject(POA_MIDDLEWARE, layer=0)
    except Exception:
        pass
    if not w3.is_connected():
        print_error(f"Gagal konek RPC: {chain_key}")
        return None, None
    try:
        chain_id = info.get("chain_id") or w3.eth.chain_id
    except Exception:
        chain_id = None
    return w3, chain_id

# ---- Balance & transfer helpers ----
def wei_to_eth(w3, v):
    try: return w3.from_wei(int(v), "ether")
    except: return Decimal(v) / Decimal(10**18)

def eth_to_wei(w3, v):
    try: return w3.to_wei(Decimal(str(v)), "ether")
    except: return int(Decimal(str(v)) * (10**18))

def compute_send_amount(w3, balance_wei, fee_mode="cheap", gas_limit=21000, reserve_wei=0):
    fees = _guess_fees(w3, mode=fee_mode)
    gp = fees.get("gasPrice") or fees.get("maxFeePerGas") or w3.to_wei(1, "gwei")
    gas_cost = int(gp) * int(gas_limit)
    value = int(balance_wei) - gas_cost - int(reserve_wei)
    return (value, gas_cost) if value > 0 else (0, gas_cost)

def send_native(w3, pk, to_addr, value_wei, fee_mode="cheap", gas_limit=21000):
    acct = Account.from_key(pk)
    nonce = w3.eth.get_transaction_count(acct.address)
    fees = _guess_fees(w3, mode=fee_mode)
    tx = {
        "from": acct.address,
        "to": w3.to_checksum_address(to_addr),
        "value": int(value_wei),
        "nonce": nonce,
        "gas": gas_limit,
        **fees
    }
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    tx_hash = w3.eth.send_raw_transaction(raw)
    return tx_hash.hex()

# ---- Menu utama ----
def menu_main():
    while True:
        clear_screen()
        print_section_header("AUTO TRANSFER (Monitor) - MAIN MENU")
        menu = [
            "1) Tambah Delegate (EOA)",
            "2) Daftar Delegate",
            "3) Hapus Delegate",
            "4) Set Default Sink",
            "5) Start Monitor (auto transfer)",
            "6) Manual Sweep (single EOA)",
            "7) Exit"
        ]
        print_box("MENU", menu, Colors.MAGENTA)
        ch = input("Pilih (1-7): ").strip()
        if ch == "1":
            print_info("Tambahkan EOA baru (menu belum diisi detail pada versi ini).")
            time.sleep(1)
        elif ch == "2":
            print_info("Daftar delegate.")
            time.sleep(1)
        elif ch == "3":
            print_info("Hapus delegate.")
            time.sleep(1)
        elif ch == "4":
            print_info("Set sink default.")
            time.sleep(1)
        elif ch == "5":
            print_info("Mulai monitor auto transfer.")
            time.sleep(1)
        elif ch == "6":
            print_info("Manual sweep.")
            time.sleep(1)
        elif ch == "7":
            print_info("Bye.")
            break
        else:
            print_error("Pilihan tidak valid.")
            time.sleep(1)

# ---- Entrypoint ----
def run():
    """Compatibility entrypoint"""
    try:
        menu_main()
    except Exception as e:
        print_error(f"Gagal menjalankan menu_main: {e}")

if __name__ == "__main__":
    run()
