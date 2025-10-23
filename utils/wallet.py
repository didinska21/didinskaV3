#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wallet generation and BIP39 mnemonic functions
"""
import random
from typing import List, Dict, Optional, Tuple

try:
    from mnemonic import Mnemonic
    MNEMONIC_AVAILABLE = True
except ImportError:
    MNEMONIC_AVAILABLE = False
    Mnemonic = None

try:
    from eth_account import Account
    from eth_account.hdaccount import key_from_seed, ETHEREUM_DEFAULT_PATH
    ACCOUNT_AVAILABLE = True
except ImportError:
    ACCOUNT_AVAILABLE = False
    Account = None

# Global wordlist
BIP39_WORDLIST = None

def load_wordlist(language='english'):
    """
    Load BIP39 wordlist
    
    Args:
        language: Language for wordlist (default: english)
    
    Returns:
        bool: Success status
    """
    global BIP39_WORDLIST
    
    if not MNEMONIC_AVAILABLE:
        return False
    
    try:
        mnemo = Mnemonic(language)
        BIP39_WORDLIST = mnemo.wordlist
        return True
    except Exception:
        return False

def get_wordlist():
    """
    Get loaded wordlist
    
    Returns:
        list: BIP39 wordlist or None
    """
    return BIP39_WORDLIST

def is_valid_bip39_word(word):
    """
    Check if word is valid BIP39 word
    
    Args:
        word: Word to check
    
    Returns:
        bool: Valid status
    """
    if not BIP39_WORDLIST:
        return False
    
    return word.lower() in BIP39_WORDLIST

def validate_phrase(phrase):
    """
    Validate mnemonic phrase
    
    Args:
        phrase: Space-separated phrase
    
    Returns:
        tuple: (is_valid, error_message)
    """
    if not MNEMONIC_AVAILABLE:
        return False, "Mnemonic library not available"
    
    if not BIP39_WORDLIST:
        return False, "Wordlist not loaded"
    
    words = phrase.strip().split()
    
    # Check word count
    valid_lengths = [12, 15, 18, 21, 24]
    if len(words) not in valid_lengths:
        return False, f"Invalid phrase length: {len(words)}. Must be one of {valid_lengths}"
    
    # Check each word
    invalid_words = []
    for i, word in enumerate(words):
        if word != '*' and word not in BIP39_WORDLIST:
            invalid_words.append((i + 1, word))
    
    if invalid_words:
        errors = ", ".join([f"Position {pos}: '{word}'" for pos, word in invalid_words])
        return False, f"Invalid BIP39 words found: {errors}"
    
    # Check checksum (if no wildcards)
    if '*' not in words:
        try:
            mnemo = Mnemonic("english")
            if not mnemo.check(phrase):
                return False, "Invalid checksum"
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    return True, "Valid"

def generate_random_phrase(word_count=12):
    """
    Generate random BIP39 phrase
    
    Args:
        word_count: Number of words (12, 15, 18, 21, or 24)
    
    Returns:
        str: Random phrase or None
    """
    if not MNEMONIC_AVAILABLE:
        return None
    
    if not BIP39_WORDLIST:
        return None
    
    if word_count not in [12, 15, 18, 21, 24]:
        return None
    
    try:
        mnemo = Mnemonic("english")
        return mnemo.generate(strength=(word_count * 32 // 3))
    except Exception:
        # Fallback to manual generation
        words = [random.choice(BIP39_WORDLIST) for _ in range(word_count)]
        return ' '.join(words)

def phrase_to_wallet(phrase, index=0):
    """
    Convert mnemonic phrase to wallet
    
    Args:
        phrase: Mnemonic phrase
        index: Derivation index (default: 0)
    
    Returns:
        dict: Wallet info {address, private_key, phrase} or None
    """
    if not MNEMONIC_AVAILABLE or not ACCOUNT_AVAILABLE:
        return None
    
    try:
        mnemo = Mnemonic("english")
        seed = mnemo.to_seed(phrase, passphrase="")
        
        # Derive key
        try:
            private_key = key_from_seed(seed, f"m/44'/60'/0'/0/{index}")
        except Exception:
            # Fallback to first 32 bytes
            private_key = seed[:32]
        
        # Create account
        account = Account.from_key(private_key)
        
        return {
            "address": account.address,
            "private_key": private_key.hex() if isinstance(private_key, bytes) else private_key,
            "phrase": phrase
        }
    
    except Exception as e:
        return None

def generate_random_wallet():
    """
    Generate completely random wallet
    
    Returns:
        dict: Wallet info or None
    """
    phrase = generate_random_phrase(12)
    if not phrase:
        return None
    
    return phrase_to_wallet(phrase, 0)

def generate_wallets_from_phrase_pattern(known_words, wildcard_positions, max_combinations=None):
    """
    Generate wallets from phrase pattern with wildcards
    
    Args:
        known_words: List of words (use '*' for wildcards)
        wildcard_positions: List of wildcard positions
        max_combinations: Max combinations to generate (None = all)
    
    Yields:
        dict: Wallet info
    """
    if not BIP39_WORDLIST:
        return
    
    num_wildcards = len(wildcard_positions)
    
    if num_wildcards == 0:
        # No wildcards, just convert
        phrase = ' '.join(known_words)
        wallet = phrase_to_wallet(phrase)
        if wallet:
            yield wallet
        return
    
    # Generate combinations
    count = 0
    
    if num_wildcards == 1:
        pos = wildcard_positions[0]
        for word in BIP39_WORDLIST:
            if max_combinations and count >= max_combinations:
                break
            
            phrase_words = known_words.copy()
            phrase_words[pos] = word
            phrase = ' '.join(phrase_words)
            
            wallet = phrase_to_wallet(phrase)
            if wallet:
                yield wallet
                count += 1
    
    elif num_wildcards == 2:
        pos1, pos2 = wildcard_positions[0], wildcard_positions[1]
        for word1 in BIP39_WORDLIST:
            for word2 in BIP39_WORDLIST:
                if max_combinations and count >= max_combinations:
                    return
                
                phrase_words = known_words.copy()
                phrase_words[pos1] = word1
                phrase_words[pos2] = word2
                phrase = ' '.join(phrase_words)
                
                wallet = phrase_to_wallet(phrase)
                if wallet:
                    yield wallet
                    count += 1
    
    elif num_wildcards == 3:
        pos1, pos2, pos3 = wildcard_positions
        for word1 in BIP39_WORDLIST:
            for word2 in BIP39_WORDLIST:
                for word3 in BIP39_WORDLIST:
                    if max_combinations and count >= max_combinations:
                        return
                    
                    phrase_words = known_words.copy()
                    phrase_words[pos1] = word1
                    phrase_words[pos2] = word2
                    phrase_words[pos3] = word3
                    phrase = ' '.join(phrase_words)
                    
                    wallet = phrase_to_wallet(phrase)
                    if wallet:
                        yield wallet
                        count += 1

def derive_multiple_addresses(phrase, count=10):
    """
    Derive multiple addresses from same phrase
    
    Args:
        phrase: Mnemonic phrase
        count: Number of addresses to derive
    
    Returns:
        list: List of wallet dicts
    """
    wallets = []
    
    for i in range(count):
        wallet = phrase_to_wallet(phrase, index=i)
        if wallet:
            wallet['index'] = i
            wallets.append(wallet)
    
    return wallets

def get_checksum_word(phrase_11_words):
    """
    Calculate 12th checksum word for 11-word phrase
    
    Args:
        phrase_11_words: First 11 words
    
    Returns:
        list: Possible checksum words
    """
    if not MNEMONIC_AVAILABLE or not BIP39_WORDLIST:
        return []
    
    words = phrase_11_words.strip().split()
    if len(words) != 11:
        return []
    
    # Try all possible last words
    valid_words = []
    mnemo = Mnemonic("english")
    
    for word in BIP39_WORDLIST:
        test_phrase = ' '.join(words + [word])
        if mnemo.check(test_phrase):
            valid_words.append(word)
    
    return valid_words

def format_wallet_info(wallet):
    """
    Format wallet info for display
    
    Args:
        wallet: Wallet dict
    
    Returns:
        str: Formatted string
    """
    if not wallet:
        return "No wallet data"
    
    lines = [
        f"Address: {wallet.get('address', 'N/A')}",
        f"Private Key: {wallet.get('private_key', 'N/A')[:20]}...",
        f"Phrase: {wallet.get('phrase', 'N/A')}"
    ]
    
    if 'index' in wallet:
        lines.append(f"Derivation Index: {wallet['index']}")
    
    return '\n'.join(lines)

def phrase_to_entropy(phrase):
    """
    Convert phrase to entropy bytes
    
    Args:
        phrase: Mnemonic phrase
    
    Returns:
        bytes: Entropy or None
    """
    if not MNEMONIC_AVAILABLE:
        return None
    
    try:
        mnemo = Mnemonic("english")
        return mnemo.to_entropy(phrase)
    except Exception:
        return None

def entropy_to_phrase(entropy):
    """
    Convert entropy to mnemonic phrase
    
    Args:
        entropy: Entropy bytes
    
    Returns:
        str: Mnemonic phrase or None
    """
    if not MNEMONIC_AVAILABLE:
        return None
    
    try:
        mnemo = Mnemonic("english")
        return mnemo.to_mnemonic(entropy)
    except Exception:
        return None

def is_phrase_with_wildcards(phrase):
    """
    Check if phrase contains wildcards
    
    Args:
        phrase: Phrase string
    
    Returns:
        tuple: (has_wildcards, wildcard_count, positions)
    """
    words = phrase.strip().split()
    wildcard_positions = [i for i, w in enumerate(words) if w == '*']
    
    return len(wildcard_positions) > 0, len(wildcard_positions), wildcard_positions

def estimate_search_time(num_wildcards, speed_per_second=10):
    """
    Estimate search time for phrase finder
    
    Args:
        num_wildcards: Number of unknown words
        speed_per_second: Wallets checked per second
    
    Returns:
        dict: Time estimates
    """
    wordlist_size = 2048  # BIP39 wordlist size
    total_combinations = wordlist_size ** num_wildcards
    
    seconds = total_combinations / speed_per_second
    minutes = seconds / 60
    hours = minutes / 60
    days = hours / 24
    years = days / 365
    
    return {
        'combinations': total_combinations,
        'seconds': seconds,
        'minutes': minutes,
        'hours': hours,
        'days': days,
        'years': years,
        'human_readable': format_time_estimate(seconds)
    }

def format_time_estimate(seconds):
    """
    Format time estimate to human readable
    
    Args:
        seconds: Time in seconds
    
    Returns:
        str: Formatted string
    """
    if seconds < 60:
        return f"{seconds:.1f} seconds"
    elif seconds < 3600:
        return f"{seconds/60:.1f} minutes"
    elif seconds < 86400:
        return f"{seconds/3600:.1f} hours"
    elif seconds < 31536000:
        return f"{seconds/86400:.1f} days"
    else:
        return f"{seconds/31536000:.1f} years"

def get_word_suggestions(partial_word, max_suggestions=10):
    """
    Get word suggestions for partial input
    
    Args:
        partial_word: Partial word input
        max_suggestions: Max suggestions to return
    
    Returns:
        list: Suggested words
    """
    if not BIP39_WORDLIST:
        return []
    
    partial = partial_word.lower()
    suggestions = [w for w in BIP39_WORDLIST if w.startswith(partial)]
    
    return suggestions[:max_suggestions]
