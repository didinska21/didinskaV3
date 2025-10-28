#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_delegate.py - Off-chain Auto Transfer (Monitor Bot)

Fitur:
- Monitor daftar EOA (private keys) pada multi-chain RPC (utils/chain.json)
- Jika balance native > threshold + reserve -> kirim (balance - gas_cost - reserve) ke sink
- Mode fee: cheap/normal/fast (default cheap), pakai feeHistory saat ada, fallback gas_price
- Menu CLI interaktif (questionary optional), layar bersih, logging ke delegate.log
- Private key disimpan hanya di memori (runtime) KECUALI user setuju simpan ke disk (berisiko).
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from getpass import getpass
from decimal import Decimal, getcontext

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3, HTTPProvider

getcontext().prec = 28  # presisi tinggi untuk perhitungan gas/saldo

# ==== Optional UI (arrow/select) ====
try:
    import questionary
    from questionary import Choice as QChoice
except Exception:
    questionary = None
    QChoice = None

# ==== POA middleware compat ====
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
RULES_FILE = os.path.join(BASE_DIR, "delegate_rules.json")   # menyimpan EOA yang dimonitor + sink
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
    from utils.ui import print_box, print_loader, print_stats_box, print_section_header, print_warning, print_error, print_success, print_info
except Exception:
    class Colors:
        YELLOW = "\033[93m"; GREEN = "\033[92m"; RED = "\033[91m"
        CYAN = "\033[96m"; MAGENTA = "\033[95m"; BLUE = "\033[94m"
        BOLD = "\033[1m"; ENDC = "\033[0m"
    def print_box(title, lines, color=None):
        print(f"\n=== {title} ===")
        for ln in lines: print(ln)
        print("="*20)
    def print_loader(msg, _secs=1): print(msg + " ...")
    def print_section_header(t): print(f"\n{Colors.BOLD}{t}{Colors.ENDC}\n")
    def print_warning(msg): print(f"{Colors.YELLOW}[!] {msg}{Colors.ENDC}")
    def print_error(msg): print(f"{Colors.RED}[ERROR] {msg}{Colors.ENDC}")
    def print_success(msg): print(f"{Colors.GREEN}[OK] {msg}{Colors.ENDC}")
    def print_info(msg): print(f"{Colors.CYAN}[i] {msg}{Colors.ENDC}")
    def print_stats_box(title, stats):
        lines = [f"{k:<14}: {v}" for k,v in stats]
        print_box(title, lines, Colors.BLUE)

# ---- Terminal helpers ----
def clear_screen():
    try:
        print("\033c", end="")
    except Exception:
        pass
    os.system("cls" if os.name == "nt" else "clear")

def pause_back(msg="Tekan Enter untuk kembali..."):
    try: input(f"{Colors.YELLOW}{msg}{Colors.ENDC}")
    except (EOFError, KeyboardInterrupt): pass

def mask_middle(s: str, head: int = 6, tail: int = 6, stars: int = 5) -> str:
    if not s: return s
    s = str(s)
    if len(s) <= head + tail: return s
    return s[:head] + ("*" * stars) + s[-tail:]

# ---- JSON utils ----
def load_json(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Gagal load JSON %s: %s", path, e)
        return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ---- Config helpers ----
def _load_chains():
    data = load_json(CHAIN_FILE, {})
    if isinstance(data, list):
        out = {}
        for item in data:
            k = item.get("key") or item.get("chain") or item.get("name")
            if k: out[str(k)] = item
        return out
    return data if isinstance(data, dict) else {}

def _ensure_rules():
    r = load_json(RULES_FILE, {})
    r.setdefault("delegates", {})      # { chain_key: [{address, label, save_pk, pk?}] }
    r.setdefault("default_sink", "")   # string
    r.setdefault("settings", {})       # optional global settings
    return r

# ---- Web3 / fee helpers ----
def _guess_fees(w3, mode="cheap"):
    """EIP-1559 prefer, fallback gasPrice. Mode: cheap|normal|fast."""
    mode = (mode or "cheap").lower()
    if mode not in ("cheap", "normal", "fast"):
        mode = "cheap"
    try:
        fh = w3.eth.fee_history(5, "latest", [10, 50, 90])
        base = int(fh.baseFeePerGas[-1])
        rewards = fh.reward[-1] if fh.reward else [w3.to_wei(1, "gwei")]
        median_tip = int(sorted(rewards)[len(rewards)//2]) if rewards else w3.to_wei(1, "gwei")
        if mode == "cheap":
            tip = max(int(median_tip * 0.5), w3.to_wei(0.2, "gwei"))
            max_fee = int(base * 1.15 + tip)
        elif mode == "fast":
            tip = max(int(median_tip * 2), w3.to_wei(2, "gwei"))
            max_fee = int(base * 2 + tip)
        else:
            tip = max(median_tip, w3.to_wei(0.5, "gwei"))
            max_fee = int(base * 1.3 + tip)
        return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip}
    except Exception:
        # legacy
        try: gp = int(w3.eth.gas_price)
        except Exception: gp = int(w3.to_wei(3, "gwei"))
        if mode == "cheap": gp = max(int(gp * 0.8), int(w3.to_wei(0.5, "gwei")))
        elif mode == "fast": gp = int(gp * 1.5)
        return {"gasPrice": gp}

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
    return w3, info

# ---- Unit helpers ----
def wei_to_native(wei: int, decimals: int) -> Decimal:
    return Decimal(wei) / (Decimal(10) ** Decimal(decimals))

def native_to_wei(amount: Decimal, decimals: int) -> int:
    return int(amount * (Decimal(10) ** Decimal(decimals)))

# ---- Amount calculation ----
def compute_send_amount(w3, balance_wei, fee_mode="cheap", gas_limit=21000, reserve_wei=0):
    fees = _guess_fees(w3, mode=fee_mode)
    gp = fees.get("gasPrice") or fees.get("maxFeePerGas") or w3.to_wei(1, "gwei")
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
        **fees
    }
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    return w3.eth.send_raw_transaction(raw).hex()

# ---- Monitor loop ----
def monitor_loop(chains_to_check, fee_mode="cheap", poll_interval=10, dry_run=False):
    """
    chains_to_check: dict { chain_key: [ {address, pk?, label?, reserve_native?} ] }
    """
    print_info(f"Mulai monitor {len(chains_to_check)} chain. Interval: {poll_interval}s. Fee: {fee_mode}. Dry run: {dry_run}")
    try:
        while True:
            for ck, delegates in chains_to_check.items():
                w3, info = connect_chain(ck)
                if not w3: continue
                decimals = int(info.get("decimals", 18))
                symbol   = info.get("native_symbol", "ETH")
                threshold= Decimal(str(info.get("threshold_native", 0.0002)))
                reserve  = Decimal(str(info.get("reserve_native", 0.00002)))
                t_wei    = native_to_wei(threshold, decimals)
                r_wei    = native_to_wei(reserve, decimals)
                for d in delegates:
                    addr  = d.get("address")
                    label = d.get("label") or addr
                    pk    = d.get("pk")  # bisa None (harus input di runtime)
                    try:
                        chksum = w3.to_checksum_address(addr)
                        bal    = int(w3.eth.get_balance(chksum))
                    except Exception as e:
                        log.warning("[%s][%s] gagal get balance: %s", ck, addr, e); continue
                    if bal <= t_wei:
                        log.debug("[%s][%s] balance <= threshold", ck, addr); continue
                    send_amt, gas_cost = compute_send_amount(w3, bal, fee_mode=fee_mode, gas_limit=21000, reserve_wei=r_wei)
                    if send_amt <= 0:
                        log.info("[%s][%s] saldo tidak cukup setelah gas+reserve", ck, addr); continue
                    sink = _ensure_rules().get("default_sink")
                    if not sink:
                        print_warning("Default sink belum diset. Stop monitor."); return
                    nat_bal = wei_to_native(bal, decimals)
                    nat_amt = wei_to_native(send_amt, decimals)
                    nat_gas = wei_to_native(gas_cost, decimals)
                    print_info(f"[{ck}] {mask_middle(addr)} bal {nat_bal} {symbol} → kirim {nat_amt} (gas≈{nat_gas}) ke {mask_middle(sink)}")
                    if dry_run: continue
                    # pastikan pk tersedia
                    if not pk:
                        try:
                            pk = getpass(f"PK untuk {addr} (runtime only): ").strip()
                        except Exception:
                            print_warning("PK tidak diberikan. Skip."); continue
                    try:
                        txh = send_native(w3, pk, sink, send_amt, fee_mode=fee_mode, gas_limit=21000)
                        print_success(f"[{ck}] TX sent: {txh}")
                        log.info("[%s] sent from %s -> %s value=%s", ck, addr, sink, nat_amt)
                    except Exception as e:
                        print_error(f"[{ck}] Gagal kirim dari {mask_middle(addr)}: {e}")
                        log.exception("send error")
            time.sleep(max(3, int(poll_interval)))
    except KeyboardInterrupt:
        print_info("Monitor dihentikan oleh user.")

# ---- Menus ----
def add_delegate_interactive():
    clear_screen(); print_section_header("Tambah Delegate (EOA)")
    chains_conf = _load_chains()
    chains = list(chains_conf.keys())
    if not chains:
        print_error("Tidak ada chain di utils/chain.json"); pause_back(); return
    ck = questionary.select("Pilih chain:", choices=chains).ask() if questionary else (print("Chains:", ", ".join(chains)) or input("Chain: ").strip())
    if not ck: print_warning("Batal."); pause_back(); return
    addr = input("Alamat wallet (EOA) yang dimonitor: ").strip()
    if not addr: print_warning("Kosong."); pause_back(); return
    savepk = False; pk = None
    if questionary:
        savepk = questionary.confirm("Simpan private key ke DISK? (tidak disarankan)").ask()
    else:
        savepk = input("Simpan PK ke disk? (y/N): ").strip().lower() == "y"
    if savepk:
        print_warning("MENYIMPAN PK DI DISK = BERISIKO. Pastikan file aman.")
        if input("Ketik 'I UNDERSTAND' untuk lanjut: ").strip() == "I UNDERSTAND":
            pk = input("Private key (0x... atau tanpa 0x): ").strip()
        else:
            print_warning("Tidak disimpan."); savepk = False
    else:
        # runtime only
        try:
            pk = getpass("Private key (runtime only, tidak disimpan): ").strip()
        except Exception:
            pk = None
    label = input("Label (opsional): ").strip() or None
    r = _ensure_rules()
    r["delegates"].setdefault(ck, [])
    entry = {"address": addr, "label": label, "save_pk": bool(savepk)}
    if savepk and pk: entry["pk"] = pk
    r["delegates"][ck].append(entry)
    save_json(RULES_FILE, r)
    print_success("Delegate ditambahkan.")
    pause_back()

def list_delegates_menu():
    clear_screen(); print_section_header("Daftar Delegates (EOA)")
    r = _ensure_rules(); delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Belum ada delegate."); pause_back(); return
    lines = []
    for ck, items in delegates.items():
        lines.append(f"{Colors.BOLD}{Colors.CYAN}Chain: {ck}{Colors.ENDC}")
        for i, it in enumerate(items, 1):
            pk_state = "(saved)" if it.get("save_pk") and it.get("pk") else "(runtime)" if it.get("save_pk") else "-"
            lines.append(f"  {i}. {it.get('label') or it['address']}")
            lines.append(f"     Addr : {it['address']}")
            lines.append(f"     PK   : {pk_state}")
        lines.append("")
    print_box("📜 DAFTAR DELEGATE", lines, Colors.BLUE)
    pause_back()

def remove_delegate_menu():
    clear_screen(); print_section_header("Hapus Delegate")
    r = _ensure_rules(); delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Tidak ada delegate."); pause_back(); return
    chains = list(delegates.keys())
    ck = questionary.select("Pilih chain:", choices=chains).ask() if questionary else (print("Chains:", ", ".join(chains)) or input("Chain: ").strip())
    items = delegates.get(ck, [])
    if not items:
        print_warning("Tidak ada delegate di chain itu."); pause_back(); return
    labels = [f"{i+1}. {it.get('label') or it['address']}" for i,it in enumerate(items)]
    if questionary:
        pick = questionary.checkbox("Pilih yang dihapus:", choices=labels).ask() or []
        idxs = set(int(x.split(".")[0]) - 1 for x in pick)
    else:
        print("\n".join(labels)); raw = input("Nomor (pisah koma): ").strip(); idxs = set(int(x)-1 for x in raw.split(",") if x.strip().isdigit())
    delegates[ck] = [it for i,it in enumerate(items) if i not in idxs]
    r["delegates"] = delegates; save_json(RULES_FILE, r)
    print_success("Perubahan disimpan."); pause_back()

def set_default_sink_menu():
    clear_screen(); print_section_header("Set Default Sink")
    r = _ensure_rules(); cur = r.get("default_sink", "")
    sink = input(f"Sink address [{cur}]: ").strip() or cur
    r["default_sink"] = sink; save_json(RULES_FILE, r)
    print_success("Default sink tersimpan."); pause_back()

def start_monitor_menu():
    clear_screen(); print_section_header("Start Monitor")
    r = _ensure_rules()
    delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Tidak ada delegate. Tambah dulu."); pause_back(); return
    # Susun map chain -> list entries (ambil pk jika tersimpan)
    chains_map = {}
    for ck, items in delegates.items():
        chains_map[ck] = []
        for it in items:
            entry = {"address": it.get("address"), "label": it.get("label")}
            if it.get("save_pk") and it.get("pk"): entry["pk"] = it.get("pk")
            chains_map[ck].append(entry)
    # opsi
    fee_mode = "cheap"; interval = 12; dry_run = False
    if questionary:
        fee_mode = questionary.select("Mode fee:", choices=["cheap","normal","fast"]).ask() or "cheap"
        interval = int(questionary.text("Polling interval (detik):", default="12").ask())
        dry_run  = questionary.confirm("Dry run? (hanya simulasi)").ask()
    else:
        m = input("Mode fee [cheap/normal/fast] (default cheap): ").strip().lower()
        if m in ("cheap","normal","fast"): fee_mode = m
        iv = input("Polling interval detik (default 12): ").strip()
        if iv.isdigit(): interval = max(3, int(iv))
        dry_run = input("Dry run? (y/N): ").strip().lower() == "y"
    print_info("Mulai monitor. Tekan Ctrl-C untuk stop.")
    monitor_loop(chains_map, fee_mode=fee_mode, poll_interval=interval, dry_run=dry_run)
    pause_back()

def manual_sweep_menu():
    clear_screen(); print_section_header("Manual Sweep (sekali kirim)")
    r = _ensure_rules(); delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Tidak ada delegate."); pause_back(); return
    chains = list(delegates.keys())
    ck = questionary.select("Pilih chain:", choices=chains).ask() if questionary else (print("Chains:", ", ".join(chains)) or input("Chain: ").strip())
    items = delegates.get(ck, [])
    if not items: print_warning("Kosong."); pause_back(); return
    labels = [f"{i+1}. {it.get('label') or it['address']}" for i,it in enumerate(items)]
    if questionary:
        sel = questionary.select("Pilih EOA:", choices=labels).ask(); idx = labels.index(sel)
    else:
        print("\n".join(labels)); idx = int(input("Nomor: ").strip()) - 1
    it = items[idx]; addr = it.get("address"); saved_pk = it.get("pk") if it.get("save_pk") else None
    w3, info = connect_chain(ck); 
    if not w3: pause_back(); return
    decimals = int(info.get("decimals", 18)); symbol = info.get("native_symbol","ETH")
    bal = int(w3.eth.get_balance(w3.to_checksum_address(addr)))
    print_info(f"Balance: {wei_to_native(bal, decimals)} {symbol}")
    reserve = Decimal(str(info.get("reserve_native", 0.00002)))
    send_amt, gas_cost = compute_send_amount(w3, bal, fee_mode="normal", gas_limit=21000, reserve_wei=native_to_wei(reserve, decimals))
    if send_amt <= 0:
        print_warning("Tidak cukup untuk gas + cadangan."); pause_back(); return
    sink = _ensure_rules().get("default_sink")
    if not sink:
        print_warning("Default sink belum di-set."); pause_back(); return
    print_info(f"Akan kirim {wei_to_native(send_amt, decimals)} {symbol} ke {mask_middle(sink)} (gas≈{wei_to_native(gas_cost, decimals)} {symbol})")
    if input("Ketik 'yes' untuk konfirmasi: ").strip().lower() != "yes":
        print_warning("Dibatalkan."); pause_back(); return
    pk = saved_pk or getpass("Masukkan private key: ").strip()
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
        print_section_header("AUTO TRANSFER (Monitor Bot) - MAIN MENU")
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
        ch = (questionary.select("Pilih:", choices=[QChoice(m, str(i+1)) for i,m in enumerate(menu)]).ask()
              if questionary else input("Pilih (1-7): ").strip())
        if ch in ("1", "1) Tambah Delegate (EOA)"): add_delegate_interactive()
        elif ch in ("2",):  list_delegates_menu()
        elif ch in ("3",):  remove_delegate_menu()
        elif ch in ("4",):  set_default_sink_menu()
        elif ch in ("5",):  start_monitor_menu()
        elif ch in ("6",):  manual_sweep_menu()
        elif ch in ("7", "Exit", None): print_info("Bye."); break
        else:
            print_error("Pilihan tidak valid."); time.sleep(1)

# ---- Entrypoint (biar bisa diimport & dipanggil dari main.py) ----
def run():
    try:
        menu_main()
    except Exception as e:
        print_error(f"Runtime error: {e}")
        log.exception("runtime error")

if __name__ == "__main__":
    run()
