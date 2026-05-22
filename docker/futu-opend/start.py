#!/usr/bin/env python3
"""Generate FutuOpenD.xml from environment variables and ensure binary is available."""

import os
import subprocess
import sys
import xml.etree.ElementTree as ET

FUTU_OPEND_VER = os.environ.get("FUTU_OPEND_VER", "10.5.6508")
_DEFAULT_DOWNLOAD_URL = (
    f"https://softwaredownload.futunn.com/"
    f"Futu_OpenD_{FUTU_OPEND_VER}_Ubuntu18.04.tar.gz"
)
FUTU_DOWNLOAD_URL = os.environ.get("FUTU_DOWNLOAD_URL", _DEFAULT_DOWNLOAD_URL)
VOLUME_DIR = "/home/futu/.com.futunn.FutuOpenD"


def get_env_or_hostname(key: str) -> str:
    """Return env var value, or read /etc/hostname if unset/empty."""
    value = os.environ.get(key, "")
    if not value:
        with open("/etc/hostname", encoding="utf-8") as f:
            value = f.read().strip()
    return value


def build_xml_tree(
    *,
    ip: str,
    api_port: str,
    login_account: str,
    login_pwd_md5: str,
    telnet_port: str,
    rsa_private_key: str,
) -> ET.Element:
    """Build and return the FutuOpenD XML root element."""
    root = ET.Element("futu_opend")

    # Basic parameters
    ET.SubElement(root, "ip").text = ip
    ET.SubElement(root, "api_port").text = api_port
    ET.SubElement(root, "login_account").text = login_account
    ET.SubElement(root, "login_pwd_md5").text = login_pwd_md5
    ET.SubElement(root, "lang").text = os.environ.get("FUTU_OPEND_LANG", "en")

    # Advanced parameters
    ET.SubElement(root, "log_level").text = "info"
    ET.SubElement(root, "push_proto_type").text = "0"
    ET.SubElement(root, "telnet_ip").text = ip
    ET.SubElement(root, "telnet_port").text = telnet_port
    ET.SubElement(root, "rsa_private_key").text = rsa_private_key
    ET.SubElement(root, "price_reminder_push").text = "1"
    ET.SubElement(root, "auto_hold_quote_right").text = "1"

    # FUTU US parameters
    ET.SubElement(root, "pdt_protection").text = "1"
    ET.SubElement(root, "dtcall_confirmation").text = "1"

    return root


def _indent_fallback(elem: ET.Element, level: int = 0) -> None:
    """Pretty-print indentation for Python < 3.9."""
    i = "\n" + level * "\t"
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "\t"
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for child in elem:
            _indent_fallback(child, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def write_xml(root: ET.Element, path: str) -> None:
    """Pretty-print and write XML to *path*, validating it."""
    if hasattr(ET, "indent"):
        ET.indent(root, space="\t")
    else:
        _indent_fallback(root)

    tree = ET.ElementTree(root)
    tree.write(path, encoding="UTF-8", xml_declaration=True)

    # Validate by reading back
    try:
        ET.parse(path)
    except ET.ParseError as exc:
        print(f"ERROR: Generated XML at {path} is invalid: {exc}", file=sys.stderr)
        sys.exit(1)


def ensure_binary() -> str:
    """Ensure FutuOpenD binary is available, downloading to volume if needed."""
    if os.environ.get("FUTU_OPEND_SKIP_DOWNLOAD"):
        # Test / CI shortcut — caller must provide a valid binary path externally
        fallback = "/bin/FutuOpenD"
        if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
            return fallback
        print(
            "ERROR: FUTU_OPEND_SKIP_DOWNLOAD is set but /bin/FutuOpenD is missing",
            file=sys.stderr,
        )
        sys.exit(1)

    version = os.environ.get("FUTU_OPEND_VER", "10.5.6508")
    download_url = os.environ.get(
        "FUTU_DOWNLOAD_URL",
        f"https://softwaredownload.futunn.com/Futu_OpenD_{version}_Ubuntu18.04.tar.gz",
    )

    os.makedirs(VOLUME_DIR, exist_ok=True)

    expected_dir = os.path.join(VOLUME_DIR, f"Futu_OpenD_{version}_Ubuntu18.04")
    # The official tar.gz has a nested directory structure:
    # Futu_OpenD_{ver}_Ubuntu18.04/Futu_OpenD_{ver}_Ubuntu18.04/FutuOpenD
    nested_dir = os.path.join(expected_dir, f"Futu_OpenD_{version}_Ubuntu18.04")
    if os.path.isdir(nested_dir):
        expected_binary = os.path.join(nested_dir, "FutuOpenD")
    else:
        expected_binary = os.path.join(expected_dir, "FutuOpenD")
    if os.path.isfile(expected_binary) and os.access(expected_binary, os.X_OK):
        print(f"Using cached Futu OpenD binary: {expected_binary}")
        return expected_binary

    # Download
    tar_path = os.path.join(VOLUME_DIR, "Futu_OpenD.tar.gz")
    print(f"Downloading Futu OpenD {version}...")
    result = subprocess.run(
        [
            "curl",
            "-k",
            "-fL",
            download_url,
            "-H",
            "accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H",
            "user-agent: Mozilla/5.0",
            "-o",
            tar_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to download Futu OpenD: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Extract
    print("Extracting Futu OpenD...")
    result = subprocess.run(
        ["tar", "-xzf", tar_path, "-C", VOLUME_DIR],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to extract Futu OpenD: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    os.unlink(tar_path)

    if not os.path.isfile(expected_binary):
        print(
            f"ERROR: FutuOpenD binary not found at {expected_binary} after extraction",
            file=sys.stderr,
        )
        sys.exit(1)

    os.chmod(expected_binary, 0o755)
    print(f"Futu OpenD ready: {expected_binary}")
    return expected_binary


def main() -> None:
    """Entry point: validate env vars, ensure binary, write XML, and exec FutuOpenD."""
    # --- Validate required credentials ---
    account_id = os.environ.get("FUTU_ACCOUNT_ID", "")
    account_pwd_md5 = os.environ.get("FUTU_ACCOUNT_PWD_MD5", "")

    if not account_pwd_md5:
        account_pwd = os.environ.get("FUTU_ACCOUNT_PWD", "")
        if account_pwd:
            print(
                "WARNING: FUTU_ACCOUNT_PWD is deprecated; "
                "set FUTU_ACCOUNT_PWD_MD5 instead.",
                file=sys.stderr,
            )
            import hashlib

            account_pwd_md5 = hashlib.md5(account_pwd.encode()).hexdigest()

    if not account_id:
        print("ERROR: FUTU_ACCOUNT_ID is required", file=sys.stderr)
        sys.exit(1)

    if not account_pwd_md5:
        print("ERROR: FUTU_ACCOUNT_PWD_MD5 is required", file=sys.stderr)
        sys.exit(1)

    # --- Optional parameters ---
    futu_opend_ip = get_env_or_hostname("FUTU_OPEND_IP")
    futu_opend_port = os.environ.get("FUTU_OPEND_PORT", "11111")
    futu_opend_telnet_port = os.environ.get("FUTU_OPEND_TELNET_PORT", "22222")
    futu_opend_rsa_file_path = os.environ.get(
        "FUTU_OPEND_RSA_FILE_PATH", "/.futu/futu.pem"
    )

    print(f"FUTU_ACCOUNT_ID: {account_id}")
    print(f"FUTU_OPEND_RSA_FILE_PATH: {futu_opend_rsa_file_path}")
    print(f"FUTU_OPEND_IP: {futu_opend_ip}")

    # --- Ensure binary is available ---
    binary_path = ensure_binary()

    # --- Build and write XML ---
    root = build_xml_tree(
        ip=futu_opend_ip,
        api_port=futu_opend_port,
        login_account=account_id,
        login_pwd_md5=account_pwd_md5,
        telnet_port=futu_opend_telnet_port,
        rsa_private_key=futu_opend_rsa_file_path,
    )

    xml_path = "/tmp/FutuOpenD.xml"
    print("Writing FutuOpenD.xml")
    write_xml(root, xml_path)

    # --- Launch FutuOpenD ---
    print("Starting FutuOpenD...")
    os.execv(binary_path, [binary_path, f"-cfg_file={xml_path}"])


if __name__ == "__main__":
    main()
