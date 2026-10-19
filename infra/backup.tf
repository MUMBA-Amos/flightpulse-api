# Daily snapshot of the server's disk, keeping the last 7. Covers everything
# on it, including .env. Snapshots only store what changed since the last one:
# well under $1 a month for an 8 GB disk.

# Lets AWS's snapshot scheduler (Data Lifecycle Manager) create and delete
# snapshots.
resource "aws_iam_role" "dlm" {
  name = "flightpulse-snapshots"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "dlm.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "daily" {
  description        = "flightpulse-api daily disk snapshots"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    # EBS snapshots of every disk attached to the server.
    policy_type    = "EBS_SNAPSHOT_MANAGEMENT"
    resource_types = ["INSTANCE"]
    # Picks the server by its Name tag (set in instance.tf).
    target_tags = { Name = "flightpulse-api" }

    schedule {
      name      = "daily-keep-7"
      copy_tags = true

      # 18:00 UTC is 02:00 in Malaysia, when traffic is lowest.
      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["18:00"]
      }

      retain_rule {
        count = 7
      }
    }
  }
}
