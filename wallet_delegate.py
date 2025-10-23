#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_delegate.py - Auto-Forward Native Coin (A -> B)
Fitur:
- Set wallet penampung (B)
- Tambah / daftar / hapus wallet delegate (A -> B)
- Monitor otomatis (polling) & sweep sekali jalan (one-shot)
- Notifikasi Telegram (opsional)
Semua teks & menu dalam Bahasa Indonesia.
"""

import os
import sys
import json
import time
import uuid
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv

# Tambahkan utils ke sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))

from colors import Colors
from ui import (
    print_box, print_loader, print_progress_bar, print_stats_box,
    print_section_header, print_success, print_warning, print_error, ask_confirmation
)
from checker import build_web3_clients, check_native_balance
from telegram import (
    is_telegram_enabled, notify_error, send_message
)

try:
    from web3 import Web3, HTTPProvider
    WEB3_AVAILABLE = True
except Exception:
    WEB3_AVAILABLE = False
    Web3 = None
    HTTPProvider = None

load_dotenv()

# ---- File Konfigurasi / Data ----
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")
CHAIN_LIST_FILE = os.path.join("utils", "chain.json")       # daftar chain yang dipantau
DELEGATIONS_FILE = "delegations.json"                        # aturan delegasi (A -> B)
DELEGATE_SETTINGS_FILE = "delegate_settings.json"            # pengaturan global modul delegate
DEFAULT_INTERVAL_SEC = 12

# ---- Notifikasi Telegram ----
TELEGRAM_ON = is_telegram_enabled()

# ---- Util I/O JSON ----
def _load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print_error(f"Gagal menyimpan {path}: {e}")
        return False

# ---- Chain Data ----
def load_chain_list():
    """
    Memuat daftar chain yang akan dipantau dari utils/chain.json
    Format setiap item:
    {
      "key": "ethereum",
      "name": "Ethereum Mainnet",
      "native_symbol": "ETH",
      "decimals": 18,
      "threshold_native": 0.0005,
      "reserve_native": 0.00002
    }
    """
    data = _load_json(CHAIN_LIST_FILE, default=[])
    # validasi dasar
    valid = []
    for it in data:
        if isinstance(it, dict) and it.get("key") and it.get("native_symbol"):
            valid.append(it)
    return valid

def chain_defaults_map():
    """
    Menghasilkan map: chain_key -> {native_symbol, decimals, threshold_native, reserve_native, name}
    """
    out = {}
    for it in load_chain_list():
        out[it["key"]] = {
            "name": it.get("name", it["key"]),
            "native_symbol": it.get("native_symbol", "ETH"),
            "decimals": int(it.get("decimals", 18)),
            "threshold_native": float(it.get("threshold_native", 0.0)),
            "reserve_native": float(it.get("reserve_native", 0.0)),
        }
    return out

# ---- Data Aturan (delegations.json) ----
def load_delegations():
    """
    Struktur:
    {
      "sink_address": "0xB....",          # penampung global
      "rules": [
        {
          "id": "uuid",
          "owner_pk": "0x....",           # private key hex dari Wallet A
          "owner_address": "0xA....",
          "chains": ["ethereum","bsc"],
          "enabled": true,
          "created_at": "2025-10-23T..."
        }
      ]
    }
    """
    return _load_json(DELEGATIONS_FILE, default={"sink_address": "", "rules": []})

def save_delegations(data):
    return _save_json(DELEGATIONS_FILE, data)

# ---- Settings Global Delegate (delegate_settings.json) ----
def load_delegate_settings():
    """
    Settings global (bisa dikembangkan sewaktu-waktu):
    {
      "interval_sec": 12,
      "gas_caps": { "maxFeePerGasGwei": null, "maxPriorityFeePerGasGwei": null }
    }
    """
    return _load_json(DELEGATE_SETTINGS_FILE, default={
        "interval_sec": DEFAULT_INTERVAL_SEC,
        "gas_caps": {
            "maxFeePerGasGwei": None,
            "maxPriorityFeePerGasGwei": None
        }
    })

def save_delegate_settings(data):
    return _save_json(DELEGATE_SETTINGS_FILE, data)

# ---- Helper Validasi ----
def checksum_or_none(addr):
    try:
        return Web3.to_checksum_address(addr) if WEB3_AVAILABLE else addr
    except Exception:
        return None

def pk_to_address(pk_hex):
    try:
        acct = Web3().eth.account.from_key(pk_hex)
        return acct.address
    except Exception:
        return None

# ---- Gas & Sweep ----
def _wei_to_native(wei, decimals=18):
    return float(Decimal(wei) / Decimal(10 ** decimals))

def _native_to_wei(amount, decimals=18):
    return int(Decimal(str(amount)) * Decimal(10 ** decimals))

def _get_gas_params(w3, gas_caps=None):
    """
    Mengambil parameter gas.
    - Coba EIP-1559 (baseFee+priority). Jika tidak tersedia, fallback ke gas_price legacy.
    - gas_caps opsional (batasi max fee/priority).
    Return dict {type: "eip1559"|"legacy", gasPrice or maxFeePerGas/maxPriorityFeePerGas}
    """
    try:
        latest = w3.eth.get_block("latest")
        base_fee = latest.get("baseFeePerGas")
    except Exception:
        base_fee = None

    if base_fee is not None:
        # EIP-1559
        # priority default konservatif
        priority = w3.to_wei(1, "gwei")
        if gas_caps:
            mp = gas_caps.get("maxPriorityFeePerGasGwei")
            if mp is not None:
                priority = w3.to_wei(mp, "gwei")
        # max fee
        max_fee = base_fee + priority
        if gas_caps:
            mf = gas_caps.get("maxFeePerGasGwei")
            if mf is not None:
                max_fee = min(max_fee, w3.to_wei(mf, "gwei"))
        return {"type": "eip1559", "maxFeePerGas": int(max_fee), "maxPriorityFeePerGas": int(priority)}
    else:
        # Legacy
        gas_price = w3.eth.gas_price
        if gas_caps and gas_caps.get("maxFeePerGasGwei") is not None:
            cap = w3.to_wei(gas_caps["maxFeePerGasGwei"], "gwei")
            gas_price = min(gas_price, cap)
        return {"type": "legacy", "gasPrice": int(gas_price)}

def _estimate_gas_limit(w3, from_addr, to_addr, value_wei):
    """
    Estimasi gas limit untuk transfer native.
    Fallback ke 21000 jika estimate gagal.
    """
    try:
        return w3.eth.estimate_gas({
            "from": from_addr,
            "to": to_addr,
            "value": int(value_wei)
        })
    except Exception:
        return 21000

def _build_and_send_tx(w3, pk_hex, to_addr, value_wei, gas_params):
    acct = w3.eth.account.from_key(pk_hex)
    from_addr = acct.address
    nonce = w3.eth.get_transaction_count(from_addr)

    tx = {
        "from": from_addr,
        "to": to_addr,
        "value": int(value_wei),
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
    }

    # gas limit (estimate menggunakan nilai kira-kira, disesuaikan di bawah)
    gas_limit = _estimate_gas_limit(w3, from_addr, to_addr, value_wei)
    tx["gas"] = int(gas_limit)

    # set gas params
    if gas_params.get("type") == "eip1559":
        tx["maxFeePerGas"] = gas_params["maxFeePerGas"]
        tx["maxPriorityFeePerGas"] = gas_params["maxPriorityFeePerGas"]
    else:
        tx["gasPrice"] = gas_params["gasPrice"]

    # tanda tangan & kirim
    signed = w3.eth.account.sign_transaction(tx, private_key=pk_hex)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    return tx_hash.hex()

def _sweep_native_on_chain(w3, chain_key, pk_hex, to_addr, defaults, gas_caps=None):
    """
    Logika utamanya:
    - Cek saldo native A
    - Ambil gas params + gas_limit
    - Hitung biaya total gas
    - Kirim amount = balance - gas_cost - reserve (kalau masih >= threshold)
    """
    info = defaults.get(chain_key) or {}
    native_symbol = info.get("native_symbol", "ETH")
    decimals = int(info.get("decimals", 18))
    threshold = float(info.get("threshold_native", 0.0))
    reserve = float(info.get("reserve_native", 0.0))

    acct = Web3().eth.account.from_key(pk_hex)
    owner = acct.address
    w3 = w3  # alias

    # saldo dalam native
    bal_wei = w3.eth.get_balance(owner)
    bal_native = _wei_to_native(bal_wei, decimals=decimals)

    if bal_native <= 0:
        return None, f"Saldo 0 {native_symbol}"

    gas_params = _get_gas_params(w3, gas_caps=gas_caps)

    # Tentukan gas_limit kira-kira untuk hitung biaya kasarnya
    gas_limit = _estimate_gas_limit(w3, owner, to_addr, 1)
    if gas_params["type"] == "eip1559":
        gas_cost_wei = gas_limit * gas_params["maxFeePerGas"]
    else:
        gas_cost_wei = gas_limit * gas_params["gasPrice"]
    gas_cost_native = _wei_to_native(gas_cost_wei, decimals=decimals)

    # Hitung amount kirim
    amount_native = bal_native - gas_cost_native - reserve
    if amount_native <= 0 or amount_native < threshold:
        return None, f"Gagal sweep: saldo {bal_native:.8f} {native_symbol} < (biaya gas {gas_cost_native:.8f} + reserve {reserve:.8f} + threshold {threshold:.8f})"

    value_wei = _native_to_wei(amount_native, decimals=decimals)
    # build & kirim
    try:
        tx_hash = _build_and_send_tx(w3, pk_hex, to_addr, value_wei, gas_params)
        return tx_hash, None
    except Exception as e:
        return None, f"TX gagal: {e}"

# ---- Menu Aksi ----
def set_wallet_penampung():
    data = load_delegations()
    print_section_header("SET WALLET PENAMPUNG (B)")
    addr = input(f"{Colors.YELLOW}Masukkan address penampung (B): {Colors.ENDC}").strip()
    cs = checksum_or_none(addr)
    if not cs:
        print_error("Alamat tidak valid.")
        return
    data["sink_address"] = cs
    if save_delegations(data):
        print_success(f"Wallet penampung diset ke: {cs}")
        if TELEGRAM_ON:
            send_message(f"📥 Penampung diupdate:\n<code>{cs}</code>")
    else:
        print_error("Gagal menyimpan pengaturan penampung.")

def tambah_wallet_delegate():
    print_section_header("TAMBAH WALLET DELEGATE (A → B)")
    data = load_delegations()
    defaults = chain_defaults_map()

    sink = data.get("sink_address") or ""
    if not sink:
        print_warning("Wallet penampung (B) belum diset. Set terlebih dahulu.")
        return

    pk = input(f"{Colors.YELLOW}Masukkan private key Wallet A (0x...): {Colors.ENDC}").strip()
    if not pk.startswith("0x") or len(pk) != 66:
        print_error("Private key harus hex 0x + 64 karakter.")
        return

    owner_addr = pk_to_address(pk)
    if not owner_addr:
        print_error("Private key tidak valid.")
        return

    # Pilih chain
    chain_items = list(defaults.keys())
    print_box("DAFTAR CHAIN TERSEDIA", [f"- {k} ({defaults[k]['native_symbol']})" for k in chain_items], Colors.CYAN)
    chain_input = input(f"{Colors.YELLOW}Ketik chain (pisahkan koma) atau 'all' untuk semua: {Colors.ENDC}").strip().lower()
    if chain_input == "all":
        chains = chain_items
    else:
        chains = [c.strip() for c in chain_input.split(",") if c.strip() in chain_items]
        if not chains:
            print_error("Tidak ada chain valid yang dipilih.")
            return

    rule = {
        "id": str(uuid.uuid4()),
        "owner_pk": pk,
        "owner_address": Web3.to_checksum_address(owner_addr),
        "chains": chains,
        "enabled": True,
        "created_at": datetime.now().isoformat()
    }

    data["rules"].append(rule)
    if save_delegations(data):
        print_success(f"Delegate ditambahkan: {rule['owner_address']} → {data['sink_address']} | chains={len(chains)}")
        if TELEGRAM_ON:
            send_message(
                f"🟢 Delegate dibuat\n\n"
                f"👤 Owner (A): <code>{rule['owner_address']}</code>\n"
                f"📥 Penampung (B): <code>{data['sink_address']}</code>\n"
                f"🔗 Chains: {', '.join(chains)}"
            )
    else:
        print_error("Gagal menyimpan aturan delegasi.")

def list_wallet_delegate():
    data = load_delegations()
    rules = data.get("rules", [])
    sink = data.get("sink_address") or "(BELUM DISET)"
    lines = [f"Penampung (B): {sink}", ""]
    if not rules:
        lines.append("Belum ada delegate.")
    else:
        for i, r in enumerate(rules, 1):
            lines.append(
                f"{i}. {r['owner_address']} → {sink} | "
                f"chains={len(r.get('chains', []))} | aktif={r.get('enabled', True)} | id={r['id'][:8]}..."
            )
    print_box("DAFTAR WALLET DELEGATE", lines, Colors.BLUE)

def hapus_wallet_delegate():
    data = load_delegations()
    rules = data.get("rules", [])
    if not rules:
        print_warning("Tidak ada aturan untuk dihapus.")
        return

    list_wallet_delegate()
    key = input(f"{Colors.YELLOW}Hapus berdasarkan (ketik 'id' atau 'nomor'): {Colors.ENDC}").strip().lower()

    target_idx = None
    if key == "id":
        rid = input(f"{Colors.YELLOW}Masukkan ID aturan (uuid atau prefix): {Colors.ENDC}").strip()
        for idx, r in enumerate(rules):
            if r["id"].startswith(rid):
                target_idx = idx
                break
    else:
        try:
            num = int(input(f"{Colors.YELLOW}Nomor aturan (sesuai daftar): {Colors.ENDC}").strip())
            target_idx = num - 1
        except Exception:
            target_idx = None

    if target_idx is None or target_idx < 0 or target_idx >= len(rules):
        print_error("Target tidak ditemukan.")
        return

    target = rules[target_idx]
    if not ask_confirmation(f"Yakin hapus delegate untuk {target['owner_address']} ?", default=False):
        print_warning("Dibatalkan.")
        return

    removed = rules.pop(target_idx)
    data["rules"] = rules
    if save_delegations(data):
        print_success("Aturan dihapus.")
        if TELEGRAM_ON:
            send_message(
                f"🗑️ Delegate dihapus\n\n"
                f"👤 Owner (A): <code>{removed['owner_address']}</code>"
            )
    else:
        print_error("Gagal menyimpan perubahan.")

def _select_active_clients_by_chain(cfg, selected_chains):
    """
    Bangun Web3 clients dari config.json, lalu filter hanya chain yang dipilih (utils/chain.json).
    """
    clients_all = build_web3_clients(cfg, alchemy_api_key=os.getenv("ALCHEMY_API_KEY"))
    return {k: v for k, v in clients_all.items() if k in selected_chains}

def _do_sweep_for_rule(rule, sink, cfg, defaults, gas_caps):
    chains = rule.get("chains", [])
    clients = _select_active_clients_by_chain(cfg, chains)
    if not clients:
        return 0, 0  # (sukses, gagal)

    ok = 0
    fail = 0
    for chain_key, client in clients.items():
        w3 = client["w3"]
        txhash, err = _sweep_native_on_chain(
            w3=w3,
            chain_key=chain_key,
            pk_hex=rule["owner_pk"],
            to_addr=sink,
            defaults=defaults,
            gas_caps=gas_caps
        )
        if txhash:
            ok += 1
            msg = f"✅ Sweep {chain_key} OK\nTX: <code>{txhash}</code>"
            print_success(msg.replace("<code>", "").replace("</code>", ""))
            if TELEGRAM_ON:
                send_message(msg)
        else:
            fail += 1
            print_warning(f"Chain {chain_key}: {err}")
            if TELEGRAM_ON and err:
                notify_error(f"Sweep gagal {chain_key}", err)
    return ok, fail

def sweep_sekali():
    """
    Cek semua aturan, lakukan sweep jika memenuhi syarat (sekali jalan).
    """
    print_section_header("SWEEP SEKALI (ONE-SHOT)")
    data = load_delegations()
    defaults = chain_defaults_map()
    sink = data.get("sink_address")
    if not sink:
        print_warning("Penampung (B) belum diset.")
        return

    cfg = _load_json(CONFIG_FILE, default={})
    settings = load_delegate_settings()
    gas_caps = settings.get("gas_caps")

    rules = [r for r in data.get("rules", []) if r.get("enabled", True)]
    if not rules:
        print_warning("Tidak ada aturan aktif.")
        return

    total_ok = 0
    total_fail = 0
    for r in rules:
        ok, fail = _do_sweep_for_rule(r, sink, cfg, defaults, gas_caps)
        total_ok += ok
        total_fail += fail

    lines = [
        f"Sweep selesai.",
        f"Berhasil: {total_ok} chain",
        f"Gagal   : {total_fail} chain"
    ]
    print_box("HASIL SWEEP", lines, Colors.GREEN)

def mulai_monitor():
    """
    Loop pemantauan berkala: setiap interval mengecek & sweep.
    """
    data = load_delegations()
    defaults = chain_defaults_map()
    sink = data.get("sink_address")
    if not sink:
        print_warning("Penampung (B) belum diset.")
        return

    cfg = _load_json(CONFIG_FILE, default={})
    settings = load_delegate_settings()
    interval = int(settings.get("interval_sec", DEFAULT_INTERVAL_SEC))
    gas_caps = settings.get("gas_caps")

    print_box("MONITOR DIMULAI", [
        f"Penampung (B): {sink}",
        f"Interval     : {interval}s",
        f"Telegram     : {'Aktif' if TELEGRAM_ON else 'Nonaktif'}"
    ], Colors.YELLOW)

    try:
        while True:
            rules = [r for r in load_delegations().get("rules", []) if r.get("enabled", True)]
            if not rules:
                print_warning("Tidak ada aturan aktif. Menunggu...")
                time.sleep(interval)
                continue

            total_ok = 0
            total_fail = 0
            for r in rules:
                ok, fail = _do_sweep_for_rule(r, sink, cfg, defaults, gas_caps)
                total_ok += ok
                total_fail += fail

            print_box("RINGKASAN SIKLUS", [
                f"Berhasil: {total_ok} chain",
                f"Gagal   : {total_fail} chain",
                f"Waktu   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ], Colors.CYAN)

            time.sleep(interval)
    except KeyboardInterrupt:
        print_warning("Monitor dihentikan oleh pengguna.")

def pengaturan_delegate():
    """
    Atur interval polling & batas gas opsional (cap).
    """
    settings = load_delegate_settings()
    print_box("PENGATURAN DELEGATE (GLOBAL)", [
        f"Interval saat ini (detik) : {settings.get('interval_sec', DEFAULT_INTERVAL_SEC)}",
        f"Gas cap (maxFeePerGas Gwei)      : {settings['gas_caps'].get('maxFeePerGasGwei')}",
        f"Gas cap (maxPriorityFeePerGas Gwei): {settings['gas_caps'].get('maxPriorityFeePerGasGwei')}",
    ], Colors.MAGENTA)

    try:
        new_int = input(f"{Colors.YELLOW}Ubah interval? (kosong=lewati): {Colors.ENDC}").strip()
        if new_int:
            settings["interval_sec"] = max(3, int(new_int))
        mf = input(f"{Colors.YELLOW}Set maxFeePerGas (Gwei)? (kosong=lewati): {Colors.ENDC}").strip()
        if mf:
            settings["gas_caps"]["maxFeePerGasGwei"] = float(mf)
        mp = input(f"{Colors.YELLOW}Set maxPriorityFeePerGas (Gwei)? (kosong=lewati): {Colors.ENDC}").strip()
        if mp:
            settings["gas_caps"]["maxPriorityFeePerGasGwei"] = float(mp)
        save_delegate_settings(settings)
        print_success("Pengaturan tersimpan.")
    except Exception as e:
        print_error(f"Gagal mengubah pengaturan: {e}")

# ---- Menu Utama Delegate ----
def menu_delegate():
    while True:
        items = [
            f"{Colors.CYAN}1){Colors.ENDC} Wallet Penampung (B) {Colors.GRAY}- set/ganti address{Colors.ENDC}",
            f"{Colors.CYAN}2){Colors.ENDC} Tambah Wallet Delegate (A → B)",
            f"{Colors.CYAN}3){Colors.ENDC} List Wallet Delegate",
            f"{Colors.CYAN}4){Colors.ENDC} Hapus Wallet Delegate",
            f"{Colors.CYAN}5){Colors.ENDC} Mulai Monitor (Auto-Forward)",
            f"{Colors.CYAN}6){Colors.ENDC} Sweep Sekali (One-shot)",
            f"{Colors.CYAN}7){Colors.ENDC} Pengaturan (Interval & Gas Cap)",
            f"{Colors.CYAN}8){Colors.ENDC} Kembali ke Menu Utama",
        ]
        print_box("🛠️  DELEGATE WALLET - MENU", items, Colors.BLUE)
        ch = input(f"{Colors.YELLOW}Pilih (1-8): {Colors.ENDC}").strip()

        if ch == "1":
            set_wallet_penampung()
        elif ch == "2":
            tambah_wallet_delegate()
        elif ch == "3":
            list_wallet_delegate()
        elif ch == "4":
            hapus_wallet_delegate()
        elif ch == "5":
            mulai_monitor()
        elif ch == "6":
            sweep_sekali()
        elif ch == "7":
            pengaturan_delegate()
        elif ch == "8":
            print_success("Kembali ke menu utama.")
            break
        else:
            print_error("Pilihan tidak valid.")

# ---- Entry point untuk dipanggil dari main.py ----
def run():
    print_section_header("DELEGATE WALLET (AUTO-FORWARD NATIVE A → B)", color=Colors.YELLOW)
    # Tampilkan info chain default (threshold & reserve) sebagai edukasi singkat
    defaults = chain_defaults_map()
    lines = ["Default per chain (threshold & reserve):"]
    for k, v in defaults.items():
        lines.append(f"- {k}: threshold={v['threshold_native']} {v['native_symbol']} | reserve={v['reserve_native']} {v['native_symbol']}")
    print_box("INFO DEFAULT CHAIN", lines, Colors.CYAN)

    menu_delegate()

if __name__ == "__main__":
    run()
