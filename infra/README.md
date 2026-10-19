# FlightPulse infrastructure

Terraform for the AWS side of the API: the EC2 server and its security group,
plus free email alerts. All commands run on your Mac; Terraform only changes
AWS resources, not the app's code.

| File | What it holds |
|---|---|
| `versions.tf` | Terraform and AWS provider versions; tags every resource `Project=flightpulse`, `ManagedBy=terraform` |
| `variables.tf` | The inputs: region, instance ID, security group ID, alert email, monthly budget |
| `terraform.tfvars` | Your values for those inputs (git-ignored; copy from `terraform.tfvars.example`) |
| `imports.tf` | Adopted the hand-built server and security group without recreating them |
| `instance.tf` | The server (`t3.small`, Ubuntu, 8 GB disk) and its firewall rules (SSH, HTTP, HTTPS) |
| `monitoring.tf` | Email alarms: status check failed, CPU credits low, extra CPU charges, CPU above 90% |
| `budget.tf` | Email when the month's spend nears `monthly_budget_usd` (counted before credits) |
| `backup.tf` | Daily snapshot of the server's disk at 02:00 Malaysia time, keeping the last 7 |
| `terraform.tfstate` | What Terraform manages in AWS (git-ignored, **only on this Mac: back it up**) |

The alerts fit AWS's free allowances (4 of 10 free CloudWatch alarms, SNS
email, a cost budget). The only cost is the disk snapshots, about $0.40 a
month at most, paid from the AWS credit.

## Changing the infrastructure

Every change follows the same loop:

```bash
cd ~/Documents/projects/flightpulse/flightpulse-api/infra
# 1. edit a .tf file, or a value in terraform.tfvars
terraform plan      # 2. read what will change
terraform apply     # 3. shows the plan again; type: yes
# 4. back up the state to iCloud Drive, then commit the .tf files
cp terraform.tfstate ~/Library/Mobile\ Documents/com~apple~CloudDocs/flightpulse-terraform.tfstate
```

## Backups

| What | Kept in |
|---|---|
| The server's whole disk (code, `.env`, nginx, cache) | EBS snapshots, daily, last 7 kept (`backup.tf`) |
| API code, `deploy/` (systemd, nginx, `setup.sh`), `infra/*.tf` | git |
| `terraform.tfstate` | iCloud Drive, copied after every apply (step 4 above) |

Snapshots are listed in the EC2 console under Elastic Block Store →
Snapshots. To restore one: Snapshots → select it → Create volume from
snapshot (same availability zone as the server, `ap-southeast-5c`), stop the
server, detach its old root volume, attach the new one as `/dev/sda1`, and
start it.

### Reading the plan

The last line sums it up, e.g. `Plan: 0 to add, 1 to change, 0 to destroy.`

| Symbol | Meaning | What to do |
|---|---|---|
| `+` | create | Usually fine |
| `~` | update in place | Usually fine |
| `-/+` | delete and recreate ("must be replaced") | **Stop.** On the server this means a new machine with a new IP |
| `-` | destroy | **Stop**, unless you removed it on purpose |

If the plan shows anything you didn't expect, don't apply.

### Common changes

| To… | Edit | Server restarts? |
|---|---|---|
| Change the budget | `monthly_budget_usd` in `terraform.tfvars` | No |
| Change an alarm threshold | `threshold` in `monitoring.tf` | No |
| Open or close a port | the `ingress` list in `instance.tf` | No |
| Stop paying for extra CPU | `cpu_credits = "standard"` in `instance.tf` (the server slows down instead) | No |
| Resize the server | `instance_type` in `instance.tf` | **Yes**, stops and starts; the public IP changes unless an Elastic IP is attached |
| Grow the disk | `volume_size` in `instance.tf` | No, but run `sudo growpart` and `sudo resize2fs` on the server afterwards |

### Example: lowering the CPU alarm

In `monitoring.tf`, in the `cpu_high` block, change `threshold = 90` to
`threshold = 80`. `terraform plan` shows only:

```
  ~ resource "aws_cloudwatch_metric_alarm" "cpu_high" {
      ~ threshold = 90 -> 80
    }
Plan: 0 to add, 1 to change, 0 to destroy.
```

After `terraform apply`, CloudWatch → Alarms → `flightpulse-cpu-high` (region
Asia Pacific (Malaysia)) shows the new condition.

### Rules

- **Change these resources here, not in the AWS console.** A console edit shows
  up on the next plan, which then tries to undo it.
- **The server can't be deleted by Terraform.** `instance.tf` has
  `prevent_destroy = true`; a plan that would delete it fails instead.
- **Keep `terraform.tfstate` safe.** Losing it means Terraform no longer knows
  what it manages, and everything has to be imported again.
- **Never commit `terraform.tfvars`, `terraform.tfstate` or AWS keys.** The
  `.gitignore` here already excludes the first two.

## How it was set up

These steps are already done; they're here to set it up again on a new Mac or
for another project.

### 1. Install the tools

Terraform isn't in Homebrew's main list anymore, so it comes from HashiCorp's
own tap. Install them separately: if one name in `brew install a b` isn't
found, neither is installed.

```bash
brew install awscli
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
terraform -version    # needs 1.7 or newer
```

### 2. Give the AWS CLI access

1. AWS Console → IAM → Users → Create user (`terraform-admin`).
2. Attach policies directly → `AdministratorAccess`. Without a policy, every
   call fails with `AccessDenied` / `UnauthorizedOperation`.
3. The user → Security credentials → Create access key → Command Line
   Interface (CLI).
4. Run `aws configure`: paste the key ID and secret, region `ap-southeast-5`,
   output `json`. The keys are saved in `~/.aws/credentials`.
5. Check: `aws sts get-caller-identity` prints the `terraform-admin` user.

The secret key controls the whole account: only ever paste it into
`aws configure`. If it ends up anywhere else, create a new key and delete the
old one.

### 3. Fill in the values

```bash
cd ~/Documents/projects/flightpulse/flightpulse-api/infra
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`, not the `.example` file: Terraform only reads
`terraform.tfvars`, and asks for each value at the prompt if it's missing.
The IDs are in the EC2 console: the instance's Details tab (`i-…`) and
Security tab (`sg-…`, the `launch-wizard-1` group, not `default`).

### 4. Import the existing server

```bash
terraform init                                   # downloads the AWS provider
terraform plan -generate-config-out=instance.tf  # writes instance.tf from the live server
```

The generate step runs **once**; afterwards it refuses because `instance.tf`
exists. The generated file had settings that can't be used together, which
`terraform plan` reported as `Conflicting configuration arguments`. They were
fixed by deleting from `instance.tf`:

- the `primary_network_interface` block, `associate_public_ip_address`,
  `ipv6_address_count`, `ipv6_addresses` and `security_groups` (AWS still
  keeps these; they just don't need to be written down)
- the `tags_all` lines and every attribute set to `null`

and adding `lifecycle { prevent_destroy = true }` to the server.
`terraform validate` checks the files without contacting AWS.

The first real plan was `2 to import, 7 to add, 2 to change, 0 to destroy`.
The 2 changes only added tags, so the server didn't restart.

### 5. Apply and confirm alerts

```bash
terraform apply   # type: yes
```

AWS then emails "AWS Notification - Subscription Confirmation". Click
**Confirm subscription**, or the alarms can't email you.

## Not set up yet

- **Elastic IP.** The public IP changes if the server is stopped and started,
  which would break `DEPLOY_HOST` and the Vercel proxy.
- **Memory and disk metrics.** CloudWatch doesn't see them without its agent
  on the server; check with `free -h` and `df -h` meanwhile.
