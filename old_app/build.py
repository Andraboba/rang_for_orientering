# -*- coding: utf-8 -*-
import sys
from PyInstaller import __main__

def convert_to_exe(script_path):
    sys.argv = ["pyinstaller", "--onefile", "--windowed", script_path]
    __main__.run()

if __name__ == "__main__":
    script_path = r"C:\Users\BAZA PC\PycharmProjects\mama\44654.py"
    convert_to_exe(script_path)


