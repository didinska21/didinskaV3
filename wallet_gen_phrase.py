#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_gen_phrase.py - Phrase Finder Mode
Search wallets by partial phrase with wildcards
"""
import os
import sys
import json
import time
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dotenv import load_dotenv

# Add utils to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))

from utils.colors import Colors
from utils.ui import print_box, print_loader, print_progress_bar, print_stats_box

try:
    from mnemonic import Mnemonic
    MNEMONIC_AVAILABLE = True
except:
    MNEMONIC_AVAILABLE = False
    print(f"{Colors.RED}[!] mnemonic library required!{Colors.ENDC}")

try:
    from eth_account import Account
    from eth_account.hdaccount import key_from_seed
    ACCOUNT_AVAILABLE = True
except:
    ACCOUNT_AVAILABLE = False

try:
    from web3 import Web3, HTTPProvider
    WEB3_AVAILABLE = True
except:
    WEB3_AVAILABLE = False

load_dotenv()

# Config
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "phrase_found.json")
EMPTY_FILE = os.getenv("EMPTY_WALLETS_FILE", "phrase_empty.json")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
DEFAULT_WORKERS = int(os.getenv("CONCURRENT_WORKERS", "16"))

# Globals
BIP39_WORDLIST = None
STATS = {
    "total_generated": 0,
    "total_checked": 0,
    "wallets_found": 0,
    "empty_wallets": 0,
    "start_time": None,
    "last_found": None
}

# -------------------- Helpers --------------------
def load_bip39_wordlist():
    """Load BIP39 wordlist"""
    global BIP39_WORDLIST
    if not MNEMONIC_AVAILABLE:
        return False
    try:
        mnemo = Mnemonic("english")
        BIP39_WORDLIST = mnemo.wordlist
        return True
    except:
        return False

def load_json_file(path):
    """Load JSON file"""
    try:
        if not os.path.exists(path):
            return {}
        with open(path, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_json_file(path, data):
    """Save to JSON"""
    try:
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"{Colors.RED}[!] Save error: {e}{Colors.ENDC}")

def append_result(wallet_data, found=True):
    """Append result to file"""
    file = OUTPUT_FILE if found else EMPTY_FILE
    try:
        existing = []
        if os.path.exists(file):
            with open(file, 'r') as f:
                existing = json.load(f)
        existing.append(wallet_data)
        with open(file, 'w') as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"{Colors.RED}[!] Append error: {e}{Colors.ENDC}")

def send_telegram(message):
    """Send Telegram notification"""
    if not TELEGRAM_ENABLED:
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        r = requests.post(url, data=data, timeout=10)
        return r.status_code == 200
    except:
        return False

# -------------------- Wallet Functions --------------------
def wallet_from_phrase(phrase_list):
    """Generate wallet from phrase list"""
    if not MNEMONIC_AVAILABLE or not ACCOUNT_AVAILABLE:
        return None
    
    try:
        phrase = " ".join(phrase_list)
        mnemo = Mnemonic("english")
        seed = mnemo.to_seed(phrase, passphrase="")
        
        try:
            private_key = key_from_seed(seed, "m/44'/60'/0'/0/0")
        except:
            private_key = seed[:32]
        
        account = Account.from_key(private_key)
        
        return {
            "address": account.address,
            "private_key": private_key.hex() if isinstance(private_key, bytes) else private_key,
            "phrase": phrase
        }
    except Exception as e:
        return None

def check_balance(wallet, web3_clients):
    """Check wallet balance"""
    if not wallet:
        return None
    
    address = wallet["address"]
    result = {
        "address": address,
        "private_key": wallet["private_key"],
        "phrase": wallet["phrase"],
        "balance_usd": 0.0,
        "coins": {},
        "chains": [],
        "nonce": 0,
        "found_at": datetime.now().isoformat()
    }
    
    has_value = False
    max_nonce = 0
    
    # Check chains
    for chain, client in web3_clients.items():
        try:
            w3 = client["w3"]
            
            # Check balance
            bal_wei = w3.eth.get_balance(address)
            if bal_wei > 0:
                bal = float(Decimal(bal_wei) / Decimal(10**18))
                sym = client.get("native_symbol", "ETH")
                result["coins"][sym] = bal
                result["chains"].append(chain)
                has_value = True
            
            # Check nonce
            nonce = w3.eth.get_transaction_count(address)
            if nonce > max_nonce:
                max_nonce = nonce
        except:
            pass
    
    result["nonce"] = max_nonce
    if max_nonce > 0:
        has_value = True
    
    return result if has_value else None

# -------------------- Search Functions --------------------
def search_1_wildcard(known_words, wildcard_positions, web3_clients, max_workers=DEFAULT_WORKERS):
    """Search with 1 unknown word"""
    pos = wildcard_positions[0]
    total = len(BIP39_WORDLIST)
    
    info = [
        f"🎯 Mode         : Single word search",
        f"📍 Position     : {pos + 1}",
        f"🔢 Combinations : {total:,}",
        f"⏱️  Est. Time    : ~3 minutes (10 wallet/s)",
    ]
    print_box("🔍 SEARCH CONFIGURATION", info, Colors.CYAN)
    
    confirm = input(f"{Colors.YELLOW}Start search? (yes/no): {Colors.ENDC}").strip().lower()
    if confirm != "yes":
        return
    
    STATS["start_time"] = time.time()
    found_count = 0
    
    print(f"\n{Colors.GREEN}🚀 Searching...{Colors.ENDC}\n")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        
        for word in BIP39_WORDLIST:
            phrase = known_words.copy()
            phrase[pos] = word
            
            wallet = wallet_from_phrase(phrase)
            if wallet:
                future = executor.submit(check_balance, wallet, web3_clients)
                futures[future] = wallet
                STATS["total_generated"] += 1
        
        for future in as_completed(futures):
            wallet = futures[future]
            STATS["total_checked"] += 1
            
            try:
                result = future.result()
                
                if result:
                    found_count += 1
                    STATS["wallets_found"] = found_count
                    STATS["last_found"] = result["address"]
                    
                    append_result(result, found=True)
                    
                    print(f"\n{Colors.GREEN}{'🎉'*35}{Colors.ENDC}")
                    print(f"{Colors.BOLD}{Colors.GREEN}💰 WALLET FOUND!{Colors.ENDC}")
                    print(f"{Colors.GREEN}{'🎉'*35}{Colors.ENDC}")
                    print(f"{Colors.CYAN}Phrase  :{Colors.ENDC} {result['phrase']}")
                    print(f"{Colors.CYAN}Address :{Colors.ENDC} {result['address']}")
                    print(f"{Colors.CYAN}Balance :{Colors.ENDC} {result['coins']}")
                    print(f"{Colors.GREEN}{'🎉'*35}{Colors.ENDC}\n")
                    
                    if TELEGRAM_ENABLED:
                        msg = f"🎉 <b>PHRASE FOUND!</b>\n\n"
                        msg += f"📝 Phrase: <code>{result['phrase']}</code>\n"
                        msg += f"📍 Address: <code>{result['address']}</code>\n"
                        msg += f"💰 Balance: {result['coins']}"
                        send_telegram(msg)
                else:
                    STATS["empty_wallets"] += 1
                    empty_data = {
                        "address": wallet["address"],
                        "phrase": wallet["phrase"],
                        "checked_at": datetime.now().isoformat()
                    }
                    append_result(empty_data, found=False)
                
                print_progress_bar(STATS["total_checked"], total)
            except:
                pass
    
    print("\n")
    print_stats_box(STATS)

def search_2_wildcards(known_words, wildcard_positions, web3_clients, max_workers=DEFAULT_WORKERS):
    """Search with 2 unknown words"""
    pos1, pos2 = wildcard_positions[0], wildcard_positions[1]
    total = len(BIP39_WORDLIST) ** 2
    
    info = [
        f"🎯 Mode         : Two words search",
        f"📍 Positions    : {pos1 + 1}, {pos2 + 1}",
        f"🔢 Combinations : {total:,}",
        f"⏱️  Est. Time    : ~5 days (10 wallet/s)",
        f"",
        f"{Colors.YELLOW}⚠️  This will take a LONG time!{Colors.ENDC}",
    ]
    print_box("🔍 SEARCH CONFIGURATION", info, Colors.CYAN)
    
    confirm = input(f"{Colors.YELLOW}Continue? Type 'I UNDERSTAND': {Colors.ENDC}").strip()
    if confirm != "I UNDERSTAND":
        return
    
    # Ask for limit
    limit_input = input(f"{Colors.CYAN}Set max combinations to check (or 'all'): {Colors.ENDC}").strip()
    if limit_input.lower() != 'all':
        try:
            max_combinations = int(limit_input)
        except:
            print(f"{Colors.RED}[!] Invalid number{Colors.ENDC}")
            return
    else:
        max_combinations = total
    
    STATS["start_time"] = time.time()
    found_count = 0
    checked = 0
    
    print(f"\n{Colors.GREEN}🚀 Searching (max {max_combinations:,} combinations)...{Colors.ENDC}\n")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        
        for word1 in BIP39_WORDLIST:
            for word2 in BIP39_WORDLIST:
                if checked >= max_combinations:
                    break
                
                phrase = known_words.copy()
                phrase[pos1] = word1
                phrase[pos2] = word2
                
                wallet = wallet_from_phrase(phrase)
                if wallet:
                    future = executor.submit(check_balance, wallet, web3_clients)
                    futures[future] = wallet
                    STATS["total_generated"] += 1
                    checked += 1
            
            if checked >= max_combinations:
                break
        
        for future in as_completed(futures):
            wallet = futures[future]
            STATS["total_checked"] += 1
            
            try:
                result = future.result()
                
                if result:
                    found_count += 1
                    STATS["wallets_found"] = found_count
                    STATS["last_found"] = result["address"]
                    
                    append_result(result, found=True)
                    
                    print(f"\n{Colors.GREEN}{'🎉'*35}{Colors.ENDC}")
                    print(f"{Colors.BOLD}{Colors.GREEN}💰 WALLET FOUND!{Colors.ENDC}")
                    print(f"{Colors.GREEN}{'🎉'*35}{Colors.ENDC}")
                    print(f"{Colors.CYAN}Phrase  :{Colors.ENDC} {result['phrase']}")
                    print(f"{Colors.CYAN}Address :{Colors.ENDC} {result['address']}")
                    print(f"{Colors.CYAN}Balance :{Colors.ENDC} {result['coins']}")
                    print(f"{Colors.GREEN}{'🎉'*35}{Colors.ENDC}\n")
                    
                    if TELEGRAM_ENABLED:
                        msg = f"🎉 <b>PHRASE FOUND!</b>\n\n"
                        msg += f"📝 Phrase: <code>{result['phrase']}</code>\n"
                        msg += f"📍 Address: <code>{result['address']}</code>\n"
                        msg += f"💰 Balance: {result['coins']}"
                        send_telegram(msg)
                else:
                    STATS["empty_wallets"] += 1
                
                print_progress_bar(STATS["total_checked"], max_combinations)
            except:
                pass
    
    print("\n")
    print_stats_box(STATS)

def search_3_wildcards(known_words, wildcard_positions, web3_clients, max_workers=DEFAULT_WORKERS):
    """Search with 3 unknown words"""
    pos1, pos2, pos3 = wildcard_positions[0], wildcard_positions[1], wildcard_positions[2]
    total = len(BIP39_WORDLIST) ** 3
    
    info = [
        f"🎯 Mode         : Three words search",
        f"📍 Positions    : {pos1 + 1}, {pos2 + 1}, {pos3 + 1}",
        f"🔢 Combinations : {total:,}",
        f"⏱️  Est. Time    : ~27 YEARS (10 wallet/s)",
        f"",
        f"{Colors.RED}⚠️  WARNING: This is EXTREMELY time consuming!{Colors.ENDC}",
        f"{Colors.RED}⚠️  Recommend using GPU acceleration or distributed computing{Colors.ENDC}",
    ]
    print_box("🔍 SEARCH CONFIGURATION", info, Colors.RED)
    
    confirm = input(f"{Colors.RED}Continue? Type 'YES I AM SURE': {Colors.ENDC}").strip()
    if confirm != "YES I AM SURE":
        return
    
    # Must set limit
    limit_input = input(f"{Colors.CYAN}Set max combinations to check: {Colors.ENDC}").strip()
    try:
        max_combinations = int(limit_input)
        if max_combinations < 1:
            print(f"{Colors.RED}[!] Must be >= 1{Colors.ENDC}")
            return
    except:
        print(f"{Colors.RED}[!] Invalid number{Colors.ENDC}")
        return
    
    STATS["start_time"] = time.time()
    found_count = 0
    checked = 0
    
    print(f"\n{Colors.GREEN}🚀 Searching (max {max_combinations:,} combinations)...{Colors.ENDC}\n")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        
        for word1 in BIP39_WORDLIST:
            for word2 in BIP39_WORDLIST:
                for word3 in BIP39_WORDLIST:
                    if checked >= max_combinations:
                        break
                    
                    phrase = known_words.copy()
                    phrase[pos1] = word1
                    phrase[pos2] = word2
                    phrase[pos3] = word3
                    
                    wallet = wallet_from_phrase(phrase)
                    if wallet:
                        future = executor.submit(check_balance, wallet, web3_clients)
                        futures[future] = wallet
                        STATS["total_generated"] += 1
                        checked += 1
                
                if checked >= max_combinations:
                    break
            
            if checked >= max_combinations:
                break
        
        for future in as_completed(futures):
            wallet = futures[future]
            STATS["total_checked"] += 1
            
            try:
                result = future.result()
                
                if result:
                    found_count += 1
                    STATS["wallets_found"] = found_count
                    STATS["last_found"] = result["address"]
                    
                    append_result(result, found=True)
                    
                    print(f"\n{Colors.GREEN}{'🎉'*35}{Colors.ENDC}")
                    print(f"{Colors.BOLD}{Colors.GREEN}💰 WALLET FOUND!{Colors.ENDC}")
                    print(f"{Colors.GREEN}{'🎉'*35}{Colors.ENDC}")
                    print(f"{Colors.CYAN}Phrase  :{Colors.ENDC} {result['phrase']}")
                    print(f"{Colors.CYAN}Address :{Colors.ENDC} {result['address']}")
                    print(f"{Colors.CYAN}Balance :{Colors.ENDC} {result['coins']}")
                    print(f"{Colors.GREEN}{'🎉'*35}{Colors.ENDC}\n")
                    
                    if TELEGRAM_ENABLED:
                        msg = f"🎉 <b>PHRASE FOUND!</b>\n\n"
                        msg += f"📝 Phrase: <code>{result['phrase']}</code>\n"
                        msg += f"📍 Address: <code>{result['address']}</code>\n"
                        msg += f"💰 Balance: {result['coins']}"
                        send_telegram(msg)
                else:
                    STATS["empty_wallets"] += 1
                
                print_progress_bar(STATS["total_checked"], max_combinations)
            except:
                pass
    
    print("\n")
    print_stats_box(STATS)

# -------------------- Main Menu --------------------
def phrase_finder_menu(web3_clients):
    """Phrase finder sub menu"""
    while True:
        menu_items = [
            f"{Colors.CYAN}1){Colors.ENDC} Search 1 word  {Colors.GRAY}(~2,048 combinations){Colors.ENDC}",
            f"   {Colors.WHITE}→ Know 11 words, find 1 missing word{Colors.ENDC}",
            f"",
            f"{Colors.CYAN}2){Colors.ENDC} Search 2 words {Colors.GRAY}(~4.2 million combinations){Colors.ENDC}",
            f"   {Colors.WHITE}→ Know 10 words, find 2 missing words{Colors.ENDC}",
            f"",
            f"{Colors.CYAN}3){Colors.ENDC} Search 3 words {Colors.GRAY}(~8.5 billion combinations){Colors.ENDC}",
            f"   {Colors.WHITE}→ Know 9 words, find 3 missing words{Colors.ENDC}",
            f"",
            f"{Colors.CYAN}4){Colors.ENDC} Exit           {Colors.GRAY}(Back to main menu){Colors.ENDC}",
        ]
        
        print_box("🔍 PHRASE FINDER - Select Search Mode", menu_items, Colors.MAGENTA)
        
        choice = input(f"{Colors.YELLOW}Choose (1-4): {Colors.ENDC}").strip()
        
        if choice in ['1', '2', '3']:
            num_wildcards = int(choice)
            
            print(f"\n{Colors.CYAN}Enter your 12-word phrase (use * for unknown words):{Colors.ENDC}")
            print(f"{Colors.GRAY}Example: wind air * break warrior extra fire door * * water color{Colors.ENDC}")
            phrase_input = input(f"{Colors.YELLOW}> {Colors.ENDC}").strip()
            
            if not phrase_input:
                print(f"{Colors.RED}[!] Empty input{Colors.ENDC}\n")
                continue
            
            words = phrase_input.split()
            
            if len(words) != 12:
                print(f"{Colors.RED}[!] Must be exactly 12 words{Colors.ENDC}\n")
                continue
            
            # Count wildcards
            wildcard_positions = [i for i, w in enumerate(words) if w == '*']
            known_words = words.copy()
            
            if len(wildcard_positions) != num_wildcards:
                print(f"{Colors.RED}[!] Expected {num_wildcards} wildcards (*), found {len(wildcard_positions)}{Colors.ENDC}\n")
                continue
            
            # Validate known words
            invalid_words = []
            for i, word in enumerate(words):
                if word != '*' and word not in BIP39_WORDLIST:
                    invalid_words.append((i+1, word))
            
            if invalid_words:
                print(f"{Colors.RED}[!] Invalid BIP39 words found:{Colors.ENDC}")
                for pos, word in invalid_words:
                    print(f"   Position {pos}: '{word}'")
                print()
                continue
            
            # Show analysis
            analysis = [
                f"📝 Input phrase  : {phrase_input}",
                f"✅ Known words   : {12 - num_wildcards}",
                f"❓ Unknown words : {num_wildcards} (positions: {', '.join([str(p+1) for p in wildcard_positions])})",
            ]
            print_box("📊 PHRASE ANALYSIS", analysis, Colors.CYAN)
            
            # Run search
            if num_wildcards == 1:
                search_1_wildcard(known_words, wildcard_positions, web3_clients)
            elif num_wildcards == 2:
                search_2_wildcards(known_words, wildcard_positions, web3_clients)
            elif num_wildcards == 3:
                search_3_wildcards(known_words, wildcard_positions, web3_clients)
        
        elif choice == '4':
            print(f"{Colors.GREEN}[+] Returning to main menu...{Colors.ENDC}\n")
            break
        
        else:
            print(f"{Colors.RED}[!] Invalid choice{Colors.ENDC}\n")

# -------------------- Entry Point --------------------
def run():
    """Main entry point"""
    print(f"\n{Colors.CYAN}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.MAGENTA}🔍 PHRASE FINDER MODE{Colors.ENDC}")
    print(f"{Colors.CYAN}{'='*70}{Colors.ENDC}\n")
    
    # Check dependencies
    if not MNEMONIC_AVAILABLE:
        print(f"{Colors.RED}[!] mnemonic library required!{Colors.ENDC}")
        return
    
    # Load wordlist
    print(f"{Colors.CYAN}[+] Loading BIP39 wordlist...{Colors.ENDC}")
    if not load_bip39_wordlist():
        print(f"{Colors.RED}[!] Failed to load wordlist{Colors.ENDC}")
        return
    print(f"{Colors.GREEN}[+] Loaded {len(BIP39_WORDLIST)} words{Colors.ENDC}")
    
    # Load config
    print(f"{Colors.CYAN}[+] Loading configuration...{Colors.ENDC}")
    cfg = load_json_file(CONFIG_FILE)
    
    if not cfg:
        print(f"{Colors.RED}[!] Config not found: {CONFIG_FILE}{Colors.ENDC}")
        return
    
    # Build web3 clients
    web3_clients = {}
    if WEB3_AVAILABLE and cfg:
        rpcs = cfg.get("rpcs", {})
        for chain, info in rpcs.items():
            if info.get("evm") is False:
                continue
            url = info.get("rpc_url", "")
            if "${ALCHEMY_API_KEY}" in url and ALCHEMY_API_KEY:
                url = url.replace("${ALCHEMY_API_KEY}", ALCHEMY_API_KEY)
            
            try:
                w3 = Web3(HTTPProvider(url, request_kwargs={"timeout": 10}))
                if w3.is_connected():
                    web3_clients[chain] = {
                        "w3": w3,
                        "native_symbol": info.get("native_symbol", "ETH"),
                        "name": info.get("name", chain)
                    }
            except:
                pass
    
    print(f"{Colors.GREEN}[+] Connected to {len(web3_clients)} chain(s){Colors.ENDC}\n")
    
    # Run menu
    phrase_finder_menu(web3_clients)

if __name__ == "__main__":
    run()
