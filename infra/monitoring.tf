# Email alerts for the things EC2's "healthy" status doesn't show.
# Uses 4 of CloudWatch's 10 free alarms.

resource "aws_sns_topic" "alerts" {
  name = "flightpulse-alerts"
}

# AWS emails a confirmation link after the first apply; alerts only arrive
# once it's clicked.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  alarm_defaults = {
    namespace     = "AWS/EC2"
    dimensions    = { InstanceId = var.instance_id }
    alarm_actions = [aws_sns_topic.alerts.arn]
    ok_actions    = [aws_sns_topic.alerts.arn]
  }
}

# The server or the AWS host under it stopped responding.
# Missing data counts as fine so stopping the server on purpose stays quiet.
resource "aws_cloudwatch_metric_alarm" "status_check" {
  alarm_name          = "flightpulse-status-check-failed"
  alarm_description   = "EC2 status check failing for 5 minutes."
  namespace           = local.alarm_defaults.namespace
  dimensions          = local.alarm_defaults.dimensions
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_defaults.alarm_actions
  ok_actions          = local.alarm_defaults.ok_actions
}

# A t3.micro is only guaranteed 10% CPU; above that it spends credits
# (144 at most). Near zero, the API slows down or AWS starts charging extra.
resource "aws_cloudwatch_metric_alarm" "cpu_credits_low" {
  alarm_name          = "flightpulse-cpu-credits-low"
  alarm_description   = "CPU credit balance below 20: the server is about to be throttled or charged for extra CPU."
  namespace           = local.alarm_defaults.namespace
  dimensions          = local.alarm_defaults.dimensions
  metric_name         = "CPUCreditBalance"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 20
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_defaults.alarm_actions
  ok_actions          = local.alarm_defaults.ok_actions
}

# Unlimited mode (the t3 default) bills CPU used beyond the credits.
resource "aws_cloudwatch_metric_alarm" "cpu_surplus_charged" {
  alarm_name          = "flightpulse-cpu-surplus-charged"
  alarm_description   = "AWS is charging for CPU beyond the instance's credits."
  namespace           = local.alarm_defaults.namespace
  dimensions          = local.alarm_defaults.dimensions
  metric_name         = "CPUSurplusCreditsCharged"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_defaults.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "flightpulse-cpu-high"
  alarm_description   = "CPU above 90% for 15 minutes."
  namespace           = local.alarm_defaults.namespace
  dimensions          = local.alarm_defaults.dimensions
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 90
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_defaults.alarm_actions
  ok_actions          = local.alarm_defaults.ok_actions
}
