#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_gen_random.py - Random Wallet Generator Mode
Generate random 12-word phrases and scan for balance
"""
import os
import sys
import json
import time
import random
from decimal import Decimal, getcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import requests
from eth_account import Account
from dotenv import load_dotenv

# Add utils to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))

from utils.colors import Colors
from utils.ui import print_box, print_loader, print_progress_bar, print_stats_box

try:
    from web3 import Web3, HTTPProvider
    WEB3_AVAILABLE = True
except Exception:
    Web3 = None
    HTTPProvider = None
    WEB3_AVAILABLE = False

try:
    from mnemonic import Mnemonic
    MNEMONIC_AVAILABLE = True
except Exception:
    MNEMONIC_AVAILABLE = False

try:
    from eth_account.hdaccount import key_from_seed, ETHEREUM_DEFAULT_PATH
    HDACCOUNT_AVAILABLE = True
except Exception:
    HDACCOUNT_AVAILABLE = False

getcontext().prec = 36
load_dotenv()

# ----------------- ENV / CONFIG -----------------
DEBANK_ACCESS_KEY = os.getenv("DEBANK_ACCESS_KEY")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "hasil.json")
EMPTY_WALLETS_FILE = os.getenv("EMPTY_WALLETS_FILE", "empty_wallets.json")
DEBANK_BASE_URL = os.getenv("DEBANK_BASE_URL", "https://pro-openapi.debank.com")
DEBANK_TIMEOUT = int(os.getenv("DEBANK_TIMEOUT", "15"))
DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() == "true"
DEFAULT_WORKERS = int(os.getenv("CONCURRENT_WORKERS", "16"))

# Telegram Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

BIP39_WORDLIST = None

# ----------------- Global Stats -----------------
STATS = {
    "total_generated": 0,
    "total_checked": 0,
    "wallets_found": 0,
    "empty_wallets": 0,
    "start_time": None,
    "last_found": None,
    "errors": 0
}

# ----------------- Telegram Integration -----------------
def send_telegram_message(message, parse_mode='HTML'):
    """Send message to Telegram"""
    if not TELEGRAM_ENABLED:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": parse_mode
        }
        response = requests.post(url, data=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        debug(f"Telegram send error: {e}")
        return False

def notify_wallet_found(wallet_data):
    """Send notification for wallet with balance"""
    message = f"""
🎉 <b>WALLET FOUND!</b> 🎉

💰 <b>Balance:</b> ${wallet_data['balance_usd']:.2f}
📍 <b>Address:</b> <code>{wallet_data['address']}</code>
🔑 <b>Private Key:</b> <code>{wallet_data['private_key']}</code>
📝 <b>Phrase:</b> <code>{wallet_data['phrase']}</code>

💎 <b>Coins:</b>
{chr(10).join([f"  • {sym}: {amt}" for sym, amt in wallet_data['coins'].items()])}

🌐 <b>Chains:</b> {', '.join(wallet_data['chains']) if wallet_data['chains'] else 'Multiple'}
📊 <b>Transactions:</b> {wallet_data['nonce']}
⏰ <b>Found at:</b> {wallet_data['found_at']}

<i>DIDINSKA Wallet Hunter v4.0</i>
"""
    return send_telegram_message(message)

def notify_empty_wallets_batch(count, total_checked):
    """Send batch notification for empty wallets"""
    message = f"""
📭 <b>Empty Wallets Report</b>

🔍 Scanned: {count} wallets
❌ Empty: {count}
📊 Total Checked: {total_checked}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

<i>Batch scan completed - DIDINSKA</i>
"""
    return send_telegram_message(message)

def notify_scan_start(count, workers):
    """Notify scan start"""
    message = f"""
🚀 <b>Scan Started</b>

🎯 Target: {count:,} wallets
⚡ Workers: {workers}
🕐 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

<i>DIDINSKA Wallet Hunter is running...</i>
"""
    return send_telegram_message(message)

def notify_scan_complete(stats):
    """Notify scan completion"""
    elapsed = time.time() - stats["start_time"] if stats["start_time"] else 0
    rate = stats["total_checked"] / elapsed if elapsed > 0 else 0
    
    message = f"""
✅ <b>Scan Completed</b>

📊 <b>Statistics:</b>
  • Generated: {stats['total_generated']:,}
  • Checked: {stats['total_checked']:,}
  • Found: {stats['wallets_found']}
  • Empty: {stats['empty_wallets']:,}
  • Speed: {rate:.2f} wallet/s
  • Runtime: {elapsed:.2f}s

<i>DIDINSKA Wallet Hunter</i>
"""
    return send_telegram_message(message)

# ----------------- Helpers -----------------
def debug(*args):
    if DEBUG_MODE:
        print(f"{Colors.GRAY}[DEBUG]{Colors.ENDC}", *args)

def load_json_file(path, expect_list=False):
    """Load JSON file"""
    try:
        if not os.path.exists(path):
            return [] if expect_list else {}
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        if expect_list:
            return data if isinstance(data, list) else []
        else:
            return data if isinstance(data, dict) else {}
            
    except json.JSONDecodeError as e:
        print(f"{Colors.RED}[!] JSON decode error in {path}: {e}{Colors.ENDC}")
        return [] if expect_list else {}
    except Exception as e:
        print(f"{Colors.RED}[!] Unexpected error loading {path}: {e}{Colors.ENDC}")
        return [] if expect_list else {}

def save_json_file(path, data):
    """Save data to JSON file"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"{Colors.RED}[!] Error saving {path}: {e}{Colors.ENDC}")

def append_to_results(wallet_data):
    """Append wallet with balance to hasil.json"""
    existing = load_json_file(OUTPUT_FILE, expect_list=True)
    existing.append(wallet_data)
    save_json_file(OUTPUT_FILE, existing)

def append_to_empty_wallets(wallet_data):
    """Append empty wallet to empty_wallets.json"""
    existing = load_json_file(EMPTY_WALLETS_FILE, expect_list=True)
    existing.append(wallet_data)
    save_json_file(EMPTY_WALLETS_FILE, existing)

# ----------------- Config / RPC setup -----------------
def inject_alchemy_key(cfg):
    """Replace ${ALCHEMY_API_KEY} placeholders"""
    if not ALCHEMY_API_KEY:
        debug("No ALCHEMY_API_KEY set")
        return cfg
    
    if not isinstance(cfg, dict):
        return cfg
    
    rpcs = cfg.get("rpcs", {})
    for k, v in rpcs.items():
        if not isinstance(v, dict):
            continue
        url = v.get("rpc_url", "")
        if "${ALCHEMY_API_KEY}" in url:
            v["rpc_url"] = url.replace("${ALCHEMY_API_KEY}", ALCHEMY_API_KEY)
    return cfg

def build_web3_clients(cfg, timeout=10):
    """Create Web3 clients for EVM chains"""
    clients = {}
    
    if not WEB3_AVAILABLE:
        debug("web3 not available")
        return clients
    
    if not isinstance(cfg, dict):
        return clients
    
    rpcs = cfg.get("rpcs", {})
    for chain, info in rpcs.items():
        if not isinstance(info, dict):
            continue
            
        if info.get("evm") is False:
            continue
            
        url = info.get("rpc_url")
        if not url:
            continue
            
        try:
            w3 = Web3(HTTPProvider(url, request_kwargs={"timeout": timeout}))
            if not w3.is_connected():
                continue
            clients[chain] = {
                "w3": w3, 
                "native_symbol": info.get("native_symbol", "ETH"),
                "name": info.get("name", chain)
            }
            print(f"{Colors.GREEN}✓{Colors.ENDC} Connected: {Colors.CYAN}{chain}{Colors.ENDC}")
        except Exception as e:
            print(f"{Colors.RED}✗{Colors.ENDC} Failed: {Colors.GRAY}{chain}{Colors.ENDC}")
    
    return clients

# ----------------- BIP39 Wordlist -----------------
def load_bip39_wordlist():
    """Load BIP39 wordlist"""
    global BIP39_WORDLIST
    
    if not MNEMONIC_AVAILABLE:
        print(f"{Colors.RED}[!] ERROR: mnemonic library not installed{Colors.ENDC}")
        return False
    
    try:
        mnemo = Mnemonic("english")
        BIP39_WORDLIST = mnemo.wordlist
        print(f"{Colors.GREEN}[+] BIP39 wordlist loaded: {len(BIP39_WORDLIST)} words{Colors.ENDC}")
        return True
    except Exception as e:
        print(f"{Colors.RED}[!] Failed to load BIP39 wordlist: {e}{Colors.ENDC}")
        return False

# ----------------- Wallet Generation -----------------
def generate_random_12word_phrase():
    """Generate random 12-word phrase"""
    if not BIP39_WORDLIST:
        return None
    
    words = [random.choice(BIP39_WORDLIST) for _ in range(12)]
    phrase = " ".join(words)
    
    STATS["total_generated"] += 1
    return phrase

def wallet_from_phrase(phrase, index=0):
    """Derive wallet from phrase"""
    if not MNEMONIC_AVAILABLE:
        return None
    
    try:
        mnemo = Mnemonic("english")
        seed = mnemo.to_seed(phrase, passphrase="")
        
        if HDACCOUNT_AVAILABLE:
            try:
                private_key = key_from_seed(seed, f"m/44'/60'/0'/0/{index}")
            except Exception:
                private_key = seed[:32]
        else:
            private_key = seed[:32]
        
        account = Account.from_key(private_key)
        
        return {
            "address": account.address,
            "private_key": private_key.hex() if isinstance(private_key, bytes) else private_key,
            "phrase": phrase
        }
        
    except Exception as e:
        debug(f"Error deriving wallet: {e}")
        return None

def create_wallet_random():
    """Create random wallet"""
    phrase = generate_random_12word_phrase()
    if not phrase:
        return None
    
    wallet = wallet_from_phrase(phrase, index=0)
    return wallet

# ----------------- API Calls -----------------
def fetch_debank_for_address(address):
    """Fetch from DeBank API"""
    if not DEBANK_ACCESS_KEY:
        return None
    
    headers = {"accept": "application/json", "AccessKey": DEBANK_ACCESS_KEY}
    url = f"{DEBANK_BASE_URL}/v1/user/all_token_list"
    params = {"id": address}
    
    try:
        r = requests.get(url, headers=headers, params=params, timeout=DEBANK_TIMEOUT)
        if r.status_code == 429:
            print(f"{Colors.YELLOW}⚠️  DeBank rate limit hit{Colors.ENDC}")
            return None
        if r.status_code != 200:
            return None
            
        data = r.json()
        items = data.get("data") or []
        coins = {}
        total_usd = Decimal(0)
        
        for t in items:
            try:
                sym = (t.get("symbol") or "").upper()
                amt = Decimal(str(t.get("amount", 0)))
                price = Decimal(str(t.get("price", 0))) if t.get("price") is not None else Decimal(0)
                if amt > 0 and sym:
                    coins[sym] = float(amt)
                    total_usd += amt * price
            except Exception:
                pass
                
        return {"coins": coins, "balance_usd": float(total_usd)}
    except Exception as e:
        debug(f"DeBank error: {e}")
        return None

def fetch_native_balance_for_chain(client, address):
    """Get native balance"""
    try:
        w3 = client["w3"]
        bal_wei = w3.eth.get_balance(address)
        val = Decimal(bal_wei) / Decimal(10 ** 18)
        return float(val)
    except Exception:
        return None

def fetch_nonce_for_chain(client, address):
    """Get transaction count"""
    try:
        w3 = client["w3"]
        nonce = w3.eth.get_transaction_count(address)
        return nonce
    except Exception:
        return 0

# ----------------- Wallet Checking -----------------
def check_single_wallet(wallet, web3_clients):
    """Check if wallet has balance or history"""
    if not wallet:
        return None
    
    address = wallet["address"]
    STATS["total_checked"] += 1
    
    result = {
        "address": address,
        "private_key": wallet["private_key"],
        "phrase": wallet.get("phrase", ""),
        "balance_usd": 0.0,
        "coins": {},
        "chains": [],
        "nonce": 0,
        "found_at": datetime.now().isoformat()
    }
    
    has_value = False
    
    # Check DeBank
    debank_data = fetch_debank_for_address(address)
    if debank_data:
        coins = debank_data.get("coins", {})
        balance_usd = debank_data.get("balance_usd", 0.0)
        if coins or balance_usd > 0:
            result["coins"].update(coins)
            result["balance_usd"] = balance_usd
            has_value = True
    
    # Check chains
    max_nonce = 0
    for chain, client in web3_clients.items():
        bal = fetch_native_balance_for_chain(client, address)
        if bal and bal > 0:
            sym = client.get("native_symbol", chain.upper())
            result["chains"].append(chain)
            prev = Decimal(str(result["coins"].get(sym, 0.0)))
            result["coins"][sym] = float(prev + Decimal(str(bal)))
            has_value = True
        
        nonce = fetch_nonce_for_chain(client, address)
        if nonce > max_nonce:
            max_nonce = nonce
    
    result["nonce"] = max_nonce
    
    if max_nonce > 0:
        has_value = True
    
    if has_value:
        return result
    
    return None

# ----------------- Batch Scanning -----------------
def scan_wallets_batch(count, web3_clients, max_workers=DEFAULT_WORKERS):
    """Scan wallets in batch"""
    
    # Print scan info
    scan_info = [
        f"🎯 Target       : {count:,} wallets",
        f"⚡ Workers      : {max_workers}",
        f"🔍 Mode         : Random Generation",
        f"📊 Search Space : 2^128 combinations",
        f"💾 Results      : {OUTPUT_FILE}",
        f"📭 Empty        : {EMPTY_WALLETS_FILE}",
    ]
    
    if TELEGRAM_ENABLED:
        scan_info.append(f"📱 Telegram     : {Colors.GREEN}✓ Enabled{Colors.ENDC}")
    else:
        scan_info.append(f"📱 Telegram     : {Colors.GRAY}✗ Disabled{Colors.ENDC}")
    
    print_box("🚀 SCAN CONFIGURATION", scan_info, Colors.YELLOW)
    
    # Send telegram notification
    if TELEGRAM_ENABLED:
        notify_scan_start(count, max_workers)
    
    STATS["start_time"] = time.time()
    found_count = 0
    empty_count = 0
    
    last_update = time.time()
    update_interval = 5
    last_telegram_batch = time.time()
    telegram_batch_interval = 60
    
    print(f"\n{Colors.CYAN}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.GREEN}🔍 SCANNING IN PROGRESS...{Colors.ENDC}")
    print(f"{Colors.CYAN}{'='*70}{Colors.ENDC}\n")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for i in range(count):
            wallet = create_wallet_random()
            if wallet:
                future = executor.submit(check_single_wallet, wallet, web3_clients)
                futures[future] = wallet
        
        for future in as_completed(futures):
            wallet = futures[future]
            try:
                result = future.result()
                
                if result:
                    # Wallet with balance found!
                    found_count += 1
                    STATS["wallets_found"] = found_count
                    STATS["last_found"] = result["address"]
                    
                    append_to_results(result)
                    
                    # Print found wallet
                    print(f"\n{Colors.GREEN}{'🎉' * 35}{Colors.ENDC}")
                    print(f"{Colors.BOLD}{Colors.LIGHT_GREEN}💰 WALLET FOUND #{found_count}!{Colors.ENDC}")
                    print(f"{Colors.GREEN}{'🎉' * 35}{Colors.ENDC}")
                    print(f"{Colors.CYAN}Phrase     :{Colors.ENDC} {Colors.WHITE}{result['phrase']}{Colors.ENDC}")
                    print(f"{Colors.CYAN}Address    :{Colors.ENDC} {Colors.YELLOW}{result['address']}{Colors.ENDC}")
                    print(f"{Colors.CYAN}Balance USD:{Colors.ENDC} {Colors.GREEN}${result['balance_usd']:.2f}{Colors.ENDC}")
                    print(f"{Colors.CYAN}Coins      :{Colors.ENDC} {Colors.WHITE}{result['coins']}{Colors.ENDC}")
                    print(f"{Colors.CYAN}Nonce      :{Colors.ENDC} {Colors.WHITE}{result['nonce']}{Colors.ENDC}")
                    print(f"{Colors.GREEN}{'🎉' * 35}{Colors.ENDC}\n")
                    
                    # Send to Telegram
                    if TELEGRAM_ENABLED:
                        notify_wallet_found(result)
                else:
                    # Empty wallet
                    empty_count += 1
                    STATS["empty_wallets"] = empty_count
                    
                    empty_data = {
                        "address": wallet["address"],
                        "phrase": wallet["phrase"],
                        "checked_at": datetime.now().isoformat()
                    }
                    append_to_empty_wallets(empty_data)
                
                # Update progress bar
                print_progress_bar(STATS["total_checked"], count)
                
                # Update stats periodically
                if time.time() - last_update > update_interval:
                    print()
                    print_stats_box(STATS)
                    last_update = time.time()
                
                # Send telegram batch for empty wallets
                if TELEGRAM_ENABLED and time.time() - last_telegram_batch > telegram_batch_interval:
                    if empty_count > 0:
                        notify_empty_wallets_batch(empty_count, STATS["total_checked"])
                    last_telegram_batch = time.time()
                    
            except Exception as e:
                STATS["errors"] += 1
                debug(f"Error processing wallet: {e}")
    
    # Final stats
    print("\n")
    print_stats_box(STATS)
    
    completion_info = [
        f"✅ Scan completed successfully!",
        f"💰 Found wallets  : {found_count}",
        f"📭 Empty wallets  : {empty_count}",
        f"💾 Saved to       : {OUTPUT_FILE}",
        f"📝 Empty saved to : {EMPTY_WALLETS_FILE}",
    ]
    print_box("🏁 SCAN COMPLETE", completion_info, Colors.GREEN)
    
    # Send telegram completion
    if TELEGRAM_ENABLED:
        notify_scan_complete(STATS)

# ----------------- Menu -----------------
def menu_loop(cfg, web3_clients):
    """Main menu for random generation"""
    
    while True:
        menu_content = [
            f"{Colors.CYAN}1){Colors.ENDC} Quick scan      {Colors.GRAY}(10 wallets){Colors.ENDC}",
            f"{Colors.CYAN}2){Colors.ENDC} Medium scan     {Colors.GRAY}(100 wallets){Colors.ENDC}",
            f"{Colors.CYAN}3){Colors.ENDC} Large scan      {Colors.GRAY}(1,000 wallets){Colors.ENDC}",
            f"{Colors.CYAN}4){Colors.ENDC} Mega scan       {Colors.GRAY}(10,000 wallets){Colors.ENDC}",
            f"{Colors.CYAN}5){Colors.ENDC} Custom scan     {Colors.GRAY}(enter amount){Colors.ENDC}",
            f"{Colors.CYAN}6){Colors.ENDC} View statistics {Colors.GRAY}(live stats){Colors.ENDC}",
            f"{Colors.CYAN}7){Colors.ENDC} Exit            {Colors.GRAY}(back to main menu){Colors.ENDC}",
        ]
        print_box("📋 RANDOM GENERATOR MENU", menu_content, Colors.BLUE)
        
        ch = input(f"{Colors.YELLOW}Choose (1-7): {Colors.ENDC}").strip()
        
        if ch == "1":
            n = 10
        elif ch == "2":
            n = 100
        elif ch == "3":
            n = 1000
        elif ch == "4":
            n = 10000
        elif ch == "5":
            try:
                n = int(input(f"{Colors.CYAN}Enter number of wallets to scan: {Colors.ENDC}"))
                if n < 1:
                    print(f"{Colors.RED}[!] Number must be >= 1{Colors.ENDC}")
                    continue
            except ValueError:
                print(f"{Colors.RED}[!] Invalid number{Colors.ENDC}")
                continue
        elif ch == "6":
            print_stats_box(STATS)
            continue
        elif ch == "7":
            print(f"\n{Colors.GREEN}[+] Returning to main menu...{Colors.ENDC}\n")
            break
        else:
            print(f"{Colors.RED}[!] Invalid choice{Colors.ENDC}")
            continue
        
        # Confirm large scans
        if n >= 10000:
            confirm = input(f"\n{Colors.YELLOW}⚠️  Scanning {n:,} wallets may take a while. Continue? (yes/no): {Colors.ENDC}").strip().lower()
            if confirm != "yes":
                print(f"{Colors.YELLOW}[+] Cancelled.{Colors.ENDC}\n")
                continue
        
        # Run scan
        max_workers = cfg.get("concurrent_workers") or DEFAULT_WORKERS
        scan_wallets_batch(n, web3_clients, max_workers=int(max_workers))

# ----------------- Main -----------------
def main():
    """Main function"""
    print(f"\n{Colors.CYAN}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.GREEN}🎲 RANDOM WALLET GENERATOR MODE{Colors.ENDC}")
    print(f"{Colors.CYAN}{'='*70}{Colors.ENDC}\n")
    
    # Check dependencies
    if not MNEMONIC_AVAILABLE:
        print(f"{Colors.RED}[!] CRITICAL ERROR: 'mnemonic' library not installed!{Colors.ENDC}")
        print(f"{Colors.YELLOW}    Install with: pip install mnemonic{Colors.ENDC}\n")
        return
    
    # Load BIP39 wordlist
    print_loader("Loading BIP39 wordlist", 1)
    if not load_bip39_wordlist():
        print(f"{Colors.RED}[!] Failed to load BIP39 wordlist. Exiting.{Colors.ENDC}")
        return
    
    # Load config
    print(f"{Colors.CYAN}[+] Loading configuration...{Colors.ENDC}")
    print_loader("Reading config.json", 1)
    
    cfg = load_json_file(CONFIG_FILE, expect_list=False)
    
    if not cfg or not isinstance(cfg, dict):
        print(f"{Colors.RED}[!] '{CONFIG_FILE}' empty, missing, or invalid format.{Colors.ENDC}")
        print(f"{Colors.YELLOW}[!] Please create a valid config.json file.{Colors.ENDC}")
        return
    
    print(f"{Colors.GREEN}[+] Config loaded successfully{Colors.ENDC}")
    
    # Inject API keys
    cfg = inject_alchemy_key(cfg)
    
    # Build RPC connections
    print(f"\n{Colors.CYAN}[+] Initializing RPC connections...{Colors.ENDC}")
    print_loader("Connecting to blockchains", 2)
    
    web3_clients = build_web3_clients(cfg)
    
    if not web3_clients:
        print(f"{Colors.YELLOW}[!] Warning: No RPC connections established{Colors.ENDC}")
    else:
        print(f"{Colors.GREEN}[+] Connected to {len(web3_clients)} chain(s){Colors.ENDC}\n")
    
    # API Status
    api_status = []
    
    if DEBANK_ACCESS_KEY:
        api_status.append(f"{Colors.GREEN}✓{Colors.ENDC} DeBank API: {Colors.GREEN}Connected{Colors.ENDC}")
    else:
        api_status.append(f"{Colors.YELLOW}⚠{Colors.ENDC} DeBank API: {Colors.GRAY}Not configured{Colors.ENDC}")
    
    if ALCHEMY_API_KEY:
        api_status.append(f"{Colors.GREEN}✓{Colors.ENDC} Alchemy API: {Colors.GREEN}Connected{Colors.ENDC}")
    else:
        api_status.append(f"{Colors.YELLOW}⚠{Colors.ENDC} Alchemy API: {Colors.GRAY}Not configured{Colors.ENDC}")
    
    if TELEGRAM_ENABLED:
        api_status.append(f"{Colors.GREEN}✓{Colors.ENDC} Telegram Bot: {Colors.GREEN}Enabled{Colors.ENDC}")
    else:
        api_status.append(f"{Colors.GRAY}✗{Colors.ENDC} Telegram Bot: {Colors.GRAY}Disabled{Colors.ENDC}")
    
    print_box("🔌 API STATUS", api_status, Colors.BLUE)
    
    # System info
    system_info = [
        f"💾 Output File    : {OUTPUT_FILE}",
        f"📭 Empty File     : {EMPTY_WALLETS_FILE}",
        f"⚡ Workers        : {cfg.get('concurrent_workers', DEFAULT_WORKERS)}",
        f"🔍 Mode           : Random Generation",
        f"📊 Search Space   : 2^128 combinations",
    ]
    print_box("⚙️  SYSTEM CONFIGURATION", system_info, Colors.MAGENTA)
    
    # Run menu
    menu_loop(cfg, web3_clients)

# ----------------- Entry Point -----------------
def run():
    """Entry point when called from main menu"""
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}[!] Interrupted. Returning to main menu...{Colors.ENDC}\n")
    except Exception as e:
        print(f"\n{Colors.RED}[!] Unexpected error: {e}{Colors.ENDC}")
        if DEBUG_MODE:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    run()
