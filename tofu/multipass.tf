resource "multipass_instance" "node" {
  for_each = var.nodes

  name          = each.key
  cpus          = each.value.cpus
  memory        = each.value.memory
  disk          = each.value.disk
  image         = each.value.image
  zone          = var.multipass_zone
  resize_policy = "in_place"

  # image and zone are launch-time. Disk stays ignored because Multipass
  # reports bytes after launch. Memory is not ignored: the declared size is
  # what the instance is created with.
  lifecycle {
    ignore_changes = [image, zone, disk]
    precondition {
      condition     = each.key != "agent-sandbox"
      error_message = "refusing to manage the agent-sandbox VM"
    }
  }
}
