#!/usr/bin/env python3
"""Reject a plan that touches agent-sandbox or any resource other than the two lab nodes."""
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
allowed = {
    'multipass_instance.node["k3s-server"]',
    'multipass_instance.node["k3s-agent"]',
}
plan_path = root / "tofu" / "tfplan"
cmd = ["tofu", "plan", "-input=false", "-out", str(plan_path)]
if mode == "destroy":
    cmd.insert(2, "-destroy")
proc = subprocess.run(cmd, cwd=root / "tofu", text=True)
if proc.returncode != 0:
    sys.exit(proc.returncode)
plan = json.loads(subprocess.check_output(["tofu", "show", "-json", str(plan_path)], cwd=root / "tofu", text=True))
blob = json.dumps(plan)
if "agent-sandbox" in blob:
    sys.exit("plan mentions agent-sandbox; refusing")
for change in plan.get("resource_changes", []):
    actions = change["change"]["actions"]
    if actions == ["no-op"]:
        continue
    address = change["address"]
    if address not in allowed:
        sys.exit(f"refusing {actions} on {address}")
    if mode != "destroy" and "delete" in actions:
        sys.exit(f"refusing to delete {address}; use make destroy")
print("plan guard ok")
