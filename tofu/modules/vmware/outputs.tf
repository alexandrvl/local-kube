output "nodes" {
  value = {
    for name, vm in vsphere_virtual_machine.node : name => {
      name     = name
      role     = var.nodes[name].role
      ip       = var.nodes[name].address == "dhcp" ? vm.default_ip_address : var.nodes[name].address
      ssh_user = var.nodes[name].ssh_user
      cpus     = var.nodes[name].cpus
      memory   = var.nodes[name].memory
      disk     = var.nodes[name].disk
      address  = var.nodes[name].address
    }
  }
}
