locals {
  memory_mb = {
    for name, node in var.nodes : name => (
      endswith(node.memory, "G") ? tonumber(trimsuffix(node.memory, "G")) * 1024 : tonumber(trimsuffix(node.memory, "M"))
    )
  }
  disk_gb = {
    for name, node in var.nodes : name => tonumber(trimsuffix(node.disk, "G"))
  }
}

resource "proxmox_virtual_environment_vm" "node" {
  for_each = var.nodes

  name      = each.key
  node_name = var.node_name

  clone {
    vm_id = var.template_id
  }

  cpu {
    cores = each.value.cpus
  }

  memory {
    dedicated = local.memory_mb[each.key]
  }

  disk {
    datastore_id = var.datastore
    interface    = "scsi0"
    size         = local.disk_gb[each.key]
  }

  network_device {
    bridge = var.bridge
  }

  initialization {
    ip_config {
      ipv4 {
        address = each.value.address == "dhcp" ? "dhcp" : each.value.address
      }
    }
    user_account {
      username = each.value.ssh_user
      keys     = var.ssh_public_key == "" ? [] : [var.ssh_public_key]
    }
  }

  lifecycle {
    precondition {
      condition     = each.key != "agent-sandbox"
      error_message = "refusing to manage the agent-sandbox VM"
    }
    precondition {
      condition     = var.endpoint != ""
      error_message = "Set proxmox_endpoint and add tofu/providers-proxmox.tf from the example before selecting proxmox."
    }
  }
}
