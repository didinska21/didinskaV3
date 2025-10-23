"""
DIDINSKA Wallet Hunter - Utilities Package
"""
from .colors import Colors
from .ui import (
    print_header,
    print_loader,
    print_box,
    print_progress_bar,
    clear_screen,
    print_stats_box
)

try:
    from .telegram import (
        is_telegram_enabled,
        send_message,
        notify_wallet_found,
        notify_phrase_found,
        notify_empty_wallets_batch,
        notify_scan_start,
        notify_scan_complete,
        notify_error,
        notify_system_status,
        test_connection
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

try:
    from .wallet import (
        load_wordlist,
        get_wordlist,
        is_valid_bip39_word,
        validate_phrase,
        generate_random_phrase,
        phrase_to_wallet,
        generate_random_wallet,
        generate_wallets_from_phrase_pattern,
        derive_multiple_addresses,
        get_checksum_word,
        format_wallet_info
    )
    WALLET_AVAILABLE = True
except ImportError:
    WALLET_AVAILABLE = False

try:
    from .checker import (
        check_debank_balance,
        check_native_balance,
        check_transaction_count,
        check_wallet_balance,
        check_multiple_wallets,
        build_web3_clients,
        get_token_balance,
        check_wallet_on_chain,
        quick_balance_check,
        format_balance_info,
        is_contract_address,
        get_chain_info
    )
    CHECKER_AVAILABLE = True
except ImportError:
    CHECKER_AVAILABLE = False

__all__ = [
    # Colors
    'Colors',
    
    # UI
    'print_header',
    'print_loader',
    'print_box',
    'print_progress_bar',
    'clear_screen',
    'print_stats_box',
    
    # Telegram (if available)
    'is_telegram_enabled',
    'send_message',
    'notify_wallet_found',
    'notify_phrase_found',
    'notify_empty_wallets_batch',
    'notify_scan_start',
    'notify_scan_complete',
    'notify_error',
    'notify_system_status',
    'test_connection',
    
    # Wallet (if available)
    'load_wordlist',
    'get_wordlist',
    'is_valid_bip39_word',
    'validate_phrase',
    'generate_random_phrase',
    'phrase_to_wallet',
    'generate_random_wallet',
    'generate_wallets_from_phrase_pattern',
    'derive_multiple_addresses',
    'get_checksum_word',
    'format_wallet_info',
    
    # Checker (if available)
    'check_debank_balance',
    'check_native_balance',
    'check_transaction_count',
    'check_wallet_balance',
    'check_multiple_wallets',
    'build_web3_clients',
    'get_token_balance',
    'check_wallet_on_chain',
    'quick_balance_check',
    'format_balance_info',
    'is_contract_address',
    'get_chain_info',
    
    # Availability flags
    'TELEGRAM_AVAILABLE',
    'WALLET_AVAILABLE',
    'CHECKER_AVAILABLE'
]
