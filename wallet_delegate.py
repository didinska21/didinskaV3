#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_delegate.py - Off-chain Auto Transfer (Monitor)

Menu:
1) Tambahkan wallet (EOA) ke chain (ambil dari utils/chain.json; yang error di-skip)
2) Lihat daftar
3) Hapus
4) Setel wallet penampung (sink)
5) Start monitor (auto transfer: ERC20 -> native)  [PAKAI PK YANG SUDAH DISIMPAN]
6) Setel ERC20 per chain (token yang akan ditransfer)
7) Exit

Perubahan penting:
- Private key TIDAK disembunyikan saat input & DISIMPAN plaintext ke delegate_rules.json
- Start monitor langsung memakai PK tersimpan; tidak prompt ulang

Fitur:
- Monitor multi-chain via RPC (utils/chain.json)
- Prioritas transfer: ERC20 dulu, baru native (agar gas tetap ada)
- Fee mode: cheap/normal/fast (default cheap); EIP-1559 aware, fallback legacy
- Estimasi gas + buffer; skip error per chain tanpa menghentikan loop
- Logging ke delegate.log
"""

import os, sys, json, time, logging
from datetime import datetime
from decimal import Decimal
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3, HTTPProvider
from web3.exceptions import ContractLogicError

# Optional nicer UI
try:
    import questionary
    from questionary import Choice as QChoice
except Exception:
    questionary = None
    QChoice = None

# POA middleware compat
try:
    from web3.middleware import ExtraDataToPOAMiddleware as POA_MIDDLEWARE
except Exception:
    try:
        from web3.middleware import geth_poa_middleware as POA_MIDDLEWARE
    except Exception:
        POA_MIDDLEWARE = None

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

CHAIN_FILE = os.path.join(BASE_DIR, "utils", "chain.json")
RULES_FILE = os.path.join(BASE_DIR, "delegate_rules.json")
LOG_FILE   = os.path.join(BASE_DIR, "delegate.log")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"),
              logging.StreamHandler()]
)
log = logging.getLogger("auto-transfer")

# UI fallbacks
try:
    from utils.colors import Colors
    from utils.ui import print_box, print_loader, print_section_header, print_warning, print_error, print_success, print_info
except Exception:
    class Colors:
        YELLOW = "\033[93m"; GREEN = "\033[92m"; RED = "\033[91m"
        CYAN   = "\033[96m"; MAGENTA="\033[95m"; BLUE = "\033[94m"
        BOLD   = "\033[1m";  ENDC = "\033[0m"
    def print_box(title, lines, color=None):
        print(f"\n=== {title} ==="); [print(ln) for ln in lines]; print("="*20)
    def print_loader(msg, _=1): print(msg+" ...")
    def print_section_header(t): print(f"\n{Colors.BOLD}{t}{Colors.ENDC}\n")
    def print_warning(msg): print(f"{Colors.YELLOW}[!] {msg}{Colors.ENDC}")
    def print_error(msg):   print(f"{Colors.RED}[ERROR] {msg}{Colors.ENDC}")
    def print_success(msg): print(f"{Colors.GREEN}[OK] {msg}{Colors.ENDC}")
    def print_info(msg):    print(f"{Colors.CYAN}[i] {msg}{Colors.ENDC}")

# Terminal helpers
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

# JSON helpers
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

# Rules schema:
# {
#   "default_sink": "0x...",
#   "delegates": { "<chain>": [ { "address": "...", "label": "...", "pk": "0x..." } ] },
#   "erc20": { "<chain>": ["0xToken1", "0xToken2", ...] },
#   "settings": { "fee_mode":"cheap", "poll":12, "reserve_native":{ "<chain>": 0.00002 }, "threshold_native":{ "<chain>": 0.0002 } }
# }
def _ensure_rules():
    r = load_json(RULES_FILE, {})
    r.setdefault("delegates", {})
    r.setdefault("erc20", {})
    r.setdefault("settings", {})
    return r

# Chain config helper (support dict or list)
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
    return {}

# Web3 connect
def connect_chain(chain_key):
    chains = _load_chains()
    info = chains.get(chain_key)
    if not info:
        print_warning(f"Chain '{chain_key}' tidak ada di chain.json (skip)")
        return None, None, None
    rpc = info.get("rpc_url") or info.get("rpc")
    if not rpc:
        print_warning(f"RPC kosong untuk {chain_key} (skip)")
        return None, None, None
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
        print_warning(f"Gagal konek RPC {chain_key} (skip)")
        return None, None, None
    chain_id = info.get("chain_id") or info.get("chainId")
    if not chain_id:
        try:
            chain_id = w3.eth.chain_id
        except Exception:
            chain_id = None
    return w3, chain_id, info

# Fees
def _guess_fees(w3, mode="cheap"):
    try:
        fh = w3.eth.fee_history(5, "latest", [10, 50, 90])
        base = int(fh.baseFeePerGas[-1])
        rewards = fh.reward[-1] if fh.reward else [w3.to_wei(1, "gwei")]
        median_tip = int(sorted(rewards)[len(rewards)//2]) if rewards else w3.to_wei(1, "gwei")
        if mode == "fast":
            tip = max(int(median_tip*2), w3.to_wei(2, "gwei")); max_fee = int(base*2 + tip)
        elif mode == "normal":
            tip = max(median_tip, w3.to_wei(0.5, "gwei"));      max_fee = int(base*1.3 + tip)
        else:
            tip = max(int(median_tip*0.5), w3.to_wei(0.2, "gwei")); max_fee = int(base*1.15 + tip)
        return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip}
    except Exception:
        try:
            gp = int(w3.eth.gas_price)
        except Exception:
            gp = int(w3.to_wei(3, "gwei"))
        if mode == "fast": gp = int(gp*1.5)
        elif mode == "cheap": gp = max(int(gp*0.8), int(w3.to_wei(0.5, "gwei")))
        return {"gasPrice": gp}

def _send_raw_tx(w3, signed):
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raw = signed if isinstance(signed, (bytes, bytearray)) else None
    if raw is None:
        raise ValueError("SignedTransaction raw tx missing")
    return w3.eth.send_raw_transaction(raw)

# Units
def wei_to_eth(w3, v): 
    try: return w3.from_wei(int(v), "ether")
    except: return Decimal(v) / Decimal(10**18)

def eth_to_wei(w3, v):
    try: return w3.to_wei(Decimal(str(v)), "ether")
    except: return int(Decimal(str(v)) * (10**18))

# ERC20 minimal ABI
ERC20_ABI = [
    {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
    {"constant":False,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
]
def token_contract(w3, addr):
    return w3.eth.contract(address=w3.to_checksum_address(addr), abi=ERC20_ABI)

# Compute native sendable after gas + reserve
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
    fees = _guess_fees(w3, mode=fee_mode)
    tx = {
        "from": acct.address,
        "to": w3.to_checksum_address(to_addr),
        "value": int(value_wei),
        "nonce": w3.eth.get_transaction_count(acct.address),
        "gas": gas_limit,
    }
    tx.update(fees)
    signed = acct.sign_transaction(tx)
    txh = _send_raw_tx(w3, signed)
    return txh.hex()

def send_erc20(w3, pk, token_addr, to_addr, amount_wei, fee_mode="cheap", gas_buffer=1.15):
    acct = Account.from_key(pk)
    c = token_contract(w3, token_addr)
    fn = c.functions.transfer(w3.to_checksum_address(to_addr), int(amount_wei))
    fees = _guess_fees(w3, mode=fee_mode)
    try:
        est = fn.estimate_gas({"from": acct.address})
        gas_limit = int(est * gas_buffer)
    except Exception:
        gas_limit = int(100000 * gas_buffer)
    tx = fn.build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "gas": gas_limit,
        **fees,
    })
    signed = acct.sign_transaction(tx)
    txh = _send_raw_tx(w3, signed)
    return txh.hex(), gas_limit

# -------------------- MENU OPS --------------------
def add_delegate_interactive():
    clear_screen()
    print_section_header("Tambah Wallet (EOA) ke Chain")
    chains_conf = _load_chains()
    all_keys = list(chains_conf.keys())
    if not all_keys:
        print_error("utils/chain.json kosong")
        pause_back(); return

    # pilih chain (ALL / multi)
    if questionary:
        choices = [{"name":"ALL CHAINS","value":"__ALL__"}] + [{"name":k,"value":k} for k in all_keys]
        picked = questionary.checkbox("Pilih chain:", choices=choices).ask() or []
        sel_chains = all_keys if (not picked or "__ALL__" in picked) else picked
    else:
        print("Chains:", ", ".join(all_keys))
        ans = input("Ketik 'all' untuk semua atau tulis pilihan dipisah koma: ").strip().lower()
        sel_chains = all_keys if ans in ("all","*","") else [x.strip() for x in ans.split(",") if x.strip() in all_keys]

    addr = input("Alamat EOA: ").strip()
    if not addr:
        print_warning("Alamat kosong."); pause_back(); return

    # PK TIDAK DIHIDDEN & DISIMPAN PLAINTEXT
    print_warning("PERINGATAN: PK akan DISIMPAN plaintext di delegate_rules.json (sesuai permintaan kamu).")
    pk = input("Private key (0x... atau hex 64 chars): ").strip()
    if pk.startswith("0x"): pk_hex = pk
    else: pk_hex = "0x"+pk

    label = input("Label (opsional): ").strip() or None

    r = _ensure_rules()
    added = 0; skipped = 0
    for ck in sel_chains:
        # validasi cepat RPC; kalau gagal -> skip
        w3, _, _ = connect_chain(ck)
        if not w3:
            skipped += 1
            continue
        r["delegates"].setdefault(ck, [])
        entry = {"address": addr, "label": label, "pk": pk_hex}
        r["delegates"][ck].append(entry)
        added += 1
    save_json(RULES_FILE, r)
    print_success(f"Ditambahkan ke {added} chain. Skip: {skipped}.")
    pause_back()

def list_delegates_menu():
    clear_screen()
    print_section_header("Daftar Delegates")
    r = _ensure_rules()
    delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Belum ada data."); pause_back(); return
    lines=[]
    for ck, items in delegates.items():
        lines.append(f"{Colors.BOLD}{Colors.CYAN}Chain: {ck}{Colors.ENDC}")
        for i, it in enumerate(items, 1):
            addr = it.get("address"); label = it.get("label") or "-"
            pk_full = it.get("pk", "(tidak ada)")
            lines.append(f"  {i}. {label}")
            lines.append(f"     Addr : {addr}")
            lines.append(f"     PK   : {pk_full}")  # diminta tidak disembunyikan
        lines.append("")
    print_box("📜 DAFTAR DELEGATE", lines, Colors.BLUE)
    pause_back()

def remove_delegate_menu():
    clear_screen()
    print_section_header("Hapus Delegate")

    r = _ensure_rules()
    delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Tidak ada data delegate.")
        pause_back(); return

    # Pilih mode hapus: per-chain atau ALL CHAINS
    mode = "single"
    if questionary:
        mode = questionary.select(
            "Mode hapus:",
            choices=[
                QChoice("Hapus di satu chain", "single"),
                QChoice("Hapus di SEMUA chain (All Chains)", "all"),
            ],
        ).ask() or "single"
    else:
        m = input("Mode [single/all] (default single): ").strip().lower()
        if m in ("single", "all"):
            mode = m

    # =============== MODE: SINGLE CHAIN (seperti sebelumnya) ===============
    if mode == "single":
        chains = list(delegates.keys())
        if questionary:
            ck = questionary.select("Pilih chain:", choices=chains).ask()
        else:
            print("Chains:", ", ".join(chains))
            ck = input("Chain: ").strip()

        items = delegates.get(ck, [])
        if not items:
            print_warning("Tidak ada delegate di chain tersebut.")
            pause_back(); return

        labels = [f"{i+1}. {(it.get('label') or it.get('address'))}" for i, it in enumerate(items)]
        if questionary:
            picked = questionary.checkbox("Pilih yang ingin dihapus:", choices=labels).ask() or []
            to_del = set(int(x.split(".")[0]) - 1 for x in picked)
        else:
            print("\n".join(labels))
            raw = input("Nomor (pisah koma): ").strip()
            to_del = set(int(x) - 1 for x in raw.split(",") if x.strip().isdigit())

        before = len(items)
        delegates[ck] = [it for i, it in enumerate(items) if i not in to_del]
        after = len(delegates[ck])
        r["delegates"] = delegates
        save_json(RULES_FILE, r)

        print_success(f"Dihapus {before - after} entri dari chain {ck}.")
        pause_back()
        return

    # =============== MODE: ALL CHAINS ===============
    # Gabungkan by address agar bisa hapus cepat di semua chain
    # mapping: addr_lower -> { "label": last_label, "chains": {ck: count} }
    combined = {}
    for ck, items in delegates.items():
        for it in items:
            addr = (it.get("address") or "").strip()
            if not addr:
                continue
            key = addr.lower()
            entry = combined.setdefault(key, {"address": addr, "label": it.get("label") or "-", "chains": {}})
            entry["chains"][ck] = entry["chains"].get(ck, 0) + 1

    if not combined:
        print_warning("Tidak ada data yang bisa dihapus.")
        pause_back(); return

    # Buat daftar pilihan
    display_items = []
    for key, info in combined.items():
        chains_str = ", ".join([f"{ck}×{cnt}" for ck, cnt in info["chains"].items()])
        display_items.append(f"{info['address']}  |  {info['label']}  |  [{chains_str}]")

    # Urutkan biar rapi
    display_items.sort()

    # Tambah opsi DELETE ALL (bahaya)
    DANGER_ALL = "🔥 HAPUS SEMUA DELEGATE DI SEMUA CHAIN (DANGEROUS)"
    if questionary:
        choices = [DANGER_ALL] + display_items
        picked = questionary.checkbox("Pilih address yang ingin dihapus di SEMUA chain:", choices=choices).ask() or []
    else:
        print_box("PILIH ADDRESS", display_items, Colors.BLUE)
        print_warning("Ketik 'ALL' untuk hapus SEMUA address di SEMUA chain!")
        raw = input("Ketik 'ALL' atau tempelkan address (pisah baris/comma): ").strip()
        if raw.upper() == "ALL":
            picked = [DANGER_ALL]
        else:
            picked = [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]

    # Konfirmasi
    if not picked:
        print_warning("Tidak ada yang dipilih.")
        pause_back(); return

    # Siapkan set address yang akan dihapus
    to_delete_addrs = set()
    if DANGER_ALL in picked:
        # Konfirmasi ekstra
        confirm = input("Ketik 'YES, DELETE ALL' untuk konfirmasi: ").strip()
        if confirm != "YES, DELETE ALL":
            print_warning("Batal hapus semua.")
            pause_back(); return
        # ambil semua address
        to_delete_addrs = {info["address"].lower() for info in combined.values()}
    else:
        # parse dari display line -> ambil address di awal
        for disp in picked:
            addr = disp.split("|", 1)[0].strip()
            if addr:
                to_delete_addrs.add(addr.lower())

        # Konfirmasi biasa
        confirm = input(f"Konfirmasi hapus {len(to_delete_addrs)} address di SEMUA chain? (ketik YES): ").strip()
        if confirm != "YES":
            print_warning("Dibatalkan.")
            pause_back(); return

    # Lakukan penghapusan di semua chain
    total_before = sum(len(v) for v in delegates.values())
    for ck, items in list(delegates.items()):
        delegates[ck] = [it for it in items if (it.get("address") or "").lower() not in to_delete_addrs]
        # bersihkan chain kosong (optional)
        if not delegates[ck]:
            # boleh dibiarkan juga; kalau mau bersihkan:
            # del delegates[ck]
            pass

    r["delegates"] = delegates
    save_json(RULES_FILE, r)
    total_after = sum(len(v) for v in delegates.values())
    print_success(f"Selesai. Terhapus {total_before - total_after} entri di semua chain.")
    pause_back()

def set_default_sink_menu():
    clear_screen()
    print_section_header("Set Wallet Penampung (Sink)")
    r = _ensure_rules()
    cur = r.get("default_sink","")
    sink = input(f"Sink address [{cur}]: ").strip() or cur
    r["default_sink"] = sink
    save_json(RULES_FILE, r)
    print_success("Disimpan.")
    pause_back()

def set_erc20_menu():
    clear_screen()
    print_section_header("Setel ERC20 Per Chain")
    r = _ensure_rules()
    chains = list(_load_chains().keys())
    if not chains:
        print_warning("Chain.json kosong."); pause_back(); return
    if questionary:
        ck = questionary.select("Pilih chain:", choices=chains).ask()
    else:
        print("Chains:", ", ".join(chains)); ck = input("Chain: ").strip()
    cur = (r.get("erc20", {}) or {}).get(ck, [])
    print_info(f"Daftar token saat ini: {', '.join(cur) if cur else '(kosong)'}")
    raw = input("Masukkan alamat token ERC20 (pisah koma), kosongkan untuk hapus semua: ").strip()
    newlist = [t.strip() for t in raw.split(",") if t.strip()] if raw else []
    r["erc20"].setdefault(ck, [])
    r["erc20"][ck] = newlist
    save_json(RULES_FILE, r)
    print_success("ERC20 diperbarui.")
    pause_back()

# -------------------- MONITOR --------------------
def _get_chain_thresholds(ck, w3):
    chains = _load_chains()
    info = chains.get(ck, {})
    settings = _ensure_rules().get("settings", {})
    threshold_native = Decimal(str(settings.get("threshold_native", {}).get(ck, info.get("threshold_native", 0.0002))))
    reserve_native   = Decimal(str(settings.get("reserve_native", {}).get(ck, info.get("reserve_native", 0.00002))))
    return eth_to_wei(w3, threshold_native), eth_to_wei(w3, reserve_native)

def monitor_loop(chains_map, fee_mode="cheap", poll_interval=12, dry_run=False):
    """
    chains_map: { chain_key: [ {address,label,pk}, ... ] }
    Proses: untuk tiap EOA -> kirim semua ERC20 (jika ada & >0) -> lalu kirim native sisa (balance - gas - reserve)
    """
    print_info(f"Mulai monitor {len(chains_map)} chain | fee_mode={fee_mode} | interval={poll_interval}s | dry_run={dry_run}")
    try:
        while True:
            for ck, items in chains_map.items():
                w3, _, _ = connect_chain(ck)
                if not w3:
                    continue
                # sink
                sink = _ensure_rules().get("default_sink")
                if not sink:
                    print_warning("Default sink belum di-set. Lewati monitor."); return

                tokens = (_ensure_rules().get("erc20", {}) or {}).get(ck, [])
                threshold_wei, reserve_wei = _get_chain_thresholds(ck, w3)

                for entry in items:
                    addr = entry.get("address"); label = entry.get("label") or addr
                    pk = entry.get("pk")
                    if not pk:
                        log.warning("[%s][%s] PK tidak ditemukan, skip.", ck, addr)
                        continue
                    try:
                        sender = w3.to_checksum_address(addr)
                        bal = w3.eth.get_balance(sender)
                    except Exception as e:
                        log.warning("[%s][%s] gagal get_balance: %s", ck, addr, e)
                        continue

                    # --- Prioritas ERC20 ---
                    if tokens:
                        for taddr in tokens:
                            try:
                                c = token_contract(w3, taddr)
                                tbal = c.functions.balanceOf(sender).call()
                                if int(tbal) <= 0:
                                    continue
                                if dry_run:
                                    print_info(f"[{ck}] {addr} ERC20 {taddr} -> {sink} amount={tbal} (dry-run)")
                                else:
                                    txh, gas_limit = send_erc20(w3, pk, taddr, sink, tbal, fee_mode=fee_mode)
                                    print_success(f"[{ck}] ERC20 sent {taddr} tx={txh}")
                                    log.info("[%s] erc20 %s from %s -> %s", ck, taddr, addr, txh)
                            except ContractLogicError as ce:
                                log.warning("[%s][%s] ERC20 fail: %s", ck, addr, ce)
                            except Exception as e:
                                log.warning("[%s][%s] ERC20 error token %s: %s", ck, addr, taddr, e)

                    # --- Lalu native (sisa) ---
                    try:
                        bal = w3.eth.get_balance(sender)  # refresh setelah ERC20 tx
                    except Exception:
                        continue
                    if bal <= threshold_wei:
                        continue
                    send_amt, gas_cost = compute_send_amount(w3, bal, fee_mode=fee_mode, gas_limit=21000, reserve_wei=reserve_wei)
                    if send_amt <= 0:
                        continue
                    if dry_run:
                        print_info(f"[{ck}] {addr} native -> {sink} amount={wei_to_eth(w3, send_amt)} (dry-run)")
                    else:
                        try:
                            txh = send_native(w3, pk, sink, send_amt, fee_mode=fee_mode, gas_limit=21000)
                            print_success(f"[{ck}] Native sent tx={txh}")
                            log.info("[%s] native from %s -> %s", ck, addr, txh)
                        except Exception as e:
                            print_error(f"[{ck}] Gagal send native dari {addr}: {e}")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print_info("Monitor berhenti.")

def start_monitor_menu():
    clear_screen()
    print_section_header("Start Monitor (Auto Transfer)")
    r = _ensure_rules()
    delegates = r.get("delegates", {})
    if not delegates:
        print_warning("Tidak ada delegate. Tambahkan dulu.")
        pause_back(); return

    all_chains = list(delegates.keys())
    # pilih chain (ALL / subset)
    if questionary:
        choices = [{"name":"ALL CHAINS","value":"__ALL__"}] + [{"name":ck,"value":ck} for ck in all_chains]
        picked = questionary.checkbox("Monitor chain apa saja?", choices=choices).ask() or []
        chosen = all_chains if (not picked or "__ALL__" in picked) else picked
    else:
        print("Chains:", ", ".join(all_chains))
        ans = input("Ketik 'all' untuk semua atau tulis dipisah koma: ").strip().lower()
        chosen = all_chains if ans in ("all","*","") else [x.strip() for x in ans.split(",") if x.strip() in all_chains]

    # map langsung pakai PK tersimpan (tidak prompt ulang)
    chains_map = {}
    for ck in chosen:
        items = delegates.get(ck, [])
        if not items:
            continue
        chains_map[ck] = []
        for it in items:
            entry = {"address": it.get("address"), "label": it.get("label"), "pk": it.get("pk")}
            chains_map[ck].append(entry)
    if not chains_map:
        print_warning("Tidak ada delegate di chain yang dipilih."); pause_back(); return

    # opsi runtime
    settings = r.get("settings", {})
    def_fee = settings.get("fee_mode","cheap")
    def_poll = int(settings.get("poll", 12))
    if questionary:
        fee_mode = questionary.select("Mode fee:", choices=["cheap","normal","fast"]).ask() or def_fee
        poll = int(questionary.text("Polling interval (detik):", default=str(def_poll)).ask())
        dry_run = questionary.confirm("Dry run? (hanya simulasi)").ask()
    else:
        fm = input(f"Mode fee [cheap/normal/fast] (default {def_fee}): ").strip().lower()
        fee_mode = fm if fm in ("cheap","normal","fast") else def_fee
        p = input(f"Polling interval detik (default {def_poll}): ").strip()
        poll = max(3, int(p)) if p.isdigit() else def_poll
        dry_run = input("Dry run? (y/N): ").strip().lower()=="y"

    print_info("Mulai monitor. Ctrl-C untuk stop.")
    monitor_loop(chains_map, fee_mode=fee_mode, poll_interval=poll, dry_run=dry_run)
    pause_back()

# -------------------- MAIN MENU --------------------
def menu_main():
    while True:
        clear_screen()
        print_section_header("AUTO TRANSFER (Off-chain) - MAIN MENU")
        menu = [
            "1) Tambahkan wallet (EOA) ke chain (ambil dari chain.json)",
            "2) Lihat daftar",
            "3) Hapus",
            "4) Setel wallet penampung (sink)",
            "5) Start monitor (auto transfer)",
            "6) Setel ERC20 per chain",
            "7) Exit",
        ]
        print_box("MENU", menu, Colors.MAGENTA)
        if questionary:
            ch = questionary.select("Pilih:", choices=[QChoice(t, str(i+1)) for i,t in enumerate(menu)]).ask()
        else:
            ch = input("Pilih (1-7): ").strip()

        if ch in ("1", "1) Tambahkan wallet (EOA) ke chain (ambil dari chain.json)"):
            add_delegate_interactive()
        elif ch in ("2",):
            list_delegates_menu()
        elif ch in ("3",):
            remove_delegate_menu()
        elif ch in ("4",):
            set_default_sink_menu()
        elif ch in ("5",):
            start_monitor_menu()
        elif ch in ("6",):
            set_erc20_menu()
        elif ch in ("7",):
            print_info("Bye.")
            break
        else:
            print_error("Pilihan tidak valid.")
            time.sleep(1)

# ---- Entry aliases ----
def run():
    """Backward-compat: keep old launcher signature."""
    menu_main()

if __name__ == "__main__":
    menu_main()
