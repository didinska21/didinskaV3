#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ANSI Color codes for terminal styling
"""

class Colors:
    """ANSI color codes"""
    
    # Reset
    ENDC = '\033[0m'
    RESET = '\033[0m'
    
    # Text styles
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ITALIC = '\033[3m'
    UNDERLINE = '\033[4m'
    BLINK = '\033[5m'
    REVERSE = '\033[7m'
    HIDDEN = '\033[8m'
    
    # Foreground colors (normal)
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    GRAY = '\033[90m'
    
    # Foreground colors (bright/light)
    LIGHT_BLACK = '\033[90m'
    LIGHT_RED = '\033[91m'
    LIGHT_GREEN = '\033[92m'
    LIGHT_YELLOW = '\033[93m'
    LIGHT_BLUE = '\033[94m'
    LIGHT_MAGENTA = '\033[95m'
    LIGHT_CYAN = '\033[96m'
    LIGHT_WHITE = '\033[97m'
    
    # Background colors (normal)
    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    BG_MAGENTA = '\033[45m'
    BG_CYAN = '\033[46m'
    BG_WHITE = '\033[47m'
    
    # Background colors (bright)
    BG_LIGHT_BLACK = '\033[100m'
    BG_LIGHT_RED = '\033[101m'
    BG_LIGHT_GREEN = '\033[102m'
    BG_LIGHT_YELLOW = '\033[103m'
    BG_LIGHT_BLUE = '\033[104m'
    BG_LIGHT_MAGENTA = '\033[105m'
    BG_LIGHT_CYAN = '\033[106m'
    BG_LIGHT_WHITE = '\033[107m'
    
    @staticmethod
    def rgb(r, g, b):
        """Generate RGB color code (24-bit)"""
        return f'\033[38;2;{r};{g};{b}m'
    
    @staticmethod
    def bg_rgb(r, g, b):
        """Generate RGB background color code (24-bit)"""
        return f'\033[48;2;{r};{g};{b}m'
    
    @staticmethod
    def colorize(text, color, bold=False):
        """Apply color to text"""
        prefix = f"{Colors.BOLD}{color}" if bold else color
        return f"{prefix}{text}{Colors.ENDC}"
    
    @staticmethod
    def success(text):
        """Green success text"""
        return Colors.colorize(text, Colors.GREEN)
    
    @staticmethod
    def error(text):
        """Red error text"""
        return Colors.colorize(text, Colors.RED)
    
    @staticmethod
    def warning(text):
        """Yellow warning text"""
        return Colors.colorize(text, Colors.YELLOW)
    
    @staticmethod
    def info(text):
        """Cyan info text"""
        return Colors.colorize(text, Colors.CYAN)
    
    @staticmethod
    def highlight(text):
        """Bold cyan highlighted text"""
        return Colors.colorize(text, Colors.CYAN, bold=True)


# Convenience functions
def green(text):
    """Shorthand for green text"""
    return f"{Colors.GREEN}{text}{Colors.ENDC}"

def red(text):
    """Shorthand for red text"""
    return f"{Colors.RED}{text}{Colors.ENDC}"

def yellow(text):
    """Shorthand for yellow text"""
    return f"{Colors.YELLOW}{text}{Colors.ENDC}"

def blue(text):
    """Shorthand for blue text"""
    return f"{Colors.BLUE}{text}{Colors.ENDC}"

def cyan(text):
    """Shorthand for cyan text"""
    return f"{Colors.CYAN}{text}{Colors.ENDC}"

def magenta(text):
    """Shorthand for magenta text"""
    return f"{Colors.MAGENTA}{text}{Colors.ENDC}"

def bold(text):
    """Shorthand for bold text"""
    return f"{Colors.BOLD}{text}{Colors.ENDC}"

def dim(text):
    """Shorthand for dim text"""
    return f"{Colors.DIM}{text}{Colors.ENDC}"


# Emoji/symbol helpers
class Symbols:
    """Common symbols for terminal UI"""
    CHECK = '✓'
    CROSS = '✗'
    ARROW = '→'
    STAR = '★'
    CIRCLE = '●'
    SQUARE = '■'
    TRIANGLE = '▲'
    DOT = '•'
    DIAMOND = '◆'
    HEART = '♥'
    COIN = '💰'
    ROCKET = '🚀'
    FIRE = '🔥'
    PARTY = '🎉'
    SEARCH = '🔍'
    WARNING = '⚠'
    INFO = 'ℹ'
    LIGHTNING = '⚡'
    LOCK = '🔒'
    KEY = '🔑'
