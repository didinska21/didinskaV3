#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_delegate.py - Smart-Contract based Delegate Wallet (Menu 3)
- Deploy kontrak auto-forwarder (DelegateWallet)
- Kelola sink, pause/unpause, sweep
- Simpan konfigurasi di delegate_rules.json
"""
import os, sys, json, time
from datetime import datetime
from getpass import getpass

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3, HTTPProvider
from web3.middleware import geth_poa_middleware

# Tambah utils ke path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))
from utils.colors import Colors
from utils.ui import print_box, print_loader, print_progress_bar, print_stats_box, print_section_header, print_warning, print_error, print_success

load_dotenv()

# ---------- Konstanta & File ----------
CONFIG_FILE   = os.getenv("CONFIG_FILE", "config.json")
CHAIN_FILE    = os.path.join("utils", "chain.json")  # daftar chain (RPC + chainId + simbol)
RULES_FILE    = "delegate_rules.json"               # simpan daftar kontrak delegate

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

# compile on the fly (py-solc-x)
def compile_contract():
    try:
        from solcx import compile_standard, install_solc, set_solc_version
    except Exception:
        print_error("py-solc-x belum terpasang. Jalankan: pip install py-solc-x")
        return None, None

    try:
        # Pastikan versi compiler ada
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
        return None, None

# ---------- Helpers ----------
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

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

    w3 = Web3(HTTPProvider(rpc, request_kwargs={"timeout": 15}))
    # POA chains (sebagian L2) kadang butuh middleware ini
    try:
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    except Exception:
        pass

    if not w3.is_connected():
        print_error("Gagal konek RPC")
        return None, None

    chain_id = info.get("chain_id")
    if not chain_id:
        # fallback baca dari node
        try:
            chain_id = w3.eth.chain_id
        except:
            chain_id = None

    return w3, chain_id

def prompt_pk():
    pk = getpass(f"{Colors.YELLOW}Masukkan Private Key (tidak ditampilkan): {Colors.ENDC}")
    pk = pk.strip().replace("0x","")
    if len(pk) != 64:
        print_error("Private key tidak valid.")
        return None
    return "0x"+pk

def checksum(w3, addr):
    try:
        return w3.to_checksum_address(addr)
    except:
        return addr

# ---------- Aksi Kontrak ----------
def deploy_delegate(chain_key, sink_addr):
    abi, bytecode = compile_contract()
    if not abi or not bytecode:
        return

    w3, chain_id = connect_chain(chain_key)
    if not w3:
        return

    sink = checksum(w3, sink_addr)
    pk = prompt_pk()
    if not pk:
        return

    acct = Account.from_key(pk)
    nonce = w3.eth.get_transaction_count(acct.address)

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor(sink).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "chainId": chain_id,
        "gas": 700000,  # konservatif
        "maxFeePerGas": w3.eth.gas_price * 2 // 1 if hasattr(w3.eth, 'gas_price') else w3.to_wei(2, 'gwei'),
        "maxPriorityFeePerGas": w3.to_wei(1, 'gwei'),
    })

    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    print_info(f"Tx deploy terkirim: {tx_hash.hex()}")

    print_loader("Menunggu konfirmasi", 2)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status != 1:
        print_error("Deploy gagal (status=0)")
        return

    addr = receipt.contractAddress
    print_success(f"DelegateWallet ter-deploy di: {addr}")

    # simpan ke RULES_FILE
    rules = load_json(RULES_FILE, {})
    rules.setdefault(chain_key, [])
    rules[chain_key].append({
        "contract": addr,
        "sink": sink,
        "owner": acct.address,
        "created_at": datetime.now().isoformat()
    })
    save_json(RULES_FILE, rules)

def call_set_sink(chain_key, contract_addr, new_sink):
    w3, chain_id = connect_chain(chain_key)
    if not w3: return
    abi, _ = compile_contract()
    if not abi: return

    pk = prompt_pk()
    if not pk: return
    acct = Account.from_key(pk)

    c = w3.eth.contract(address=checksum(w3, contract_addr), abi=abi)
    tx = c.functions.setSink(checksum(w3, new_sink)).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": chain_id,
        "gas": 200000,
        "maxFeePerGas": w3.eth.gas_price * 2 // 1 if hasattr(w3.eth, 'gas_price') else w3.to_wei(2, 'gwei'),
        "maxPriorityFeePerGas": w3.to_wei(1, 'gwei'),
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    print_info(f"Tx setSink: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status == 1:
        print_success("Sink berhasil diubah")
        # update file rules
        rules = load_json(RULES_FILE, {})
        if chain_key in rules:
            for r in rules[chain_key]:
                if r["contract"].lower() == contract_addr.lower():
                    r["sink"] = checksum(w3, new_sink)
        save_json(RULES_FILE, rules)
    else:
        print_error("Gagal setSink")

def call_pause(chain_key, contract_addr, to_pause=True):
    w3, chain_id = connect_chain(chain_key)
    if not w3: return
    abi, _ = compile_contract()
    if not abi: return
    pk = prompt_pk()
    if not pk: return
    acct = Account.from_key(pk)

    c = w3.eth.contract(address=checksum(w3, contract_addr), abi=abi)
    tx = c.functions.setPaused(bool(to_pause)).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": chain_id,
        "gas": 150000,
        "maxFeePerGas": w3.eth.gas_price * 2 // 1 if hasattr(w3.eth, 'gas_price') else w3.to_wei(2, 'gwei'),
        "maxPriorityFeePerGas": w3.to_wei(1, 'gwei'),
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    print_info(f"Tx setPaused: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status == 1:
        print_success("Status pause berhasil diubah")
    else:
        print_error("Gagal setPaused")

def call_sweep(chain_key, contract_addr):
    w3, chain_id = connect_chain(chain_key)
    if not w3: return
    abi, _ = compile_contract()
    if not abi: return
    pk = prompt_pk()
    if not pk: return
    acct = Account.from_key(pk)

    c = w3.eth.contract(address=checksum(w3, contract_addr), abi=abi)
    tx = c.functions.sweep().build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": chain_id,
        "gas": 150000,
        "maxFeePerGas": w3.eth.gas_price * 2 // 1 if hasattr(w3.eth, 'gas_price') else w3.to_wei(2, 'gwei'),
        "maxPriorityFeePerGas": w3.to_wei(1, 'gwei'),
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    print_info(f"Tx sweep: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status == 1:
        print_success("Sweep berhasil")
    else:
        print_error("Gagal sweep")

# ---------- UI ----------
def list_delegates():
    rules = load_json(RULES_FILE, {})
    if not rules:
        print_warning("Belum ada delegate terdaftar.")
        return
    lines = []
    for chain_key, items in rules.items():
        lines.append(f"{Colors.BOLD}{Colors.CYAN}Chain: {chain_key}{Colors.ENDC}")
        for i, it in enumerate(items, 1):
            lines.append(f"  {i}. Kontrak : {it['contract']}")
            lines.append(f"     Sink    : {it['sink']}")
            lines.append(f"     Owner   : {it['owner']}")
            lines.append(f"     Dibuat  : {it.get('created_at','-')}")
        lines.append("")
    print_box("📜 DAFTAR WALLET DELEGATE", lines, Colors.BLUE)

def set_global_sink():
    """Simpan default sink (opsional) di delegate_rules.json: field 'default_sink'"""
    sink = input(f"{Colors.YELLOW}Masukkan alamat wallet penampung (sink): {Colors.ENDC}").strip()
    rules = load_json(RULES_FILE, {})
    rules["default_sink"] = sink
    save_json(RULES_FILE, rules)
    print_success("Default sink tersimpan.")

def menu_delegate():
    while True:
        menu = [
            f"{Colors.CYAN}1){Colors.ENDC} Atur Wallet Penampung (Sink default)",
            f"{Colors.CYAN}2){Colors.ENDC} Buat Wallet Delegate (Deploy Kontrak)",
            f"{Colors.CYAN}3){Colors.ENDC} Daftar Wallet Delegate",
            f"{Colors.CYAN}4){Colors.ENDC} Nonaktifkan/Aktifkan Delegate (Pause/Unpause)",
            f"{Colors.CYAN}5){Colors.ENDC} Ubah Sink pada Delegate",
            f"{Colors.CYAN}6){Colors.ENDC} Sweep Manual (paksa kirim saldo kontrak ke sink)",
            f"{Colors.CYAN}7){Colors.ENDC} Hapus dari daftar (off-chain)",
            f"{Colors.CYAN}8){Colors.ENDC} Kembali"
        ]
        print_box("🧭 MENU DELEGATE WALLET (Smart Contract)", menu, Colors.MAGENTA)
        ch = input(f"{Colors.YELLOW}Pilih (1-8): {Colors.ENDC}").strip()

        if ch == "1":
            set_global_sink()

        elif ch == "2":
            chain_key = input(f"{Colors.YELLOW}Chain key (contoh: ethereum, bsc, polygon): {Colors.ENDC}").strip()
            rules = load_json(RULES_FILE, {})
            default_sink = rules.get("default_sink","").strip()
            sink = input(f"{Colors.YELLOW}Sink address [{default_sink}]: {Colors.ENDC}").strip() or default_sink
            if not sink:
                print_error("Sink address wajib diisi.")
                continue
            deploy_delegate(chain_key, sink)

        elif ch == "3":
            list_delegates()

        elif ch == "4":
            chain_key = input(f"{Colors.YELLOW}Chain key: {Colors.ENDC}").strip()
            contract_addr = input(f"{Colors.YELLOW}Alamat kontrak delegate: {Colors.ENDC}").strip()
            mode = input(f"{Colors.YELLOW}Ketik 'pause' atau 'unpause': {Colors.ENDC}").strip().lower()
            call_pause(chain_key, contract_addr, to_pause=True if mode == "pause" else False)

        elif ch == "5":
            chain_key = input(f"{Colors.YELLOW}Chain key: {Colors.ENDC}").strip()
            contract_addr = input(f"{Colors.YELLOW}Alamat kontrak delegate: {Colors.ENDC}").strip()
            new_sink = input(f"{Colors.YELLOW}Alamat sink baru: {Colors.ENDC}").strip()
            call_set_sink(chain_key, contract_addr, new_sink)

        elif ch == "6":
            chain_key = input(f"{Colors.YELLOW}Chain key: {Colors.ENDC}").strip()
            contract_addr = input(f"{Colors.YELLOW}Alamat kontrak delegate: {Colors.ENDC}").strip()
            call_sweep(chain_key, contract_addr)

        elif ch == "7":
            # hanya hapus dari file JSON lokal (tidak menyentuh on-chain)
            chain_key = input(f"{Colors.YELLOW}Chain key: {Colors.ENDC}").strip()
            contract_addr = input(f"{Colors.YELLOW}Alamat kontrak untuk dihapus dari daftar: {Colors.ENDC}").strip()
            rules = load_json(RULES_FILE, {})
            if chain_key in rules:
                before = len(rules[chain_key])
                rules[chain_key] = [r for r in rules[chain_key] if r["contract"].lower() != contract_addr.lower()]
                after = len(rules[chain_key])
                save_json(RULES_FILE, rules)
                if before != after:
                    print_success("Dihapus dari daftar lokal.")
                else:
                    print_warning("Tidak ditemukan di daftar.")
            else:
                print_warning("Chain belum ada di daftar.")

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
