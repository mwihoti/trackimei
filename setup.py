from setuptools import setup, find_packages

setup(
    name="vaulttrace-imei",
    version="0.1.0",
    description="VaultTrace IMEI TAC decoder and carrier inference",
    author="Nicole / The OSINT Vault",
    packages=find_packages(),
    install_requires=["phonenumbers>=8.13"],
    entry_points={
        "console_scripts": [
            "vaulttrace-imei=vaulttrace_imei.cli:main",
        ],
    },
    python_requires=">=3.8",
)
