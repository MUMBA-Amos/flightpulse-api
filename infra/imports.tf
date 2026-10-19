# Brings the server built by hand in the console under Terraform without
# recreating it. Their resource blocks are generated from the live settings
# on the first plan (see README.md), so they match exactly.

import {
  to = aws_instance.api
  id = var.instance_id
}

import {
  to = aws_security_group.api
  id = var.security_group_id
}
