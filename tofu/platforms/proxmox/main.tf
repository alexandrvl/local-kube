terraform {
  required_version = "1.13.10"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.124.0"
    }
  }
}

provider "proxmox" {
  endpoint = var.proxmox_endpoint
  insecure = true
}

module "nodes" {
  source = "../../modules/proxmox"

  nodes          = var.nodes
  endpoint       = var.proxmox_endpoint
  node_name      = var.proxmox_node_name
  datastore      = var.proxmox_datastore
  bridge         = var.proxmox_bridge
  template_id    = var.proxmox_template_id
  ssh_public_key = var.ssh_public_key
}

output "nodes" {
  value = module.nodes.nodes
}

output "hypervisor" {
  value = "proxmox"
}
