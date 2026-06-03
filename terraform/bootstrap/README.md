# CI/CD bootstrap (GitHub OIDC → AWS)

One-time setup that creates the trust GitHub Actions uses to reach AWS without
long-lived keys. Apply this **once with admin credentials**, before the CI
workflows can authenticate.

## What it creates

- `aws_iam_openid_connect_provider` for `token.actions.githubusercontent.com`
- **`Project1-CI-Plan`** — read-only role assumed from `pull_request` runs
  (`terraform-plan.yml`). `ReadOnlyAccess` + remote-state read.
- **`Project1-CI-Deploy`** — write role assumed only from the gated
  `production` GitHub Environment (`deploy.yml`). Manages the ETL stack +
  remote-state read/write.

The roles' trust is conditioned on the repo slug (`henryantwi/Project-1`) and,
for deploy, the `production` environment — set via the `github_repo` and
`deploy_environment` variables.

## Apply

```bash
cd terraform/bootstrap
terraform init          # local state — do not commit terraform.tfstate
terraform apply
```

State is intentionally **local**: this module bootstraps the very remote-state
access the main stack relies on, so it cannot live in that remote state.

## After apply

1. Copy the outputs into GitHub repo secrets:
   - `terraform output plan_role_arn`  → secret `AWS_PLAN_ROLE_ARN`
   - `terraform output deploy_role_arn` → secret `AWS_DEPLOY_ROLE_ARN`
2. GitHub → Settings → Environments → create **`production`** with a required
   reviewer (this is the deploy approval gate referenced by `deploy.yml`).

## Notes

- Deploy `iam:*` actions are scoped to `role/etl-*`; broaden only if the main
  stack starts creating roles under a different prefix.
- The OIDC thumbprint is GitHub's well-known value; AWS now validates the cert
  chain regardless, so it rarely needs updating.
