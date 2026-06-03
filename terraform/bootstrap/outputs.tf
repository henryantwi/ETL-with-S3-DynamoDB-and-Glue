output "plan_role_arn" {
  description = "ARN for repo secret AWS_PLAN_ROLE_ARN"
  value       = aws_iam_role.plan.arn
}

output "deploy_role_arn" {
  description = "ARN for repo secret AWS_DEPLOY_ROLE_ARN"
  value       = aws_iam_role.deploy.arn
}

output "oidc_provider_arn" {
  description = "GitHub Actions OIDC provider ARN"
  value       = aws_iam_openid_connect_provider.github.arn
}
