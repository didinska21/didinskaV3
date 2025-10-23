#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram bot integration for notifications
"""
import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

def is_telegram_enabled():
    """Check if Telegram is enabled"""
    return TELEGRAM_ENABLED

def send_message(message, parse_mode='HTML'):
    """
    Send message to Telegram
    
    Args:
        message: Message text
        parse_mode: Parse mode (HTML or Markdown)
    
    Returns:
        bool: Success status
    """
    if not TELEGRAM_ENABLED:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        
        response = requests.post(url, data=data, timeout=10)
        return response.status_code == 200
    
    except requests.exceptions.Timeout:
        print("⚠️  Telegram: Request timeout")
        return False
    except Exception as e:
        print(f"⚠️  Telegram error: {e}")
        return False

def notify_wallet_found(wallet_data):
    """
    Send notification for wallet with balance
    
    Args:
        wallet_data: Wallet information dict
    
    Returns:
        bool: Success status
    """
    balance_usd = wallet_data.get('balance_usd', 0)
    address = wallet_data.get('address', 'N/A')
    private_key = wallet_data.get('private_key', 'N/A')
    phrase = wallet_data.get('phrase', 'N/A')
    coins = wallet_data.get('coins', {})
    chains = wallet_data.get('chains', [])
    nonce = wallet_data.get('nonce', 0)
    found_at = wallet_data.get('found_at', datetime.now().isoformat())
    
    # Build coins text
    coins_text = ""
    if coins:
        for symbol, amount in coins.items():
            coins_text += f"  • {symbol}: {amount}\n"
    else:
        coins_text = "  (No balance info)"
    
    # Build chains text
    chains_text = ', '.join(chains) if chains else 'Multiple'
    
    message = f"""
🎉 <b>WALLET FOUND!</b> 🎉

💰 <b>Balance:</b> ${balance_usd:.2f}
📍 <b>Address:</b> <code>{address}</code>

🔑 <b>Private Key:</b>
<code>{private_key}</code>

📝 <b>Seed Phrase:</b>
<code>{phrase}</code>

💎 <b>Coins:</b>
{coins_text}
🌐 <b>Chains:</b> {chains_text}
📊 <b>Transactions:</b> {nonce}
⏰ <b>Found at:</b> {found_at}

<i>DIDINSKA Wallet Hunter v4.0</i>
"""
    
    return send_message(message.strip())

def notify_phrase_found(wallet_data):
    """
    Send notification for phrase finder success
    
    Args:
        wallet_data: Wallet information dict
    
    Returns:
        bool: Success status
    """
    balance_usd = wallet_data.get('balance_usd', 0)
    address = wallet_data.get('address', 'N/A')
    phrase = wallet_data.get('phrase', 'N/A')
    coins = wallet_data.get('coins', {})
    chains = wallet_data.get('chains', [])
    
    # Build coins text
    coins_text = ""
    if coins:
        for symbol, amount in coins.items():
            coins_text += f"  • {symbol}: {amount}\n"
    
    # Build chains text
    chains_text = ', '.join(chains) if chains else 'Multiple'
    
    message = f"""
🔍 <b>PHRASE FOUND!</b> 🔍

📝 <b>Recovered Phrase:</b>
<code>{phrase}</code>

📍 <b>Address:</b> <code>{address}</code>

💰 <b>Balance:</b> ${balance_usd:.2f}

💎 <b>Coins:</b>
{coins_text}
🌐 <b>Chains:</b> {chains_text}

<i>Phrase Finder - DIDINSKA v4.0</i>
"""
    
    return send_message(message.strip())

def notify_empty_wallets_batch(count, total_checked):
    """
    Send batch notification for empty wallets
    
    Args:
        count: Number of empty wallets in batch
        total_checked: Total wallets checked
    
    Returns:
        bool: Success status
    """
    message = f"""
📭 <b>Empty Wallets Report</b>

🔍 Scanned: {count} wallets
❌ Empty: {count}
📊 Total Checked: {total_checked:,}
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

<i>Batch scan completed - DIDINSKA</i>
"""
    
    return send_message(message.strip())

def notify_scan_start(count, workers, mode='Random'):
    """
    Notify scan start
    
    Args:
        count: Number of wallets to scan
        workers: Number of workers
        mode: Scan mode name
    
    Returns:
        bool: Success status
    """
    message = f"""
🚀 <b>Scan Started</b>

🎯 <b>Target:</b> {count:,} wallets
⚡ <b>Workers:</b> {workers}
🔍 <b>Mode:</b> {mode}
🕐 <b>Started:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

<i>DIDINSKA Wallet Hunter is running...</i>
"""
    
    return send_message(message.strip())

def notify_scan_complete(stats):
    """
    Notify scan completion
    
    Args:
        stats: Statistics dict
    
    Returns:
        bool: Success status
    """
    import time
    
    elapsed = time.time() - stats.get("start_time", time.time())
    rate = stats.get("total_checked", 0) / elapsed if elapsed > 0 else 0
    
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = int(elapsed % 60)
    runtime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    message = f"""
✅ <b>Scan Completed</b>

📊 <b>Statistics:</b>
  • Generated: {stats.get('total_generated', 0):,}
  • Checked: {stats.get('total_checked', 0):,}
  • Found: {stats.get('wallets_found', 0)}
  • Empty: {stats.get('empty_wallets', 0):,}
  • Speed: {rate:.2f} wallet/s
  • Runtime: {runtime}

<i>DIDINSKA Wallet Hunter</i>
"""
    
    return send_message(message.strip())

def notify_error(error_type, error_message):
    """
    Send error notification
    
    Args:
        error_type: Type of error
        error_message: Error message
    
    Returns:
        bool: Success status
    """
    message = f"""
⚠️ <b>Error Alert</b>

🔴 <b>Type:</b> {error_type}
📝 <b>Message:</b> {error_message}
⏰ <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

<i>DIDINSKA Wallet Hunter</i>
"""
    
    return send_message(message.strip())

def notify_system_status(status_data):
    """
    Send system status notification
    
    Args:
        status_data: System status dict
    
    Returns:
        bool: Success status
    """
    chains = status_data.get('chains_connected', 0)
    debank = '✅ Active' if status_data.get('debank_enabled') else '❌ Disabled'
    telegram = '✅ Active' if status_data.get('telegram_enabled') else '❌ Disabled'
    workers = status_data.get('workers', 16)
    
    message = f"""
🖥️ <b>System Status</b>

🌐 <b>Chains:</b> {chains} connected
🔍 <b>DeBank:</b> {debank}
📱 <b>Telegram:</b> {telegram}
⚡ <b>Workers:</b> {workers}
⏰ <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

<i>DIDINSKA Wallet Hunter v4.0</i>
"""
    
    return send_message(message.strip())

def test_connection():
    """
    Test Telegram bot connection
    
    Returns:
        bool: Connection status
    """
    if not TELEGRAM_ENABLED:
        print("❌ Telegram not configured")
        print(f"   Please set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return False
    
    try:
        # Get bot info
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            print(f"❌ Telegram bot error: {response.status_code}")
            return False
        
        data = response.json()
        if not data.get('ok'):
            print("❌ Telegram bot not responding")
            return False
        
        bot_info = data.get('result', {})
        bot_name = bot_info.get('first_name', 'Unknown')
        bot_username = bot_info.get('username', 'Unknown')
        
        print(f"✅ Telegram bot connected!")
        print(f"   Bot Name: {bot_name}")
        print(f"   Username: @{bot_username}")
        
        # Send test message
        test_msg = f"""
🧪 <b>Test Connection</b>

✅ Bot is working correctly!
⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

<i>DIDINSKA Wallet Hunter v4.0</i>
"""
        
        if send_message(test_msg.strip()):
            print(f"✅ Test message sent successfully!")
            return True
        else:
            print(f"❌ Failed to send test message")
            return False
    
    except requests.exceptions.Timeout:
        print("❌ Connection timeout")
        return False
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False

def send_photo(photo_path, caption=''):
    """
    Send photo to Telegram
    
    Args:
        photo_path: Path to photo file
        caption: Photo caption
    
    Returns:
        bool: Success status
    """
    if not TELEGRAM_ENABLED:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            data = {
                'chat_id': TELEGRAM_CHAT_ID,
                'caption': caption,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, files=files, data=data, timeout=30)
            return response.status_code == 200
    
    except Exception as e:
        print(f"⚠️  Failed to send photo: {e}")
        return False

def send_document(document_path, caption=''):
    """
    Send document to Telegram
    
    Args:
        document_path: Path to document file
        caption: Document caption
    
    Returns:
        bool: Success status
    """
    if not TELEGRAM_ENABLED:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        
        with open(document_path, 'rb') as document:
            files = {'document': document}
            data = {
                'chat_id': TELEGRAM_CHAT_ID,
                'caption': caption,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, files=files, data=data, timeout=30)
            return response.status_code == 200
    
    except Exception as e:
        print(f"⚠️  Failed to send document: {e}")
        return False

def get_updates():
    """
    Get bot updates (for debugging)
    
    Returns:
        dict: Updates data or None
    """
    if not TELEGRAM_ENABLED:
        return None
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            return response.json()
        return None
    
    except Exception:
        return None
