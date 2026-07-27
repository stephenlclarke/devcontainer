FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b AS development

ARG PARITY_BUILD_ARG=missing
RUN printf '%s\n' "$PARITY_BUILD_ARG" > /etc/devcontainer-build-arg

RUN printf '%s\n' "development" > /etc/devcontainer-build-target
