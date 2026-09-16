#!/usr/bin/env python3
"""Launch the debug iOS app once with private relay pairing material."""

import argparse
import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any


def main() -> None:
    """Pass pairing secrets directly from protected files to a USB device launch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--device", required=True)
    parser.add_argument("--developer-dir", required=True, type=Path)
    args = parser.parse_args()

    directory = args.directory.expanduser().resolve()
    metadata: dict[str, Any] = json.loads((directory / "pairing.json").read_text())
    environment = {
        "LIFEOS_RELAY_PAIRING": "1",
        "LIFEOS_RELAY_ROTATION": "1",
        "LIFEOS_RELAY_ORIGIN": str(metadata["origin"]),
        "LIFEOS_RELAY_CONVERSATION_ID": str(metadata["conversationID"]),
        "LIFEOS_RELAY_PARTICIPANT_ID": str(metadata["participantID"]),
        "LIFEOS_RELAY_DEVICE_ID": str(metadata["deviceID"]),
        "LIFEOS_RELAY_SIGNING_KEY": (directory / "request-signing-key.b64").read_text().strip(),
        "LIFEOS_RELAY_PKCS12": base64.b64encode((directory / "client.p12").read_bytes()).decode(),
        "LIFEOS_RELAY_PKCS12_PASSWORD": (directory / "client-p12-password").read_text().strip(),
        "LIFEOS_RELAY_SERVER_CERT_SHA256": str(metadata["serverCertificateSHA256"]),
    }
    command = [
        "/usr/bin/env",
        *(f"DEVICECTL_CHILD_{key}={value}" for key, value in environment.items()),
        "/usr/bin/xcrun",
        "devicectl",
        "device",
        "process",
        "launch",
        "--device",
        args.device,
        "--terminate-existing",
        "com.lifeos.SecretaryApp",
    ]
    child_environment = dict(os.environ)
    child_environment["DEVELOPER_DIR"] = str(args.developer_dir)
    result = subprocess.run(command, env=child_environment, check=False)  # noqa: S603
    if result.returncode != 0:
        message = "Pairing launch failed; launch credentials were not retained by the app."
        raise SystemExit(message)
    print("One-shot pairing launch completed; relaunch without pairing variables next.")


if __name__ == "__main__":
    main()
