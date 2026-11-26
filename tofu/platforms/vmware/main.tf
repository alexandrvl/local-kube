terraform {
  required_version = "1.13.10"

  required_providers {
    vsphere = {
      source  = "vmware/vsphere"
      version = "2.18.0"
    }
  }
}

provider "vsphere" {
  allow_unverified_ssl = true
}

module "nodes" {
  source = "../../modules/vmware"

  nodes          = var.nodes
  datacenter     = var.vmware_datacenter
  datastore      = var.vmware_datastore
  network        = var.vmware_network
  resource_pool  = var.vmware_resource_pool
  template       = var.vmware_template
  folder         = var.vmware_folder
  ssh_public_key = var.ssh_public_key
}

output "nodes" {
  value = module.nodes.nodes
}

output "hypervisor" {
  value = "vmware"
}
