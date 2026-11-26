variable "hypervisor" {
  type        = string
  default     = "multipass"
  description = "This workspace is Multipass only. VMware and Proxmox are separate roots under platforms/. Changing this value is rejected so a switch cannot drop the live VMs from this state."

  validation {
    condition     = var.hypervisor == "multipass"
    error_message = "This workspace only applies Multipass. Use tofu/platforms/vmware or tofu/platforms/proxmox for the other hypervisors."
  }
}

variable "nodes" {
  description = "Node contract shared by every hypervisor module. address is \"dhcp\" or a host IP. role is server or agent."
  type = map(object({
    role     = string
    cpus     = number
    memory   = string
    disk     = string
    address  = string
    ssh_user = string
    image    = string
  }))
  default = {
    k3s-server = {
      role     = "server"
      cpus     = 2
      memory   = "4G"
      disk     = "30G"
      address  = "dhcp"
      ssh_user = "ubuntu"
      image    = "24.04"
    }
    k3s-agent = {
      role     = "agent"
      cpus     = 2
      memory   = "3G"
      disk     = "30G"
      address  = "dhcp"
      ssh_user = "ubuntu"
      image    = "24.04"
    }
  }

  validation {
    condition = alltrue([
      for name, node in var.nodes : contains(["server", "agent"], node.role) && name != "agent-sandbox"
    ])
    error_message = "Each node role must be server or agent, and agent-sandbox must never be declared."
  }
}

variable "multipass_zone" {
  type        = string
  default     = "zone1"
  description = "Multipass availability zone. Both adopted lab nodes already sit in zone1."
}
