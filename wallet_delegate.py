#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_delegate.py - Smart-Contract based Delegate Wallet (Menu 3)

Fitur:
- Deploy kontrak auto-forward (DelegateWallet) 1 chain atau multi-chain (All Chains)
- Kelola sink, pause/unpause, sweep
- UI panah (questionary) bila terpasang; fallback ke input()
- Logging ke file delegate.log + output CLI yang jelas
- Kompatibel Web3.py v6 & v7 (POA + raw_transaction)
"""

import os, sys, json, time, logging
from datetime import datetime
from getpass import getpass

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3, HTTPProvider

# ==== Web3 v6/v7 POA middleware compatibility ====
try:
    # Web3.py v7+
    from web3.middleware import ExtraDataToPOAMiddleware as POA_MIDDLEWARE
except ImportError:
    # Web3.py v6
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

def _guess_fees(w3):
    """Return fee fields (EIP-1559/legacy)."""
    try:
        base = w3.eth.gas_price
        return {"maxFeePerGas": int(base * 2), "maxPriorityFeePerGas": w3.to_wei(1, "gwei")}
    except Exception:
        gp = w3.to_wei(3, "gwei")
        try:
            gp = w3.eth.gas_price
        except Exception:
            pass
        return {"gasPrice": int(gp)}

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
        return None, None

    rpc = info.get("rpc_url")
    if not rpc:
        print_error("RPC URL kosong")
        return None, None

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
        return None, None

    chain_id = info.get("chain_id")
    if not chain_id:
        try:
            chain_id = w3.eth.chain_id
        except Exception:
            chain_id = None

    return w3, chain_id

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

def list_chain_keys():
    data = load_json(CHAIN_FILE, {})
    return list(data.keys())

def choose_chains(multi=True):
    """Pilih chain via panah (jika questionary tersedia)."""
    keys = list_chain_keys()
    if not keys:
        print_error("Tidak ada chain pada utils/chain.json")
        return []

    if questionary and multi:
        choices = [{"name": "ALL CHAINS", "value": "__ALL__"}] + [{"name": k, "value": k} for k in keys]
        sel = questionary.checkbox("Pilih chain (Space=pilih, Enter=ok):", choices=choices).ask()
        if not sel:
            return []
        if "__ALL__" in sel:
            return keys
        return sel
    elif questionary and not multi:
        sel = questionary.select("Pilih chain:", choices=keys).ask()
        return [sel] if sel else []
    else:
        print_info("questionary tidak terpasang. Input manual dipakai.")
        print(f"Tersedia: {', '.join(keys)} atau ketik 'all'")
        ans = input("Chain key (pisah koma): ").strip().lower()
        if ans in ("all", "semua", "*"):
            return keys
        chosen = [a.strip() for a in ans.split(",") if a.strip()]
        return chosen

# ---------- Aksi Kontrak inti ----------
def _deploy_on_chain(chain_key, sink, pk):
    """Deploy 1 chain, return (success, address/err)."""
    abi, bytecode = compile_contract()
    if not abi or not bytecode:
        return False, "compile_failed"

    w3, chain_id = connect_chain(chain_key)
    if not w3:
        return False, "rpc_failed"

    acct = Account.from_key(pk)
    nonce = w3.eth.get_transaction_count(acct.address)
    fees = _guess_fees(w3)
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    tx = contract.constructor(checksum(w3, sink)).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "chainId": chain_id,
        "gas": 700000,
        **fees,
    })

    signed = acct.sign_transaction(tx)
    tx_hash = _send_raw_tx(w3, signed)
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

def deploy_delegate_interactive():
    # sink
    rules = load_json(RULES_FILE, {})
    default_sink = rules.get("default_sink", "").strip()
    if questionary:
        sink = questionary.text(f"Sink address [{default_sink}]:").ask() or default_sink
    else:
        sink = input(f"{Colors.YELLOW}Sink address [{default_sink}]: {Colors.ENDC}").strip() or default_sink
    if not sink:
        print_error("Sink address wajib diisi.")
        return
        print_info(f"Sink : {mask_middle(sink, 6, 6, 5)}")

    # pilih chain(s)
    chains = choose_chains(multi=True)
    if not chains:
        print_warning("Tidak ada chain dipilih.")
        return

    # pk
    pk = prompt_pk()
    if not pk:
        return

    # batch deploy
    total = len(chains)
    ok = 0
    for i, ck in enumerate(chains, 1):
    local_progress(i-1, total, prefix="Progres")
    success, msg = _deploy_on_chain(ck, sink, pk)
    ok += 1 if success else 0
    local_progress(i, total, prefix="Progres")
    print_stats_box(title="Ringkasan Deploy", stats=[
        ("Dipilih", str(total)),
        ("Berhasil", str(ok)),
        ("Gagal", str(total - ok)),
        ("Sink", sink),
    ])

def _tx_template(chain_key, builder_fn, gas=200000):
    """Helper untuk call tx ke kontrak yang sudah ada."""
    w3, chain_id = connect_chain(chain_key)
    if not w3: return False
    abi, _ = compile_contract()
    if not abi: return False
    pk = prompt_pk()
    if not pk: return False
    acct = Account.from_key(pk)
    caddr, fn = builder_fn(w3, abi)  # return (contract_addr, function)
    tx = fn.build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": chain_id,
        "gas": gas,
        **_guess_fees(w3),
    })
    signed = acct.sign_transaction(tx)
    tx_hash = _send_raw_tx(w3, signed)
    print_info(f"[{chain_key}] Tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    return receipt.status == 1

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
            f"{Colors.CYAN}2){Colors.ENDC} Buat Wallet Delegate (Single/Multi Chain)",
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
                    QChoice("2) Buat Delegate (Single/Multi)", "2"),
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
            idx = 0
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
