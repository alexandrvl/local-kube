locals {
  multipass_nodes = {
    for name, inst in multipass_instance.node : name => {
      name     = name
      role     = var.nodes[name].role
      ip       = [for ip in inst.ipv4 : ip if !startswith(ip, "10.42.")][0]
      ssh_user = var.nodes[name].ssh_user
      cpus     = var.nodes[name].cpus
      memory   = var.nodes[name].memory
      disk     = var.nodes[name].disk
      address  = var.nodes[name].address
    }
  }
}

output "nodes" {
  description = "Node contract after the selected hypervisor has assigned addresses."
  value       = local.multipass_nodes
}

output "hypervisor" {
  value = var.hypervisor
}
