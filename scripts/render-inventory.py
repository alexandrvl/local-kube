#!/usr/bin/env python3
"""Write ansible/inventory/hosts.yml from `tofu output -json nodes`."""
import argparse
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "workspace",
    nargs="?",
    default="tofu",
    help="OpenTofu workspace directory (default: tofu/). Relative paths start at the repo root.",
)
args = parser.parse_args()
workspace = Path(args.workspace)
if not workspace.is_absolute():
    workspace = root / workspace
try:
    raw = subprocess.check_output(
        ["tofu", "output", "-json", "nodes"],
        cwd=workspace,
        text=True,
        stderr=subprocess.STDOUT,
    )
except subprocess.CalledProcessError as exc:
    raise SystemExit(f"tofu output failed in {workspace}:\n{exc.output}") from exc
nodes = json.loads(raw)
key = root / "credentials" / "lab-ssh"
servers = {name: node for name, node in nodes.items() if node["role"] == "server"}
agents = {name: node for name, node in nodes.items() if node["role"] == "agent"}
if not servers:
    raise SystemExit("node contract has no server")

def host_lines(group):
    lines = []
    for name, node in group.items():
        lines += [
            f"            {name}:",
            f"              ansible_host: {node['ip']}",
            f"              ansible_user: {node['ssh_user']}",
            f"              node_role: {node['role']}",
        ]
    return lines

text = "\n".join([
    "all:",
    "  vars:",
    f"    ansible_ssh_private_key_file: {key}",
    '    ansible_ssh_common_args: "-o StrictHostKeyChecking=accept-new"',
    "  children:",
    "    k3s:",
    "      children:",
    "        k3s_server:",
    "          hosts:",
    *host_lines(servers),
    "        k3s_agent:",
    "          hosts:",
    *host_lines(agents),
]) + "\n"
dest = root / "ansible" / "inventory" / "hosts.yml"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(text)
print(dest)
