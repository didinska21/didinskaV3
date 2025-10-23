#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DIDINSKA Wallet Hunter - Main Menu
Mode: Interaktif CLI (Bahasa Indonesia)
"""

import os
import sys
import time
from utils import Colors, print_header, print_box, print_loader

# Tambahkan direktori project agar bisa import file utama
sys.path.insert(0, os.path.dirname(__file__))


def main_menu():
    """Tampilkan menu utama"""
    while True:
        # Header
        print_header()

        # Daftar menu utama
        menu_items = [
            f"{Colors.CYAN}1){Colors.ENDC} Wallet Generator {Colors.GRAY}(Mode Acak){Colors.ENDC}",
            f"   {Colors.WHITE}→ Membuat wallet acak (12 kata) & memeriksa saldo otomatis{Colors.ENDC}",
            "",
            f"{Colors.CYAN}2){Colors.ENDC} Phrase Finder {Colors.GRAY}(Pencarian Kata BIP39){Colors.ENDC}",
            f"   {Colors.WHITE}→ Menemukan wallet berdasarkan frase sebagian (contoh: wind * * fire){Colors.ENDC}",
            "",
            f"{Colors.CYAN}3){Colors.ENDC} Delegate Wallet {Colors.GRAY}(Auto Forward Native Coin){Colors.ENDC}",
            f"   {Colors.WHITE}→ Atur wallet penampung, tambah/list/hapus delegate, dan monitor otomatis{Colors.ENDC}",
            "",
            f"{Colors.CYAN}4){Colors.ENDC} Keluar {Colors.GRAY}(Tutup program){Colors.ENDC}"
        ]

        print_box("🎯 MENU UTAMA - DIDINSKA WALLET HUNTER", menu_items, Colors.BLUE)

        # Input pilihan
        choice = input(f"{Colors.YELLOW}Pilih menu (1-4): {Colors.ENDC}").strip()

        if choice == "1":
            print(f"\n{Colors.CYAN}[+] Membuka Wallet Generator (Mode Acak)...{Colors.ENDC}")
            print_loader("Menyiapkan modul", 1)
            try:
                import wallet_gen_random
                wallet_gen_random.run()
            except KeyboardInterrupt:
                print(f"\n{Colors.YELLOW}[!] Kembali ke menu utama...{Colors.ENDC}\n")
            except Exception as e:
                print(f"\n{Colors.RED}[!] Terjadi kesalahan: {e}{Colors.ENDC}\n")
                input("Tekan Enter untuk kembali...")

        elif choice == "2":
            print(f"\n{Colors.CYAN}[+] Membuka Phrase Finder (Mode Pencarian)...{Colors.ENDC}")
            print_loader("Menyiapkan modul", 1)
            try:
                import wallet_gen_phrase
                wallet_gen_phrase.run()
            except KeyboardInterrupt:
                print(f"\n{Colors.YELLOW}[!] Kembali ke menu utama...{Colors.ENDC}\n")
            except Exception as e:
                print(f"\n{Colors.RED}[!] Terjadi kesalahan: {e}{Colors.ENDC}\n")
                input("Tekan Enter untuk kembali...")

        elif choice == "3":
            print(f"\n{Colors.CYAN}[+] Membuka Delegate Wallet (Auto Forward)...{Colors.ENDC}")
            print_loader("Menyiapkan modul", 1)
            try:
                import wallet_delegate
                wallet_delegate.run()
            except KeyboardInterrupt:
                print(f"\n{Colors.YELLOW}[!] Kembali ke menu utama...{Colors.ENDC}\n")
            except Exception as e:
                print(f"\n{Colors.RED}[!] Terjadi kesalahan: {e}{Colors.ENDC}\n")
                input("Tekan Enter untuk kembali...")

        elif choice == "4":
            print(f"\n{Colors.GREEN}{'=' * 70}{Colors.ENDC}")
            print(f"{Colors.BOLD}{Colors.CYAN}Terima kasih telah menggunakan DIDINSKA Wallet Hunter!{Colors.ENDC}")
            print(f"{Colors.GREEN}{'=' * 70}{Colors.ENDC}\n")
            print(f"{Colors.YELLOW}Selamat berburu dan semoga sukses! 🚀{Colors.ENDC}\n")
            break

        else:
            print(f"{Colors.RED}[!] Pilihan tidak valid. Silakan pilih antara 1 sampai 4.{Colors.ENDC}\n")
            input("Tekan Enter untuk mencoba lagi...")


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}[!] Program dihentikan oleh pengguna.{Colors.ENDC}")
