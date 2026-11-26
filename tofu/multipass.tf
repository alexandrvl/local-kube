resource "multipass_instance" "node" {
  for_each = var.nodes

  name          = each.key
  cpus          = each.value.cpus
  memory        = each.value.memory
  disk          = each.value.disk
  image         = each.value.image
  zone          = var.multipass_zone
  resize_policy = "in_place"

  # image and zone are launch-time. Imported memory and disk come back as byte
  # counts (4GiB, 3GiB, 30GiB). Ignoring those three avoids stopping a live VM
  # just to rewrite the unit string. New instances still use the values above.
  lifecycle {
    ignore_changes = [image, zone, memory, disk]
    precondition {
      condition     = each.key != "agent-sandbox"
      error_message = "refusing to manage the agent-sandbox VM"
    }
  }
}
