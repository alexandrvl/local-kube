data "vsphere_datacenter" "dc" {
  name = var.datacenter
}

data "vsphere_datastore" "ds" {
  name          = var.datastore
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_network" "net" {
  name          = var.network
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_resource_pool" "pool" {
  name          = var.resource_pool
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_virtual_machine" "template" {
  name          = var.template
  datacenter_id = data.vsphere_datacenter.dc.id
}

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

resource "vsphere_virtual_machine" "node" {
  for_each = var.nodes

  name             = each.key
  resource_pool_id = data.vsphere_resource_pool.pool.id
  datastore_id     = data.vsphere_datastore.ds.id
  folder           = var.folder != "" ? var.folder : null
  num_cpus         = each.value.cpus
  memory           = local.memory_mb[each.key]
  guest_id         = data.vsphere_virtual_machine.template.guest_id
  scsi_type        = data.vsphere_virtual_machine.template.scsi_type

  network_interface {
    network_id   = data.vsphere_network.net.id
    adapter_type = data.vsphere_virtual_machine.template.network_interface_types[0]
  }

  disk {
    label            = "disk0"
    size             = local.disk_gb[each.key]
    thin_provisioned = true
  }

  clone {
    template_uuid = data.vsphere_virtual_machine.template.id
  }

  lifecycle {
    precondition {
      condition     = each.key != "agent-sandbox"
      error_message = "refusing to manage the agent-sandbox VM"
    }
  }
}
