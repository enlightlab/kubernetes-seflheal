package enlight.secrets

deny[msg] {
    input.content
    regex.match(`(?i)(api[_-]?key|secret|password)\s*=\s*["'][^"']{8,}["']`, input.content)
    msg := "Hardcoded secret detected (control SEC-001)"
}

deny[msg] {
    input.content
    regex.match(`ghp_[A-Za-z0-9]{20,}`, input.content)
    msg := "GitHub token pattern detected (control SEC-002)"
}
