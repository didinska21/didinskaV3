#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Balance checking and blockchain interaction functions
"""
import os
import requests
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv

try:
    from web3 import Web3, HTTPProvider
    WEB3_AVAILABLE = True
except:
    WEB3_AVAILABLE = False
    Web3 = None
    HTTPProvider = None

load_dotenv()

# DeBank configuration
DEBANK_ACCESS_KEY = os.getenv("DEBANK_ACCESS_KEY")
DEBANK_BASE_URL = os.getenv("DEBANK_BASE_URL", "https://pro-openapi.debank.com")
DEBANK_TIMEOUT = int(os.getenv("DEBANK_TIMEOUT", "15"))

def check_debank_balance(address):
    """
    Check balance using DeBank API
    
    Args:
        address: Ethereum address
    
    Returns:
        dict: {coins: {symbol: amount}, balance_usd: float} or None
    """
    if not DEBANK_ACCESS_KEY:
        return None
    
    headers = {
        "accept": "application/json",
        "AccessKey": DEBANK_ACCESS_KEY
    }
    
    url = f"{DEBANK_BASE_URL}/v1/user/all_token_list"
    params = {"id": address}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=DEBANK_TIMEOUT)
        
        if response.status_code == 429:
            # Rate limit
            return None
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        items = data.get("data") or []
        
        coins = {}
        total_usd = Decimal(0)
        
        for token in items:
            try:
                symbol = (token.get("symbol") or "").upper()
                amount = Decimal(str(token.get("amount", 0)))
                price = Decimal(str(token.get("price", 0))) if token.get("price") is not None else Decimal(0)
                
                if amount > 0 and symbol:
                    coins[symbol] = float(amount)
                    total_usd += amount * price
            except:
                continue
        
        return {
            "coins": coins,
            "balance_usd": float(total_usd)
        }
    
    except requests.exceptions.Timeout:
        return None
    except Exception as e:
        return None

def check_native_balance(w3, address):
    """
    Check native token balance via RPC
    
    Args:
        w3: Web3 instance
        address: Ethereum address
    
    Returns:
        float: Balance in native token (ETH, BNB, etc.)
    """
    if not WEB3_AVAILABLE or not w3:
        return 0.0
    
    try:
        balance_wei = w3.eth.get_balance(address)
        balance = Decimal(balance_wei) / Decimal(10 ** 18)
        return float(balance)
    except Exception:
        return 0.0

def check_transaction_count(w3, address):
    """
    Check transaction count (nonce)
    
    Args:
        w3: Web3 instance
        address: Ethereum address
    
    Returns:
        int: Transaction count
    """
    if not WEB3_AVAILABLE or not w3:
        return 0
    
    try:
        nonce = w3.eth.get_transaction_count(address)
        return nonce
    except Exception:
        return 0

def check_wallet_balance(wallet, web3_clients):
    """
    Check wallet balance across multiple chains
    
    Args:
        wallet: Wallet dict with address
        web3_clients: Dict of chain -> {w3, native_symbol, name}
    
    Returns:
        dict: Balance info or None if empty
    """
    if not wallet or 'address' not in wallet:
        return None
    
    address = wallet['address']
    
    result = {
        "address": address,
        "private_key": wallet.get("private_key", ""),
        "phrase": wallet.get("phrase", ""),
        "balance_usd": 0.0,
        "coins": {},
        "chains": [],
        "nonce": 0,
        "found_at": datetime.now().isoformat()
    }
    
    has_value = False
    max_nonce = 0
    
    # Check DeBank
    debank_data = check_debank_balance(address)
    if debank_data:
        coins = debank_data.get("coins", {})
        balance_usd = debank_data.get("balance_usd", 0.0)
        
        if coins or balance_usd > 0:
            result["coins"].update(coins)
            result["balance_usd"] = balance_usd
            has_value = True
    
    # Check each chain
    for chain_name, client in web3_clients.items():
        w3 = client.get("w3")
        if not w3:
            continue
        
        # Check native balance
        balance = check_native_balance(w3, address)
        if balance > 0:
            symbol = client.get("native_symbol", "ETH")
            result["chains"].append(chain_name)
            
            # Add to coins (sum if already exists)
            prev_balance = Decimal(str(result["coins"].get(symbol, 0.0)))
            result["coins"][symbol] = float(prev_balance + Decimal(str(balance)))
            has_value = True
        
        # Check transaction count
        nonce = check_transaction_count(w3, address)
        if nonce > max_nonce:
            max_nonce = nonce
    
    result["nonce"] = max_nonce
    
    # Consider wallet with history as having value
    if max_nonce > 0:
        has_value = True
    
    return result if has_value else None

def check_multiple_wallets(wallets, web3_clients, max_workers=10):
    """
    Check multiple wallets concurrently
    
    Args:
        wallets: List of wallet dicts
        web3_clients: Dict of Web3 clients
        max_workers: Max concurrent workers
    
    Returns:
        list: List of wallets with balance
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    found_wallets = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(check_wallet_balance, wallet, web3_clients): wallet
            for wallet in wallets
        }
        
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    found_wallets.append(result)
            except Exception:
                pass
    
    return found_wallets

def build_web3_clients(config, alchemy_api_key=None):
    """
    Build Web3 clients from config
    
    Args:
        config: Config dict with rpcs
        alchemy_api_key: Alchemy API key for URL injection
    
    Returns:
        dict: Chain name -> {w3, native_symbol, name}
    """
    if not WEB3_AVAILABLE:
        return {}
    
    clients = {}
    rpcs = config.get("rpcs", {})
    
    for chain, info in rpcs.items():
        if not isinstance(info, dict):
            continue
        
        # Skip non-EVM chains
        if info.get("evm") is False:
            continue
        
        # Get RPC URL
        url = info.get("rpc_url", "")
        if not url:
            continue
        
        # Inject Alchemy API key if needed
        if "${ALCHEMY_API_KEY}" in url and alchemy_api_key:
            url = url.replace("${ALCHEMY_API_KEY}", alchemy_api_key)
        
        # Try to connect
        try:
            w3 = Web3(HTTPProvider(url, request_kwargs={"timeout": 10}))
            
            if not w3.is_connected():
                continue
            
            clients[chain] = {
                "w3": w3,
                "native_symbol": info.get("native_symbol", "ETH"),
                "name": info.get("name", chain)
            }
        except Exception:
            continue
    
    return clients

def get_token_balance(w3, token_address, wallet_address):
    """
    Get ERC20 token balance
    
    Args:
        w3: Web3 instance
        token_address: Token contract address
        wallet_address: Wallet address
    
    Returns:
        float: Token balance
    """
    if not WEB3_AVAILABLE or not w3:
        return 0.0
    
    try:
        # ERC20 ABI for balanceOf
        abi = [
            {
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function"
            },
            {
                "constant": True,
                "inputs": [],
                "name": "decimals",
                "outputs": [{"name": "", "type": "uint8"}],
                "type": "function"
            }
        ]
        
        contract = w3.eth.contract(address=token_address, abi=abi)
        balance = contract.functions.balanceOf(wallet_address).call()
        decimals = contract.functions.decimals().call()
        
        return float(Decimal(balance) / Decimal(10 ** decimals))
    except Exception:
        return 0.0

def check_wallet_on_chain(w3, address, chain_name="Unknown"):
    """
    Check wallet on specific chain
    
    Args:
        w3: Web3 instance
        address: Wallet address
        chain_name: Chain name for display
    
    Returns:
        dict: Balance info
    """
    result = {
        "chain": chain_name,
        "balance": 0.0,
        "nonce": 0,
        "has_activity": False
    }
    
    try:
        # Check balance
        result["balance"] = check_native_balance(w3, address)
        
        # Check nonce
        result["nonce"] = check_transaction_count(w3, address)
        
        # Has activity if balance > 0 or nonce > 0
        result["has_activity"] = result["balance"] > 0 or result["nonce"] > 0
    except Exception:
        pass
    
    return result

def get_transaction_history(w3, address, limit=10):
    """
    Get recent transactions (basic implementation)
    
    Args:
        w3: Web3 instance
        address: Wallet address
        limit: Max transactions to fetch
    
    Returns:
        list: List of transaction dicts
    """
    if not WEB3_AVAILABLE or not w3:
        return []
    
    transactions = []
    
    try:
        # Get latest block
        latest_block = w3.eth.block_number
        
        # Search recent blocks (this is a simple implementation)
        # For production, use an indexer like Etherscan API
        for block_num in range(latest_block, max(0, latest_block - 1000), -1):
            try:
                block = w3.eth.get_block(block_num, full_transactions=True)
                
                for tx in block['transactions']:
                    if tx['from'] == address or tx['to'] == address:
                        transactions.append({
                            "hash": tx['hash'].hex(),
                            "from": tx['from'],
                            "to": tx['to'],
                            "value": float(Decimal(tx['value']) / Decimal(10**18)),
                            "block": block_num
                        })
                        
                        if len(transactions) >= limit:
                            return transactions
            except:
                continue
        
    except Exception:
        pass
    
    return transactions

def estimate_wallet_value_usd(coins, prices=None):
    """
    Estimate wallet value in USD
    
    Args:
        coins: Dict of symbol -> amount
        prices: Dict of symbol -> price (optional)
    
    Returns:
        float: Estimated USD value
    """
    if not prices:
        # Default prices (should be fetched from API in production)
        prices = {
            "ETH": 2000.0,
            "BNB": 300.0,
            "MATIC": 0.8,
            "AVAX": 30.0,
            "USDT": 1.0,
            "USDC": 1.0,
            "DAI": 1.0
        }
    
    total_usd = 0.0
    
    for symbol, amount in coins.items():
        price = prices.get(symbol, 0.0)
        total_usd += amount * price
    
    return total_usd

def quick_balance_check(address, web3_clients):
    """
    Quick balance check (faster, less detailed)
    
    Args:
        address: Wallet address
        web3_clients: Web3 clients dict
    
    Returns:
        bool: True if wallet has any balance or activity
    """
    # Check nonce first (fastest)
    for client in web3_clients.values():
        w3 = client.get("w3")
        if not w3:
            continue
        
        nonce = check_transaction_count(w3, address)
        if nonce > 0:
            return True
    
    # Check balances
    for client in web3_clients.values():
        w3 = client.get("w3")
        if not w3:
            continue
        
        balance = check_native_balance(w3, address)
        if balance > 0:
            return True
    
    return False

def format_balance_info(balance_data):
    """
    Format balance data for display
    
    Args:
        balance_data: Balance dict
    
    Returns:
        str: Formatted string
    """
    if not balance_data:
        return "No balance data"
    
    lines = [
        f"Address: {balance_data.get('address', 'N/A')}",
        f"Balance USD: ${balance_data.get('balance_usd', 0):.2f}",
        f"Chains: {', '.join(balance_data.get('chains', []))}",
        f"Transactions: {balance_data.get('nonce', 0)}"
    ]
    
    coins = balance_data.get('coins', {})
    if coins:
        lines.append("Coins:")
        for symbol, amount in coins.items():
            lines.append(f"  • {symbol}: {amount}")
    
    return "\n".join(lines)

def is_contract_address(w3, address):
    """
    Check if address is a contract
    
    Args:
        w3: Web3 instance
        address: Address to check
    
    Returns:
        bool: True if contract
    """
    if not WEB3_AVAILABLE or not w3:
        return False
    
    try:
        code = w3.eth.get_code(address)
        return len(code) > 0
    except:
        return False

def get_chain_info(w3):
    """
    Get chain information
    
    Args:
        w3: Web3 instance
    
    Returns:
        dict: Chain info
    """
    if not WEB3_AVAILABLE or not w3:
        return {}
    
    try:
        return {
            "chain_id": w3.eth.chain_id,
            "block_number": w3.eth.block_number,
            "gas_price": w3.eth.gas_price,
            "is_connected": w3.is_connected()
        }
    except:
        return {}
