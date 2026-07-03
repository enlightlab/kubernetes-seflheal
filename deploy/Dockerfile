FROM python:3.12-slim-bookworm

ARG KUBECTL_VERSION=v1.31.4
ARG K8SGPT_VERSION=0.4.33
ARG TARGETARCH=amd64

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) K8SGPT_ARCH=x86_64 ;; \
      arm64) K8SGPT_ARCH=arm64 ;; \
      *) K8SGPT_ARCH=x86_64 ;; \
    esac; \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
        -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors \
        "https://github.com/k8sgpt-ai/k8sgpt/releases/download/v${K8SGPT_VERSION}/k8sgpt_Linux_${K8SGPT_ARCH}.tar.gz" \
        | tar -xz -C /usr/local/bin k8sgpt \
    && chmod +x /usr/local/bin/k8sgpt \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY web/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY web/ /app/web/
COPY deploy/k8s/staging-heal/ /app/deploy/k8s/staging-heal/
COPY deploy/k8s/staging-app/ /app/deploy/k8s/staging-app/
COPY deploy/k8s/staging-nginx/ /app/deploy/k8s/staging-nginx/
COPY deploy/k8s/argocd/ /app/deploy/k8s/argocd/

ENV DEPLOY_TARGET=oci \
    IN_CLUSTER=true \
    USE_PORT_FORWARD=false \
    PYTHONUNBUFFERED=1 \
    HEAL_OVERLAY_PATH=/app/deploy/k8s/staging-heal

WORKDIR /app/web

EXPOSE 30901

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "30901"]
