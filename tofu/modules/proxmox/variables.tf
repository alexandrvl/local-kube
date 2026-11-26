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

variable "endpoint" { type = string }
variable "node_name" { type = string }
variable "datastore" { type = string }
variable "bridge" { type = string }
variable "template_id" { type = number }
variable "ssh_public_key" { type = string }
