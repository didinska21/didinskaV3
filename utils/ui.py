#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI components for terminal interface
"""
import os
import sys
import time
from .colors import Colors

def clear_screen():
    """Clear terminal screen"""
    os.system('cls' if os.name == 'nt' else 'clear')

def print_header():
    """Print DIDINSKA header banner"""
    banner = f"""
{Colors.CYAN}╔══════════════════════════════════════════════════════════════════════╗
║  {Colors.LIGHT_CYAN}██████╗ ██╗██████╗ ██╗███╗   ██╗███████╗██╗  ██╗ █████╗{Colors.CYAN}         ║
║  {Colors.LIGHT_CYAN}██╔══██╗██║██╔══██╗██║████╗  ██║██╔════╝██║ ██╔╝██╔══██╗{Colors.CYAN}    ║
║  {Colors.LIGHT_CYAN}██║  ██║██║██║  ██║██║██╔██╗ ██║███████╗█████╔╝ ███████║{Colors.CYAN}      ║
║  {Colors.LIGHT_CYAN}██║  ██║██║██║  ██║██║██║╚██╗██║╚════██║██╔═██╗ ██╔══██║{Colors.CYAN}      ║
║  {Colors.LIGHT_CYAN}██████╔╝██║██████╔╝██║██║ ╚████║███████║██║  ██╗██║  ██║{Colors.CYAN}      ║
║  {Colors.LIGHT_CYAN}╚═════╝ ╚═╝╚═════╝ ╚═╝╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝{Colors.CYAN}        ║
╚══════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
{Colors.YELLOW}                   🔍 Wallet Hunter v4.0 🔍{Colors.ENDC}
{Colors.GRAY}              Advanced Multi-Chain Wallet Scanner{Colors.ENDC}
"""
    clear_screen()
    print(banner)

def print_box(title, content_lines, color=Colors.CYAN):
    """
    Print a beautiful box with content
    
    Args:
        title: Box title
        content_lines: List of content lines
        color: Border color
    """
    # Calculate max width
    max_width = max(len(line) for line in [title] + content_lines)
    box_width = max(max_width + 4, 70)
    
    # Remove color codes for width calculation
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    
    def visible_len(text):
        return len(ansi_escape.sub('', text))
    
    # Top border
    print(f"{color}╔{'═' * (box_width - 2)}╗{Colors.ENDC}")
    
    # Title
    title_padding = box_width - visible_len(title) - 4
    print(f"{color}║ {Colors.BOLD}{Colors.WHITE}{title}{Colors.ENDC}{' ' * title_padding} {color}║{Colors.ENDC}")
    
    # Separator
    print(f"{color}╠{'═' * (box_width - 2)}╣{Colors.ENDC}")
    
    # Content
    for line in content_lines:
        visible_length = visible_len(line)
        padding = box_width - visible_length - 4
        print(f"{color}║ {line}{' ' * padding} {color}║{Colors.ENDC}")
    
    # Bottom border
    print(f"{color}╚{'═' * (box_width - 2)}╝{Colors.ENDC}")

def print_loader(message, duration=2, style='dots'):
    """
    Show loading animation
    
    Args:
        message: Loading message
        duration: Duration in seconds
        style: Animation style ('dots', 'spin', 'bar')
    """
    animations = {
        'dots': ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'],
        'spin': ['|', '/', '-', '\\'],
        'bar': ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█', '▇', '▆', '▅', '▄', '▃', '▂'],
        'arrow': ['←', '↖', '↑', '↗', '→', '↘', '↓', '↙'],
        'circle': ['◐', '◓', '◑', '◒']
    }
    
    chars = animations.get(style, animations['dots'])
    end_time = time.time() + duration
    i = 0
    
    while time.time() < end_time:
        sys.stdout.write(f"\r{Colors.CYAN}{chars[i % len(chars)]} {message}...{Colors.ENDC}")
        sys.stdout.flush()
        time.sleep(0.1)
        i += 1
    
    sys.stdout.write(f"\r{Colors.GREEN}✓ {message} - Done!{Colors.ENDC}" + " " * 20 + "\n")
    sys.stdout.flush()

def print_progress_bar(current, total, width=50, prefix='Progress', show_percent=True):
    """
    Print progress bar
    
    Args:
        current: Current progress
        total: Total items
        width: Bar width
        prefix: Bar prefix text
        show_percent: Show percentage
    """
    if total == 0:
        return
    
    percent = current / total
    filled = int(width * percent)
    bar = '█' * filled + '░' * (width - filled)
    
    if show_percent:
        percent_str = f"{percent * 100:.1f}%"
        print(f"\r{Colors.CYAN}{prefix}:{Colors.ENDC} [{bar}] {Colors.YELLOW}{percent_str}{Colors.ENDC} ({current}/{total})", end='', flush=True)
    else:
        print(f"\r{Colors.CYAN}{prefix}:{Colors.ENDC} [{bar}] ({current}/{total})", end='', flush=True)

def print_stats_box(stats):
    """
    Print statistics box
    
    Args:
        stats: Dict with statistics
    """
    start_time = stats.get("start_time")
    elapsed = time.time() - start_time if start_time else 0
    
    rate = stats.get("total_checked", 0) / elapsed if elapsed > 0 else 0
    
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = int(elapsed % 60)
    runtime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    stats_lines = [
        f"{Colors.CYAN}Generated:{Colors.ENDC}  {Colors.WHITE}{stats.get('total_generated', 0):,}{Colors.ENDC}",
        f"{Colors.CYAN}Checked:{Colors.ENDC}    {Colors.WHITE}{stats.get('total_checked', 0):,}{Colors.ENDC}",
        f"{Colors.GREEN}Found:{Colors.ENDC}      {Colors.LIGHT_GREEN}{stats.get('wallets_found', 0):,}{Colors.ENDC}",
        f"{Colors.GRAY}Empty:{Colors.ENDC}      {Colors.WHITE}{stats.get('empty_wallets', 0):,}{Colors.ENDC}",
        f"{Colors.YELLOW}Speed:{Colors.ENDC}      {Colors.WHITE}{rate:.2f} wallet/s{Colors.ENDC}",
        f"{Colors.MAGENTA}Runtime:{Colors.ENDC}    {Colors.WHITE}{runtime}{Colors.ENDC}",
    ]
    
    if stats.get("last_found"):
        stats_lines.append("")
        stats_lines.append(f"{Colors.GREEN}Last Found:{Colors.ENDC} {Colors.LIGHT_GREEN}{stats['last_found'][:20]}...{Colors.ENDC}")
    
    print_box("📊 LIVE STATISTICS", stats_lines, Colors.BLUE)

def print_separator(char='═', length=70, color=Colors.CYAN):
    """Print separator line"""
    print(f"{color}{char * length}{Colors.ENDC}")

def print_section_header(text, color=Colors.YELLOW):
    """Print section header"""
    print(f"\n{color}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{color}{text.center(70)}{Colors.ENDC}")
    print(f"{color}{'='*70}{Colors.ENDC}\n")

def print_success(message):
    """Print success message"""
    print(f"{Colors.GREEN}✓ {message}{Colors.ENDC}")

def print_error(message):
    """Print error message"""
    print(f"{Colors.RED}✗ {message}{Colors.ENDC}")

def print_warning(message):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠ {message}{Colors.ENDC}")

def print_info(message):
    """Print info message"""
    print(f"{Colors.CYAN}ℹ {message}{Colors.ENDC}")

def print_wallet_found(wallet_data, index=None):
    """
    Print wallet found alert
    
    Args:
        wallet_data: Wallet information dict
        index: Wallet number (optional)
    """
    title = f"💰 WALLET FOUND #{index}!" if index else "💰 WALLET FOUND!"
    
    print(f"\n{Colors.GREEN}{'🎉' * 35}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.LIGHT_GREEN}{title.center(70)}{Colors.ENDC}")
    print(f"{Colors.GREEN}{'🎉' * 35}{Colors.ENDC}")
    
    print(f"{Colors.CYAN}Phrase     :{Colors.ENDC} {Colors.WHITE}{wallet_data.get('phrase', 'N/A')}{Colors.ENDC}")
    print(f"{Colors.CYAN}Address    :{Colors.ENDC} {Colors.YELLOW}{wallet_data.get('address', 'N/A')}{Colors.ENDC}")
    print(f"{Colors.CYAN}Private Key:{Colors.ENDC} {Colors.GRAY}{wallet_data.get('private_key', 'N/A')[:20]}...{Colors.ENDC}")
    print(f"{Colors.CYAN}Balance USD:{Colors.ENDC} {Colors.GREEN}${wallet_data.get('balance_usd', 0):.2f}{Colors.ENDC}")
    
    coins = wallet_data.get('coins', {})
    if coins:
        print(f"{Colors.CYAN}Coins      :{Colors.ENDC}")
        for symbol, amount in coins.items():
            print(f"            {Colors.WHITE}• {symbol}: {amount}{Colors.ENDC}")
    
    print(f"{Colors.CYAN}Chains     :{Colors.ENDC} {Colors.WHITE}{', '.join(wallet_data.get('chains', []))}{Colors.ENDC}")
    print(f"{Colors.CYAN}Nonce      :{Colors.ENDC} {Colors.WHITE}{wallet_data.get('nonce', 0)}{Colors.ENDC}")
    print(f"{Colors.CYAN}Found At   :{Colors.ENDC} {Colors.GRAY}{wallet_data.get('found_at', 'N/A')}{Colors.ENDC}")
    
    print(f"{Colors.GREEN}{'🎉' * 35}{Colors.ENDC}\n")

def print_table(headers, rows, colors=None):
    """
    Print formatted table
    
    Args:
        headers: List of header strings
        rows: List of row lists
        colors: List of colors for each column (optional)
    """
    if not colors:
        colors = [Colors.WHITE] * len(headers)
    
    # Calculate column widths
    col_widths = []
    for i, header in enumerate(headers):
        max_width = len(header)
        for row in rows:
            if i < len(row):
                max_width = max(max_width, len(str(row[i])))
        col_widths.append(max_width + 2)
    
    # Print table
    print(f"{Colors.CYAN}┌{'─' * (sum(col_widths) + len(headers) - 1)}┐{Colors.ENDC}")
    
    # Headers
    header_row = ""
    for i, (header, width) in enumerate(zip(headers, col_widths)):
        header_row += f" {Colors.YELLOW}{header.ljust(width - 1)}{Colors.ENDC}"
        if i < len(headers) - 1:
            header_row += f"{Colors.CYAN}│{Colors.ENDC}"
    print(f"{Colors.CYAN}│{header_row}{Colors.CYAN}│{Colors.ENDC}")
    
    # Separator
    print(f"{Colors.CYAN}├{'─' * (sum(col_widths) + len(headers) - 1)}┤{Colors.ENDC}")
    
    # Rows
    for row in rows:
        row_str = ""
        for i, (cell, width, color) in enumerate(zip(row, col_widths, colors)):
            row_str += f" {color}{str(cell).ljust(width - 1)}{Colors.ENDC}"
            if i < len(row) - 1:
                row_str += f"{Colors.CYAN}│{Colors.ENDC}"
        print(f"{Colors.CYAN}│{row_str}{Colors.CYAN}│{Colors.ENDC}")
    
    # Bottom border
    print(f"{Colors.CYAN}└{'─' * (sum(col_widths) + len(headers) - 1)}┘{Colors.ENDC}")

def ask_confirmation(question, default=False):
    """
    Ask user for confirmation
    
    Args:
        question: Question to ask
        default: Default answer (True/False)
    
    Returns:
        bool: User's answer
    """
    choices = "Y/n" if default else "y/N"
    answer = input(f"{Colors.YELLOW}{question} ({choices}): {Colors.ENDC}").strip().lower()
    
    if not answer:
        return default
    
    return answer in ['y', 'yes']

def print_ascii_art(art_name='didinska'):
    """Print ASCII art"""
    arts = {
        'didinska': """
    ╔══════════════════════════════════════════════╗
    ║   ██████╗ ██╗██████╗ ██╗███╗   ██╗███████╗ ║
    ║   ██╔══██╗██║██╔══██╗██║████╗  ██║██╔════╝ ║
    ║   ██║  ██║██║██║  ██║██║██╔██╗ ██║███████╗ ║
    ║   ██║  ██║██║██║  ██║██║██║╚██╗██║╚════██║ ║
    ║   ██████╔╝██║██████╔╝██║██║ ╚████║███████║ ║
    ║   ╚═════╝ ╚═╝╚═════╝ ╚═╝╚═╝  ╚═══╝╚══════╝ ║
    ╚══════════════════════════════════════════════╝
        """,
        'wallet': """
    💰 WALLET HUNTER 💰
        """,
        'success': """
    ✨ SUCCESS! ✨
        """
    }
    
    art = arts.get(art_name, arts['didinska'])
    print(f"{Colors.CYAN}{art}{Colors.ENDC}")

def wait_for_enter(message="Press Enter to continue..."):
    """Wait for user to press Enter"""
    input(f"{Colors.GRAY}{message}{Colors.ENDC}")
