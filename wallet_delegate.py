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
RULES_FILE = os.path.join(BASE_DIR, "delegate_rules.json")   # stores delegated EOA metadata (addresses, optional saved PKs if user allows)
LOG_FILE   = os.path.join(BASE_DIR, "delegate.log")

# ---- Logging ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger("auto-transfer")

# ---- UI/color helpers (reuse if you have utils.colors) ----
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

    def print_progress_bar(prefix, *args, **kwargs):
        pass

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

# ---- Chain config helper (support array or dict format) ----
def _load_chains():
    data = load_json(CHAIN_FILE, {})
    if isinstance(data, list):
        # convert to dict by key field
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
    """Return fee dict depending on mode. cheap -> lower priority fees."""
    try:
        base = w3.eth.gas_price
    except Exception:
        base = w3.to_wei(3, "gwei")
    # multipliers
    if mode == "fast":
        mult = 2.5
        prio = 3
    elif mode == "normal":
        mult = 1.5
        prio = 2
    else:  # cheap
        mult = 1.0
        prio = 1
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
        print_error("RPC URL kosong untuk chain %s" % chain_key)
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
        print_error(f"Gagal konek RPC: {chain_key} ({rpc})")
        return None, None
    chain_id = info.get("chain_id") or info.get("chainId")
    if not chain_id:
        try:
            chain_id = w3.eth.chain_id
        except Exception:
            chain_id = None
    return w3, chain_id

# ---- Delegates storage format ----
# RULES_FILE will store a dict:
# { "delegates": { "<chain>": [ { "address": "...", "label":"...", "save_pk": false, "pk": null } ] }, "default_sink": "0x...", "settings": {} }
def _ensure_rules():
    r = load_json(RULES_FILE, {})
    if "delegates" not in r:
        r["delegates"] = {}
    return r

# ---- Balance & transfer helpers ----
def wei_to_eth(w3, v):
    try:
        return w3.from_wei(int(v), "ether")
    except Exception:
        # fallback / no conversion
        return Decimal(v) / Decimal(10**18)

def eth_to_wei(w3, v):
    try:
        return w3.to_wei(Decimal(str(v)), "ether")
    except Exception:
        return int(Decimal(str(v)) * (10**18))

def compute_send_amount(w3, balance_wei, fee_mode="cheap", gas_limit=21000, reserve_wei=0):
    """
    Compute value to send = balance - gas_cost - reserve_wei.
    gas_cost estimated using _guess_fees (price * gas_limit)
    """
    fees = _guess_fees(w3, mode=fee_mode)
    if "gasPrice" in fees:
        gp = fees["gasPrice"]
    else:
        # use maxFeePerGas if available
        gp = fees.get("maxFeePerGas") or fees.get("gasPrice") or w3.to_wei(1, "gwei")
    gas_cost = int(gp) * int(gas_limit)
    value = int(balance_wei) - gas_cost - int(reserve_wei)
    if value <= 0:
        return 0, gas_cost
    return value, gas_cost

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
    }
    tx.update(fees)
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    tx_hash = w3.eth.send_raw_transaction(raw)
    return tx_hash.hex()

# ---- Monitoring loop ----
def monitor_loop(chains_to_check, fee_mode="cheap", poll_interval=10, dry_run=False):
    """
    chains_to_check: dict mapping chain_key -> list of delegate entries.
    Each delegate entry: { "address": "", "pk": optional, "reserve_native": optional, "label": optional }
    """
    print_info(f"Mulai monitor {len(chains_to_check)} chain. Interval: {poll_interval}s. Fee mode: {fee_mode}. Dry run: {dry_run}")
    try:
        while True:
            for chain_key, delegates in chains_to_check.items():
                w3, _ = connect_chain(chain_key)
                if not w3:
                    continue
                # chain config defaults
                chain_cfg = _load_chains().get(chain_key, {})
                decimals = chain_cfg.get("decimals", 18)
                threshold_native = Decimal(str(chain_cfg.get("threshold_native", 0.0002)))
                reserve_native = Decimal(str(chain_cfg.get("reserve_native", 0.00002)))
                threshold_wei = eth_to_wei(w3, threshold_native)
                reserve_wei = eth_to_wei(w3, reserve_native)
                for entry in delegates:
                    addr = entry.get("address")
                    pk = entry.get("pk")  # may be None
                    label = entry.get("label") or addr
                    try:
                        checksum = w3.to_checksum_address(addr)
                        bal = w3.eth.get_balance(checksum)
                    except Exception as e:
                        log.warning("[%s][%s] gagal cek balance: %s", chain_key, addr, e)
                        continue
                    if bal <= threshold_wei:
                        # skip
                        log.debug("[%s][%s] balance %s <= threshold %s", chain_key, wei_to_eth(w3, bal), threshold_native)
                        continue
                    # eligible
                    send_amt, gas_cost = compute_send_amount(w3, bal, fee_mode=fee_mode, gas_limit=21000, reserve_wei=reserve_wei)
                    if send_amt <= 0:
                        log.info("[%s][%s] saldo %s tapi tidak cukup untuk gas+reserve", chain_key, addr, wei_to_eth(w3, bal))
                        continue
                    sink = _ensure_rules().get("default_sink")
                    if not sink:
                        print_warning("Default sink belum diset. Batalkan send.")
                        return
                    print_info(f"[{chain_key}] {label} ({mask_middle(addr)}) => balance {wei_to_eth(w3, bal)} will send {wei_to_eth(w3, send_amt)} to {mask_middle(sink)} (gas cost {wei_to_eth(w3, gas_cost)})")
                    log.info("[%s] prepare send from %s -> %s value=%s gas=%s", chain_key, addr, sink, send_amt, gas_cost)
                    if dry_run:
                        continue
                    if not pk:
                        print_warning(f"No PK for {addr} — cannot send. Skipping.")
                        continue
                    try:
                        txh = send_native(w3, pk, sink, send_amt, fee_mode=fee_mode, gas_limit=21000)
                        print_success(f"[{chain_key}] Sent tx: {txh}")
                        log.info("[%s] sent %s -> tx %s", chain_key, addr, txh)
                    except Exception as e:
                        print_error(f"[{chain_key}] Gagal kirim dari {mask_middle(addr)}: {e}")
                        log.exception("send error")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print_info("Monitor dihentikan oleh user.")

# ---- Menu operations ----
def add_delegate_interactive():
    clear_screen()
    print_section_header("Tambah Delegate (EOA)")

    # choose chain
    chains = list(_load_chains().keys())
    if not chains:
        print_error("Tidak ada chain di utils/chain.json")
        pause_back()
        return
    if questionary:
        ck = questionary.select("Pilih chain:", choices=chains).ask()
    else:
        print("Tersedia chains:", ", ".join(chains))
        ck = input("Chain key: ").strip()
    if not ck:
        print_error("Chain kosong"); pause_back(); return
    addr = input("Alamat wallet (EOA) yang ingin dimonitor: ").strip()
    if not addr:
        print_error("Alamat kosong"); pause_back(); return
    savepk = False
    pk = None
    if questionary:
        savepk = questionary.confirm("Simpan private key ke disk? (tidak disarankan)").ask()
    else:
        ans = input("Simpan private key ke disk? (y/N): ").strip().lower()
        savepk = ans == "y"
    if savepk:
        print_warning("Menyimpan private key di disk = BERISIKO. Pastikan file aman.")
        confirm = input("Ketik 'I UNDERSTAND' untuk konfirmasi menyimpan plaintext PK: ").strip()
        if confirm == "I UNDERSTAND":
            pk = input("Masukkan private key (hex, tanpa 0x or with): ").strip()
        else:
            print_warning("Konfirmasi tidak diterima. PK tidak disimpan. Kamu harus input PK tiap sesi.")
            savepk = False
    else:
        # keep pk in-memory for this session only
        pk = getpass("Masukkan private key (akan disimpan ke memori selama runtime): ").strip()
    label = input("Label (opsional): ").strip() or None

    # store
    r = _ensure_rules()
    r.setdefault("delegates", {})
    r["delegates"].setdefault(ck, [])
    entry = {"address": addr, "label": label, "save_pk": bool(savepk)}
    if savepk and pk:
        entry["pk"] = pk
    else:
        # store pk only in memory in runtime map (not persisted)
        entry["pk"] = pk if pk else None
    r["delegates"][ck].append(entry)
    save_json(RULES_FILE, r)
    print_success("Delegate ditambahkan (ingat: jika kamu tidak menyimpan PK ke disk, masukkan kembali saat restart).")
    pause_back()

def list_delegates_menu():
    clear_screen()
    print_section_header("Daftar Delegates (EOA)")
    r = _ensure_rules()
    delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Belum ada delegate tersimpan.")
        pause_back()
        return
    lines = []
    for ck, items in delegates.items():
        lines.append(f"{Colors.BOLD}{Colors.CYAN}Chain: {ck}{Colors.ENDC}")
        for i, it in enumerate(items, 1):
            addr = it.get("address")
            label = it.get("label") or "-"
            saved = it.get("save_pk", False)
            pk_display = mask_middle(it.get("pk", "")) if it.get("pk") else ("(runtime only)" if not saved else "(saved)")
            lines.append(f"  {i}. {label}")
            lines.append(f"     Addr : {addr}")
            lines.append(f"     PK   : {pk_display}")
            lines.append(f"     Save : {saved}")
        lines.append("")
    print_box("📜 DAFTAR DELEGATE (EOA)", lines, Colors.BLUE)
    pause_back()

def remove_delegate_menu():
    clear_screen()
    print_section_header("Hapus Delegate")
    r = _ensure_rules()
    delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Tidak ada delegate.")
        pause_back(); return
    chains = list(delegates.keys())
    if questionary:
        ck = questionary.select("Pilih chain:", choices=chains).ask()
    else:
        print("Chains:", ", ".join(chains))
        ck = input("Chain: ").strip()
    items = delegates.get(ck, [])
    if not items:
        print_warning("Tidak ada delegate di chain itu."); pause_back(); return
    labels = [f"{i+1}. {it.get('label') or it.get('address')}" for i, it in enumerate(items)]
    if questionary:
        sel = questionary.checkbox("Pilih yang ingin dihapus:", choices=labels).ask() or []
        to_delete = set(int(s.split(".")[0]) - 1 for s in sel)
    else:
        print("\n".join(labels))
        raw = input("Nomor (pisah koma): ").strip()
        to_delete = set(int(x) - 1 for x in raw.split(",") if x.strip().isdigit())
    newlist = [it for i, it in enumerate(items) if i not in to_delete]
    delegates[ck] = newlist
    r["delegates"] = delegates
    save_json(RULES_FILE, r)
    print_success("Perubahan disimpan.")
    pause_back()

def set_default_sink_menu():
    clear_screen()
    print_section_header("Set Default Sink")
    r = _ensure_rules()
    cur = r.get("default_sink", "")
    sink = input(f"Sink address [{cur}]: ").strip() or cur
    r["default_sink"] = sink
    save_json(RULES_FILE, r)
    print_success("Default sink tersimpan.")
    pause_back()

def start_monitor_menu():
    clear_screen()
    print_section_header("Start Monitor")
    r = _ensure_rules()
    delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Tidak ada delegate. Tambah dulu.")
        pause_back(); return
    # collect delegates but ensure we don't lose runtime-only PKs stored earlier in the file:
    # The RULES_FILE may contain pk fields if user chose to save them; otherwise we assume pk None.
    chains_to_check = {}
    for ck, items in delegates.items():
        chains_to_check[ck] = []
        for it in items:
            chains_to_check[ck].append({"address": it.get("address"), "pk": it.get("pk"), "label": it.get("label")})
    # ask options
    fee_mode = "cheap"
    interval = 12
    dry_run = False
    if questionary:
        fee_mode = questionary.select("Mode biaya gas:", choices=["cheap", "normal", "fast"]).ask() or "cheap"
        interval = int(questionary.text("Polling interval (detik):", default="12").ask())
        dry_run = questionary.confirm("Dry run? (tidak mengirim tx, hanya simulate)").ask()
    else:
        ans = input("Mode fee [cheap/normal/fast] (default cheap): ").strip().lower()
        if ans in ("cheap", "normal", "fast"):
            fee_mode = ans
        try:
            iv = input("Polling interval (detik, default 12): ").strip()
            if iv:
                interval = max(3, int(iv))
        except Exception:
            pass
        dr = input("Dry run? (y/N): ").strip().lower()
        dry_run = dr == "y"
    print_info("Mulai monitor. Tekan Ctrl-C untuk stop.")
    monitor_loop(chains_to_check, fee_mode=fee_mode, poll_interval=interval, dry_run=dry_run)
    pause_back()

def manual_sweep_menu():
    clear_screen()
    print_section_header("Manual Sweep (sekali kirim dari EOA)")
    r = _ensure_rules()
    delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Tidak ada delegate tersedia.")
        pause_back(); return
    # select chain then address
    chains = list(delegates.keys())
    if questionary:
        ck = questionary.select("Pilih chain:", choices=chains).ask()
    else:
        print("Chains:", ", ".join(chains)); ck = input("Chain: ").strip()
    items = delegates.get(ck, [])
    labels = [f"{i+1}. {it.get('label') or it.get('address')}" for i, it in enumerate(items)]
    if questionary:
        sel = questionary.select("Pilih delegate:", choices=labels).ask()
        idx = labels.index(sel)
    else:
        print("\n".join(labels))
        idx = int(input("Nomor: ").strip()) - 1
    entry = items[idx]
    addr = entry.get("address")
    pk = entry.get("pk")
    if not pk:
        pk = getpass("Masukkan private key untuk address ini: ").strip()
    # connect
    w3, _ = connect_chain(ck)
    if not w3:
        pause_back(); return
    bal = w3.eth.get_balance(w3.to_checksum_address(addr))
    print_info(f"Balance {wei_to_eth(w3, bal)}")
    # threshold/reserve from chain config
    cfg = _load_chains().get(ck, {})
    reserve_native = Decimal(str(cfg.get("reserve_native", 0.00002)))
    reserve_wei = eth_to_wei(w3, reserve_native)
    send_amt, gas_cost = compute_send_amount(w3, bal, fee_mode="normal", gas_limit=21000, reserve_wei=reserve_wei)
    if send_amt <= 0:
        print_warning("Tidak cukup balance untuk menutup gas + reserve.")
        pause_back(); return
    sink = _ensure_rules().get("default_sink")
    if not sink:
        print_warning("Default sink belum di-set."); pause_back(); return
    print_info(f"Siap kirim {wei_to_eth(w3, send_amt)} ke {mask_middle(sink)} (gas {wei_to_eth(w3, gas_cost)})")
    confirm = input("Ketik 'yes' untuk konfirmasi: ").strip().lower()
    if confirm != "yes":
        print_warning("Dibatalkan.")
        pause_back(); return
    try:
        txh = send_native(w3, pk, sink, send_amt, fee_mode="normal", gas_limit=21000)
        print_success(f"Tx sent: {txh}")
    except Exception as e:
        print_error(f"Gagal kirim: {e}")
    pause_back()

# ---- Main menu ----
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
        if questionary:
            ch = questionary.select("Pilih:", choices=[QChoice(m, str(i+1)) for i, m in enumerate(menu)]).ask()
        else:
            ch = input("Pilih (1-7): ").strip()
        if ch == "1" or ch == "1) Tambah Delegate (EOA)":
            add_delegate_interactive()
        elif ch == "2":
            list_delegates_menu()
        elif ch == "3":
            remove_delegate_menu()
        elif ch == "4":
            set_default_sink_menu()
        elif ch == "5":
            start_monitor_menu()
        elif ch == "6":
            manual_sweep_menu()
        elif ch == "7":
            print_info("Bye.")
            break
        else:
            print_error("Pilihan tidak valid.")
            time.sleep(1)

if __name__ == "__main__":
    menu_main()
