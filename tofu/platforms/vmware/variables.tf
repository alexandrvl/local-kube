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

variable "vmware_datacenter" { type = string }
variable "vmware_datastore" { type = string }
variable "vmware_network" { type = string }
variable "vmware_resource_pool" { type = string }
variable "vmware_template" { type = string }
variable "vmware_folder" {
  type    = string
  default = ""
}
variable "ssh_public_key" {
  type    = string
  default = ""
}
