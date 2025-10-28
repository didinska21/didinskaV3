#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_delegate.py - Smart-Contract based Delegate Wallet (Menu 3)

Fitur:
- Deploy kontrak auto-forward (DelegateWallet) 1 chain atau multi-chain
- Mode hemat fee (default: cheap) berbasis feeHistory + estimate_gas()*buffer
- Menu 2: cek saldo deployer per chain -> hanya tampil chain dengan saldo >= threshold
- Kelola sink, pause/unpause, sweep
- UI panah (questionary) bila terpasang; fallback ke input()
- Logging ke delegate.log + output CLI yang jelas
- Kompatibel Web3.py v6 & v7 (POA + raw_transaction)
"""

import os, sys, json, time, logging
from datetime import datetime
from decimal import Decimal, getcontext

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3, HTTPProvider

getcontext().prec = 28  # presisi tinggi utk perhitungan gas & saldo

# ==== Web3 v6/v7 POA middleware compatibility ====
try:
    from web3.middleware import ExtraDataToPOAMiddleware as POA_MIDDLEWARE
except ImportError:
    from web3.middleware import geth_poa_middleware as POA_MIDDLEWARE

# ==== Optional arrow/checkbox UI ====
try:
    import questionary
    from questionary import Choice as QChoice
except Exception:
    questionary = None
    QChoice = None

# ==== Path setup (pastikan base project ada di sys.path) ====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# utils/*
from utils.colors import Colors
from utils.ui import (
    print_box, print_loader, print_progress_bar, print_stats_box,
    print_section_header, print_warning, print_error, print_success, print_info
)

# .env di root modul ini
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ---------- Konstanta & File ----------
CONFIG_FILE = os.getenv("CONFIG_FILE", os.path.join(BASE_DIR, "config.json"))
CHAIN_FILE  = os.path.join(BASE_DIR, "utils", "chain.json")   # daftar chain (RPC + chainId + simbol)
RULES_FILE  = os.path.join(BASE_DIR, "delegate_rules.json")   # simpan daftar kontrak delegate
LOG_FILE    = os.path.join(BASE_DIR, "delegate.log")

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ],
)
log = logging.getLogger("delegate")

# ---------- Kontrak Solidity (inline) ----------
SOL_SOURCE = r"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract DelegateWallet {
    event SinkUpdated(address indexed oldSink, address indexed newSink);
    event Paused(address indexed by, bool paused);
    event Forwarded(address indexed to, uint256 amount);
    event Received(address indexed from, uint256 amount);

    address public owner;
    address public sink;
    bool    public paused;
    uint256 private _guard;

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }
    modifier nonReentrant() {
        require(_guard == 0, "Reentrancy");
        _guard = 1;
        _;
        _guard = 0;
    }
    modifier notPaused() {
        require(!paused, "Paused");
        _;
    }
    constructor(address _sink) {
        require(_sink != address(0), "Invalid sink");
        owner = msg.sender;
        sink = _sink;
        _guard = 0;
    }
    function setSink(address _sink) external onlyOwner {
        require(_sink != address(0), "Invalid sink");
        address old = sink;
        sink = _sink;
        emit SinkUpdated(old, _sink);
    }
    function setPaused(bool _paused) external onlyOwner {
        paused = _paused;
        emit Paused(msg.sender, _paused);
    }
    function sweep() public nonReentrant {
        uint256 bal = address(this).balance;
        if (bal == 0) return;
        (bool ok, ) = payable(sink).call{value: bal}("");
        require(ok, "Forward failed");
        emit Forwarded(sink, bal);
    }
    function sweepToken(address token) external onlyOwner nonReentrant {
        (bool s1, bytes memory d1) = token.staticcall(abi.encodeWithSignature("balanceOf(address)", address(this)));
        require(s1 && d1.length >= 32, "balanceOf fail");
        uint256 bal = abi.decode(d1, (uint256));
        if (bal == 0) return;
        (bool s2, ) = token.call(abi.encodeWithSignature("transfer(address,uint256)", sink, bal));
        require(s2, "transfer fail");
    }
    receive() external payable notPaused nonReentrant {
        emit Received(msg.sender, msg.value);
        uint256 bal = address(this).balance;
        if (bal == 0) return;
        (bool ok, ) = payable(sink).call{value: bal}("");
        require(ok, "Forward failed");
        emit Forwarded(sink, bal);
    }
    fallback() external payable notPaused nonReentrant {
        if (msg.value > 0) {
            emit Received(msg.sender, msg.value);
            uint256 bal = address(this).balance;
            (bool ok, ) = payable(sink).call{value: bal}("");
            require(ok, "Forward failed");
            emit Forwarded(sink, bal);
        }
    }
}
"""

# ---------- Masker & Progress ----------
def mask_middle(s: str, head: int = 6, tail: int = 6, stars: int = 5) -> str:
    """Sensor string: simpan head & tail, tengah ganti *****"""
    if not s:
        return s
    if len(s) <= head + tail:
        return s
    return s[:head] + ("*" * stars) + s[-tail:]

def local_progress(current: int, total: int, prefix: str = "Progres"):
    """Progress bar sederhana tanpa dependency utils.ui."""
    width = 30
    ratio = 0 if total == 0 else current / total
    fill = int(width * ratio)
    bar = "#" * fill + "-" * (width - fill)
    print(f"\r{prefix} [{bar}] {current}/{total}", end="", flush=True)
    if current == total:
        print()

# ---------- Helpers ----------
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _wei_to_native(wei: int, decimals: int) -> Decimal:
    return Decimal(wei) / (Decimal(10) ** Decimal(decimals))

def _native_to_wei(amount: Decimal, decimals: int) -> int:
    return int(amount * (Decimal(10) ** Decimal(decimals)))

# ---------- Compiler ----------
def compile_contract():
    try:
        from solcx import compile_standard, install_solc, set_solc_version
    except Exception:
        print_error("py-solc-x belum terpasang. Jalankan: pip install py-solc-x")
        return None, None

    try:
        install_solc("0.8.24")
        set_solc_version("0.8.24")

        source_json = {
            "language": "Solidity",
            "sources": {"DelegateWallet.sol": {"content": SOL_SOURCE}},
            "settings": {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode"]}}
            },
        }
        compiled = compile_standard(source_json)
        contract = compiled["contracts"]["DelegateWallet.sol"]["DelegateWallet"]
        abi = contract["abi"]
        bytecode = contract["evm"]["bytecode"]["object"]
        return abi, bytecode
    except Exception as e:
        print_error(f"Gagal compile: {e}")
        log.exception("Compile error")
        return None, None

# ---------- Chain / Gas ----------
def _guess_fees(w3, mode: str = None):
    """
    Kembalikan dict fee:
      - EIP-1559: {maxFeePerGas, maxPriorityFeePerGas}
      - Legacy  : {gasPrice}
    Mode: cheap (default), normal, fast
    """
    # default mode dari ENV atau argumen fungsi
    mode = mode or os.getenv("FEE_MODE", "cheap").lower()
    if mode not in ("cheap", "normal", "fast"):
        mode = "cheap"
    try:
        # EIP-1559 path
        fh = w3.eth.fee_history(5, "latest", [10, 50, 90])
        base = int(fh.baseFeePerGas[-1])
        rewards = fh.reward[-1] if fh.reward else [w3.to_wei(1, "gwei")]
        median_tip = int(sorted(rewards)[len(rewards)//2]) if rewards else w3.to_wei(1, "gwei")

        if mode == "cheap":
            tip = max(int(median_tip * 0.5), w3.to_wei(0.2, "gwei"))
            max_fee = int(base * 1.15 + tip)  # 15% headroom
        elif mode == "fast":
            tip = max(int(median_tip * 2), w3.to_wei(2, "gwei"))
            max_fee = int(base * 2 + tip)
        else:
            tip = max(median_tip, w3.to_wei(0.5, "gwei"))
            max_fee = int(base * 1.3 + tip)  # 30% headroom

        return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip}
    except Exception:
        # Legacy fallback
        try:
            gp = int(w3.eth.gas_price)
        except Exception:
            gp = int(w3.to_wei(3, "gwei"))
        if mode == "cheap":
            gp = max(int(gp * 0.8), int(w3.to_wei(0.5, "gwei")))
        elif mode == "fast":
            gp = int(gp * 1.5)
        return {"gasPrice": gp}

def _send_raw_tx(w3, signed):
    """Kirim TX mentah; kompatibel Web3/eth-account v6 & v7."""
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        if isinstance(signed, (bytes, bytearray)):
            raw = signed
        else:
            raise ValueError("SignedTransaction: raw tx not found")
    return w3.eth.send_raw_transaction(raw)

def connect_chain(chain_key):
    chains = load_json(CHAIN_FILE, {})
    info = chains.get(chain_key)
    if not info:
        print_error(f"Chain '{chain_key}' tidak ditemukan di utils/chain.json")
        return None, None, None

    rpc = info.get("rpc_url")
    if not rpc:
        print_error("RPC URL kosong")
        return None, None, None

    # inject ALCHEMY_API_KEY jika ada placeholder
    alchemy = os.getenv("ALCHEMY_API_KEY")
    if "${ALCHEMY_API_KEY}" in rpc and alchemy:
        rpc = rpc.replace("${ALCHEMY_API_KEY}", alchemy)

    w3 = Web3(HTTPProvider(rpc, request_kwargs={"timeout": 20}))
    try:
        w3.middleware_onion.inject(POA_MIDDLEWARE, layer=0)
    except Exception:
        pass

    if not w3.is_connected():
        print_error(f"Gagal konek RPC: {chain_key}")
        return None, None, None

    chain_id = info.get("chain_id")
    if not chain_id:
        try:
            chain_id = w3.eth.chain_id
        except Exception:
            chain_id = None

    return w3, chain_id, info

def _get_chain_meta(info: dict):
    decimals = int(info.get("decimals", 18))
    symbol = info.get("native_symbol", "ETH")
    threshold = Decimal(str(info.get("threshold_native", 0.0001)))
    fee_mode_default = info.get("fee_mode_default", None)  # override global kalau ada
    gas_buffer = float(info.get("deploy_gas_buffer", 1.2))
    return decimals, symbol, threshold, fee_mode_default, gas_buffer

# ---------- Saldo helper ----------
def get_native_balance(chain_key, address):
    w3, _, info = connect_chain(chain_key)
    if not w3:
        return {"ok": False, "msg": "rpc_failed", "balance": Decimal(0), "symbol": "?", "decimals": 18, "threshold": Decimal(0)}
    decimals, symbol, threshold, *_ = _get_chain_meta(info)
    try:
        wei = w3.eth.get_balance(address)
        bal = _wei_to_native(wei, decimals)
        return {"ok": True, "balance": bal, "symbol": symbol, "decimals": decimals, "threshold": threshold}
    except Exception as e:
        return {"ok": False, "msg": str(e), "balance": Decimal(0), "symbol": symbol, "decimals": decimals, "threshold": threshold}

def show_balances_and_filter(address):
    chains = list(load_json(CHAIN_FILE, {}).keys())
    lines = []
    eligible = []
    for ck in chains:
        r = get_native_balance(ck, address)
        if r["ok"]:
            status = "✅" if r["balance"] >= r["threshold"] else "❌"
            if r["balance"] >= r["threshold"]:
                eligible.append(ck)
            lines.append(f"{ck:<14} | {str(r['balance']):>18} {r['symbol']:<5} | need ≥ {r['threshold']} → {status}")
        else:
            lines.append(f"{ck:<14} | (ERR: {r['msg']})")
    print_box("🔎 SALDO DEPLOYER (native)", lines, Colors.BLUE)
    if eligible:
        print_success("Eligible: " + ", ".join(eligible))
    else:
        print_warning("Tidak ada chain dengan saldo cukup.")
    return eligible

# ---------- Input PK / Sink ----------
def prompt_pk():
    raw = input(f"{Colors.YELLOW}Masukkan Private Key (ditampilkan & disensor): {Colors.ENDC}").strip()
    raw = raw.replace("0x", "")
    if len(raw) != 64:
        print_error("Private key tidak valid (panjang harus 64 hex).")
        return None
    pk = "0x" + raw
    print_info(f"PK   : {mask_middle(pk, 6, 6, 5)}")   # tampilkan versi sensor
    return pk

def checksum(w3, addr):
    try:
        return w3.to_checksum_address(addr)
    except Exception:
        return addr

def select_sink_default():
    """Minta sink address 1x, simpan sebagai default."""
    sink = input(f"{Colors.YELLOW}Masukkan alamat wallet penampung (sink): {Colors.ENDC}").strip()
    rules = load_json(RULES_FILE, {})
    rules["default_sink"] = sink
    save_json(RULES_FILE, rules)
    print_success("Default sink tersimpan.")
    log.info("Set default sink: %s", sink)

# ---------- Aksi Kontrak inti ----------
def _build_estimated_tx(w3, acct, chain_id, gas_func, fee_mode, gas_buffer):
    """
    gas_func -> fungsi yang mengembalikan (fn_txbuilder, est_gas_suggestion)
    """
    fees = _guess_fees(w3, mode=fee_mode)
    fn_builder, est = gas_func()
    # estimate_gas dengan buffer
    try:
        est_gas = est()
        gas_limit = int(est_gas * gas_buffer)
    except Exception:
        gas_limit = int(700000 * gas_buffer)  # fallback aman
    tx = fn_builder({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": chain_id,
        "gas": gas_limit,
        **fees,
    })
    return tx

def _deploy_on_chain(chain_key, sink, pk, fee_mode=None):
    """Deploy 1 chain, return (success, address/err)."""
    abi, bytecode = compile_contract()
    if not abi or not bytecode:
        return False, "compile_failed"

    w3, chain_id, info = connect_chain(chain_key)
    if not w3:
        return False, "rpc_failed"

    decimals, symbol, threshold, fee_mode_default, gas_buffer = _get_chain_meta(info)
    fee_mode = fee_mode or fee_mode_default or os.getenv("FEE_MODE", "cheap")

    acct = Account.from_key(pk)
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    constructor = contract.constructor(checksum(w3, sink))

    def _gas_func():
        fn_builder = lambda txparams: constructor.build_transaction(txparams)
        est = lambda: constructor.estimate_gas({"from": acct.address})
        return fn_builder, est

    # Bangun TX dengan estimate + buffer + fee_mode
    tx = _build_estimated_tx(w3, acct, chain_id, _gas_func, fee_mode, gas_buffer)

    # Kirim (dengan simple retry jika underpriced)
    try:
        signed = acct.sign_transaction(tx)
        tx_hash = _send_raw_tx(w3, signed)
    except Exception as e:
        msg = str(e).lower()
        if any(s in msg for s in ["underpriced", "fee too low", "max fee per gas"]):
            # bump sekali (switch mode -> normal/fast)
            bump_mode = "normal" if fee_mode == "cheap" else "fast"
            print_warning(f"[{chain_key}] Fee terlalu rendah, retry dengan mode '{bump_mode}'")
            tx = _build_estimated_tx(w3, acct, chain_id, _gas_func, bump_mode, gas_buffer)
            signed = acct.sign_transaction(tx)
            tx_hash = _send_raw_tx(w3, signed)
        else:
            print_error(f"[{chain_key}] Gagal kirim tx: {e}")
            return False, "send_failed"

    print_info(f"[{chain_key}] Tx deploy: {tx_hash.hex()}")
    log.info("[%s] deploy sent: %s", chain_key, tx_hash.hex())

    print_loader(f"[{chain_key}] Menunggu konfirmasi...", 2)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status != 1:
        print_error(f"[{chain_key}] Deploy gagal (status=0)")
        log.error("[%s] deploy failed (status=0)", chain_key)
        return False, "receipt_status_0"

    addr = receipt.contractAddress
    print_success(f"[{chain_key}] DelegateWallet: {addr}")
    log.info("[%s] contract deployed at %s", chain_key, addr)

    rules = load_json(RULES_FILE, {})
    rules.setdefault(chain_key, [])
    rules[chain_key].append({
        "contract": addr,
        "sink": checksum(w3, sink),
        "owner": acct.address,
        "created_at": datetime.now().isoformat()
    })
    save_json(RULES_FILE, rules)
    return True, addr

# ---------- FLOW: Deploy (dengan cek saldo & filter chain) ----------
def deploy_delegate_interactive():
    # ambil sink default
    rules = load_json(RULES_FILE, {})
    default_sink = rules.get("default_sink", "").strip()

    # input sink (tampil & disensor)
    if questionary:
        sink = questionary.text(f"Sink address [{default_sink}]:").ask() or default_sink
    else:
        sink = input(f"{Colors.YELLOW}Sink address [{default_sink}]: {Colors.ENDC}").strip() or default_sink
    if not sink:
        print_error("Sink address wajib diisi.")
        return
    print_info(f"Sink : {mask_middle(sink, 6, 6, 5)}")

    # input PK (tampil & disensor)
    pk = prompt_pk()
    if not pk:
        return
    deployer = Account.from_key(pk).address
    print_info(f"Deployer : {mask_middle(deployer, 6, 6, 5)}")

    # Cek saldo semua chain & filter eligible
    eligible = show_balances_and_filter(deployer)
    if not eligible:
        return

    # Pilih fee mode
    fee_mode = "cheap"
    if questionary:
        fee_mode = questionary.select("Mode biaya gas:", choices=["cheap", "normal", "fast"]).ask() or "cheap"
    else:
        ans = input("Mode fee [cheap/normal/fast] (default cheap): ").strip().lower()
        if ans in ("cheap", "normal", "fast"):
            fee_mode = ans

    # Pilih chain dari eligible saja (ALL / multi / single)
    if questionary:
        choices = [{"name": "ALL ELIGIBLE", "value": "__ALL__"}] + [{"name": c, "value": c} for c in eligible]
        sel = questionary.checkbox("Pilih chain untuk deploy:", choices=choices).ask()
        if not sel:
            print_warning("Tidak ada chain dipilih."); return
        chains = eligible if "__ALL__" in sel else sel
    else:
        print_info("Ketik 'all' untuk semua eligible, atau pilih pisah koma.")
        ans = input(f"Eligible: {', '.join(eligible)}\nPilih: ").strip().lower()
        chains = eligible if ans in ("all", "*") else [x.strip() for x in ans.split(",") if x.strip() in eligible]
        if not chains:
            print_warning("Tidak ada chain valid yang dipilih."); return

    # Deploy batch
    total = len(chains)
    ok = 0
    for i, ck in enumerate(chains, 1):
        local_progress(i - 1, total, prefix="Progres")
        success, _ = _deploy_on_chain(ck, sink, pk, fee_mode=fee_mode)
        if success:
            ok += 1
        local_progress(i, total, prefix="Progres")

    print_stats_box(title="Ringkasan Deploy", stats=[
        ("Dipilih", str(total)),
        ("Berhasil", str(ok)),
        ("Gagal", str(total - ok)),
        ("Sink", mask_middle(sink, 6, 6, 5)),
        ("Fee mode", fee_mode),
    ])

# ---------- TX helpers (tidak diubah) ----------
def _tx_template(chain_key, builder_fn, gas=200000, fee_mode=None, gas_buffer=None):
    """Helper untuk call tx ke kontrak yang sudah ada."""
    w3, chain_id, info = connect_chain(chain_key)
    if not w3: return False
    abi, _ = compile_contract()
    if not abi: return False

    decimals, symbol, threshold, fee_mode_default, gas_buffer_default = _get_chain_meta(info)
    fee_mode = fee_mode or fee_mode_default or os.getenv("FEE_MODE", "cheap")
    gas_buffer = gas_buffer or gas_buffer_default

    pk = prompt_pk()
    if not pk: return False
    acct = Account.from_key(pk)

    caddr, fn = builder_fn(w3, abi)  # return (contract_addr, function)

    # estimate + buffer + fee
    def _gas_func():
        fn_builder = lambda txparams: fn.build_transaction(txparams)
        est = lambda: fn.estimate_gas({"from": acct.address})
        return fn_builder, est

    try:
        tx = _build_estimated_tx(w3, acct, chain_id, _gas_func, fee_mode, gas_buffer)
        signed = acct.sign_transaction(tx)
        tx_hash = _send_raw_tx(w3, signed)
        print_info(f"[{chain_key}] Tx: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        return receipt.status == 1
    except Exception as e:
        print_error(f"[{chain_key}] Gagal kirim tx: {e}")
        return False

def call_set_sink(chain_key, contract_addr, new_sink):
    return _tx_template(chain_key,
        lambda w3, abi: (contract_addr, w3.eth.contract(address=checksum(w3, contract_addr), abi=abi)
                         .functions.setSink(checksum(w3, new_sink))),
        gas=200000)

def call_pause(chain_key, contract_addr, to_pause=True):
    return _tx_template(chain_key,
        lambda w3, abi: (contract_addr, w3.eth.contract(address=checksum(w3, contract_addr), abi=abi)
                         .functions.setPaused(bool(to_pause))),
        gas=150000)

def call_sweep(chain_key, contract_addr):
    return _tx_template(chain_key,
        lambda w3, abi: (contract_addr, w3.eth.contract(address=checksum(w3, contract_addr), abi=abi)
                         .functions.sweep()),
        gas=150000)

# ---------- UI ----------
def list_delegates():
    rules = load_json(RULES_FILE, {})
    if not rules or all(k == "default_sink" for k in rules.keys()):
        print_warning("Belum ada delegate terdaftar.")
        return
    lines = []
    for chain_key, items in rules.items():
        if chain_key == "default_sink":
            continue
        lines.append(f"{Colors.BOLD}{Colors.CYAN}Chain: {chain_key}{Colors.ENDC}")
        for i, it in enumerate(items, 1):
            lines.append(f"  {i}. Kontrak : {it['contract']}")
            lines.append(f"     Sink    : {it['sink']}")
            lines.append(f"     Owner   : {it['owner']}")
            lines.append(f"     Dibuat  : {it.get('created_at','-')}")
        lines.append("")
    print_box("📜 DAFTAR WALLET DELEGATE", lines, Colors.BLUE)

def set_global_sink():
    select_sink_default()

def menu_delegate():
    while True:
        menu = [
            f"{Colors.CYAN}1){Colors.ENDC} Atur Wallet Penampung (Sink default)",
            f"{Colors.CYAN}2){Colors.ENDC} Buat Wallet Delegate (Cek saldo → pilih eligible)",
            f"{Colors.CYAN}3){Colors.ENDC} Daftar Wallet Delegate",
            f"{Colors.CYAN}4){Colors.ENDC} Nonaktifkan/Aktifkan Delegate (Pause/Unpause)",
            f"{Colors.CYAN}5){Colors.ENDC} Ubah Sink pada Delegate",
            f"{Colors.CYAN}6){Colors.ENDC} Sweep Manual (paksa kirim saldo kontrak ke sink)",
            f"{Colors.CYAN}7){Colors.ENDC} Hapus dari daftar (off-chain)",
            f"{Colors.CYAN}8){Colors.ENDC} Kembali"
        ]
        print_box("🧭 MENU DELEGATE WALLET (Smart Contract)", menu, Colors.MAGENTA)

        # --- Pilihan menu utama ---
        if questionary:
            ch = questionary.select(
                "Pilih menu:",
                choices=[
                    QChoice("1) Atur Sink default", "1"),
                    QChoice("2) Buat Delegate (cek saldo & filter)", "2"),
                    QChoice("3) Daftar Delegate", "3"),
                    QChoice("4) Pause/Unpause", "4"),
                    QChoice("5) Ubah Sink", "5"),
                    QChoice("6) Sweep Manual", "6"),
                    QChoice("7) Hapus dari daftar (off-chain)", "7"),
                    QChoice("8) Kembali", "8"),
                ],
            ).ask()
        else:
            ch = input(f"{Colors.YELLOW}Pilih (1-8): {Colors.ENDC}").strip()

        if ch == "1":
            set_global_sink()

        elif ch == "2":
            deploy_delegate_interactive()

        elif ch == "3":
            list_delegates()

        elif ch == "4":
            # pilih chain & kontrak dari daftar
            rules = load_json(RULES_FILE, {})
            chains = [k for k in rules.keys() if k != "default_sink"]
            if not chains:
                print_warning("Belum ada kontrak terdaftar.")
                continue

            if questionary:
                ck = questionary.select("Pilih chain:", choices=chains).ask()
            else:
                ck = input("Chain key: ").strip()

            if not ck or ck not in rules:
                print_warning("Chain tidak valid.")
                continue

            items = rules[ck]
            labels = [f"{i+1}. {it['contract']}" for i, it in enumerate(items)]
            if questionary:
                sel = questionary.select("Pilih kontrak:", choices=labels).ask()
                idx = labels.index(sel)
            else:
                print("\n".join(labels))
                idx = int(input("Nomor: ").strip()) - 1

            mode = "pause"
            if questionary:
                mode = questionary.select("Mode:", choices=["pause", "unpause"]).ask()
            else:
                mode = input("Ketik 'pause' atau 'unpause': ").strip().lower()

            ok = call_pause(ck, items[idx]["contract"], to_pause=(mode=="pause"))
            print_success("Status pause diubah.") if ok else print_error("Gagal setPaused")

        elif ch == "5":
            rules = load_json(RULES_FILE, {})
            chains = [k for k in rules.keys() if k != "default_sink"]
            if not chains:
                print_warning("Belum ada kontrak terdaftar.")
                continue
            ck = questionary.select("Pilih chain:", choices=chains).ask() if questionary else input("Chain key: ").strip()
            if not ck or ck not in rules:
                print_warning("Chain tidak valid."); continue
            items = rules[ck]
            labels = [f"{i+1}. {it['contract']}" for i, it in enumerate(items)]
            if questionary:
                sel = questionary.select("Pilih kontrak:", choices=labels).ask()
                idx = labels.index(sel)
            else:
                print("\n".join(labels)); idx = int(input("Nomor: ").strip()) - 1

            new_sink = questionary.text("Alamat sink baru:").ask() if questionary else input("Alamat sink baru: ").strip()
            ok = call_set_sink(ck, items[idx]["contract"], new_sink)
            print_success("Sink berhasil diubah.") if ok else print_error("Gagal setSink")

        elif ch == "6":
            rules = load_json(RULES_FILE, {})
            chains = [k for k in rules.keys() if k != "default_sink"]
            if not chains:
                print_warning("Belum ada kontrak terdaftar."); continue
            ck = questionary.select("Pilih chain:", choices=chains).ask() if questionary else input("Chain key: ").strip()
            if not ck or ck not in rules:
                print_warning("Chain tidak valid."); continue
            items = rules[ck]
            labels = [f"{i+1}. {it['contract']}" for i, it in enumerate(items)]
            if questionary:
                sel = questionary.select("Pilih kontrak:", choices=labels).ask()
                idx = labels.index(sel)
            else:
                print("\n".join(labels)); idx = int(input("Nomor: ").strip()) - 1

            ok = call_sweep(ck, items[idx]["contract"])
            print_success("Sweep berhasil.") if ok else print_error("Gagal sweep")

        elif ch == "7":
            rules = load_json(RULES_FILE, {})
            chains = [k for k in rules.keys() if k != "default_sink"]
            if not chains:
                print_warning("Tidak ada entri untuk dihapus."); continue
            ck = questionary.select("Pilih chain:", choices=chains).ask() if questionary else input("Chain key: ").strip()
            if not ck or ck not in rules:
                print_warning("Chain tidak valid."); continue
            items = rules[ck]
            labels = [f"{i+1}. {it['contract']}" for i, it in enumerate(items)]
            if questionary:
                idxs = questionary.checkbox("Pilih yang ingin dihapus:", choices=labels).ask() or []
                to_delete = set(int(s.split(".")[0])-1 for s in idxs)
            else:
                print("\n".join(labels))
                raw = input("Nomor (pisah koma): ").strip()
                to_delete = set(int(x)-1 for x in raw.split(",") if x.strip().isdigit())

            before = len(items)
            rules[ck] = [r for i, r in enumerate(items) if i not in to_delete]
            after = len(rules[ck])
            save_json(RULES_FILE, rules)
            if before != after:
                print_success(f"Dihapus {before-after} entri dari daftar lokal.")
            else:
                print_warning("Tidak ada perubahan.")

        elif ch == "8":
            print_success("Kembali ke menu utama.")
            break

        else:
            print_error("Pilihan tidak valid.")

# ---------- Entry ----------
def run():
    print_section_header("DELEGATE WALLET (Smart Contract)")
    menu_delegate()

if __name__ == "__main__":
    run()
