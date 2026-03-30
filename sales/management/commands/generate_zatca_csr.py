import os
import stat
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate a private key + CSR for ZATCA onboarding (OpenSSL)."

    def add_arguments(self, parser):
        parser.add_argument("--out-dir", required=True, help="Output directory for key/csr files.")
        parser.add_argument("--name", required=True, help="Certificate logical name (used in filenames).")
        parser.add_argument("--cn", required=True, help="Common Name (CN) for CSR.")
        parser.add_argument("--o", default="ZATCA Accounting", help="Organization (O).")
        parser.add_argument("--c", default="SA", help="Country (C).")

    def handle(self, *args, **options):
        out_dir = Path(options["out_dir"]).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        name = options["name"].strip()
        key_path = out_dir / f"{name}.key.pem"
        csr_path = out_dir / f"{name}.csr.pem"

        subj = f"/C={options['c']}/O={options['o']}/CN={options['cn']}"

        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key_path)],
            check=True,
        )
        os.chmod(str(key_path), stat.S_IRUSR | stat.S_IWUSR)  # 0600

        subprocess.run(
            ["openssl", "req", "-new", "-key", str(key_path), "-out", str(csr_path), "-subj", subj],
            check=True,
        )
        os.chmod(str(csr_path), stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)  # 0640

        self.stdout.write(self.style.SUCCESS("Generated:"))
        self.stdout.write(self.style.SUCCESS(f"  Private key: {key_path} (0600)"))
        self.stdout.write(self.style.SUCCESS(f"  CSR:         {csr_path} (0640)"))

