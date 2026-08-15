# -*- coding: utf-8 -*-
"""安装依赖（Windows）"""
import subprocess
import sys

subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])
print('依赖安装完成')
