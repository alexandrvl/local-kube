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

variable "datacenter" { type = string }
variable "datastore" { type = string }
variable "network" { type = string }
variable "resource_pool" { type = string }
variable "template" { type = string }
variable "folder" { type = string }
variable "ssh_public_key" { type = string }
