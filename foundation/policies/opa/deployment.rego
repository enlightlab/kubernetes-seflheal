package enlight.deployment

deny[msg] {
    input.kind == "Deployment"
    container := input.spec.template.spec.containers[_]
    not container.resources.limits.cpu
    msg := sprintf("container '%v' must declare cpu limits", [container.name])
}

deny[msg] {
    input.kind == "Deployment"
    container := input.spec.template.spec.containers[_]
    not container.resources.limits.memory
    msg := sprintf("container '%v' must declare memory limits", [container.name])
}

deny[msg] {
    input.kind == "Deployment"
    container := input.spec.template.spec.containers[_]
    endswith(container.image, ":latest")
    msg := sprintf("container '%v' uses forbidden :latest tag", [container.name])
}

deny[msg] {
    input.kind == "Deployment"
    container := input.spec.template.spec.containers[_]
    not approved_registry(container.image)
    msg := sprintf("image '%v' is not from an approved registry", [container.image])
}

approved_registry(image) {
    startswith(image, "public.ecr.aws/")
}

approved_registry(image) {
    startswith(image, "123456789012.dkr.ecr.")
}

approved_registry(image) {
    startswith(image, "172.26.48.1:5000/")
}

approved_registry(image) {
    startswith(image, "localhost:5000/")
}

approved_registry(image) {
    startswith(image, "enlight-fastapi:")
}
