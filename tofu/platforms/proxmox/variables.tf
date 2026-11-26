variable "nodes" {
  type = map(object({
    role     = string
    cpus     = number
    memory   = string
    disk     = string
    address  = string
    ssh_user = string
    image    = string
  }))
}

variable "proxmox_endpoint" { type = string }
variable "proxmox_node_name" { type = string }
variable "proxmox_datastore" {
  type    = string
  default = "local-lvm"
}
variable "proxmox_bridge" {
  type    = string
  default = "vmbr0"
}
variable "proxmox_template_id" {
  type    = number
  default = 9000
}
variable "ssh_public_key" {
  type    = string
  default = ""
}
