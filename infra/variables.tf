variable "region" {
  description = "AWS region the EC2 server runs in, e.g. ap-southeast-1."
  type        = string
}

variable "instance_id" {
  description = "ID of the existing EC2 server (EC2 console -> Instances), e.g. i-0abc123."
  type        = string
}

variable "security_group_id" {
  description = "ID of the server's security group (instance -> Security tab), e.g. sg-0abc123."
  type        = string
}

variable "alert_email" {
  description = "Where alarm and budget emails go."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly spend that triggers a budget email. Credits are not subtracted, so this tracks how fast the credit is used."
  type        = number
  default     = 15
}
