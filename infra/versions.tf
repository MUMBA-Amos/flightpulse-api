terraform {
  # 1.7+ for import blocks that take variables.
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State is kept on this machine (terraform.tfstate, git-ignored).
  # Back it up: losing it means re-importing everything.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "flightpulse"
      ManagedBy = "terraform"
    }
  }
}
