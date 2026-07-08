# -*- mode: python ; coding: utf-8 -*-

import fnmatch
import os


def _filter_binaries(toc, patterns):
    """从 TOC 中移除文件名匹配 patterns 的非必要系统二进制文件"""
    filtered = []
    for item in toc:
        dest_name = os.path.basename(item[0]).lower()
        src_path = item[1]
        if any(fnmatch.fnmatch(dest_name, pattern) for pattern in patterns):
            if 'Java' in src_path or 'temurin' in src_path.lower():
                continue
        filtered.append(item)
    return filtered


block_cipher = None


a = Analysis(['TwoPush.py'],
             pathex=[],
             binaries=[],
             datas=[],
             hiddenimports=[
                 'onepush',
                 'requests',
                 'socks',
                 'colorama',
                 'modules.config_manager',
                 'modules.config_migration',
                 'modules.logger_manager',
                 'modules.notification',
                 'modules.self_config',
                 'modules.self_updater',
                 'modules.self_utils',
                 'modules.utils',
                 'modules.version',
             ],
             hookspath=[],
             runtime_hooks=[],
             excludes=[
                 'altgraph',
                 'astroid',
                 'atomicwrites',
                 'attrs',
                 'babel',
                 'bcrypt',
                 'black',
                 'blinker',
                 'boto',
                 'boto3',
                 'botocore',
                 'cairo',
                 'cffi',
                 'cryptography',
                 'curses',
                 'distutils',
                 'docutils',
                 'easy_install',
                 'faulthandler',
                 'flask',
                 'future',
                 'gevent',
                 'greenlet',
                 'h5py',
                 'idlelib',
                 'ipykernel',
                 'IPython',
                 'isort',
                 'jinja2',
                 'jupyter',
                 'lib2to3',
                 'markupsafe',
                 'matplotlib',
                 'mock',
                 'multiprocessing',
                 'nacl',
                 'numpy',
                 'paramiko',
                 'pexpect',
                 'pickle',
                 'pickleshare',
                 'PIL',
                 'pip',
                 'pkg_resources',
                 'prompt_toolkit',
                 'psutil',
                 'ptyprocess',
                 'pyasn1',
                 'pycodestyle',
                 'pycparser',
                 'pyflakes',
                 'pygame',
                 'pygments',
                 'pylint',
                 'pynacl',
                 'PyQt4',
                 'PyQt5',
                 'PyQt6',
                 'PySide2',
                 'PySide6',
                 'pytest',
                 'scipy',
                 'setuptools',
                 'shelve',
                 'sphinx',
                 'sqlalchemy',
                 'tkinter',
                 'toml',
                 'tornado',
                 'traitlets',
                 'unittest',
                 'wcwidth',
                 'wheel',
                 'wx',
                 'xml',
                 'xmlrpc',
                 'yaml',
                 'zmq',
             ],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)

# 过滤 runner 环境泄漏的 JDK/系统 DLL（api-ms-win-*, ucrtbase）
# VCRUNTIME140.dll 保留不过滤，保证用户端兼容性
a.binaries = _filter_binaries(
    a.binaries,
    ['api-ms-win-*.dll', 'ucrtbase.dll'],
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name='TwoPush',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=['python3*.dll', 'VCRUNTIME*.dll', 'api-ms-win-*.dll'],
          runtime_tmpdir=None,
          console=True,
          disable_windowed_traceback=False,
          target_arch=None,
          codesign_identity=None,
          entitlements_file=None)
