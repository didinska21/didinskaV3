#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_delegate.py - Smart-Contract based Delegate Wallet (Menu 3)
Fitur:
- Deploy kontrak auto-forward (DelegateWallet) 1 chain atau multi-chain (All Chains)
- Mode "Cek Saldo" → hanya chain dengan saldo cukup yang tampil
- Kelola sink, pause/unpause, sweep
- UI panah (questionary) bila terpasang; fallback ke input()
- Logging ke delegate.log + output CLI yang jelas
"""

import os, sys, json, time, logging
from datetime import datetime
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3, HTTPProvider

getcontext().prec = 28

# ==== Web3 v6/v7 POA middleware compatibility ====
try:
    from web3.middleware import ExtraDataToPOAMiddleware as POA_MIDDLEWARE
except ImportError:
    from web3.middleware import geth_poa_middleware as POA_MIDDLEWARE

# ==== Optional UI ====
try:
    import questionary
    from questionary import Choice as QChoice
except Exception:
    questionary = None
    QChoice = None

# ==== Path setup ====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# utils/*
from utils.colors import Colors
from utils.ui import (
    print_box, print_loader, print_progress_bar, print_stats_box,
    print_section_header, print_warning, print_error, print_success, print_info
)

load_dotenv(os.path.join(BASE_DIR, ".env"))

CONFIG_FILE = os.getenv("CONFIG_FILE", os.path.join(BASE_DIR, "config.json"))
CHAIN_FILE  = os.path.join(BASE_DIR, "utils", "chain.json")
RULES_FILE  = os.path.join(BASE_DIR, "delegate_rules.json")
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

# ---------- Kontrak Solidity ----------
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
    bool public paused;
    uint256 private _guard;
    modifier onlyOwner(){require(msg.sender==owner,"Not owner");_; }
    modifier nonReentrant(){require(_guard==0,"Reentrancy");_guard=1;_;_guard=0;}
    modifier notPaused(){require(!paused,"Paused");_; }
    constructor(address _sink){require(_sink!=address(0),"Invalid sink");owner=msg.sender;sink=_sink;}
    function setSink(address _sink) external onlyOwner {require(_sink!=address(0),"Invalid sink");address old=sink;sink=_sink;emit SinkUpdated(old,_sink);}
    function setPaused(bool _p) external onlyOwner {paused=_p;emit Paused(msg.sender,_p);}
    function sweep() public nonReentrant {uint256 bal=address(this).balance;if(bal==0)return;(bool ok,)=payable(sink).call{value:bal}("");require(ok,"Forward failed");emit Forwarded(sink,bal);}
    receive() external payable notPaused nonReentrant {emit Received(msg.sender,msg.value);uint256 bal=address(this).balance;if(bal==0)return;(bool ok,)=payable(sink).call{value:bal}("");require(ok,"Forward failed");emit Forwarded(sink,bal);}
}
"""

# ---------- Helpers ----------
def mask_middle(s: str, head: int = 6, tail: int = 6, stars: int = 5) -> str:
    if not s: return s
    if len(s) <= head + tail: return s
    return s[:head] + ("*" * stars) + s[-tail:]

def load_json(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)

def compile_contract():
    from solcx import compile_standard, install_solc, set_solc_version
    install_solc("0.8.24"); set_solc_version("0.8.24")
    src = {"language": "Solidity","sources":{"DW.sol":{"content":SOL_SOURCE}},
           "settings":{"optimizer":{"enabled":True},"outputSelection":{"*":{"*":["abi","evm.bytecode"]}}}}
    out = compile_standard(src)
    c = out["contracts"]["DW.sol"]["DelegateWallet"]
    return c["abi"], c["evm"]["bytecode"]["object"]

def _guess_fees(w3):
    try:
        base = w3.eth.gas_price
        return {"maxFeePerGas": int(base*2), "maxPriorityFeePerGas": w3.to_wei(1,"gwei")}
    except: return {"gasPrice": w3.to_wei(3,"gwei")}

def connect_chain(chain_key):
    chains = load_json(CHAIN_FILE, {})
    info = chains.get(chain_key)
    if not info: return None, None
    rpcs = info.get("rpc_urls") or info.get("rpc_url")
    if isinstance(rpcs, str): rpcs = [rpcs]
    alchemy = os.getenv("ALCHEMY_API_KEY")
    for rpc in rpcs:
        rpc_url = rpc.replace("${ALCHEMY_API_KEY}", alchemy) if "${ALCHEMY_API_KEY}" in rpc and alchemy else rpc
        try:
            w3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout":20}))
            try: w3.middleware_onion.inject(POA_MIDDLEWARE, layer=0)
            except: pass
            if w3.is_connected(): return w3, w3.eth.chain_id
        except: continue
    print_error(f"Gagal konek RPC {chain_key}"); return None, None

def _wei_to_native(wei, decimals): return Decimal(wei)/(Decimal(10)**Decimal(decimals))

def get_chain_meta(chain_key):
    info = load_json(CHAIN_FILE, {}).get(chain_key, {})
    return {"symbol": info.get("native_symbol","ETH"),
            "decimals": int(info.get("decimals",18)),
            "threshold": Decimal(str(info.get("threshold_native",0)))}

def get_native_balance(chain_key, address):
    w3, _ = connect_chain(chain_key); meta = get_chain_meta(chain_key)
    if not w3: return {"ok":False,"balance":Decimal(0),**meta}
    try:
        b = _wei_to_native(w3.eth.get_balance(address), meta["decimals"])
        return {"ok":True,"balance":b,**meta}
    except Exception as e:
        return {"ok":False,"balance":Decimal(0),"err":str(e),**meta}

def show_balances(address):
    keys = list(load_json(CHAIN_FILE, {}).keys()); rows=[]; eligible=[]
    for ck in keys:
        r = get_native_balance(ck, address)
        bal, need = r["balance"], r["threshold"]
        st = "OK" if r["ok"] and bal>=need else ("LOW" if r["ok"] else "ERR")
        rows.append(f"{ck:<10} | {bal:>15} {r['symbol']:<4} | need ≥ {need} | {st}")
        if st=="OK": eligible.append(ck)
    print_box("🔎 SALDO DEPLOYER", rows, Colors.BLUE)
    if eligible: print_success("Eligible: " + ", ".join(eligible))
    else: print_warning("Tidak ada saldo cukup.")
    return eligible

def prompt_pk():
    raw=input(f"{Colors.YELLOW}Masukkan Private Key (ditampilkan & disensor): {Colors.ENDC}").strip().replace("0x","")
    if len(raw)!=64: print_error("PK tidak valid"); return None
    pk="0x"+raw; print_info(f"PK: {mask_middle(pk)}"); return pk

def checksum(w3, a): 
    try: return w3.to_checksum_address(a)
    except: return a

# ---------- Deploy ----------
def _deploy_on_chain(chain_key, sink, pk):
    abi, bytecode = compile_contract()
    w3, cid = connect_chain(chain_key)
    if not w3: return False, "rpc_failed"
    acct=Account.from_key(pk)
    tx=w3.eth.contract(abi=abi,bytecode=bytecode).constructor(checksum(w3,sink)).build_transaction({
        "from":acct.address,"nonce":w3.eth.get_transaction_count(acct.address),
        "chainId":cid,"gas":700000,**_guess_fees(w3)})
    signed=acct.sign_transaction(tx)
    h=w3.eth.send_raw_transaction(signed.rawTransaction)
    print_info(f"[{chain_key}] TX deploy: {h.hex()}")
    r=w3.eth.wait_for_transaction_receipt(h)
    if r.status!=1: print_error(f"[{chain_key}] Gagal"); return False,"fail"
    addr=r.contractAddress
    print_success(f"[{chain_key}] DelegateWallet: {addr}")
    data=load_json(RULES_FILE,{})
    data.setdefault(chain_key,[]).append({
        "contract":addr,"sink":sink,"owner":acct.address,"created_at":datetime.now().isoformat()})
    save_json(RULES_FILE,data)
    return True, addr

# ---------- Main Deploy Flow ----------
def deploy_delegate_interactive():
    rules=load_json(RULES_FILE,{}); default_sink=rules.get("default_sink","").strip()
    sink=input(f"{Colors.YELLOW}Sink address [{default_sink}]: {Colors.ENDC}").strip() or default_sink
    if not sink: print_error("Sink wajib diisi"); return
    print_info(f"Sink: {mask_middle(sink)}")
    pk=prompt_pk(); 
    if not pk: return
    acct=Account.from_key(pk)
    print_info(f"Deployer: {mask_middle(acct.address)}")
    eligible=show_balances(acct.address)
    if not eligible: return
    if questionary:
        sel=questionary.checkbox("Pilih chain (Space=pilih, Enter=ok):",
            choices=[{"name":"ALL ELIGIBLE","value":"__ALL__"}]+[{"name":c,"value":c} for c in eligible]).ask()
        chains=eligible if "__ALL__" in sel else sel
    else:
        ans=input("Ketik 'all' atau pilih chain, pisah koma: ").strip().lower()
        chains=eligible if ans in ("all","*") else [x.strip() for x in ans.split(",") if x.strip() in eligible]
    if not chains: print_warning("Tidak ada chain dipilih"); return
    print_info(f"Mulai deploy di {len(chains)} chain...")
    total=len(chains); ok=0
    for i,ck in enumerate(chains,1):
        print_info(f"({i}/{total}) {ck}..."); 
        s,_=_deploy_on_chain(ck,sink,pk)
        ok+=1 if s else 0
    print_stats_box("Ringkasan Deploy",[
        ("Total dipilih",str(total)),("Berhasil",str(ok)),
        ("Gagal",str(total-ok)),("Sink",mask_middle(sink))])

# ---------- Menu ----------
def menu_delegate():
    while True:
        print_box("🧭 MENU DELEGATE WALLET",[
            "1) Atur Sink default","2) Buat Delegate (cek saldo otomatis)",
            "3) Daftar Delegate","4) Keluar"],Colors.MAGENTA)
        ch=input(f"{Colors.YELLOW}Pilih (1-4): {Colors.ENDC}").strip()
        if ch=="1":
            sink=input("Masukkan sink address: ").strip()
            r=load_json(RULES_FILE,{}); r["default_sink"]=sink; save_json(RULES_FILE,r)
            print_success("Sink default disimpan.")
        elif ch=="2": deploy_delegate_interactive()
        elif ch=="3":
            data=load_json(RULES_FILE,{})
            lines=[]
            for ck,items in data.items():
                if ck=="default_sink": continue
                lines.append(f"{ck}:")
                for it in items:
                    lines.append(f"  - {it['contract']} → {it['sink']} ({it['owner']})")
            print_box("DAFTAR DELEGATE",lines,Colors.CYAN)
        elif ch=="4": break
        else: print_error("Pilihan tidak valid.")

def run():
    print_section_header("DELEGATE WALLET (Smart Contract)")
    menu_delegate()

if __name__=="__main__": run()
