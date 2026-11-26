output "nodes" {
  value = {
    for name, vm in proxmox_virtual_environment_vm.node : name => {
      name     = name
      role     = var.nodes[name].role
      ip       = var.nodes[name].address == "dhcp" ? vm.ipv4_addresses[0][0] : var.nodes[name].address
      ssh_user = var.nodes[name].ssh_user
      cpus     = var.nodes[name].cpus
      memory   = var.nodes[name].memory
      disk     = var.nodes[name].disk
      address  = var.nodes[name].address
    }
  }
}
